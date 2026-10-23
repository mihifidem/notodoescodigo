import logging
import uuid

import stripe
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.conf import settings

from .billing_models import BillingSubscription
from .billing_services import BillingUnavailable, TERMS_VERSION
from . import stripe_billing


logger = logging.getLogger(__name__)
QUOTE_SALT = 'users.recurring-membership-v1'


def quote_token(user, plan, period, amount):
    return signing.dumps({
        'user': user.pk, 'plan': plan, 'period': period, 'amount': amount,
        'key': f'subscription:{uuid.uuid4()}',
    }, salt=QUOTE_SALT)


def _notify_error(request, error):
    if isinstance(error, stripe.StripeError):
        logger.warning('Stripe billing request failed (%s)', type(error).__name__)
        detail = 'No se pudo completar la operación con Stripe. Reintenta desde tu pago pendiente; no se ha activado ningún plan sin confirmar el cobro.'
    else:
        detail = ' '.join(error.messages) if isinstance(error, ValidationError) else str(error)
    messages.error(request, detail)


@never_cache
@login_required
@require_POST
def subscribe(request):
    if request.POST.get('accept_terms') != 'yes':
        messages.error(request, 'Debes aceptar el importe y la renovación automática para continuar.')
        return redirect('users:billing')
    try:
        quote = signing.loads(request.POST.get('quote', ''), salt=QUOTE_SALT, max_age=1800)
    except signing.BadSignature:
        messages.error(request, 'La oferta ha caducado o no es válida. Actualiza la página.')
        return redirect('users:billing')
    if quote['user'] != request.user.pk:
        return HttpResponse(status=403)
    try:
        subscription = stripe_billing.prepare_subscription(
            user=request.user, plan=quote['plan'], period=quote['period'], expected_price=quote['amount'],
            idempotency_key=quote['key'], accepted_terms={
                'accepted': True, 'version': TERMS_VERSION, 'accepted_at': timezone.now().isoformat(),
            },
        )
        url = stripe_billing.checkout(subscription)
    except (BillingUnavailable, ValidationError, stripe.StripeError) as error:
        _notify_error(request, error)
        return redirect('users:billing')
    return redirect(url)


@never_cache
@login_required
@require_POST
def resume(request, pk):
    subscription = get_object_or_404(BillingSubscription, pk=pk, user=request.user)
    try:
        url = stripe_billing.checkout(subscription)
    except (BillingUnavailable, ValidationError, stripe.StripeError) as error:
        _notify_error(request, error)
        return redirect('users:billing')
    return redirect(url)


@never_cache
@login_required
@require_POST
def cancel(request, pk):
    get_object_or_404(BillingSubscription, pk=pk, user=request.user)
    try:
        stripe_billing.cancel_subscription(user=request.user, pk=pk)
    except (BillingUnavailable, ValidationError, stripe.StripeError) as error:
        _notify_error(request, error)
    else:
        messages.success(request, 'Cancelación registrada. Si el periodo ya está pagado, conservas su acceso hasta el vencimiento.')
    return redirect('users:billing')


@never_cache
@login_required
@require_POST
def reactivate_renewal(request, pk):
    get_object_or_404(BillingSubscription, pk=pk, user=request.user)
    if request.POST.get('accept_renewal') != 'yes':
        messages.error(request, 'Debes aceptar la renovación automática para volver a activarla.')
        return redirect('users:billing')
    try:
        stripe_billing.reactivate_renewal(user=request.user, pk=pk)
    except (BillingUnavailable, ValidationError, stripe.StripeError) as error:
        _notify_error(request, error)
    else:
        messages.success(request, 'Renovación automática reactivada. Se mantiene el periodo actual y el próximo cobro será al vencimiento, sin un nuevo cobro ahora.')
    return redirect('users:billing')


@never_cache
@login_required
@require_GET
def payment_return(request, pk):
    subscription = get_object_or_404(
        BillingSubscription.objects.select_related('initial_order'), pk=pk, user=request.user,
    )
    response = render(request, 'users/subscription_return.html', {'subscription': subscription})
    response['Cache-Control'] = 'private, no-store'
    return response


@csrf_exempt
@require_POST
def stripe_webhook(request):
    try:
        stripe_billing.provider_configuration()
    except BillingUnavailable:
        logger.error('Stripe billing webhook received while billing is unavailable')
        return HttpResponse(status=503)
    try:
        event = stripe.Webhook.construct_event(
            request.body, request.headers.get('Stripe-Signature', ''),
            settings.STRIPE_BILLING_WEBHOOK_SECRET,
        ).to_dict()
    except (ValueError, stripe.SignatureVerificationError):
        logger.warning('Rejected Stripe billing webhook: invalid payload or signature')
        return HttpResponse(status=400)
    try:
        stripe_billing.process_event(event)
    except ValidationError:
        logger.exception('Stripe billing event %s failed validation', event.get('id'))
        return HttpResponse(status=400)
    except stripe.StripeError:
        logger.exception('Stripe billing event %s could not be reconciled', event.get('id'))
        return HttpResponse(status=503)
    return JsonResponse({'received': True})
