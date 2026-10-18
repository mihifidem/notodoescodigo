"""Durable bank saga. No network call runs inside a database transaction."""

import logging
import uuid
from dataclasses import asdict
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from .bank import BankOutcome, BankRequest, BankResult, BankUnavailable, get_payment_gateway
from .models import Duel, DuelResolution, EconomicOperation

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
MAX_RECONCILIATIONS = 6
LEASE_SECONDS = 60
RETRY_SECONDS = 30


def operation_request(operation: EconomicOperation) -> BankRequest:
    payload = dict(operation.request_payload)
    payload['reservation_keys'] = tuple(payload.get('reservation_keys', ()))
    payload['reservation_references'] = tuple(payload.get('reservation_references', ()))
    return BankRequest(**payload)


def plan_operation(duel, kind, player_id=None, *, reserves=(), winner_id=None):
    key = uuid.uuid5(duel.session_id, f'{kind}:{player_id or "pot"}')
    request = BankRequest(
        key=str(key), duel=str(duel.session_id), kind=kind,
        amount=duel.agreement.offer.stake * (2 if kind == EconomicOperation.Kind.SETTLE else 1),
        player=str(player_id) if player_id is not None else '',
        winner=str(winner_id) if winner_id is not None else '',
        reservation_keys=tuple(str(reserve.operation_key) for reserve in reserves),
        reservation_references=tuple(reserve.bank_reference for reserve in reserves),
    )
    payload = asdict(request)
    payload['reservation_keys'] = list(request.reservation_keys)
    payload['reservation_references'] = list(request.reservation_references)
    operation, created = EconomicOperation.objects.get_or_create(
        duel=duel, player_id=player_id, kind=kind,
        defaults={'operation_key': key, 'amount': request.amount, 'request_payload': payload},
    )
    if not created and (
        operation.request_payload != payload or operation.operation_key != key or operation.amount != request.amount
    ):
        raise ValidationError('La operación bancaria persistida no coincide con el acuerdo. Requiere revisión.')
    return operation


def completed(operation):
    return operation.status == EconomicOperation.Status.CONFIRMED


def terminal(operation):
    return operation.status in (EconomicOperation.Status.CONFIRMED, EconomicOperation.Status.FAILED)


def change_duel(duel, **values):
    Duel.objects.filter(pk=duel.pk).update(**values, revision=F('revision') + 1)
    for name, value in values.items():
        setattr(duel, name, value)
    if values.get('status') in (Duel.Status.FINISHED, Duel.Status.CANCELED):
        from .notifications import duel_notice
        message = ('El banco ha confirmado el pago del bote.' if duel.bank_mode == Duel.BankMode.PAYOUT
                   else 'El proceso de liberación/devolución ha terminado; no se ha pagado un premio.')
        duel_notice(duel, 'settlement', 'Liquidación de DuckyClash', message)


@transaction.atomic
def next_operation(duel_id):
    duel = Duel.objects.select_for_update().select_related('agreement__offer').get(pk=duel_id)
    if duel.status in (Duel.Status.FINISHED, Duel.Status.CANCELED):
        return None
    now = timezone.now()
    if (duel.bank_mode == Duel.BankMode.RESERVE and duel.status == Duel.Status.PENDING_RESERVATION
            and duel.agreement.offer.scheduled_at <= now):
        change_duel(duel, bank_mode=Duel.BankMode.CANCEL, bank_reason='Inicio superado sin ambas reservas.')
    players = (duel.agreement.creator_id, duel.agreement.opponent_id)
    reserves = list(duel.economic_operations.filter(kind=EconomicOperation.Kind.RESERVE).order_by('pk'))
    for reserve in reserves:
        if reserve.player_id not in players:
            raise ValidationError('La reserva no corresponde a los participantes del acuerdo.')
        plan_operation(duel, EconomicOperation.Kind.RESERVE, reserve.player_id)
    if duel.bank_mode == Duel.BankMode.RESERVE:
        if duel.status != Duel.Status.PENDING_RESERVATION:
            return None
        for player_id in players:
            reserve = plan_operation(duel, EconomicOperation.Kind.RESERVE, player_id)
            if reserve.status == EconomicOperation.Status.FAILED:
                change_duel(duel, bank_mode=Duel.BankMode.CANCEL, bank_reason='Reserva rechazada por el banco.')
                break
            if not completed(reserve):
                return reserve
        else:
            change_duel(duel, status=Duel.Status.SCHEDULED)
            return None
        reserves = list(duel.economic_operations.filter(kind=EconomicOperation.Kind.RESERVE).order_by('pk'))
    if duel.bank_mode == Duel.BankMode.PAYOUT:
        if duel.status != Duel.Status.SETTLEMENT_PENDING:
            raise ValidationError('El duelo no está pendiente de liquidación.')
        resolution = DuelResolution.objects.get(duel=duel)
        if resolution.is_draw or {item.player_id for item in reserves if completed(item)} != set(players):
            raise ValidationError('No hay resultado ganador o faltan ambas reservas verificadas.')
        settlement = plan_operation(
            duel, EconomicOperation.Kind.SETTLE, reserves=reserves, winner_id=resolution.winner_id,
        )
        if completed(settlement):
            change_duel(duel, status=Duel.Status.FINISHED, finished_at=now)
            return None
        return settlement
    if duel.bank_mode in (Duel.BankMode.CANCEL, Duel.BankMode.DRAW):
        # Never refund while a reserve may still succeed remotely.
        for reserve in reserves:
            if not terminal(reserve):
                if reserve.attempts == 0 and reserve.lease_token is None:
                    EconomicOperation.objects.filter(pk=reserve.pk).update(
                        status=EconomicOperation.Status.FAILED, last_error='cancelled_before_submission',
                    )
                else:
                    return reserve
        releases = [
            plan_operation(duel, EconomicOperation.Kind.RELEASE, reserve.player_id, reserves=(reserve,))
            for reserve in reserves if completed(reserve)
        ]
        for release in releases:
            if (not terminal(release) and release.next_attempt_at <= timezone.now()
                    and (release.lease_until is None or release.lease_until <= now)
                    and release.reconciliation_attempts < MAX_RECONCILIATIONS):
                return release
        if not all(completed(release) for release in releases):
            return None
        change_duel(
            duel, status=Duel.Status.FINISHED if duel.bank_mode == Duel.BankMode.DRAW else Duel.Status.CANCELED,
            finished_at=now,
        )
    return None


