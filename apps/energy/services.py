"""Servicio único de Energía Ducky.

Política de consumo (común para todos los módulos):
1. Si un derecho de servicio vigente cubre la ejecución, se usa ese derecho y no se cobra energía.
2. Primero energía recuperable.
3. Después energía comprada, empezando por los lotes que caducan antes (los que no caducan, al final).

Todas las operaciones bloquean el monedero (``select_for_update``) y registran movimientos
con clave de idempotencia. Ningún módulo debe modificar saldos fuera de este archivo.
"""
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.module_loading import import_string

from .models import (
    EnergyActivityGrant, EnergyLedgerEntry, EnergyOperation, EnergyPolicy, EnergyPurchasedLot,
    EnergyRechargeProduct, EnergyReservation, EnergyRewardRule, EnergyToolCost, EnergyWallet,
)

logger = logging.getLogger(__name__)

QUANT = Decimal('0.001')
ZERO = Decimal('0')
LEVEL_HIGH = Decimal('60')
LEVEL_MEDIUM = Decimal('25')
DEFAULT_MAX_CONCURRENT = 1
PLANS = ('free', 'plus', 'pro')

MSG_ENOUGH = 'Tienes energía suficiente para esta tarea.'
MSG_ACTIVITY = 'Has completado una actividad y recuperado energía.'
MSG_RECOVERING = 'Tu energía se está recuperando.'
MSG_CONTINUE = 'Puedes seguir con tus cursos y herramientas que no requieren energía.'
MSG_DAILY_DONE = 'Has recuperado toda la energía disponible por actividad de hoy.'
MSG_FULL = 'Energía completa.'
MSG_NOT_CONFIGURED = 'La energía recuperable de tu nivel aún no está configurada.'


class EnergyError(Exception):
    code = 'energy_error'

    def __init__(self, message, *, code=None, **extra):
        super().__init__(message)
        if code:
            self.code = code
        self.extra = extra


class EnergyUnavailable(EnergyError):
    code = 'tool_unavailable'


class InsufficientEnergy(EnergyError):
    code = 'insufficient_energy'


class CostChanged(EnergyError):
    code = 'cost_changed'


class EnergyConflict(EnergyError):
    code = 'conflict'


class ConcurrencyLimit(EnergyError):
    code = 'concurrency_limit'


def q(value):
    return Decimal(value).quantize(QUANT, rounding=ROUND_DOWN)


def day_timezone():
    return ZoneInfo(getattr(settings, 'ENERGY_DAY_TIMEZONE', 'Europe/Madrid'))


def energy_day(now=None):
    return timezone.localtime(now or timezone.now(), day_timezone()).date()


def format_energy(value):
    if value is None:
        return None
    text = f'{q(value).normalize():f}'
    return text.replace('.', ',')


def user_plan(user):
    if not getattr(user, 'is_authenticated', False) or not user.is_active:
        return None
    from apps.users.access import effective_plan

    plan = effective_plan(user)
    return plan if plan in PLANS else None


def policy_for_plan(plan):
    if plan is None:
        return None
    return EnergyPolicy.objects.filter(plan=plan).first()


def get_executor(tool_code):
    """Ejecutor real de proveedor IA configurado para la herramienta, o ``None``."""
    path = getattr(settings, 'ENERGY_TOOL_EXECUTORS', {}).get(tool_code)
    return import_string(path) if path else None


# ---------------------------------------------------------------------------
# Monedero y recuperación periódica
# ---------------------------------------------------------------------------

def _locked_wallet(user):
    if not EnergyWallet.objects.filter(user=user).exists():
        try:
            with transaction.atomic():
                EnergyWallet.objects.create(user=user)
        except IntegrityError:
            pass
    return EnergyWallet.objects.select_for_update().get(user=user)


def _ledger(wallet, *, kind, key, reason, process, component=EnergyLedgerEntry.Component.RECOVERABLE,
            available=ZERO, reserved=ZERO, lot=None, operation=None, tool_code='', activity_code='',
            rule_reference='', actor=None):
    return EnergyLedgerEntry.objects.create(
        user_id=wallet.user_id, kind=kind, component=component, lot=lot, available_delta=available,
        reserved_delta=reserved, reason=reason[:240], tool_code=tool_code, activity_code=activity_code,
        operation=operation, rule_reference=rule_reference, idempotency_key=key, actor=actor, process=process,
    )


