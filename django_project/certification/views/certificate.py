# coding=utf-8

import base64
import datetime
from io import BytesIO
import os
import zipfile
from PIL import Image
import re
from decimal import Decimal
from django.contrib import messages
from django.core.mail import send_mail
from django.http import (
    Http404, HttpResponse, HttpResponseRedirect, FileResponse
)
from django.views.generic import CreateView, DetailView, TemplateView
from django.conf import settings
from django.urls import reverse
from django.db import IntegrityError
from django.core.exceptions import ValidationError
from django.shortcuts import render
from braces.views import LoginRequiredMixin
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFError
import djstripe.models
import djstripe.settings
from ..models import (
    Certificate,
    Course,
    Attendee,
    CertifyingOrganisation,
    CourseConvener,
    TrainingCenter,
    CourseType
)
from ..forms import CertificateForm
from base.models.project import Project
from changes import (
    NOTICE_TOP_UP_SUCCESS
)
from helpers.notification import send_notification


class CertificateMixin(object):
    """Mixin class to provide standard settings for Certificate."""

    model = Certificate
    form_class = CertificateForm


class CertificateCreateView(
    LoginRequiredMixin, CertificateMixin, CreateView):
    """Create view for Certificate."""

    context_object_name = 'certificate'
    template_name = 'certificate/create.html'

    def get_success_url(self):
        """Define the redirect URL.

        After successful creation of the object, the User will be redirected
        to the Course detail page.

       :returns: URL
       :rtype: HttpResponse
       """

        return reverse('course-detail', kwargs={
            'project_slug': self.project_slug,
            'organisation_slug': self.organisation_slug,
            'slug': self.course_slug
        })

    def get_context_data(self, **kwargs):
        """Get the context data which is passed to a template.

        :param kwargs: Any arguments to pass to the superclass.
        :type kwargs: dict

        :returns: Context data which will be passed to the template.
        :rtype: dict
        """

        context = super(
            CertificateCreateView, self).get_context_data(**kwargs)
        context['course'] = Course.objects.get(slug=self.course_slug)
        context['attendee'] = Attendee.objects.get(pk=self.pk)
        return context

    def get_form_kwargs(self):
        """Get keyword arguments from form.

        :returns keyword argument from the form
        :rtype: dict
        """

        kwargs = super(CertificateCreateView, self).get_form_kwargs()
        self.project_slug = self.kwargs.get('project_slug', None)
        self.organisation_slug = self.kwargs.get('organisation_slug', None)
        self.course_slug = self.kwargs.get('course_slug', None)
        self.pk = self.kwargs.get('pk', None)
        self.course = Course.objects.get(slug=self.course_slug)
        self.attendee = Attendee.objects.get(pk=self.pk)
        kwargs.update({
            'user': self.request.user,
            'course': self.course,
            'attendee': self.attendee,
        })
        return kwargs

    def form_valid(self, form):
        """Save new created certificate

        :param form
        :type form

        :returns HttpResponseRedirect object to success_url
        :rtype: HttpResponseRedirect

        We check that there is no referential integrity error when saving."""

        try:
            super(CertificateCreateView, self).form_valid(form)

            # Update organisation credits every time a certificate is issued.
            organisation = \
                CertifyingOrganisation.objects.get(
                    slug=self.organisation_slug)
            remaining_credits = \
                organisation.organisation_credits - \
                organisation.project.certificate_credit
            organisation.organisation_credits = remaining_credits
            organisation.save()

            return HttpResponseRedirect(self.get_success_url())
        except IntegrityError:
            return ValidationError(
                'ERROR: Certificate already exists!')


