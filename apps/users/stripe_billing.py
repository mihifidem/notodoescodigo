import calendar
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal
from urllib.parse import urlparse

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .access import effective_plan
from .billing_models import (
    BillingEvent, BillingOrder, BillingOrderLine, BillingSubscription, Charge,
    ConfirmedPayment, MembershipContract, PaymentAttempt, PaymentProviderConfiguration,
)
from .billing_services import (
    BillingUnavailable, PERIOD_MONTHS, TERMS_VERSION, _period_end, _validate_terms,
    confirm_verified_payment, membership_price, money,
)


PLAN_RANK = {'free': 0, 'plus': 1, 'pro': 2}


def configuration_error(configuration):
    if configuration.provider != 'stripe':
        return 'Solo Stripe dispone de integración de suscripciones.'
    if configuration.methods != ['card'] or configuration.currencies != ['EUR']:
        return 'Las suscripciones admiten únicamente methods=["card"] y currencies=["EUR"].'
    if configuration.supports_one_time or configuration.supports_refunds:
        return 'Esta integración no admite pagos únicos ni devoluciones desde el portal.'
    if not configuration.supports_recurring:
        return 'Activa la capacidad de suscripciones recurrentes.'
    key = settings.STRIPE_SECRET_KEY
    prefix = ('sk_live_', 'rk_live_') if configuration.environment == 'production' else ('sk_test_', 'rk_test_')
    if not key.startswith(prefix) or not settings.STRIPE_BILLING_WEBHOOK_SECRET.startswith('whsec_'):
        return 'Faltan las credenciales Stripe del entorno y el secreto del webhook de facturación.'
    url = urlparse(settings.BILLING_PUBLIC_BASE_URL)
    local = settings.DEBUG and url.hostname in {'localhost', '127.0.0.1'}
    if (
        not url.netloc or url.username or url.password or url.query or url.fragment
        or (url.scheme != 'https' and not (local and url.scheme == 'http'))
    ):
        return 'Configura BILLING_PUBLIC_BASE_URL con el origen HTTPS del portal.'
    return ''


def provider_configuration():
    configuration = PaymentProviderConfiguration.objects.filter(provider='stripe', enabled=True).first()
    if configuration is None:
        raise BillingUnavailable('Stripe todavía no está habilitado para suscripciones.')
    error = configuration_error(configuration)
    if error:
        raise BillingUnavailable(error)
    return configuration


def subscriptions_available():
    try:
        provider_configuration()
    except BillingUnavailable:
        return False
    return True


def _api_options():
    return {'api_key': settings.STRIPE_SECRET_KEY}


def _data(value):
    return value.to_dict() if isinstance(value, stripe.StripeObject) else value


def _event(user, action, subscription, key):
    BillingEvent.objects.get_or_create(
        idempotency_key=key,
        defaults={
            'user': user, 'actor': user, 'action': action,
            'object_type': 'billing_subscription', 'object_id': str(subscription.pk),
        },
    )


