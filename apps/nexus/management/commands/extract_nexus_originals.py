import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.nexus.original_sources import DATA_DIR, SOURCES, SourceError, extract_source


class Command(BaseCommand):
    help = 'Extrae los siete bancos originales a JSON sin ejecutar HTML/JavaScript.'

    def add_arguments(self, parser):
        parser.add_argument('--source', type=Path, default=settings.MEDIA_ROOT / 'nexus' / 'tests')
        parser.add_argument('--output', type=Path, default=DATA_DIR)
        parser.add_argument('--check', action='store_true', help='Verifica sin escribir.')

    def handle(self, *args, **options):
        try:
            banks = {family: extract_source(options['source'], family) for family in SOURCES}
            if not options['check']:
                options['output'].mkdir(parents=True, exist_ok=True)
            for family, bank in banks.items():
                path = options['output'] / f'{family}.json'
                if options['check']:
                    if json.loads(path.read_text(encoding='utf-8')) != bank:
                        raise CommandError(f'{family}: la extracción persistida no coincide.')
                else:
                    path.write_text(json.dumps(bank, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                counts = ', '.join(f'{mode}: {len(items)}' for mode, items in bank['modes'].items())
                self.stdout.write(f'{family}: {sum(map(len, bank["questions"].values()))} preguntas; {counts}')
        except (SourceError, OSError, ValueError, KeyError, TypeError) as error:
            raise CommandError(str(error)) from error
