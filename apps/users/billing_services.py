import calendar
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .access import effective_plan
from .billing_models import (
    BillingConfiguration,
    BillingEvent,
    BillingOrder,
    BillingOrderLine,
    Charge,
    ConfirmedPayment,
    MembershipContract,
    PaymentAttempt,
    PaymentProviderConfiguration,
    PromotionalVoucher,
    ServiceProduct,
    UserVoucher,
)
from .models import AccessMembership, PurchasedEntitlement


class BillingUnavailable(Exception):
    """Raised when no verified provider is available for the requested operation."""


MONEY_QUANTUM = Decimal('0.01')
PROVIDER_ADAPTERS = {}
PLAN_PRICE_FIELDS = {
    (AccessMembership.Plan.PLUS, 'monthly'): 'plus_monthly',
    (AccessMembership.Plan.PLUS, 'semiannual'): 'plus_semiannual',
    (AccessMembership.Plan.PLUS, 'annual'): 'plus_annual',
    (AccessMembership.Plan.PRO, 'monthly'): 'pro_monthly',
    (AccessMembership.Plan.PRO, 'semiannual'): 'pro_semiannual',
    (AccessMembership.Plan.PRO, 'annual'): 'pro_annual',
}
PERIOD_MONTHS = {'monthly': 1, 'semiannual': 6, 'annual': 12}
TERMS_VERSION = 'billing-terms-v1'


def money(value):
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def membership_price(plan, period):
    field = PLAN_PRICE_FIELDS.get((plan, period))
    configuration = BillingConfiguration.current()
    if field is None or configuration is None:
        return None, 'not_configured'
    value = getattr(configuration, field)
    if value is None:
        return None, 'not_configured'
    return money(value), 'configured'


def service_price(service, plan):
    if not service.is_active or service.base_price is None:
        return None
    return service.price_for(plan)


def _period_end(start, period):
    months = PERIOD_MONTHS[period]
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def _voucher_discount(*, user, voucher_code, plan, period, subtotal, now):
    if not voucher_code:
        return Decimal('0'), None
    voucher = PromotionalVoucher.objects.select_for_update().filter(
        code__iexact=voucher_code.strip(),
        is_active=True,
    ).first()
    if voucher is None:
        raise ValidationError({'voucher': 'El bono no existe o no está activo.'})
    if voucher.starts_at and voucher.starts_at > now:
        raise ValidationError({'voucher': 'El bono todavía no está vigente.'})
    if voucher.expires_at and voucher.expires_at <= now:
        raise ValidationError({'voucher': 'El bono ha caducado.'})
    if voucher.allowed_plans and plan not in voucher.allowed_plans:
        raise ValidationError({'voucher': 'El bono no es compatible con este plan.'})
    if voucher.allowed_periods and period not in voucher.allowed_periods:
        raise ValidationError({'voucher': 'El bono no es compatible con esta periodicidad.'})

    user_voucher = UserVoucher.objects.select_for_update().filter(
        user=user,
        voucher=voucher,
        status=UserVoucher.Status.AVAILABLE,
    ).first()
    if user_voucher is None:
        raise ValidationError({'voucher': 'El bono no está concedido a esta cuenta.'})
    expiry = user_voucher.effective_expiry
    if expiry and expiry <= now:
        raise ValidationError({'voucher': 'La concesión del bono ha caducado.'})
    if voucher.global_limit is not None:
        reservations_and_uses = UserVoucher.objects.filter(
            voucher=voucher,
            status__in=[UserVoucher.Status.RESERVED, UserVoucher.Status.USED],
        ).count()
        if reservations_and_uses >= voucher.global_limit:
            raise ValidationError({'voucher': 'Se ha alcanzado el límite de usos del bono.'})
    return money(subtotal * voucher.discount_percent / Decimal('100')), user_voucher


def _get_provider_adapter(provider):
    configuration = PaymentProviderConfiguration.objects.filter(
        provider=provider,
        enabled=True,
    ).first()
    adapter_class = PROVIDER_ADAPTERS.get(provider)
    if configuration is None or adapter_class is None:
        raise BillingUnavailable('El método de pago no está configurado ni verificado para esta operación.')
    return configuration, adapter_class


def _validate_terms(accepted_terms):
    if (
        not isinstance(accepted_terms, dict)
        or accepted_terms.get('accepted') is not True
        or accepted_terms.get('version') != TERMS_VERSION
        or not accepted_terms.get('accepted_at')
    ):
        raise ValidationError({'accepted_terms': 'Falta la aceptación explícita de las condiciones vigentes.'})