@transaction.atomic
def prepare_subscription(*, user, plan, period, expected_price, idempotency_key, accepted_terms):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied('Se requiere una cuenta activa.')
    provider_configuration()
    _validate_terms(accepted_terms)
    get_user_model().objects.select_for_update().get(pk=user.pk)
    previous = BillingSubscription.objects.filter(initial_order__idempotency_key=idempotency_key).first()
    if previous:
        order = previous.initial_order
        if order.user_id != user.pk or order.membership_plan != plan or order.billing_period != period:
            raise ValidationError('Esta solicitud pertenece a otra operación.')
        if previous.status != BillingSubscription.Status.PENDING:
            raise ValidationError('Esta solicitud ya se ha completado o cancelado. Actualiza la página.')
        return previous
    if plan not in {'plus', 'pro'} or period not in PERIOD_MONTHS:
        raise ValidationError('Selecciona un plan y una periodicidad válidos.')
    if PLAN_RANK[plan] <= PLAN_RANK[effective_plan(user)]:
        raise ValidationError('Solo puedes contratar una mejora de tu plan actual.')
    price, status = membership_price(plan, period)
    if status != 'configured' or price is None or price <= 0:
        raise BillingUnavailable('La suscripción requiere una tarifa positiva aprobada y activa.')
    if price != money(expected_price):
        raise ValidationError('La tarifa ha cambiado. Actualiza la página y acepta el nuevo importe.')
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValidationError('Solicitud de pago no válida.')
    if BillingSubscription.objects.filter(user=user, status='pending').exists():
        raise ValidationError('Ya tienes un pago pendiente. Continúalo o cancélalo antes de elegir otro plan.')
    current = BillingSubscription.objects.filter(
        user=user, status__in=['active', 'past_due'],
    ).order_by('-created_at').first()
    order = BillingOrder.objects.create(
        user=user, kind='membership', status='pending', membership_plan=plan, billing_period=period,
        currency='EUR', subtotal=price, total=price, idempotency_key=idempotency_key,
        accepted_terms={
            **accepted_terms, 'plan': plan, 'period': period, 'total': str(price), 'currency': 'EUR',
            'auto_renew': True, 'upgrade_policy': 'full_period_no_proration',
        },
    )
    BillingOrderLine.objects.create(
        order=order, product_code=f'membership-{plan}-{period}',
        description=f'Suscripción {plan.upper()}', quantity=1, unit_price=price,
        line_total=price, period=period, product_snapshot={'auto_renew': True},
    )
    charge = Charge.objects.create(
        user=user, order=order, concept=f'Suscripción {plan.upper()}', currency='EUR',
        subtotal=price, total=price, status='pending', idempotency_key=f'charge:{idempotency_key}',
    )
    PaymentAttempt.objects.create(
        charge=charge, provider='stripe', method='card', amount=price, currency='EUR',
        idempotency_key=f'attempt:{idempotency_key}',
    )
    subscription = BillingSubscription.objects.create(user=user, initial_order=order, replaces=current)
    _event(user, 'subscription_requested', subscription, f'subscription-requested:{subscription.pk}')
    return subscription


@transaction.atomic
def checkout(subscription):
    subscription = BillingSubscription.objects.select_for_update().select_related('initial_order').get(pk=subscription.pk)
    if subscription.status != 'pending':
        raise ValidationError('El pago ya no está pendiente.')
    provider_configuration()
    if subscription.checkout_id:
        return subscription.checkout_url
    if (timezone.now() - subscription.created_at).total_seconds() > 23 * 3600:
        raise BillingUnavailable('La solicitud necesita conciliación administrativa antes de volver a crear un checkout. No se iniciará otro cobro.')
    order = subscription.initial_order
    base = settings.BILLING_PUBLIC_BASE_URL.rstrip('/')
    metadata = {'billing_subscription': str(subscription.pk), 'billing_order': str(order.pk)}
    session = _data(stripe.checkout.Session.create(
        mode='subscription', payment_method_types=['card'],
        client_reference_id=str(order.pk), metadata=metadata,
        subscription_data={'metadata': metadata},
        line_items=[{
            'quantity': 1,
            'price_data': {
                'currency': 'eur', 'unit_amount': int(order.total * 100),
                'recurring': {'interval': 'month', 'interval_count': PERIOD_MONTHS[order.billing_period]},
                'product_data': {'name': f'Membresía {order.membership_plan.upper()}'},
            },
        }],
        success_url=base + reverse('users:subscription_return', kwargs={'pk': subscription.pk}),
        cancel_url=base + reverse('users:billing'),
        idempotency_key=f'billing-checkout-{subscription.pk}',
        **_api_options(),
    ))
    if not session.get('id') or not session.get('url') or urlparse(session['url']).hostname != 'checkout.stripe.com':
        raise BillingUnavailable('Stripe no ha devuelto un checkout válido. Reintenta la misma solicitud.')
    subscription.checkout_id = session['id']
    subscription.checkout_url = session['url']
    subscription.save(update_fields=['checkout_id', 'checkout_url', 'updated_at'])
    return subscription.checkout_url