def _expire_lots(wallet, now):
    lots = EnergyPurchasedLot.objects.select_for_update().filter(
        wallet=wallet, status=EnergyPurchasedLot.Status.ACTIVE, expires_at__lte=now,
    )
    for lot in lots:
        if lot.available > 0:
            _ledger(
                wallet, kind=EnergyLedgerEntry.Kind.EXPIRY, component=EnergyLedgerEntry.Component.PURCHASED,
                lot=lot, available=-lot.available, key=f'expiry:{lot.pk}',
                reason='Caducidad del lote comprado según sus condiciones.', process='energy.expiry',
            )
            lot.removed += lot.available
            lot.available = ZERO
        lot.status = EnergyPurchasedLot.Status.EXPIRED
        lot.save(update_fields=['available', 'removed', 'status'])


def _refresh(wallet, policy, now):
    """Recuperación periódica diferida. Sin reserva de tiempo acumulada a capacidad completa."""
    _expire_lots(wallet, now)
    if policy is None or not policy.recovery_configured:
        wallet.recovery_anchor = now
        wallet.save(update_fields=['recovery_anchor', 'updated_at'])
        return
    space = policy.capacity - wallet.recoverable_available - wallet.recoverable_reserved
    if space <= 0 or wallet.recovery_anchor is None or wallet.recovery_anchor > now:
        wallet.recovery_anchor = now
        wallet.save(update_fields=['recovery_anchor', 'updated_at'])
        return
    interval = timedelta(minutes=policy.recovery_interval_minutes)
    steps = int((now - wallet.recovery_anchor) / interval)
    if steps <= 0:
        return
    amount = q(min(policy.recovery_amount * steps, space))
    _ledger(
        wallet, kind=EnergyLedgerEntry.Kind.RECOVERY, available=amount,
        key=f'recovery:{wallet.pk}:{wallet.recovery_anchor.isoformat()}',
        reason=f'Recuperación periódica: {steps} intervalo(s) de {policy.recovery_interval_minutes} min.',
        process='energy.recovery', rule_reference=f'policy:{policy.plan}:v{policy.version}',
    )
    wallet.recoverable_available += amount
    wallet.recovery_anchor = now if amount >= space else wallet.recovery_anchor + interval * steps
    wallet.save(update_fields=['recoverable_available', 'recovery_anchor', 'updated_at'])


def refresh_recovery(user, now=None):
    """Actualiza recuperación y caducidades. Lo usan la tarea programada y las consultas."""
    plan = user_plan(user)
    if plan is None:
        return None
    with transaction.atomic():
        wallet = _locked_wallet(user)
        _refresh(wallet, policy_for_plan(plan), now or timezone.now())
        return wallet