@transaction.atomic
def create_membership_order(
    *,
    user,
    plan,
    period,
    idempotency_key,
    accepted_terms,
    voucher_code='',
    provider='',
    method='',
):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied('Se requiere una cuenta activa.')
    _validate_terms(accepted_terms)
    if (plan, period) not in PLAN_PRICE_FIELDS:
        raise ValidationError('Plan o periodicidad no válidos.')
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValidationError({'idempotency_key': 'Clave de idempotencia no válida.'})

    previous = BillingOrder.objects.select_for_update().filter(idempotency_key=idempotency_key).first()
    if previous:
        if (
            previous.user_id != user.pk
            or previous.kind != BillingOrder.Kind.MEMBERSHIP
            or previous.membership_plan != plan
            or previous.billing_period != period
        ):
            raise ValidationError('La clave ya se utilizó para otra operación.')
        return previous

    subtotal, price_status = membership_price(plan, period)
    if price_status != 'configured':
        raise BillingUnavailable('Esta tarifa de membresía todavía no está configurada.')

    now = timezone.now()
    discount, user_voucher = _voucher_discount(
        user=user,
        voucher_code=voucher_code,
        plan=plan,
        period=period,
        subtotal=subtotal,
        now=now,
    )
    total = max(subtotal - discount, Decimal('0.00'))
    if total > 0:
        configuration, _ = _get_provider_adapter(provider)
        if (
            not configuration.supports_one_time
            or method not in configuration.methods
        ):
            raise BillingUnavailable('El proveedor no admite esta operación o método de pago.')
        if 'EUR' not in configuration.currencies:
            raise BillingUnavailable('El proveedor no admite EUR para este pago.')
        raise BillingUnavailable(
            f'El adaptador {provider} no está implementado en este entorno; no se ha creado ningún cobro.'
        )

    configuration = BillingConfiguration.current()
    accepted_terms = {
        **accepted_terms,
        'plan': plan,
        'period': period,
        'price': str(subtotal),
        'discount': str(discount),
        'total': str(total),
        'currency': configuration.currency,
    }
    order, created = BillingOrder.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            'user': user,
            'kind': BillingOrder.Kind.MEMBERSHIP,
            'status': BillingOrder.Status.FREE,
            'currency': configuration.currency,
            'subtotal': subtotal,
            'discount_total': discount,
            'total': total,
            'accepted_terms': accepted_terms,
            'membership_plan': plan,
            'billing_period': period,
        },
    )
    if not created:
        if (
            order.user_id != user.pk
            or order.kind != BillingOrder.Kind.MEMBERSHIP
            or order.membership_plan != plan
            or order.billing_period != period
        ):
            raise ValidationError('La clave ya se utilizó para otra operación.')
        return order
    line = BillingOrderLine.objects.create(
        order=order,
        product_code=f'membership-{plan}-{period}',
        description=f'Membresía {plan.upper()} — {period}',
        quantity=1,
        unit_price=subtotal,
        discount_percent=(discount / subtotal * Decimal('100')) if subtotal else Decimal('0'),
        discount_amount=discount,
        line_total=total,
        period=period,
        product_snapshot={'plan': plan, 'period': period, 'price': str(subtotal)},
    )
    charge = Charge.objects.create(
        user=user,
        order=order,
        concept=line.description,
        currency=order.currency,
        subtotal=subtotal,
        discount_total=discount,
        tax_total=Decimal('0'),
        total=total,
        status=Charge.Status.FREE,
        idempotency_key=f'charge:{idempotency_key}',
    )
    contract = MembershipContract.objects.create(
        user=user,
        plan=plan,
        billing_period=period,
        contracted_price=total,
        currency=order.currency,
        status=MembershipContract.Status.ACTIVE,
        starts_at=now,
        period_ends_at=_period_end(now, period),
        idempotency_key=f'contract:{idempotency_key}',
    )
    if user_voucher:
        user_voucher.status = UserVoucher.Status.USED
        user_voucher.used_at = now
        user_voucher.charge = charge
        user_voucher.save(update_fields=['status', 'used_at', 'charge'])
    BillingEvent.objects.get_or_create(
        idempotency_key=f'free-membership:{idempotency_key}',
        defaults={
            'actor': user,
            'user': user,
            'action': 'membership_granted_free',
            'object_type': 'membership_contract',
            'object_id': str(contract.pk),
            'metadata': {'order_id': order.pk, 'charge_id': charge.pk},
        },
    )
    return order