@transaction.atomic
def claim_operation(operation_id):
    operation = EconomicOperation.objects.select_related('duel').get(pk=operation_id)
    # Always serialize against cancellation and other operation claims for this duel.
    duel = Duel.objects.select_for_update().get(pk=operation.duel_id)
    operation.refresh_from_db()
    now = timezone.now()
    if terminal(operation) or operation.next_attempt_at > now or (
        operation.lease_until and operation.lease_until > now
    ):
        return None
    recovering = operation.lease_token is not None
    lookup = recovering or operation.status == EconomicOperation.Status.UNCERTAIN or (
        operation.kind == EconomicOperation.Kind.RESERVE
        and duel.bank_mode != Duel.BankMode.RESERVE and operation.attempts > 0
    )
    if (lookup and operation.reconciliation_attempts >= MAX_RECONCILIATIONS) or (
        not lookup and operation.attempts >= MAX_ATTEMPTS
    ):
        EconomicOperation.objects.filter(pk=operation.pk).update(last_error='retry_limit_review_required')
        logger.error('DuckyClash bank retry budget exhausted for operation %s', operation.operation_key)
        return None
    if operation.kind == EconomicOperation.Kind.RESERVE and duel.bank_mode != Duel.BankMode.RESERVE:
        if not lookup:
            EconomicOperation.objects.filter(pk=operation.pk).update(
                status=EconomicOperation.Status.FAILED, last_error='cancelled_before_submission',
            )
            return None
    token = uuid.uuid4()
    updates = {
        'lease_token': token, 'lease_until': now + timedelta(seconds=LEASE_SECONDS),
        'status': EconomicOperation.Status.UNCERTAIN,
        'updated_at': now,
    }
    count_field = 'reconciliation_attempts' if lookup else 'attempts'
    updates[count_field] = F(count_field) + 1
    claimed = EconomicOperation.objects.filter(
        pk=operation.pk, lease_token=operation.lease_token,
    ).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now)).update(**updates)
    if claimed != 1:
        return None
    return operation_request(operation), token, lookup


def validate_result(request, result, *, lookup):
    if (not isinstance(result, BankResult) or not isinstance(result.request, BankRequest)
            or type(result.request.amount) is not int
            or result.request != request or not isinstance(result.outcome, BankOutcome)):
        raise BankUnavailable('Respuesta bancaria no verificable.')
    if result.outcome == BankOutcome.NOT_FOUND and not lookup:
        raise BankUnavailable('Resultado bancario no verificable.')
    if result.outcome == BankOutcome.CONFIRMED and (
        not isinstance(result.reference, str) or not result.reference.strip() or len(result.reference) > 200
    ):
        raise BankUnavailable('Referencia bancaria no verificable.')
    return result


@transaction.atomic
def record_result(operation_id, token, result):
    operation = EconomicOperation.objects.get(pk=operation_id)
    duel = Duel.objects.select_for_update().get(pk=operation.duel_id)
    operation.refresh_from_db()
    if operation.lease_token != token:
        return
    status = EconomicOperation.Status.UNCERTAIN
    error = 'bank_outcome_unknown'
    if result.outcome == BankOutcome.CONFIRMED:
        status, error = EconomicOperation.Status.CONFIRMED, ''
    elif result.outcome == BankOutcome.REJECTED:
        status, error = EconomicOperation.Status.FAILED, 'bank_rejected'
        logger.warning('DuckyClash bank rejected operation %s (%s)', operation.operation_key, operation.kind)
    elif result.outcome == BankOutcome.NOT_FOUND:
        status, error = EconomicOperation.Status.PENDING, ''
        if (operation.kind == EconomicOperation.Kind.RESERVE and duel.bank_mode != Duel.BankMode.RESERVE):
            status, error = EconomicOperation.Status.UNCERTAIN, 'absence_not_terminal'
        elif operation.attempts >= MAX_ATTEMPTS:
            status, error = EconomicOperation.Status.UNCERTAIN, 'submission_limit_review_required'
    EconomicOperation.objects.filter(pk=operation.pk, lease_token=token).update(
        status=status, bank_reference=result.reference if status == EconomicOperation.Status.CONFIRMED else '',
        last_error=error, lease_token=None, lease_until=None,
        next_attempt_at=timezone.now() + timedelta(seconds=RETRY_SECONDS), updated_at=timezone.now(),
    )


