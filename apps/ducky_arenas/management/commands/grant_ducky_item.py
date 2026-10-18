from django.core.management.base import BaseCommand, CommandError

from apps.ducky_arenas.models import ArenaItem, UserArenaInventory, UserProfileArenas


class Command(BaseCommand):
    help = 'Grant an item to a user profile in Ducky Arenas.'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str)
        parser.add_argument('item_code', type=str)
        parser.add_argument('--quantity', type=int, default=1)

    def handle(self, *args, **options):
        username = options['username']
        item_code = options['item_code']
        quantity = options['quantity']

        try:
            profile = UserProfileArenas.objects.get(user__username=username)
        except UserProfileArenas.DoesNotExist as exc:
            raise CommandError(f'No profile found for user: {username}') from exc

        try:
            item = ArenaItem.objects.get(code=item_code)
        except ArenaItem.DoesNotExist as exc:
            raise CommandError(f'No item found with code: {item_code}') from exc

        inventory, created = UserArenaInventory.objects.get_or_create(profile=profile, item=item, defaults={'quantity': 0})
        inventory.quantity = max(1, inventory.quantity + quantity)
        inventory.save()

        self.stdout.write(self.style.SUCCESS(f'{username} granted {item.name} x{inventory.quantity}.'))