@transaction.atomic
def create_zero_cost_service_order(*, user, service_code, idempotency_key, accepted_terms):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied('Se requiere una cuenta activa.')
    _validate_terms(accepted_terms)
    previous = BillingOrder.objects.select_for_update().filter(idempotency_key=idempotency_key).first()
    if previous:
        if (
            previous.user_id != user.pk
            or previous.kind != BillingOrder.Kind.SERVICE
            or not previous.lines.filter(product_code=service_code).exists()
        ):
            raise ValidationError('La clave ya se utilizó para otra operación.')
        return previous
    service = ServiceProduct.objects.select_for_update().filter(code=service_code, is_active=True).first()
    if service is None:
        raise BillingUnavailable('El servicio no está disponible.')
    price = service_price(service, effective_plan(user))
    if price is None:
        raise BillingUnavailable('El servicio no tiene una tarifa vigente.')
    if service.fulfillment != ServiceProduct.Fulfillment.AUTOMATIC:
        raise BillingUnavailable('La prestación requiere un flujo de entrega que aún no está integrado.')
    if not service.entitlement_code:
        raise BillingUnavailable('El servicio no tiene un derecho de entrega configurado.')
    if price != 0:
        raise BillingUnavailable('El servicio requiere un proveedor de pago verificado.')
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValidationError({'idempotency_key': 'Clave de idempotencia no válida.'})
    total = money(price * service.quantity)
    accepted_terms = {
        **accepted_terms,
        'product_code': service.code,
        'quantity': service.quantity,
        'price': str(total),
        'currency': service.currency,
    }

    order, created = BillingOrder.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            'user': user,
            'kind': BillingOrder.Kind.SERVICE,
            'status': BillingOrder.Status.FREE,
            'currency': service.currency,
            'subtotal': total,
            'total': total,
            'accepted_terms': accepted_terms,
        },
    )
    if not created:
        if (
            order.user_id != user.pk
            or order.kind != BillingOrder.Kind.SERVICE
            or not order.lines.filter(product_code=service.code).exists()
        ):
            raise ValidationError('La clave ya se utilizó para otra operación.')
        return order
    line = BillingOrderLine.objects.create(
        order=order,
        product_code=service.code,
        description=service.name,
        quantity=service.quantity,
        unit_price=price,
        line_total=total,
        entitlement_code=service.entitlement_code,
        product_snapshot={
            'name': service.name,
            'conditions': service.conditions,
            'validity_days': service.validity_days,
            'components': service.package_components,
        },
    )
    charge = Charge.objects.create(
        user=user,
        order=order,
        concept=line.description,
        currency=order.currency,
        subtotal=total,
        total=total,
        status=Charge.Status.FREE,
        idempotency_key=f'charge:{idempotency_key}',
    )
    _grant_order_entitlements(order)
    BillingEvent.objects.get_or_create(
        idempotency_key=f'free-service:{idempotency_key}',
        defaults={
            'actor': user,
            'user': user,
            'action': 'service_order_free',
            'object_type': 'billing_order',
            'object_id': str(order.pk),
            'metadata': {'service_code': service.code, 'charge_id': charge.pk},
        },
    )
    return order


def _grant_order_entitlements(order):
    for line in order.lines.exclude(entitlement_code=''):
        from datetime import timedelta

        expires_at = None
        validity_days = line.product_snapshot.get('validity_days')
        if validity_days is not None:
            expires_at = timezone.now() + timedelta(days=int(validity_days))
        for index in range(line.quantity):
            PurchasedEntitlement.objects.get_or_create(
                idempotency_key=f'order:{order.pk}:line:{line.pk}:unit:{index}',
                defaults={
                    'user': order.user,
                    'code': line.entitlement_code,
                    'source': 'billing_order',
                    'source_reference': f'{order.pk}:{line.pk}:{index}',
                    'expires_at': expires_at,
                },
            )