@transaction.atomic
def cancel_subscription(*, user, pk):
    get_user_model().objects.select_for_update().get(pk=user.pk)
    subscription = BillingSubscription.objects.select_for_update().get(pk=pk, user=user)
    provider_configuration()
    if subscription.status == 'pending':
        if subscription.checkout_id:
            session = _data(stripe.checkout.Session.retrieve(subscription.checkout_id, **_api_options()))
            if session['status'] == 'complete':
                raise ValidationError('Stripe ya ha procesado el pago. Espera su confirmación antes de cancelar la renovación.')
            if session['status'] == 'open':
                stripe.checkout.Session.expire(subscription.checkout_id, **_api_options())
        _cancel_pending(subscription)
    elif subscription.status in {'active', 'past_due'} and not subscription.cancel_at_period_end:
        stripe.Subscription.modify(
            subscription.stripe_id, cancel_at_period_end=True,
            idempotency_key=f'billing-cancel-{subscription.pk}-{subscription.updated_at.isoformat()}', **_api_options(),
        )
        subscription.cancel_at_period_end = True
        subscription.save(update_fields=['cancel_at_period_end', 'updated_at'])
        MembershipContract.objects.filter(
            user=user, provider='stripe', provider_reference=subscription.stripe_id,
        ).update(auto_renew=False, cancel_at_period_end=True)
    _event(user, 'subscription_cancellation_requested', subscription, f'subscription-cancel:{pk}')


@transaction.atomic
def reactivate_renewal(*, user, pk):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied('Inicia sesión para gestionar tu renovación.')
    get_user_model().objects.select_for_update().get(pk=user.pk)
    subscription = BillingSubscription.objects.select_for_update().get(pk=pk, user=user)
    if not subscription.can_reactivate_renewal:
        raise ValidationError('Solo puedes reactivar la renovación de una suscripción activa con cancelación programada y periodo vigente.')
    provider_configuration()
    remote = _data(stripe.Subscription.retrieve(subscription.stripe_id, **_api_options()))
    linked = _subscription_from_remote(remote)
    if linked is None or linked.pk != subscription.pk or remote['status'] != 'active':
        raise ValidationError('Stripe no confirma una suscripción activa. Actualiza la página o revisa su estado antes de reintentar.')
    key = f'billing-reactivate-{subscription.pk}-{subscription.updated_at.isoformat()}'
    updated = _data(stripe.Subscription.modify(
        subscription.stripe_id, cancel_at_period_end=False, idempotency_key=key, **_api_options(),
    ))
    if (
        updated.get('id') != subscription.stripe_id or updated.get('status') != 'active'
        or updated.get('cancel_at_period_end') is not False
    ):
        raise ValidationError('Stripe no ha confirmado la reactivación de la renovación. Revisa su estado antes de reintentar.')
    subscription.cancel_at_period_end = False
    subscription.save(update_fields=['cancel_at_period_end', 'updated_at'])
    MembershipContract.objects.filter(
        user=user, provider='stripe', provider_reference=subscription.stripe_id,
    ).update(auto_renew=True, cancel_at_period_end=False)
    _event(user, 'subscription_renewal_reactivated', subscription, key)
    return subscription


def _cancel_pending(subscription):
    subscription.status = 'canceled'
    subscription.save(update_fields=['status', 'updated_at'])
    order = subscription.initial_order
    order.status = 'canceled'
    order.save(update_fields=['status', 'updated_at'])
    order.charges.update(status='canceled')
    PaymentAttempt.objects.filter(charge__order=order).update(status='canceled')


def _reference(value):
    return value.get('id') if isinstance(value, dict) else value


def _invoice_subscription(invoice):
    parent = invoice.get('parent') or {}
    details = parent.get('subscription_details') or {}
    return _reference(details.get('subscription') or invoice.get('subscription'))


def _subscription_from_remote(remote):
    metadata = remote.get('metadata') or {}
    pk = metadata.get('billing_subscription')
    if not pk:
        return None
    subscription = BillingSubscription.objects.select_related('initial_order', 'user').filter(pk=pk).first()
    if subscription is None or str(subscription.initial_order_id) != metadata.get('billing_order'):
        raise ValidationError('La suscripción Stripe no corresponde a un pedido local.')
    if subscription.stripe_id and subscription.stripe_id != remote['id']:
        raise ValidationError('La referencia Stripe no coincide con la suscripción.')
    return subscription


