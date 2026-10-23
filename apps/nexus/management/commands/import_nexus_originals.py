from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.nexus.original_assessments import import_original
from apps.nexus.original_sources import MODES, SOURCES, SourceError
from apps.users.access import is_platform_admin


class Command(BaseCommand):
    help = 'Importa bancos JSON originales al motor Nexus. Publicar requiere autorización y editor con permiso.'

    def add_arguments(self, parser):
        parser.add_argument('--family', choices=tuple(SOURCES))
        parser.add_argument('--content-version', type=int, default=1)
        parser.add_argument('--editor', help='Nombre de usuario responsable de la revisión.')
        parser.add_argument('--publish', action='store_true')
        parser.add_argument('--authorization', default='')

    @transaction.atomic
    def handle(self, *args, **options):
        if options['content_version'] < 1:
            raise CommandError('--content-version debe ser positivo.')
        editor = None
        if options['editor']:
            editor = get_user_model().objects.filter(username=options['editor'], is_active=True).first()
            if not editor or not is_platform_admin(editor) or not editor.has_perm('nexus.publish_nexusversion'):
                raise CommandError('El editor necesita cuenta ADMIN activa y permiso nexus.publish_nexusversion.')
        if options['publish'] and (editor is None or not options['authorization'].strip()):
            raise CommandError('Publicar requiere --editor y --authorization explícitos.')
        try:
            for family in ([options['family']] if options['family'] else SOURCES):
                for mode in MODES:
                    version = import_original(
                        family, mode, options['content_version'], editor, options['publish'], options['authorization'],
                    )
                    self.stdout.write(f'{version.code} v{version.version}: {version.status}')
        except (SourceError, ValidationError, OSError, ValueError) as error:
            raise CommandError(str(error)) from error