class CertificateDetailView(DetailView):
    """Detail view for Certificate."""

    model = Certificate
    context_object_name = 'certificate'
    template_name = 'certificate/detail.html'

    def get_context_data(self, **kwargs):
        """Get the context data which is passed to a template.

        :param kwargs: Any arguments to pass to the superclass.
        :type kwargs: dict

        :returns: Context data which will be passed to the template.
        :rtype: dict
        """

        self.certificateID = self.kwargs.get('id', None)
        self.project_slug = self.kwargs.get('project_slug', None)
        context = super(
            CertificateDetailView, self).get_context_data(**kwargs)
        issued_id = \
            Certificate.objects.all().values_list('certificateID', flat=True)
        if self.certificateID in issued_id:
            context['certificate'] = \
                Certificate.objects.get(certificateID=self.certificateID)
            certificate = context['certificate']
            convener_name = \
                '{} {}'.format(
                    certificate.course.course_convener.user.first_name,
                    certificate.course.course_convener.user.last_name)

            if certificate.course.course_convener.title:
                convener_name = \
                    '{} {}'.format(
                        certificate.course.course_convener.title,
                        convener_name)

            if certificate.course.course_convener.degree:
                convener_name = \
                    '{}, {}'.format(
                        convener_name,
                        certificate.course.course_convener.degree)

            context['convener_name'] = convener_name
        context['project_slug'] = self.project_slug
        if self.project_slug:
            context['the_project'] = \
                Project.objects.get(slug=self.project_slug)
            context['project'] = context['the_project']
        return context

    def get_queryset(self):
        """Get the queryset for this view.

        :returns: Queryset which is all certificate in the
            corresponding organisation.
        :rtype: QuerySet
        """

        qs = Certificate.objects.all()
        return qs

    def get_object(self, queryset=None):
        """Get the object for this view.

        :param queryset: A query set
        :type queryset: QuerySet

        :returns: Queryset which is filtered to only show a certificate
            depends on the input certificate ID.
        :rtype: QuerySet
        :raises: Http404
        """

        if queryset is None:
            queryset = self.get_queryset()
            certificateID = self.kwargs.get('id', None)
            if certificateID:
                try:
                    obj = queryset.get(certificateID=certificateID)
                    return obj
                except Certificate.DoesNotExist:
                    return None
            else:
                raise Http404('Sorry! Certificate by this ID is not exist.')