@transaction.atomic
def process_event(event):
    event = _data(event)
    configuration = provider_configuration()
    if bool(event.get('livemode')) != (configuration.environment == 'production'):
        raise ValidationError('El evento pertenece a otro entorno Stripe.')
    kind = event['type']
    obj = event['data']['object']
    if kind == 'checkout.session.expired':
        subscription = BillingSubscription.objects.select_for_update().filter(
            checkout_id=obj['id'], status='pending',
        ).first()
        if subscription:
            _cancel_pending(subscription)
        return
    if kind not in {'invoice.payment_succeeded', 'invoice.payment_failed',
                    'customer.subscription.updated', 'customer.subscription.deleted'}:
        return
    remote_id = _invoice_subscription(obj) if kind.startswith('invoice.') else obj['id']
    if not remote_id:
        return
    remote = _data(stripe.Subscription.retrieve(remote_id, **_api_options()))
    subscription = _subscription_from_remote(remote)
    if subscription is None:
        return
    get_user_model().objects.select_for_update().get(pk=subscription.user_id)
    subscription = BillingSubscription.objects.select_for_update().select_related('initial_order', 'user').get(
        pk=subscription.pk,
    )
    if kind == 'invoice.payment_succeeded':
        # Retrieve the current invoice rather than trusting an old event's payment state.
        invoice = _data(stripe.Invoice.retrieve(obj['id'], **_api_options()))
        _apply_invoice(subscription, remote, invoice)
    elif kind == 'invoice.payment_failed':
        # An old failure must never undo a subsequent successful payment.
        if remote['status'] in {'past_due', 'unpaid'} and subscription.status != 'canceled':
            subscription.status = 'past_due' if subscription.stripe_id else 'pending'
            subscription.save(update_fields=['status', 'updated_at'])
            _event(subscription.user, 'subscription_payment_failed', subscription, f'stripe-failure:{obj["id"]}')
    elif subscription.stripe_id:
        canceled = remote['status'] in {'canceled', 'incomplete_expired'}
        subscription.cancel_at_period_end = bool(remote.get('cancel_at_period_end')) or canceled
        if canceled:
            subscription.status = 'canceled'
        elif remote['status'] in {'past_due', 'unpaid'} and subscription.status != 'canceled':
            subscription.status = 'past_due'
        subscription.save(update_fields=['status', 'cancel_at_period_end', 'updated_at'])
        MembershipContract.objects.filter(
            user=subscription.user, provider='stripe', provider_reference=subscription.stripe_id,
        ).update(auto_renew=not subscription.cancel_at_period_end, cancel_at_period_end=subscription.cancel_at_period_end)


