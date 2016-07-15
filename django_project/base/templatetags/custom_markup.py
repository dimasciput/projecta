import markdown
from django import template
from django.template.defaultfilters import stringfilter
from django.utils.encoding import force_unicode
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name='base_markdown', is_safe=True)
@stringfilter
def base_markdown(value):
    extensions = ["nl2br", ]

    return mark_safe(markdown.markdown(force_unicode(value),
                                       extensions,
                                       safe_mode=True,
                                       enable_attributes=False))


@register.filter(name='is_gif', is_safe=True)
@stringfilter
def is_gif(value):
    return value[-4:] == '.gif'


@register.inclusion_tag('button_span.html', takes_context=True)
def show_button_icon(context, value):

    context_icon = {
        'add': 'glyphicon glyphicon-asterisk',
        'update': 'glyphicon glyphicon-pencil',
        'delete': 'glyphicon glyphicon-minus'
    }

    return {
        'button_icon': context_icon[value]
    }


class ArgumentError(ValueError):
    """Missing or incompatible argument."""


def _regroup_table(seq, rows=None, columns=None):
    if not (rows or columns):
        raise ArgumentError("Missing one of rows or columns")

    if columns:
        rows = (len(seq) // columns) + 1
    table = [seq[i::rows] for i in range(rows)]

    # Pad out short rows
    n = len(table[0])
    return [row + [None for x in range(n - len(row))] for row in table]


@register.filter
def groupby_rows(seq, n):
    """Returns a list of n lists. Each sub-list is the same length.

    Short lists are padded with None. This is useful for creating HTML tables
    from a sequence.

    >>> groupby_rows(range(1, 11), 3)
    [[1, 4, 7, 10], [2, 5, 8, None], [3, 6, 9, None]]
    """
    return _regroup_table(seq, rows=int(n))


@register.filter
def groupby_columns(seq, n):
    """Returns a list of lists where each sub-list has n items.

    Short lists are padded with None. This is useful for creating HTML tables
    from a sequence.

    >>> groupby_columns(range(1, 11), 3)
    [[1, 5, 9], [2, 6, 10], [3, 7, None], [4, 8, None]]
    """
    return _regroup_table(seq, columns=int(n))