@transaction.atomic
def confirm_verified_payment(*, attempt_id, provider_event_key, external_reference, amount, currency):
    if not provider_event_key or not external_reference:
        raise ValidationError('La notificación verificada necesita identificadores del proveedor.')
    amount = money(amount)
    currency = currency.upper()
    previous = ConfirmedPayment.objects.select_for_update().filter(
        idempotency_key=provider_event_key,
    ).first()
    if previous:
        if (
            previous.attempt_id != attempt_id
            or previous.external_reference != external_reference
            or previous.amount != amount
            or previous.currency != currency
        ):
            raise ValidationError('La clave de notificación ya se utilizó para otro pago.')
        return previous

    attempt = PaymentAttempt.objects.select_for_update().select_related(
        'charge__order__user',
    ).get(pk=attempt_id)
    charge = Charge.objects.select_for_update().get(pk=attempt.charge_id)
    previous = ConfirmedPayment.objects.select_for_update().filter(
        idempotency_key=provider_event_key,
    ).first()
    if previous:
        if (
            previous.attempt_id != attempt_id
            or previous.external_reference != external_reference
            or previous.amount != amount
            or previous.currency != currency
        ):
            raise ValidationError('La clave de notificación ya se utilizó para otro pago.')
        return previous
    if (
        attempt.status not in {PaymentAttempt.Status.CREATED, PaymentAttempt.Status.PENDING}
        or charge.order.status != BillingOrder.Status.PENDING
        or amount != attempt.amount
        or amount != charge.total
        or currency != attempt.currency.upper()
        or currency != charge.currency.upper()
        or external_reference != attempt.external_reference
        or charge.status in {Charge.Status.CANCELED, Charge.Status.PAID, Charge.Status.FREE}
    ):
        raise ValidationError('El importe, la moneda, el proveedor o la referencia no coinciden.')

    payment = ConfirmedPayment.objects.create(
        user=charge.user,
        charge=charge,
        attempt=attempt,
        provider=attempt.provider,
        method=attempt.method,
        external_reference=external_reference,
        idempotency_key=provider_event_key,
        amount=amount,
        currency=currency,
    )
    attempt.status = PaymentAttempt.Status.SUCCEEDED
    attempt.save(update_fields=['status', 'updated_at'])
    charge.status = Charge.Status.PAID
    charge.save(update_fields=['status'])
    order = charge.order
    order.status = BillingOrder.Status.PAID
    order.save(update_fields=['status', 'updated_at'])
    if order.kind == BillingOrder.Kind.MEMBERSHIP:
        now = timezone.now()
        contract = MembershipContract.objects.create(
            user=order.user,
            plan=order.membership_plan,
            billing_period=order.billing_period,
            contracted_price=charge.total,
            currency=charge.currency,
            status=MembershipContract.Status.ACTIVE,
            starts_at=now,
            period_ends_at=_period_end(now, order.billing_period),
            provider=attempt.provider,
            provider_reference=external_reference,
            idempotency_key=f'paid-contract:{provider_event_key}',
        )
        BillingEvent.objects.get_or_create(
            idempotency_key=f'paid-membership:{provider_event_key}',
            defaults={
                'actor': None,
                'user': order.user,
                'action': 'membership_activated',
                'object_type': 'membership_contract',
                'object_id': str(contract.pk),
                'metadata': {'charge_id': charge.pk, 'payment_id': payment.pk},
            },
        )
    else:
        _grant_order_entitlements(order)
        BillingEvent.objects.get_or_create(
            idempotency_key=f'paid-service:{provider_event_key}',
            defaults={
                'actor': None,
                'user': order.user,
                'action': 'service_order_paid',
                'object_type': 'billing_order',
                'object_id': str(order.pk),
                'metadata': {'charge_id': charge.pk, 'payment_id': payment.pk},
            },
        )
    for user_voucher in UserVoucher.objects.select_for_update().filter(
        reservation_reference=str(order.pk),
        status=UserVoucher.Status.RESERVED,
    ):
        if user_voucher.status != UserVoucher.Status.RESERVED:
            raise ValidationError('El bono reservado ya no está disponible.')
        user_voucher.status = UserVoucher.Status.USED
        user_voucher.used_at = timezone.now()
        user_voucher.save(update_fields=['status', 'used_at'])
    return payment


def billing_options():
    config = BillingConfiguration.current()
    prices = {}
    for plan in (AccessMembership.Plan.PLUS, AccessMembership.Plan.PRO):
        for period in PERIOD_MONTHS:
            value, status = membership_price(plan, period)
            prices[f'{plan}_{period}'] = {
                'amount': str(value) if value is not None else None,
                'currency': config.currency if config else 'EUR',
                'configured': status == 'configured',
            }
    providers = []
    for provider in PROVIDER_ADAPTERS:
        configuration = PaymentProviderConfiguration.objects.filter(
            provider=provider,
            enabled=True,
            supports_one_time=True,
        ).first()
        if configuration:
            providers.append({
                'provider': provider,
                'methods': configuration.methods,
                'currencies': configuration.currencies,
            })
    return {
        'prices': prices,
        'providers': providers,
    }
