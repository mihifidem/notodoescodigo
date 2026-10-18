from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.duckyclash.combat import resolve_round
from apps.duckyclash.ecomotor import EcoMotorUnavailable
from apps.duckyclash.models import RoundResolution


class Command(BaseCommand):
    help = 'Reintenta una resolución EcoMotor con la misma entrada y clave, sin editar HP ni ganador.'

    def add_arguments(self, parser):
        parser.add_argument('question_id', type=int)
        parser.add_argument('--reset-budget', action='store_true', help='Autoriza tres intentos adicionales tras revisión del proveedor.')

    def handle(self, question_id, reset_budget=False, **options):
        if reset_budget:
            with transaction.atomic():
                row = RoundResolution.objects.select_for_update().filter(question_id=question_id).first()
                if row and row.status == RoundResolution.Status.PENDING:
                    if row.gateway != settings.DUCKYCLASH_ECOMOTOR_GATEWAY:
                        raise CommandError('El proveedor debe coincidir con el capturado en la ronda.')
                    if row.next_attempt_at > timezone.now():
                        raise CommandError('Hay un intento reciente. Espera al vencimiento de su ventana de 30 segundos.')
                    RoundResolution.objects.filter(pk=row.pk).update(attempts=0, next_attempt_at=timezone.now())
        try:
            result = resolve_round(question_id)
        except (ValidationError, EcoMotorUnavailable) as exc:
            raise CommandError(str(exc)) from exc
        if result.status != RoundResolution.Status.APPLIED:
            raise CommandError(f'Resolución pendiente: {result.last_error or "en curso o presupuesto agotado"}.')
        self.stdout.write(self.style.SUCCESS(f'Ronda aplicada una sola vez: {result.outcome}.'))
