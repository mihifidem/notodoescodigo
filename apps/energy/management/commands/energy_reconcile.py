from django.core.management.base import BaseCommand

from apps.energy import services
from apps.energy.models import EnergyWallet


class Command(BaseCommand):
    help = 'Concilia operaciones de Energía Ducky y verifica saldos frente a movimientos.'

    def handle(self, *args, **options):
        released = services.reconcile_operations()
        self.stdout.write(f'Operaciones liberadas por conciliación: {len(released)}')
        problems = 0
        for wallet in EnergyWallet.objects.select_related('user'):
            services.refresh_recovery(wallet.user)
            for issue in services.reconcile_wallet(wallet.user):
                problems += 1
                self.stdout.write(self.style.ERROR(f'{wallet.user.username}: {issue}'))
        self.stdout.write(self.style.SUCCESS(f'Conciliación terminada. Incidencias: {problems}'))
