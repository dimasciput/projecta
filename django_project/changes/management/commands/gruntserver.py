import subprocess
import atexit
from django.core.management.base import BaseCommand
from core.settings.utils import absolute_path


class Command(BaseCommand):

    def __init__(self):
        super(Command, self).__init__()
        self.grunt_process = None

    def handle(self, *args, **options):
        self.start_grunt()
        # return super(Command, self).inner_run(*args, **options)

    def start_grunt(self):
        self.stdout.write('>>> Starting grunt')

        self.grunt_process = subprocess.call([
            'grunt',
            '--gruntfile={0}/Gruntfile.js'.format(absolute_path()),
            '--base=.'],
                shell=True)

        def kill_grunt_process(grunt_process):
            self.stdout.write('>>> Closing grunt process')

        atexit.register(kill_grunt_process, self.grunt_process)