def _apply_invoice(subscription, remote, invoice):
    order = subscription.initial_order
    key = f'stripe-invoice:{invoice["id"]}'
    if ConfirmedPayment.objects.filter(idempotency_key=key).exists():
        return
    lines = invoice.get('lines') or {}
    items = lines.get('data', [])
    if (
        _invoice_subscription(invoice) != remote['id'] or invoice.get('status') != 'paid'
        or invoice.get('currency') != 'eur' or invoice.get('amount_paid') != int(order.total * 100)
        or invoice.get('total') != int(order.total * 100)
        or lines.get('has_more') or len(items) != 1
        or items[0].get('quantity') != 1 or items[0].get('amount') != int(order.total * 100)
        or invoice.get('billing_reason') not in {'subscription_create', 'subscription_cycle'}
        or bool(invoice.get('livemode')) != (provider_configuration().environment == 'production')
    ):
        raise ValidationError('El pago Stripe no coincide con el contrato de suscripción.')
    period = items[0]['period']
    start = datetime.fromtimestamp(period['start'], tz=datetime_timezone.utc)
    end = datetime.fromtimestamp(period['end'], tz=datetime_timezone.utc)
    expected_end = _period_end(start, order.billing_period)
    anchor = datetime.fromtimestamp(remote['billing_cycle_anchor'], tz=datetime_timezone.utc)
    expected_end = expected_end.replace(day=min(anchor.day, calendar.monthrange(expected_end.year, expected_end.month)[1]))
    if end <= start or abs((expected_end - end).total_seconds()) > 1:
        raise ValidationError('La vigencia del pago no coincide con el periodo contratado.')
    if subscription.stripe_id and subscription.stripe_id != remote['id']:
        raise ValidationError('La factura pertenece a otra suscripción.')
    if not subscription.stripe_id and subscription.status != 'pending':
        raise ValidationError('La solicitud inicial ya no está pendiente.')
    if subscription.replaces and subscription.replaces.status != 'canceled':
        old = subscription.replaces
        previous_remote = _data(stripe.Subscription.retrieve(old.stripe_id, **_api_options()))
        if previous_remote['status'] != 'canceled':
            stripe.Subscription.cancel(
                old.stripe_id, prorate=False, invoice_now=False,
                idempotency_key=f'billing-replaced-{subscription.pk}', **_api_options(),
            )
        old.status = 'canceled'
        old.cancel_at_period_end = True
        old.save(update_fields=['status', 'cancel_at_period_end', 'updated_at'])
        MembershipContract.objects.filter(
            user=old.user, provider='stripe', provider_reference=old.stripe_id,
        ).update(auto_renew=False, cancel_at_period_end=True)
    if order.status == 'pending':
        attempt = PaymentAttempt.objects.get(charge__order=order)
        attempt.external_reference = invoice['id']
        attempt.save(update_fields=['external_reference', 'updated_at'])
    else:
        renewal = BillingOrder.objects.create(
            user=subscription.user, kind='membership', status='pending', currency=order.currency,
            membership_plan=order.membership_plan, billing_period=order.billing_period,
            subtotal=order.total, total=order.total, accepted_terms=order.accepted_terms,
            idempotency_key=key,
        )
        BillingOrderLine.objects.create(
            order=renewal, product_code=f'membership-{order.membership_plan}-{order.billing_period}',
            description=f'Renovación {order.membership_plan.upper()}', quantity=1,
            unit_price=order.total, line_total=order.total, period=order.billing_period,
        )
        charge = Charge.objects.create(
            user=subscription.user, order=renewal, concept=f'Renovación {order.membership_plan.upper()}',
            currency='EUR', subtotal=order.total, total=order.total,
            idempotency_key=f'charge:{key}',
        )
        attempt = PaymentAttempt.objects.create(
            charge=charge, provider='stripe', method='card', amount=order.total, currency='EUR',
            external_reference=invoice['id'], idempotency_key=f'attempt:{key}',
        )
    confirm_verified_payment(
        attempt_id=attempt.pk, provider_event_key=key, external_reference=invoice['id'],
        amount=Decimal(invoice['amount_paid']) / 100, currency=invoice['currency'],
    )
    cancellation = bool(remote.get('cancel_at_period_end')) or remote['status'] in {'canceled', 'incomplete_expired'}
    MembershipContract.objects.filter(idempotency_key=f'paid-contract:{key}').update(
        starts_at=start, period_ends_at=end, auto_renew=not cancellation,
        provider_reference=remote['id'], cancel_at_period_end=cancellation,
    )
    subscription.stripe_id = remote['id']
    if subscription.status != 'canceled':
        if remote['status'] in {'canceled', 'incomplete_expired'}:
            subscription.status = 'canceled'
        else:
            subscription.status = 'past_due' if remote['status'] in {'past_due', 'unpaid'} else 'active'
    subscription.cancel_at_period_end = bool(remote.get('cancel_at_period_end')) or subscription.status == 'canceled'
    subscription.period_ends_at = max(end, subscription.period_ends_at or end)
    subscription.save(update_fields=['stripe_id', 'status', 'cancel_at_period_end', 'period_ends_at', 'updated_at'])
    _event(subscription.user, 'subscription_invoice_paid', subscription, f'subscription-paid:{invoice["id"]}')