def generate_pdf(
        pathname, project, course, attendee, certificate, current_site):
    """Create the PDF object, using the response object as its file."""

    # Register new font
    try:
        font_folder = os.path.join(
            settings.STATIC_ROOT, 'fonts/times-new-roman')
        bold_ttf_file = os.path.join(
            font_folder, 'Times New Roman Gras 700.ttf')
        regular_ttf_file = os.path.join(
            font_folder, 'Times New Roman 400.ttf')
        pdfmetrics.registerFont(TTFont('Noto-Bold', bold_ttf_file))
        pdfmetrics.registerFont(TTFont('Noto-Regular', regular_ttf_file))
    except (TTFError, KeyError):
        pass

    page = canvas.Canvas(pathname, pagesize=landscape(A4))
    width, height = A4
    center = height * 0.5
    convener_name = \
        '{} {}'.format(
            course.course_convener.user.first_name,
            course.course_convener.user.last_name)

    if course.course_convener.title:
        convener_name = \
            '{} {}'.format(
                course.course_convener.title,
                convener_name)

    if course.course_convener.degree:
        convener_name = \
            '{}, {}'.format(
                convener_name,
                course.course_convener.degree)

    course_duration = \
        'From {} {} {} to {} {} {}'.format(
            course.start_date.day,
            course.start_date.strftime('%B'),
            course.start_date.year,
            course.end_date.day,
            course.end_date.strftime('%B'),
            course.end_date.year)

    if course.course_type.instruction_hours:
        course_duration = \
            '{} ({} hours of instruction)'.format(
                course_duration,
                course.course_type.instruction_hours)

    if project.image_file:
        project_logo = ImageReader(project.image_file)
    else:
        project_logo = None

    if course.certifying_organisation.logo:
        organisation_logo = ImageReader(
            course.certifying_organisation.logo)
    else:
        organisation_logo = None

    if project.project_representative_signature:
        project_representative_signature = \
            ImageReader(project.project_representative_signature)
    else:
        project_representative_signature = None

    if course.course_convener.signature:
        convener_signature = ImageReader(course.course_convener.signature)
    else:
        convener_signature = None

    if course.template_certificate:
        background = ImageReader(course.template_certificate)
    else:
        background = None

    # Certificate margin.
    margin_right = height - 50
    margin_left = 50
    margin_bottom = 50
    max_left = margin_right - 100

    # Draw things on the PDF. Here's where the PDF generation happens.
    # See the ReportLab documentation for the full list of functionality.
    if background is not None:
        page.drawImage(
            background, 0, 0, height=width, width=height,
            preserveAspectRatio=True, mask='auto')
    page.setFillColorRGB(0.1, 0.1, 0.1)
    page.setFont('Noto-Regular', 18)

    if project_logo is not None:
        page.drawImage(
            project_logo, 50, 450, width=100, height=100,
            preserveAspectRatio=True, mask='auto')

    if organisation_logo is not None:
        page.drawImage(
            organisation_logo, max_left, 450, height=100, width=100,
            preserveAspectRatio=True, anchor='c', mask='auto')

    page.setFont('Noto-Bold', 26)
    page.drawCentredString(center, 480, 'Certificate of Completion')

    page.setFont('Noto-Bold', 26)

    page.drawCentredString(
        center, 400, '%s %s' % (
            attendee.firstname,
            attendee.surname))
    page.setFont('Noto-Regular', 16)
    page.drawCentredString(
        center, 370, 'Has attended and completed the course:')
    page.setFont('Noto-Bold', 20)
    page.drawCentredString(
            center, 335, course.course_type.name)
    page.setFont('Noto-Regular', 16)
    page.drawCentredString(
        center, 300, 'With a trained competence in:')
    page.setFont('Noto-Bold', 14)
    page.drawCentredString(
        center, 280, '{}'.format(course.trained_competence))
    page.setFont('Noto-Regular', 16)
    page.drawCentredString(
        center, 250, '{}'.format(course_duration))

    page.setFillColorRGB(0.1, 0.1, 0.1)
    page.drawCentredString(
        center, 220, 'Convened by {} at {}'.format(
            convener_name,
            course.training_center))

    if project_representative_signature is not None:
        page.drawImage(
            project_representative_signature,
            (margin_left + 100),
            (margin_bottom + 70),
            width=100,
            height=70,
            preserveAspectRatio=True,
            anchor='s',
            mask='auto')

    if convener_signature is not None:
        page.drawImage(
            convener_signature, (margin_right - 200), (margin_bottom + 70),
            width=100, height=70, preserveAspectRatio=True, anchor='s',
            mask='auto')

    page.setFont('Noto-Regular', 12)
    if project.project_representative:
        page.drawCentredString(
            (margin_left + 150), (margin_bottom + 60),
            '{} {}'.format(
                project.project_representative.first_name,
                project.project_representative.last_name))
    page.drawCentredString(
        (margin_right - 150), (margin_bottom + 60),
        '{}'.format(convener_name))
    page.line(
        (margin_left + 70), (margin_bottom + 55),
        (margin_left + 230), (margin_bottom + 55))
    page.line(
        (margin_right - 70), (margin_bottom + 55),
        (margin_right - 230), (margin_bottom + 55))
    page.setFont('Noto-Regular', 13)
    page.drawCentredString(
        (margin_left + 150),
        (margin_bottom + 40),
        'Project Representative')
    page.drawCentredString(
        (margin_right - 150), (margin_bottom + 40), 'Course Convener')

    # Footnotes.
    page.setFont('Noto-Regular', 14)
    page.drawString(
        margin_left,
        margin_bottom - 10,
        'ID: {}'.format(certificate.certificateID))
    page.setFont('Noto-Regular', 8)
    page.drawString(
        margin_left, (margin_bottom - 20),
        'You can verify this certificate by visiting '
        'http://{}/en/{}/certificate/{}/.'
        ''.format(current_site, project.slug, certificate.certificateID))

    # Close the PDF object cleanly.
    page.showPage()
    page.save()


