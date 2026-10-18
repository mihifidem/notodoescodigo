from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.duckyclash.bank import BankUnavailable
from apps.duckyclash.economy import reconcile_duel
from apps.duckyclash.models import Duel, EconomicOperation


class Command(BaseCommand):
    help = 'Concilia un duelo con DuckyBank sin duplicar operaciones económicas.'

    def add_arguments(self, parser):
        parser.add_argument('--duel', type=int, required=True)
        parser.add_argument(
            '--resume-lookups', action='store_true',
            help='Tras revisión bancaria, renueva solo el presupuesto de consultas inciertas; no el de envíos.',
        )

    def handle(self, *args, **options):
        if not Duel.objects.filter(pk=options['duel']).exists():
            raise CommandError('Duelo no encontrado.')
        if options['resume_lookups']:
            EconomicOperation.objects.filter(
                duel_id=options['duel'], status=EconomicOperation.Status.UNCERTAIN,
            ).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=timezone.now())).update(
                reconciliation_attempts=0, next_attempt_at=timezone.now(),
            )
        try:
            duel = reconcile_duel(options['duel'])
        except (BankUnavailable, ValidationError) as exc:
            raise CommandError('No se pudo conciliar: revisa la configuración y el registro operativo.') from exc
        self.stdout.write(f'Duelo {duel.pk}: {duel.get_status_display()}')
