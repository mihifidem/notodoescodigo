from django.core.management.base import BaseCommand

from apps.ducky_arenas.models import ArenaItem


ITEM_SEED = [
    {
        'code': 'cyber_hoodie_01',
        'name': 'Cyber Hoodie',
        'description': 'Chaqueta técnica con aislamiento térmico y un diseño discreto.',
        'slot': 'TORSO',
        'rarity': 'COMMON',
        'avatarType': 'CYBERSECURITY',
        'statBonuses': {'attack': 2, 'defense': 3, 'speed': 1, 'logic': 0, 'energy': 2},
        'icon': 'hoodie',
        'isActive': True,
    },
    {
        'code': 'gamer_headset_01',
        'name': 'Gamer Headset',
        'description': 'Auriculares para mantener foco y mejorar la reacción.',
        'slot': 'HEAD',
        'rarity': 'RARE',
        'avatarType': 'GAMER',
        'statBonuses': {'attack': 0, 'defense': 0, 'speed': 2, 'logic': 2, 'energy': 2},
        'icon': 'headset',
        'isActive': True,
    },
    {
        'code': 'developer_laptop_01',
        'name': 'Developer Laptop',
        'description': 'Portátil de alto rendimiento para mejorar la lógica.',
        'slot': 'DEVICE',
        'rarity': 'EPIC',
        'avatarType': 'DEVELOPER',
        'statBonuses': {'attack': 1, 'defense': 0, 'speed': 1, 'logic': 5, 'energy': 2},
        'icon': 'laptop',
        'isActive': True,
    },
    {
        'code': 'roman_helmet_01',
        'name': 'Roman Helmet',
        'description': 'Casco de combate con presencia imponente.',
        'slot': 'HEAD',
        'rarity': 'RARE',
        'avatarType': 'ROMAN',
        'statBonuses': {'attack': 0, 'defense': 2, 'speed': 1, 'logic': 0, 'energy': 0},
        'icon': 'helmet',
        'isActive': True,
    },
    {
        'code': 'systems_visor_01',
        'name': 'Systems Visor',
        'description': 'Visor táctil para mejorar la vigilancia y la energía.',
        'slot': 'FACE',
        'rarity': 'EPIC',
        'avatarType': 'SYSTEMS',
        'statBonuses': {'attack': 1, 'defense': 2, 'speed': 0, 'logic': 3, 'energy': 2},
        'icon': 'visor',
        'isActive': True,
    },
]


class Command(BaseCommand):
    help = 'Seed initial shop/catalog items for Ducky Arenas.'

    def handle(self, *args, **options):
        created = 0
        for payload in ITEM_SEED:
            item, item_created = ArenaItem.objects.get_or_create(code=payload['code'], defaults=payload)
            if item_created:
                created += 1
            for field, value in payload.items():
                setattr(item, field, value)
            item.save()

        self.stdout.write(self.style.SUCCESS(f'Seeded {len(ITEM_SEED)} items. Created: {created}.'))