def certificate_pdf_view(request, **kwargs):
    project_slug = kwargs.pop('project_slug')
    course_slug = kwargs.pop('course_slug')
    pk = kwargs.pop('pk')
    project = Project.objects.get(slug=project_slug)
    course = Course.objects.get(slug=course_slug)
    attendee = Attendee.objects.get(pk=pk)
    certificate = Certificate.objects.get(course=course, attendee=attendee)
    current_site = request.META['HTTP_HOST']

    # Create the HttpResponse object with the appropriate PDF headers.
    filename = '{}.{}'.format(certificate.certificateID, 'pdf')
    project_folder = (project.name.lower()).replace(' ', '_')
    pathname = \
        os.path.join(
            '/home/web/media', 'pdf/{}/{}'.format(project_folder, filename))
    found = os.path.exists(pathname)
    if found:
        try:
            return FileResponse(open(pathname, 'rb'),
                                content_type='application/pdf')
        except FileNotFoundError:
            raise Http404()
    else:
        makepath = '/home/web/media/pdf/{}/'.format(project_folder)
        if not os.path.exists(makepath):
            os.makedirs(makepath)

        generate_pdf(
            pathname, project, course, attendee, certificate, current_site)
        try:
            return FileResponse(open(pathname, 'rb'),
                                content_type='application/pdf')
        except FileNotFoundError:
            raise Http404()


def download_certificates_zip(request, **kwargs):
    """Download all certificates in a course as one zip file."""

    project_slug = kwargs.pop('project_slug')
    course_slug = kwargs.pop('course_slug')
    course = Course.objects.get(slug=course_slug)
    organisation_slug = kwargs.pop('organisation_slug')

    certificates = Certificate.objects.filter(course=course)

    filenames = []
    for certificate in certificates:
        pdf_file = certificate_pdf_view(
            request, pk=certificate.attendee.pk, project_slug=project_slug,
            course_slug=course_slug, organisation_slug=organisation_slug)

        with open('/tmp/%s.pdf' % certificate.certificateID, 'wb') as pdf:
            pdf.write(pdf_file.getvalue())

        filenames.append('/tmp/%s.pdf' % certificate.certificateID)

    zip_subdir = '%s' % course.name

    in_memory_data = BytesIO()
    zf = zipfile.ZipFile(in_memory_data, "w")

    for fpath in filenames:
        fdir, fname = os.path.split(fpath)
        zip_path = os.path.join(zip_subdir, fname)

        zf.write(fpath, zip_path)

    zf.close()

    response = HttpResponse(
        in_memory_data.getvalue(), content_type="application/x-zip-compressed")
    response['Content-Disposition'] = \
        'attachment; filename=certificates.zip'

    return response


def update_paid_status(request, **kwargs):
    """View to update the is_paid status of certificate in a course."""

    project_slug = kwargs.pop('project_slug')
    organisation_slug = kwargs.pop('organisation_slug')
    course_slug = kwargs.pop('course_slug')
    attendee_pk = kwargs.pop('pk')
    course = Course.objects.get(slug=course_slug)
    attendee = Attendee.objects.get(pk=attendee_pk)
    project = Project.objects.get(slug=project_slug)
    url = reverse('course-detail', kwargs={
        'project_slug': project_slug,
        'organisation_slug': organisation_slug,
        'slug': course_slug
    })

    if request.method == 'POST':
        queryset = \
            Certificate.objects.filter(course=course, attendee=attendee)
        queryset.update(is_paid=True)
        organisation = \
            CertifyingOrganisation.objects.get(slug=organisation_slug)
        remaining_credits = \
            organisation.organisation_credits - project.certificate_credit
        organisation.organisation_credits = remaining_credits
        organisation.save()
        return HttpResponseRedirect(url)

    return render(
        request, 'certificate/update_is_paid.html',
        context={
            'course': course,
            'project_slug': project_slug,
            'organisation_slug': organisation_slug,
            'course_slug': course_slug,
            'attendee': attendee,
            'project': project})


def top_up_unavailable(request, **kwargs):
    project_slug = kwargs.get('project_slug', None)
    project = Project.objects.get(slug=project_slug)
    organisation = CertifyingOrganisation.objects.filter(approved=False)
    has_pending = False
    if organisation:
        has_pending = True

    return render(
        request, 'certificate/top_up_unavailable.html',
        context={
            'the_project': project,
            'has_pending_organisations': has_pending})


