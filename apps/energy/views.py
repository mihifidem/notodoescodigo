import uuid

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from apps.users.access import is_platform_admin

from . import services
from .models import (
    EnergyLedgerEntry, EnergyOperation, EnergyPolicy, EnergyPurchasedLot, EnergyRechargeProduct,
    EnergyRewardRule, EnergyToolCost,
)


def _private(response):
    response['Cache-Control'] = 'private, no-store'
    return response


@never_cache
@require_GET
def status(request):
    if not request.user.is_authenticated:
        return _private(JsonResponse({'ok': False, 'reason': 'registration_required'}, status=401))
    return _private(JsonResponse(services.status_payload(services.get_status(request.user))))


@never_cache
@require_GET
def estimate(request):
    if not request.user.is_authenticated:
        return _private(JsonResponse({'ok': False, 'reason': 'registration_required'}, status=401))
    tool = request.GET.get('tool', '')[:80]
    variant = request.GET.get('variant', '')[:60]
    return _private(JsonResponse(services.estimate_payload(services.estimate(request.user, tool, variant))))


def _recharge_offers(user):
    from apps.users.billing_services import service_price

    plan = services.user_plan(user)
    offers = []
    for recharge in EnergyRechargeProduct.objects.filter(
        enabled=True, service__is_active=True,
    ).select_related('service'):
        offers.append({
            'recharge': recharge,
            'price': service_price(recharge.service, plan) if plan else recharge.service.base_price,
        })
    return offers


@never_cache
@require_GET
def detail(request):
    context = {
        'policies': EnergyPolicy.objects.order_by('pk'),
        'rules': EnergyRewardRule.objects.filter(enabled=True, amount__isnull=False),
        'tools': EnergyToolCost.objects.order_by('module', 'name'),
        'recharges': _recharge_offers(request.user),
    }
    if request.user.is_authenticated:
        context['energy'] = services.get_status(request.user)
        context['operations'] = EnergyOperation.objects.filter(
            user=request.user, status=EnergyOperation.Status.RESERVED,
        ).select_related('tool')
        context['lots'] = EnergyPurchasedLot.objects.filter(wallet__user=request.user).order_by('-created_at')[:20]
        ledger = EnergyLedgerEntry.objects.filter(user=request.user).select_related('lot')
        context['page_obj'] = Paginator(ledger, 25).get_page(request.GET.get('page'))
    return _private(render(request, 'energy/detail.html', context))


class AdjustForm(forms.Form):
    username = forms.CharField(label='Usuario', max_length=150)
    amount = forms.DecimalField(label='Cantidad (positiva o negativa)', max_digits=14, decimal_places=3)
    reason = forms.CharField(label='Motivo', widget=forms.Textarea(attrs={'rows': 3}), max_length=240)
    key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def clean_username(self):
        user = get_user_model().objects.filter(username=self.cleaned_data['username']).first()
        if user is None:
            raise forms.ValidationError('Usuario no encontrado.')
        return user


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def admin_adjust(request):
    if not (is_platform_admin(request.user) or request.user.has_perm('energy.adjust_energy')):
        raise PermissionDenied('No tienes permiso para ajustar energía.')
    form = AdjustForm(request.POST or None, initial={'key': uuid.uuid4()})
    if request.method == 'POST' and form.is_valid():
        try:
            services.adjust(
                actor=request.user, user=form.cleaned_data['username'], amount=form.cleaned_data['amount'],
                reason=form.cleaned_data['reason'], key=form.cleaned_data['key'],
            )
        except services.EnergyError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, 'Ajuste registrado como movimiento compensatorio.')
            return redirect('energy:admin_adjust')
    issues = []
    if request.GET.get('reconcile'):
        user = get_user_model().objects.filter(username=request.GET['reconcile']).first()
        issues = services.reconcile_wallet(user) if user else ['Usuario no encontrado.']
        if not issues:
            messages.success(request, 'El saldo coincide con sus movimientos.')
    return _private(render(request, 'energy/admin_adjust.html', {'form': form, 'issues': issues}))
