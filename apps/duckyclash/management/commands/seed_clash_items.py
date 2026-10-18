from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.ducky_arenas.models import ArenaItem
from apps.duckyclash.items import DEFAULT_RULES


class Command(BaseCommand):
    help = 'Registra los seis consumibles en ArenaItem sin regalar stock ni modificar objetos existentes.'

    @transaction.atomic
    def handle(self, **options):
        names = {
            'NUBE': 'Nube', 'ACELERADOR': 'Acelerador', 'MULTIPLICADOR': 'Multiplicador',
            'ESCUDO': 'Escudo', 'POCIMA_TIEMPO': 'Pócima de tiempo', 'BOTIQUIN': 'Botiquín',
        }
        for code in DEFAULT_RULES:
            item, _ = ArenaItem.objects.get_or_create(
                code=code, defaults={'name': names[code], 'itemType': ArenaItem.ArenaItemType.COMPLEMENT},
            )
            if item.itemType != ArenaItem.ArenaItemType.COMPLEMENT:
                raise CommandError(f'{code} ya existe como equipamiento. Revisa el catálogo; no se ha cambiado.')
        self.stdout.write(self.style.SUCCESS('Consumibles registrados en el inventario real, sin añadir existencias.'))