def email_all_attendees(request, **kwargs):
    """Send email to all attendees in a course."""

    project_slug = kwargs.get('project_slug', None)
    course_slug = kwargs.get('course_slug', None)
    organisation_slug = kwargs.get('organisation_slug', None)
    project = Project.objects.get(slug=project_slug)
    course = Course.objects.get(slug=course_slug)
    attendee_list = \
        Certificate.objects.filter(
            is_paid=True, course=course).values_list('attendee', flat=True)
    attendee_list_object = []
    for attendee_pk in attendee_list:
        attendee = Attendee.objects.get(pk=attendee_pk)
        attendee_list_object.append(attendee)

    organisation = CertifyingOrganisation.objects.filter(approved=False)
    has_pending = False
    if organisation:
        has_pending = True

    url = reverse('course-detail', kwargs={
        'project_slug': project_slug,
        'organisation_slug': organisation_slug,
        'slug': course_slug
    })

    if request.method == 'POST':

        site = request.get_host()
        for attendee in attendee_list_object:
            # Send email to each attendee with the link to his certificate.
            data = {
                'firstname': attendee.firstname.encode('utf-8'),
                'lastname': attendee.surname.encode('utf-8'),
                'coursetype': course.course_type,
                'start_date': course.start_date.strftime('%d %B %Y'),
                'end_date': course.end_date.strftime('%d %B %Y'),
                'training_center': course.training_center,
                'organisation': course.certifying_organisation.name.encode(
                    'utf-8'),
                'domain': site,
                'project_slug': course.certifying_organisation.project.slug,
                'organisation_slug': course.certifying_organisation.slug,
                'course_slug': course.slug,
                'pk': attendee.pk,
                'convener_firstname':
                    course.course_convener.user.first_name.encode('utf-8'),
                'convener_lastname':
                    course.course_convener.user.last_name.encode('utf-8')}

            send_mail(
                'Certificate from {} Course'.format(course.course_type),
                'Dear {firstname} {lastname},\n\n'
                'Congratulations!\n'
                'Your certificate from the following course '
                'has been issued.\n\n'
                'Course type: {coursetype}\n'
                'Course date: {start_date} to {end_date}\n'
                'Training center: {training_center}\n'
                'Certifying organisation: {organisation}\n\n'
                'You may print the certificate '
                'by visiting:\n'
                'http://{domain}/en/{project_slug}/certifyingorganisation/'
                '{organisation_slug}/course/'
                '{course_slug}/print/{pk}/\n\n'
                'Sincerely,\n{convener_firstname} {convener_lastname}'
                ''.format(**data),
                course.course_convener.user.email,
                [attendee.email],
                fail_silently=False,
            )

        messages.success(request, 'Email sent', 'email_sent')
        return HttpResponseRedirect(url)

    return render(
        request, 'certificate/send_email_confirm.html',
        context={
            'the_project': project,
            'has_pending_organisations': has_pending,
            'attendees': attendee_list_object})