def _active_lots(wallet, now, lock=False):
    lots = EnergyPurchasedLot.objects.filter(
        wallet=wallet, status=EnergyPurchasedLot.Status.ACTIVE,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    if lock:
        lots = lots.select_for_update()
    # Primero los lotes que caducan antes; los que no caducan, al final.
    return sorted(lots, key=lambda lot: (lot.expires_at is None, lot.expires_at or now, lot.created_at))


def daily_activity(user, policy, now=None):
    day = energy_day(now)
    earned = EnergyActivityGrant.objects.filter(user=user, day=day).aggregate(
        total=Sum('granted'),
    )['total'] or ZERO
    limit = ZERO
    if policy is not None and policy.is_configured:
        limit = q(policy.capacity * policy.activity_daily_percent / Decimal('100'))
    remaining = max(limit - earned, ZERO)
    return {
        'day': day, 'limit': limit, 'earned': q(earned), 'remaining': q(remaining),
        'percent': policy.activity_daily_percent if policy else None,
        'limit_reached': limit > 0 and remaining <= 0,
    }


def get_status(user, now=None):
    """Fuente única para cabecera, dashboard y Mi cuenta."""
    now = now or timezone.now()
    plan = user_plan(user)
    if plan is None:
        return {'ok': False, 'reason': 'registration_required'}
    with transaction.atomic():
        wallet = _locked_wallet(user)
        policy = policy_for_plan(plan)
        _refresh(wallet, policy, now)
        lots = _active_lots(wallet, now)
    purchased_available = sum((lot.available for lot in lots), ZERO)
    purchased_reserved = EnergyPurchasedLot.objects.filter(wallet=wallet).aggregate(
        total=Sum('reserved'),
    )['total'] or ZERO
    configured = bool(policy and policy.is_configured)
    capacity = policy.capacity if configured else None
    available = wallet.recoverable_available
    percent = None
    level = 'unconfigured'
    full = False
    if configured:
        percent = min(Decimal('100'), q(available * 100 / capacity))
        full = available + wallet.recoverable_reserved >= capacity
        level = 'high' if percent >= LEVEL_HIGH else 'medium' if percent >= LEVEL_MEDIUM else 'low'
    activity = daily_activity(user, policy, now)
    next_recovery = None
    periodic = bool(configured and policy.recovery_configured and not full and wallet.recovery_anchor)
    if periodic:
        next_recovery = wallet.recovery_anchor + timedelta(minutes=policy.recovery_interval_minutes)
    recent_activity = bool(
        configured and policy.activity_recent_minutes and wallet.last_activity_grant_at
        and now - wallet.last_activity_grant_at <= timedelta(minutes=policy.activity_recent_minutes)
        and not full
    )
    recharging_source = 'periodic' if periodic else 'activity' if recent_activity else None
    if not configured:
        message = MSG_NOT_CONFIGURED
    elif full:
        message = MSG_FULL
    elif activity['limit_reached']:
        message = MSG_DAILY_DONE + (' ' + MSG_RECOVERING if periodic else '')
    elif recent_activity:
        message = MSG_ACTIVITY
    elif periodic:
        message = MSG_RECOVERING
    else:
        message = MSG_CONTINUE
    return {
        'ok': True,
        'plan': plan,
        'configured': configured,
        'policy_version': policy.version if policy else None,
        'recoverable': {
            'available': available, 'reserved': wallet.recoverable_reserved, 'capacity': capacity,
            'percent': percent, 'surplus': max(available + wallet.recoverable_reserved - capacity, ZERO)
            if configured else ZERO,
        },
        'purchased': {'available': purchased_available, 'reserved': purchased_reserved, 'lots': len(lots)},
        'total_available': available + purchased_available,
        'reserved_total': wallet.recoverable_reserved + purchased_reserved,
        'level': level,
        'full': full,
        'recharging': recharging_source is not None,
        'recharging_source': recharging_source,
        'next_recovery_at': next_recovery,
        'recovery': {
            'amount': policy.recovery_amount if configured and policy.recovery_configured else None,
            'interval_minutes': policy.recovery_interval_minutes if configured else None,
        },
        'activity': activity,
        'message': message,
    }


def status_payload(status):
    """Serialización JSON estable del estado (sin tokens del proveedor)."""
    if not status.get('ok'):
        return status

    def num(value):
        return None if value is None else str(q(value))

    return {
        'ok': True,
        'plan': status['plan'],
        'configured': status['configured'],
        'level': status['level'],
        'full': status['full'],
        'recharging': status['recharging'],
        'recharging_source': status['recharging_source'],
        'next_recovery_at': status['next_recovery_at'].isoformat() if status['next_recovery_at'] else None,
        'message': status['message'],
        'recoverable': {key: num(value) for key, value in status['recoverable'].items()},
        'purchased': {
            'available': num(status['purchased']['available']),
            'reserved': num(status['purchased']['reserved']), 'lots': status['purchased']['lots'],
        },
        'total_available': num(status['total_available']),
        'reserved_total': num(status['reserved_total']),
        'activity': {
            'limit': num(status['activity']['limit']), 'earned': num(status['activity']['earned']),
            'remaining': num(status['activity']['remaining']),
            'limit_reached': status['activity']['limit_reached'],
        },
    }


# ---------------------------------------------------------------------------
# Recompensas por actividad
# ---------------------------------------------------------------------------

def reward_activity(user, source, event_key, now=None):
    """El servidor valida el evento y aplica la regla configurada. Idempotente por evento."""
    rule = EnergyRewardRule.objects.filter(source=source).first()
    if rule is None or not rule.enabled or not rule.amount:
        return None
    plan = user_plan(user)
    if plan is None:
        return None
    now = now or timezone.now()
    with transaction.atomic():
        wallet = _locked_wallet(user)
        previous = EnergyActivityGrant.objects.filter(user=user, rule=rule, event_key=event_key).first()
        if previous:
            return previous
        policy = policy_for_plan(plan)
        _refresh(wallet, policy, now)
        budget = daily_activity(user, policy, now)
        requested = rule.amount
        if policy is None or not policy.is_configured:
            granted, result = ZERO, EnergyActivityGrant.Result.UNAVAILABLE
        else:
            space = max(policy.capacity - wallet.recoverable_available - wallet.recoverable_reserved, ZERO)
            events_today = EnergyActivityGrant.objects.filter(user=user, rule=rule, day=budget['day']).count()
            if rule.max_events_per_day is not None and events_today >= rule.max_events_per_day:
                granted, result = ZERO, EnergyActivityGrant.Result.DAILY_LIMIT
            else:
                granted = q(min(requested, budget['remaining'], space))
                if granted >= requested:
                    result = EnergyActivityGrant.Result.GRANTED
                elif granted > 0:
                    result = EnergyActivityGrant.Result.PARTIAL
                elif budget['remaining'] <= 0:
                    result = EnergyActivityGrant.Result.DAILY_LIMIT
                else:
                    result = EnergyActivityGrant.Result.FULL
        grant = EnergyActivityGrant.objects.create(
            user=user, rule=rule, rule_version=rule.version, event_key=event_key, day=budget['day'], plan=plan,
            daily_limit=budget['limit'], requested=requested, granted=granted, result=result,
        )
        if granted > 0:
            _ledger(
                wallet, kind=EnergyLedgerEntry.Kind.ACTIVITY, available=granted,
                key=f'activity:{user.pk}:{rule.pk}:{event_key}', activity_code=rule.source,
                reason=f'{rule.name} (solicitada {format_energy(requested)}, concedida {format_energy(granted)}).',
                process='energy.activity', rule_reference=f'rule:{rule.code}:v{rule.version}',
            )
            wallet.recoverable_available += granted
            wallet.last_activity_grant_at = now
            wallet.save(update_fields=['recoverable_available', 'last_activity_grant_at', 'updated_at'])
        return grant


def emit_activity_on_commit(user_id, source, event_key):
    """Contrato para módulos: emitir un evento verificado sin afectar a su transacción."""
    def _apply():
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(pk=user_id).first()
        if user is None:
            return
        try:
            reward_activity(user, source, event_key)
        except Exception:  # La energía nunca bloquea el aprendizaje.
            logger.exception('No se pudo aplicar la recompensa de energía %s %s', source, event_key)

    transaction.on_commit(_apply)


# ---------------------------------------------------------------------------
# Herramientas IA: estimación, reserva, confirmación y liberación
# ---------------------------------------------------------------------------

def _provider_spend(tool, now):
    if not tool.budget_period_days:
        return ZERO
    since = now - timedelta(days=tool.budget_period_days)
    rows = EnergyOperation.objects.filter(tool=tool, created_at__gte=since).exclude(
        status=EnergyOperation.Status.RELEASED,
    )
    total = ZERO
    for confirmed, estimated in rows.values_list('provider_cost_confirmed', 'provider_cost_estimated'):
        total += confirmed if confirmed is not None else estimated or ZERO
    return total


def tool_state(tool_code, variant='', now=None):
    """Comprueba la herramienta sin depender del usuario. Devuelve (tool, reason, cost)."""
    tool = EnergyToolCost.objects.filter(code=tool_code).first()
    if tool is None:
        return None, 'tool_not_configured', None
    cost = tool.cost_for(variant)
    if not tool.enabled:
        return tool, 'tool_disabled', cost
    if cost is None:
        return tool, 'cost_not_configured', None
    if tool.paused:
        return tool, 'tool_paused', cost
    if get_executor(tool.code) is None:
        return tool, 'provider_unavailable', cost
    if tool.provider_budget is None or not tool.budget_period_days:
        return tool, 'budget_not_configured', cost
    if _provider_spend(tool, now or timezone.now()) >= tool.provider_budget:
        return tool, 'budget_exhausted', cost
    return tool, 'ok', cost


TOOL_MESSAGES = {
    'tool_not_configured': 'Herramienta IA sin coste energético configurado: no está disponible. Coste desconocido, no cero.',
    'tool_disabled': 'Herramienta IA desactivada por administración.',
    'cost_not_configured': 'Herramienta IA sin coste energético configurado. Coste desconocido, no cero.',
    'tool_paused': 'Herramienta IA en pausa. No se consume energía; el resto de funciones sigue disponible.',
    'provider_unavailable': 'No hay un proveedor IA configurado para esta herramienta. No se ha enviado ningún dato ni reservado energía.',
    'budget_not_configured': 'Herramienta IA sin presupuesto interno aprobado. No se ha reservado energía.',
    'budget_exhausted': 'Presupuesto interno de la herramienta agotado. Herramienta en pausa; no se consume energía.',
    'registration_required': 'Regístrate para usar herramientas con Energía Ducky.',
    'insufficient_energy': 'No tienes energía suficiente para esta tarea. ' + MSG_CONTINUE,
    'covered': 'Esta ejecución está cubierta por un servicio comprado: no consume energía.',
    'ok': MSG_ENOUGH,
}


def _covering_entitlement(user, tool, now, lock=False):
    if not tool.service_entitlement_code:
        return None
    from apps.users.models import PurchasedEntitlement

    used = EnergyOperation.objects.filter(entitlement__isnull=False).exclude(
        status=EnergyOperation.Status.RELEASED,
    ).values('entitlement_id')
    qs = PurchasedEntitlement.objects.filter(
        user=user, code=tool.service_entitlement_code, revoked_at__isnull=True, starts_at__lte=now,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).exclude(pk__in=used).order_by(
        'expires_at', 'created_at',
    )
    if lock:
        qs = qs.select_for_update()
    return qs.first()


def estimate(user, tool_code, variant='', now=None):
    """Lo que el usuario ve antes de ejecutar: tarea, alcance y consumo calculados en servidor."""
    now = now or timezone.now()
    tool, reason, cost = tool_state(tool_code, variant, now)
    result = {
        'tool': tool_code, 'tool_name': tool.name if tool else tool_code, 'variant': variant,
        'energy_cost': cost, 'cost_version': tool.version if tool else None,
        'covered_by_service': False, 'enough_energy': None, 'total_available': None,
    }
    if user_plan(user) is None:
        reason = 'registration_required'
    if reason == 'ok':
        if _covering_entitlement(user, tool, now):
            result['covered_by_service'] = True
            reason = 'covered'
        else:
            status = get_status(user, now)
            result['total_available'] = status['total_available']
            result['enough_energy'] = status['total_available'] >= cost
            if not result['enough_energy']:
                reason = 'insufficient_energy'
    result.update({
        'available': reason in ('ok', 'covered'), 'reason': reason, 'message': TOOL_MESSAGES[reason],
    })
    return result


def estimate_payload(result):
    payload = dict(result)
    for key in ('energy_cost', 'total_available'):
        payload[key] = None if payload[key] is None else str(q(payload[key]))
    return payload


MODULE_EXECUTION_PENDING = (
    'La herramienta tiene energía y proveedor configurados, pero este módulo aún no ejecuta IA real. '
    'No se ha enviado ningún dato ni reservado energía.'
)
MODULE_SUFFIX = ' Puedes seguir trabajando manualmente; nada se ha cobrado.'


def module_gate(user, tool_code, variant=''):
    """Disponibilidad para los módulos 07–09. Mientras el módulo no conecte un ejecutor real, nunca
    devuelve available=True, pero expone el motivo y el coste reales calculados por Energía Ducky."""
    if user is not None and getattr(user, 'is_authenticated', False):
        result = estimate(user, tool_code, variant)
        reason, cost = result['reason'], result['energy_cost']
    else:
        _tool, reason, cost = tool_state(tool_code, variant)
    if reason in ('ok', 'covered'):
        reason, message = 'module_execution_pending', MODULE_EXECUTION_PENDING
    else:
        message = TOOL_MESSAGES[reason] + MODULE_SUFFIX
    return {'available': False, 'reason': reason, 'message': message, 'energy_cost': cost, 'tool': tool_code}


def reserve(user, tool_code, *, idempotency_key, variant='', expected_cost=None, related_object='', now=None):
    now = now or timezone.now()
    plan = user_plan(user)
    if plan is None:
        raise EnergyUnavailable(TOOL_MESSAGES['registration_required'], code='registration_required')
    key = str(idempotency_key)
    with transaction.atomic():
        wallet = _locked_wallet(user)
        previous = EnergyOperation.objects.filter(user=user, idempotency_key=key).first()
        if previous:
            if previous.tool.code != tool_code or previous.variant != variant:
                raise EnergyConflict('La clave de la operación ya pertenece a otra solicitud.')
            return previous
        tool, reason, cost = tool_state(tool_code, variant, now)
        if reason != 'ok':
            raise EnergyUnavailable(TOOL_MESSAGES[reason], code=reason)
        tool = EnergyToolCost.objects.select_for_update().get(pk=tool.pk)
        if expected_cost is not None and q(Decimal(str(expected_cost))) != q(cost):
            raise CostChanged(
                f'El consumo ha cambiado a {format_energy(cost)}. Revísalo y acepta de nuevo.', new_cost=cost,
            )
        policy = policy_for_plan(plan)
        limit = (policy.max_concurrent_operations if policy else None) or DEFAULT_MAX_CONCURRENT
        if EnergyOperation.objects.filter(user=user, status=EnergyOperation.Status.RESERVED).count() >= limit:
            raise ConcurrencyLimit('Ya tienes el máximo de operaciones IA en curso. Espera a que terminen.')
        _refresh(wallet, policy, now)
        entitlement = _covering_entitlement(user, tool, now, lock=True)
        operation = EnergyOperation.objects.create(
            user=user, tool=tool, variant=variant, amount=ZERO if entitlement else cost,
            cost_version=tool.version, idempotency_key=key, related_object=related_object[:180],
            entitlement=entitlement,
        )
        if entitlement:
            return operation
        lots = _active_lots(wallet, now, lock=True)
        total = wallet.recoverable_available + sum((lot.available for lot in lots), ZERO)
        if total < cost:
            raise InsufficientEnergy(TOOL_MESSAGES['insufficient_energy'], available=total, cost=cost)
        pending = cost
        take = min(wallet.recoverable_available, pending)
        if take > 0:
            wallet.recoverable_available -= take
            wallet.recoverable_reserved += take
            wallet.save(update_fields=['recoverable_available', 'recoverable_reserved', 'updated_at'])
            EnergyReservation.objects.create(operation=operation, amount=take)
            _ledger(
                wallet, kind=EnergyLedgerEntry.Kind.RESERVE, available=-take, reserved=take, operation=operation,
                key=f'reserve:{operation.pk}:recoverable', tool_code=tool.code, process='energy.reserve',
                reason=f'Reserva para {tool.name}.', rule_reference=f'tool:{tool.code}:v{tool.version}',
            )
            pending -= take
        for lot in lots:
            if pending <= 0:
                break
            take = min(lot.available, pending)
            if take <= 0:
                continue
            lot.available -= take
            lot.reserved += take
            lot.save(update_fields=['available', 'reserved'])
            EnergyReservation.objects.create(operation=operation, lot=lot, amount=take)
            _ledger(
                wallet, kind=EnergyLedgerEntry.Kind.RESERVE, component=EnergyLedgerEntry.Component.PURCHASED,
                lot=lot, available=-take, reserved=take, operation=operation,
                key=f'reserve:{operation.pk}:{lot.pk}', tool_code=tool.code, process='energy.reserve',
                reason=f'Reserva para {tool.name}.', rule_reference=f'tool:{tool.code}:v{tool.version}',
            )
            pending -= take
        return operation


def _resolve(operation_id, *, consume, reason='', provider=None, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        user_id = EnergyOperation.objects.values_list('user_id', flat=True).get(pk=operation_id)
        wallet = EnergyWallet.objects.select_for_update().get(user_id=user_id)
        operation = EnergyOperation.objects.select_for_update().select_related('tool').get(pk=operation_id)
        if operation.status != EnergyOperation.Status.RESERVED:
            if consume and operation.status == EnergyOperation.Status.RELEASED:
                operation.status = EnergyOperation.Status.REVIEW
                operation.failure_reason = 'Resultado tardío tras liberar la reserva: requiere conciliación.'
                operation.save(update_fields=['status', 'failure_reason'])
            return operation
        plan = user_plan(operation.user)
        _refresh(wallet, policy_for_plan(plan), now)
        for reservation in operation.reservations.select_for_update().filter(
            status=EnergyReservation.Status.RESERVED,
        ):
            lot = EnergyPurchasedLot.objects.select_for_update().get(pk=reservation.lot_id) if reservation.lot_id else None
            component = EnergyLedgerEntry.Component.PURCHASED if lot else EnergyLedgerEntry.Component.RECOVERABLE
            suffix = lot.pk if lot else 'recoverable'
            if consume:
                _ledger(
                    wallet, kind=EnergyLedgerEntry.Kind.CONSUME, component=component, lot=lot,
                    reserved=-reservation.amount, operation=operation, tool_code=operation.tool.code,
                    key=f'consume:{operation.pk}:{suffix}', process='energy.confirm',
                    reason=f'Consumo de {operation.tool.name}.',
                    rule_reference=f'tool:{operation.tool.code}:v{operation.cost_version}',
                )
            else:
                _ledger(
                    wallet, kind=EnergyLedgerEntry.Kind.RELEASE, component=component, lot=lot,
                    available=reservation.amount, reserved=-reservation.amount, operation=operation,
                    tool_code=operation.tool.code, key=f'release:{operation.pk}:{suffix}',
                    process='energy.release', reason=(reason or 'Sin resultado utilizable.')[:240],
                    rule_reference=f'tool:{operation.tool.code}:v{operation.cost_version}',
                )
            if lot:
                lot.reserved -= reservation.amount
                if consume:
                    lot.consumed += reservation.amount
                else:
                    lot.available += reservation.amount
                lot.save(update_fields=['available', 'reserved', 'consumed'])
            else:
                wallet.recoverable_reserved -= reservation.amount
                if not consume:
                    wallet.recoverable_available += reservation.amount
            reservation.status = (
                EnergyReservation.Status.CONSUMED if consume else EnergyReservation.Status.RELEASED
            )
            reservation.resolved_at = now
            reservation.save(update_fields=['status', 'resolved_at'])
        wallet.save(update_fields=['recoverable_available', 'recoverable_reserved', 'updated_at'])
        _expire_lots(wallet, now)
        if consume:
            operation.status = (
                EnergyOperation.Status.COVERED if operation.entitlement_id else EnergyOperation.Status.CONFIRMED
            )
        else:
            operation.status = EnergyOperation.Status.RELEASED
            operation.failure_reason = reason[:200]
        for field, value in (provider or {}).items():
            if field in {'provider', 'provider_model', 'provider_usage', 'provider_cost_estimated',
                         'provider_cost_confirmed', 'duration_ms'}:
                setattr(operation, field, value)
        operation.resolved_at = now
        operation.save()
        return operation


def confirm(operation_id, *, provider=None, now=None):
    """Confirma el consumo después de guardar un resultado utilizable."""
    return _resolve(operation_id, consume=True, provider=provider, now=now)


def release(operation_id, reason, *, provider=None, now=None):
    """Devuelve cada unidad reservada a su procedencia, sin duplicarla."""
    return _resolve(operation_id, consume=False, reason=reason, provider=provider, now=now)


@dataclass
class ToolResult:
    usable: bool
    payload: object = None
    provider: dict = None


def run_tool(user, tool_code, *, idempotency_key, execute, variant='', expected_cost=None, related_object=''):
    """Recorrido completo: reservar, ejecutar, guardar (en ``execute``), confirmar o liberar.

    ``execute(operation)`` debe guardar el resultado utilizable y devolver ``ToolResult``.
    Un reintento con la misma clave no vuelve a ejecutar ni a cobrar.
    """
    operation = reserve(
        user, tool_code, idempotency_key=idempotency_key, variant=variant, expected_cost=expected_cost,
        related_object=related_object,
    )
    if operation.status != EnergyOperation.Status.RESERVED or operation.reservations.filter(
        status=EnergyReservation.Status.CONSUMED,
    ).exists():
        return operation, None
    started = time.monotonic()
    try:
        result = execute(operation)
    except Exception as error:
        release(operation.pk, f'Fallo de la herramienta: {type(error).__name__}.')
        raise
    provider = dict(result.provider or {})
    provider.setdefault('duration_ms', int((time.monotonic() - started) * 1000))
    if not result.usable:
        return release(operation.pk, 'La herramienta no produjo un resultado utilizable.', provider=provider), result
    return confirm(operation.pk, provider=provider), result


def reconcile_operations(now=None):
    """Libera operaciones sin resultado que superan la duración máxima configurada."""
    now = now or timezone.now()
    released = []
    for operation in EnergyOperation.objects.filter(
        status=EnergyOperation.Status.RESERVED, tool__max_duration_seconds__isnull=False,
    ).select_related('tool'):
        if operation.created_at + timedelta(seconds=operation.tool.max_duration_seconds * 2) <= now:
            released.append(release(operation.pk, 'Conciliación: trabajo interrumpido sin resultado.', now=now))
    return released


def reconcile_wallet(user):
    """Comprueba que saldo y reservas coinciden con la suma de movimientos."""
    wallet = EnergyWallet.objects.filter(user=user).first()
    if wallet is None:
        return []
    issues = []
    rec = EnergyLedgerEntry.objects.filter(user=user, component='recoverable').aggregate(
        available=Sum('available_delta'), reserved=Sum('reserved_delta'),
    )
    if (rec['available'] or ZERO) != wallet.recoverable_available:
        issues.append(f'Recuperable disponible {wallet.recoverable_available} ≠ movimientos {rec["available"]}.')
    if (rec['reserved'] or ZERO) != wallet.recoverable_reserved:
        issues.append(f'Recuperable reservada {wallet.recoverable_reserved} ≠ movimientos {rec["reserved"]}.')
    for lot in wallet.lots.all():
        sums = EnergyLedgerEntry.objects.filter(lot=lot).aggregate(
            available=Sum('available_delta'), reserved=Sum('reserved_delta'),
        )
        if (sums['available'] or ZERO) != lot.available or (sums['reserved'] or ZERO) != lot.reserved:
            issues.append(f'Lote {lot.pk}: saldo no coincide con movimientos.')
        if lot.available + lot.reserved + lot.consumed + lot.removed != lot.quantity:
            issues.append(f'Lote {lot.pk}: cantidades no suman el total comprado.')
    return issues


# ---------------------------------------------------------------------------
# Compras (00B), devoluciones y ajustes administrativos
# ---------------------------------------------------------------------------

def recharge_for_code(product_code):
    return EnergyRechargeProduct.objects.filter(service__code=product_code).select_related('service').first()


def credit_purchase(order, line):
    """Acredita un lote solo tras pago verificado. Idempotente por línea de pedido."""
    from apps.users.billing_models import BillingOrder

    recharge = recharge_for_code(line.product_code)
    if recharge is None:
        return None
    if order.status != BillingOrder.Status.PAID or order.kind != BillingOrder.Kind.SERVICE:
        return None
    if not order.charges.filter(payments__isnull=False).exists():
        return None
    key = f'purchase:order:{order.pk}:line:{line.pk}'
    with transaction.atomic():
        wallet = _locked_wallet(order.user)
        previous = EnergyPurchasedLot.objects.filter(idempotency_key=key).first()
        if previous:
            return previous
        units = Decimal(str(line.product_snapshot.get('energy_units', recharge.energy_units)))
        quantity = q(units * line.quantity)
        validity_days = line.product_snapshot.get('validity_days')
        lot = EnergyPurchasedLot.objects.create(
            wallet=wallet, quantity=quantity, available=quantity, order=order, order_line=line,
            product_code=line.product_code, conditions=line.product_snapshot.get('conditions', ''),
            expires_at=timezone.now() + timedelta(days=int(validity_days)) if validity_days else None,
            idempotency_key=key,
        )
        _ledger(
            wallet, kind=EnergyLedgerEntry.Kind.PURCHASE, component=EnergyLedgerEntry.Component.PURCHASED,
            lot=lot, available=quantity, key=key, process='billing.confirmed_payment',
            reason=f'Recarga comprada: {line.description} (pedido {order.pk}).',
            rule_reference=f'recharge:{line.product_code}:v{recharge.version}',
        )
        return lot


def revert_lot_for_refund(lot_id, *, actor, reason):
    """Devolución vinculada al lote. Si ya se consumió en parte, queda en revisión sin saldo negativo."""
    from apps.users.billing_models import Refund

    with transaction.atomic():
        lot = EnergyPurchasedLot.objects.select_related('wallet').get(pk=lot_id)
        wallet = EnergyWallet.objects.select_for_update().get(pk=lot.wallet_id)
        lot = EnergyPurchasedLot.objects.select_for_update().get(pk=lot_id)
        if lot.status in (EnergyPurchasedLot.Status.REFUNDED, EnergyPurchasedLot.Status.REVIEW):
            return lot
        if not Refund.objects.filter(
            payment__charge__order_id=lot.order_id, status=Refund.Status.CONFIRMED,
        ).exists():
            raise EnergyError('El lote no tiene una devolución confirmada en facturación.', code='refund_required')
        if lot.available > 0:
            _ledger(
                wallet, kind=EnergyLedgerEntry.Kind.REVERSAL, component=EnergyLedgerEntry.Component.PURCHASED,
                lot=lot, available=-lot.available, key=f'refund:{lot.pk}', actor=actor,
                process='admin.refund', reason=f'Devolución del lote: {reason}',
            )
            lot.removed += lot.available
            lot.available = ZERO
        lot.status = (
            EnergyPurchasedLot.Status.REFUNDED if lot.consumed == 0 and lot.reserved == 0
            else EnergyPurchasedLot.Status.REVIEW
        )
        lot.save(update_fields=['available', 'removed', 'status'])
        return lot


def adjust(*, actor, user, amount, reason, key):
    """Ajuste compensatorio de energía recuperable. Nunca deja saldo negativo."""
    from apps.users.access import is_platform_admin

    if not (is_platform_admin(actor) or actor.has_perm('energy.adjust_energy')):
        raise EnergyError('No tienes permiso para ajustar energía.', code='forbidden')
    if not reason or not reason.strip():
        raise EnergyError('El ajuste necesita un motivo.', code='reason_required')
    amount = q(Decimal(str(amount)))
    if amount == 0:
        raise EnergyError('El ajuste no puede ser cero.', code='invalid_amount')
    with transaction.atomic():
        wallet = _locked_wallet(user)
        if EnergyLedgerEntry.objects.filter(idempotency_key=f'adjust:{key}').exists():
            return wallet
        if wallet.recoverable_available + amount < 0:
            raise EnergyError('El ajuste dejaría saldo negativo.', code='negative_balance')
        _ledger(
            wallet, kind=EnergyLedgerEntry.Kind.ADJUST, available=amount, key=f'adjust:{key}', actor=actor,
            process='admin.adjust', reason=reason.strip(),
        )
        wallet.recoverable_available += amount
        wallet.save(update_fields=['recoverable_available', 'updated_at'])
        return wallet