def reconcile_duel(duel_id):
    if connection.in_atomic_block:
        raise RuntimeError('La conciliación bancaria no puede ejecutarse dentro de una transacción.')
    duel = Duel.objects.get(pk=duel_id)
    gateway = get_payment_gateway(duel.payment_gateway or None)
    from .local_bank import LocalPointsBankGateway
    if (isinstance(gateway, LocalPointsBankGateway) and not duel.payment_gateway
            and duel.economic_operations.filter(attempts__gt=0).exists()):
        raise BankUnavailable('El proveedor de este duelo anterior no está identificado. Requiere revisión antes de usar puntos.')
    if Duel.objects.filter(
        pk=duel_id, bank_mode=Duel.BankMode.RESERVE, status=Duel.Status.SETTLEMENT_PENDING,
        resolution__isnull=False,
    ).exists():
        request_settlement(duel_id)
    # Two holds, two releases, one settlement; retries are persisted, not a busy loop.
    for _ in range(8):
        operation = next_operation(duel_id)
        if operation is None:
            break
        claim = claim_operation(operation.pk)
        if claim is None:
            break
        request, token, lookup = claim
        try:
            if lookup:
                result = gateway.lookup(request)
            else:
                result = getattr(gateway, request.kind)(request)
            result = validate_result(request, result, lookup=lookup)
        except (BankUnavailable, TimeoutError, ConnectionError):
            logger.warning('DuckyClash bank outcome unknown for operation %s', request.key)
            result = BankResult(request, BankOutcome.UNKNOWN)
        record_result(operation.pk, token, result)
    return Duel.objects.get(pk=duel_id)


def enqueue_local_reconciliation(duel_id):
    """Local payments need no running broker; external gateways retain the durable worker."""
    from .local_bank import LOCAL_GATEWAY
    if not Duel.objects.filter(pk=duel_id, payment_gateway=LOCAL_GATEWAY).exists():
        return

    def run():
        try:
            reconcile_duel(duel_id)
        except (BankUnavailable, ValidationError):
            logger.exception('Local DuckyClash payment requires reconciliation for duel %s', duel_id)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                raise
            logger.warning('Concurrent local DuckyClash reconciliation for duel %s', duel_id)

    transaction.on_commit(run)


@transaction.atomic
def request_cancellation(*, actor, duel_id):
    duel = Duel.objects.select_for_update().select_related('agreement__offer').get(pk=duel_id)
    if actor.pk not in (duel.agreement.creator_id, duel.agreement.opponent_id):
        raise PermissionDenied('Solo los participantes pueden cancelar.')
    if duel.bank_mode == Duel.BankMode.CANCEL:
        return duel
    if (duel.bank_mode != Duel.BankMode.RESERVE
            or duel.status not in (Duel.Status.PENDING_RESERVATION, Duel.Status.SCHEDULED)
            or duel.agreement.offer.scheduled_at <= timezone.now()):
        raise ValidationError('Solo puedes cancelar antes del inicio y sin resultado ni liquidación.')
    change_duel(duel, bank_mode=Duel.BankMode.CANCEL, bank_reason='Cancelación solicitada por un participante.')
    if not duel.economic_operations.exists():
        change_duel(duel, status=Duel.Status.CANCELED, finished_at=timezone.now())
    enqueue_local_reconciliation(duel.pk)
    return duel


@transaction.atomic
def request_settlement(duel_id):
    """Trusted server-only entry point; never accepts a browser-supplied winner."""
    duel = Duel.objects.select_for_update().get(pk=duel_id)
    if duel.bank_mode in (Duel.BankMode.DRAW, Duel.BankMode.PAYOUT):
        return duel
    if duel.bank_mode != Duel.BankMode.RESERVE or duel.status != Duel.Status.SETTLEMENT_PENDING:
        raise ValidationError('No hay un resultado del servidor pendiente de liquidación.')
    resolution = DuelResolution.objects.get(duel=duel)
    players = {duel.agreement.creator_id, duel.agreement.opponent_id}
    holds = set(duel.economic_operations.filter(
        kind=EconomicOperation.Kind.RESERVE, status=EconomicOperation.Status.CONFIRMED,
    ).values_list('player_id', flat=True))
    if holds != players:
        raise ValidationError('Se necesitan las dos reservas verificadas.')
    change_duel(duel, bank_mode=Duel.BankMode.DRAW if resolution.is_draw else Duel.BankMode.PAYOUT)
    return duel