def regenerate_certificate(request, **kwargs):
    """Regenerate a pdf certificate for an attendee."""

    project_slug = kwargs.pop('project_slug', None)
    organisation_slug = kwargs.pop('organisation_slug', None)
    course_slug = kwargs.pop('course_slug', None)
    pk = kwargs.pop('pk')
    course = Course.objects.get(slug=course_slug)
    attendee = Attendee.objects.get(pk=pk)
    project = Project.objects.get(slug=project_slug)
    certificate = Certificate.objects.get(course=course, attendee=attendee)
    certifying_organisation = (
        CertifyingOrganisation.objects.get(slug=organisation_slug))

    # Checking user permissions.
    if request.user.is_staff or request.user == project.owner or \
            request.user in project.certification_managers.all() or \
            request.user in certifying_organisation.organisation_owners.all() \
            or request.user == project.project_representative:
        pass
    else:
        raise Http404

    filename = "{}.{}".format(certificate.certificateID, "pdf")
    project_folder = (project.name.lower()).replace(' ', '_')

    pending_organisation = \
        CertifyingOrganisation.objects.filter(approved=False)
    has_pending = False
    if pending_organisation:
        has_pending = True

    pathname = \
        os.path.join(
            '/home/web/media', 'pdf/{}/{}'.format(project_folder, filename))
    found = os.path.exists(pathname)

    if request.method == 'POST':
        if found:
            # Delete existing certificate
            os.remove(pathname)

        makepath = '/home/web/media/pdf/%s/' % project_folder
        if not os.path.exists(makepath):
            os.makedirs(makepath)

        current_site = request.META['HTTP_HOST']
        generate_pdf(
            pathname, project, course, attendee, certificate, current_site)
        try:
            return FileResponse(open(pathname, 'rb'),
                                content_type='application/pdf')
        except FileNotFoundError:
            raise Http404()

    return render(
        request, 'certificate/regenerate_certificate.html',
        context={
            'the_project': project,
            'has_pending_organisations': has_pending,
            'attendee': attendee,
            'id': certificate.certificateID,
            'course': course})


def regenerate_all_certificate(request, **kwargs):
    """Regenerate all certificates within a course."""

    project_slug = kwargs.pop('project_slug', None)
    course_slug = kwargs.pop('course_slug', None)
    organisation_slug = kwargs.get('organisation_slug', None)
    course = Course.objects.get(slug=course_slug)
    project = Project.objects.get(slug=project_slug)
    certifying_organisation = \
        CertifyingOrganisation.objects.get(slug=organisation_slug)

    # Checking user permissions.
    if request.user.is_staff or request.user == project.owner or \
            request.user in project.certification_managers.all() or \
            request.user in certifying_organisation.organisation_owners.all() \
            or request.user == project.project_representative:
        pass
    else:
        raise Http404

    attendees_pk = \
        Certificate.objects.filter(
            course=course).values_list('attendee', flat=True)

    attendees = []
    for pk in attendees_pk:
        attendees.append(Attendee.objects.get(pk=pk))

    pending_organisation = \
        CertifyingOrganisation.objects.filter(approved=False)
    has_pending = False
    if pending_organisation:
        has_pending = True

    url = reverse('course-detail', kwargs={
        'project_slug': project_slug,
        'organisation_slug': organisation_slug,
        'slug': course_slug
    })

    # Attendee and her certificate
    certificates_dict = {}
    for attendee in attendees:
        certificates_dict[attendee] = \
            Certificate.objects.get(
                course=course, attendee=attendee)

    project_folder = (project.name.lower()).replace(' ', '_')
    if request.method == 'POST':

        # Delete all existing certificate in a course.
        for key, value in certificates_dict.items():
            filename = "{}.{}".format(value.certificateID, "pdf")
            pathname = \
                os.path.join(
                    '/home/web/media',
                    'pdf/{}/{}'.format(project_folder, filename))
            found = os.path.exists(pathname)
            if found:
                os.remove(pathname)

        # Generate all certificates in a course
        current_site = request.META['HTTP_HOST']
        makepath = '/home/web/media/pdf/{}/'.format(project_folder)
        if not os.path.exists(makepath):
            os.makedirs(makepath)

        for key, value in certificates_dict.items():
            filename = "{}.{}".format(value.certificateID, "pdf")
            pathname = \
                os.path.join(
                    '/home/web/media',
                    'pdf/{}/{}'.format(project_folder, filename))
            generate_pdf(
                pathname, project, course, key, value, current_site)

        messages.success(request, 'All certificates are updated', 'regenerate')
        return HttpResponseRedirect(url)

    return render(
        request, 'certificate/regenerate_all_certificate.html',
        context={
            'the_project': project,
            'has_pending_organisations': has_pending,
            'certificates': certificates_dict,
            'course': course})


class DummyAttendee(object):
    """Dummy object for preview certificate."""

    def __init__(self):
        self.firstname = 'Jane'
        self.surname = 'Doe'


