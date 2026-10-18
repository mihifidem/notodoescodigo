import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from apps.lms.lifecycle import process_lms_lifecycle


class Command(BaseCommand):
    help = 'Publica/cierra cursos y pruebas, publica notas y verifica finalización de alumnos.'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true', help='Mantener el proceso activo.')
        parser.add_argument('--interval', type=int, default=5, help='Intervalo en segundos (mínimo 1).')

    def handle(self, *args, **options):
        if options['interval'] < 1:
            raise CommandError('El intervalo debe ser al menos 1 segundo.')
        while True:
            close_old_connections()
            result = process_lms_lifecycle()
            self.stdout.write(str(result))
            if not options['watch']:
                break
            time.sleep(options['interval'])