class DummyCourse(object):
    """Dummy object for preview certificate."""

    def __init__(
            self, course_convener, course_type, training_center, start_date,
            end_date, certifying_organisation, template_certificate,
            trained_competence):
        self.course_convener = course_convener
        self.course_type = course_type
        self.training_center = training_center
        self.start_date = start_date
        self.end_date = end_date
        self.certifying_organisation = certifying_organisation
        self.template_certificate = template_certificate
        self.trained_competence = trained_competence


class DummyCertificate(object):
    """Dummy object for preview certificate."""

    def __init__(self, course, attendee, project):
        self.certificateID = '{}-DEMO'.format(project.name)
        self.course = course
        self.attendee = attendee


def preview_certificate(request, **kwargs):
    """Generate pdf for preview upon creating new course."""
    # Register new font
    try:
        font_folder = os.path.join(
            settings.STATIC_ROOT, 'fonts/times-new-roman')
        bold_ttf_file = os.path.join(
            font_folder, 'Times New Roman Gras 700.ttf')
        regular_ttf_file = os.path.join(
            font_folder, 'Times New Roman 400.ttf')
        pdfmetrics.registerFont(TTFont('Noto-Bold', bold_ttf_file))
        pdfmetrics.registerFont(TTFont('Noto-Regular', regular_ttf_file))
    except (TTFError, KeyError):
        pass

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'filename="preview.pdf"'

    project_slug = kwargs.pop('project_slug')
    project = Project.objects.get(slug=project_slug)
    organisation_slug = kwargs.pop('organisation_slug')

    convener_id = request.POST.get('course_convener', None)
    if convener_id is not None:
        # Get all posted data.
        course_convener = CourseConvener.objects.get(id=convener_id)
        training_center_id = request.POST.get('training_center', None)
        training_center = TrainingCenter.objects.get(id=training_center_id)
        course_type_id = request.POST.get('course_type', None)
        course_type = CourseType.objects.get(id=course_type_id)
        start_date = request.POST.get('start_date', None)
        course_start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d')
        end_date = request.POST.get('end_date', None)
        course_end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d')
        certifying_organisation = \
            CertifyingOrganisation.objects.get(slug=organisation_slug)
        raw_image = request.POST.get('template_certificate', None)
        trained_competence = request.POST.get('trained_competence', '')
        if 'base64' in raw_image:
            image_data = re.sub('^data:image/.+;base64,', '',
                                raw_image)
            decoded_image = base64.b64decode(image_data.encode('utf-8'))
            template_certificate = Image.open(BytesIO(decoded_image))
        else:
            template_certificate = None

        # Create dummy objects
        attendee = DummyAttendee()
        course = \
            DummyCourse(
                course_convener, course_type, training_center,
                course_start_date, course_end_date, certifying_organisation,
                template_certificate, trained_competence)
        certificate = DummyCertificate(course, attendee, project)

        current_site = request.META['HTTP_HOST']

        generate_pdf(
            response, project, course, attendee, certificate, current_site)

    else:
        # When preview page is refreshed, the data is gone so user needs to
        # go back to create page.

        page = canvas.Canvas(response, pagesize=landscape(A4))
        width, height = A4
        center = height * 0.5
        page.setFillColorRGB(0.1, 0.1, 0.1)
        page.setFont('Noto-Bold', 26)
        page.drawCentredString(center, 480, 'Certificate of Completion')
        page.setFont('Noto-Regular', 16)
        page.drawCentredString(
            center, 360,
            'To preview your certificate template, '
            'please go to create new course page')
        page.drawCentredString(
            center, 335, 'and click on Preview Certificate button.')
        page.showPage()
        page.save()

    return response


class TopUpView(TemplateView):
    template_name = 'certificate/top_up.html'
    project_slug = ''
    organisation_slug = ''

    def get_context_data(self, **kwargs):
        context = super(TopUpView, self).get_context_data(**kwargs)
        self.project_slug = self.kwargs.get('project_slug', None)
        self.organisation_slug = self.kwargs.get('organisation_slug', None)

        certifying_organisation = (
            CertifyingOrganisation.objects.get(slug=self.organisation_slug)
        )
        project = Project.objects.get(slug=self.project_slug)

        context['the_project'] = project
        context['cert_organisation'] = certifying_organisation

        return context

    def process_charge(self,
                       stripe_source_id,
                       cost_of_credits,
                       currency,
                       statement_descriptor='',
                       description='',
                       metadata=None):
        """
        Creates a charge for user.

        :param stripe_source_id: Id from stripe source
        :param cost_of_credits: cost of the total credits
        :param currency: currency of the cost
        :param metadata: A set of key/value pairs useful for storing
            additional information.
        :param statement_descriptor: An arbitrary string to be displayed on the
            customer's credit card statement.
        :param description: Description of the payment
        :return: paid, outcome
        """
        # Create the stripe Customer, by default subscriber Model is User,
        # this can be overridden with settings.DJSTRIPE_SUBSCRIBER_MODEL
        if metadata is None:
            metadata = {}
        customer, created = djstripe.models.Customer.get_or_create(
            subscriber=self.request.user)

        customer.add_card(stripe_source_id)

        charge = customer.charge(
            amount=cost_of_credits,
            currency=currency,
            description=description,
            metadata=metadata,
            statement_descriptor=statement_descriptor
        )
        return charge.paid, charge.outcome

    def post(self, request, *args, **kwargs):
        """Post the project_slug from the URL and define the Project

        :param request: HTTP request object
        :type request: HttpRequest

        :param args: Positional arguments
        :type args: tuple

        :param kwargs: Keyword arguments
        :type kwargs: dict

        :returns: Unaltered request object
        :rtype: HttpResponse
        """
        total_credits = self.request.POST.get('total-credits', None)
        stripe_source_id = self.request.POST.get('stripe-source-id', None)

        self.project_slug = self.kwargs.get('project_slug', None)
        self.organisation_slug = self.kwargs.get('organisation_slug', None)

        project = Project.objects.get(
            slug=self.project_slug
        )

        organisation = CertifyingOrganisation.objects.get(
            slug=self.organisation_slug
        )

        if not total_credits or not stripe_source_id:
            raise Http404('Missing important value')

        try:
            total_credits_decimal = Decimal(total_credits)
            total_credits = int(total_credits)
        except ValueError:
            raise Http404('Wrong total credits format')

        cost_of_credits = project.credit_cost * total_credits_decimal
        description = 'Top up {total} credit{plural}'.format(
            total=total_credits,
            plural='s' if total_credits > 1 else '')
        charged, outcome = self.process_charge(
            stripe_source_id=stripe_source_id,
            cost_of_credits=cost_of_credits,
            currency=project.credit_cost_currency,
            metadata={
                'name': 'Top up credits',
                'organisation': organisation,
                'organisation_id': organisation.id,
                'project': project,
                'project_id': project.id,
                'user_id': self.request.user.id,
                'added_credit': total_credits,
                'credit_cost': project.credit_cost,
                'total_payment': cost_of_credits,
                'currency': project.credit_cost_currency,
            },
            description=description,
            statement_descriptor=description
        )

        if charged:
            organisation.organisation_credits += total_credits
            organisation.save()
            organisation_owners = organisation.organisation_owners.all()
            send_notification(
                users=[self.request.user] + list(organisation_owners),
                label=NOTICE_TOP_UP_SUCCESS,
                extra_context={
                    'author': self.request.user,
                    'top_up_credits': total_credits,
                    'currency': project.get_credit_cost_currency_display(),
                    'payment_amount': cost_of_credits,
                    'certifying_organisation': organisation,
                    'total_credits': organisation.organisation_credits
                },
                request_user=self.request.user
            )
            messages.success(
                request,
                'Your purchase of <b>{}</b>'
                ' credits has been'
                ' successful'.format(
                    total_credits
                ),
                'credits_top_up'
            )

        return HttpResponseRedirect(
            reverse('certifyingorganisation-detail', kwargs={
                'project_slug': self.project_slug,
                'slug': self.organisation_slug
            })
        )
