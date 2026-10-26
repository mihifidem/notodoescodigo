from django import forms
from django.contrib import admin, messages
from django.forms.models import model_to_dict
from django.urls import reverse
from django.utils.html import format_html

from . import services
from .models import (
    EnergyActivityGrant, EnergyConfigVersion, EnergyLedgerEntry, EnergyOperation, EnergyPolicy,
    EnergyPurchasedLot, EnergyRechargeProduct, EnergyReservation, EnergyRewardRule, EnergyToolCost,
    EnergyWallet,
)


class VersionedForm(forms.ModelForm):
    change_reason = forms.CharField(
        label='Motivo del cambio', widget=forms.Textarea(attrs={'rows': 2}),
        help_text='Se guarda con responsable, fecha y versión.',
    )


class VersionedAdmin(admin.ModelAdmin):
    """Cada guardado incrementa la versión y registra una instantánea inmutable."""

    form = VersionedForm
    readonly_fields = ('version', 'updated_by', 'updated_at')
    version_kind = ''

    def object_key(self, obj):
        return str(obj.pk)

    def save_model(self, request, obj, form, change):
        if change:
            obj.version += 1
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        snapshot = model_to_dict(obj, exclude=['updated_by'])
        EnergyConfigVersion.objects.create(
            kind=self.version_kind, object_key=self.object_key(obj), version=obj.version,
            snapshot={key: str(value) if value is not None else None for key, value in snapshot.items()},
            changed_by=request.user, reason=form.cleaned_data.get('change_reason', ''),
        )


@admin.register(EnergyPolicy)
class EnergyPolicyAdmin(VersionedAdmin):
    version_kind = 'policy'
    list_display = (
        'plan', 'enabled', 'capacity', 'recovery_amount', 'recovery_interval_minutes',
        'activity_daily_percent', 'max_concurrent_operations', 'version', 'updated_at',
    )

    def object_key(self, obj):
        return obj.plan

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EnergyToolCost)
class EnergyToolCostAdmin(VersionedAdmin):
    version_kind = 'tool'
    list_display = ('code', 'module', 'enabled', 'paused', 'cost', 'provider_budget', 'budget_state', 'version')
    list_filter = ('module', 'enabled', 'paused')

    def object_key(self, obj):
        return obj.code

    @admin.display(description='Gasto del periodo')
    def budget_state(self, obj):
        from django.utils import timezone

        spent = services._provider_spend(obj, timezone.now())
        if obj.provider_budget is None:
            return f'{spent} € (sin presupuesto: no ejecuta)'
        alert = obj.budget_alert_percent and obj.provider_budget and (
            spent * 100 >= obj.provider_budget * obj.budget_alert_percent
        )
        return f'{spent} / {obj.provider_budget} €' + (' ⚠ alerta' if alert else '')


@admin.register(EnergyRewardRule)
class EnergyRewardRuleAdmin(VersionedAdmin):
    version_kind = 'rule'
    list_display = ('code', 'source', 'enabled', 'amount', 'frequency', 'max_events_per_day', 'version')

    def object_key(self, obj):
        return obj.code


@admin.register(EnergyRechargeProduct)
class EnergyRechargeProductAdmin(VersionedAdmin):
    version_kind = 'recharge'
    list_display = ('service', 'energy_units', 'enabled', 'version')

    def object_key(self, obj):
        return obj.service.code


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EnergyConfigVersion)
class EnergyConfigVersionAdmin(ReadOnlyAdmin):
    list_display = ('kind', 'object_key', 'version', 'changed_by', 'changed_at', 'reason')
    list_filter = ('kind',)


@admin.register(EnergyWallet)
class EnergyWalletAdmin(ReadOnlyAdmin):
    list_display = ('user', 'recoverable_available', 'recoverable_reserved', 'recovery_anchor', 'adjust_link')
    search_fields = ('user__username', 'user__email')

    @admin.display(description='Ajuste / conciliación')
    def adjust_link(self, obj):
        url = reverse('energy:admin_adjust')
        return format_html(
            '<a href="{}">Ajustar</a> · <a href="{}?reconcile={}">Conciliar</a>', url, url, obj.user.username,
        )


@admin.register(EnergyLedgerEntry)
class EnergyLedgerEntryAdmin(ReadOnlyAdmin):
    list_display = (
        'created_at', 'user', 'kind', 'component', 'available_delta', 'reserved_delta', 'tool_code',
        'activity_code', 'process', 'actor',
    )
    list_filter = ('kind', 'component', 'process')
    search_fields = ('user__username', 'idempotency_key', 'reason')


class ReservationInline(admin.TabularInline):
    model = EnergyReservation
    extra = 0
    can_delete = False
    readonly_fields = ('lot', 'amount', 'status', 'created_at', 'resolved_at')

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(EnergyOperation)
class EnergyOperationAdmin(ReadOnlyAdmin):
    list_display = ('created_at', 'user', 'tool', 'variant', 'amount', 'status', 'provider', 'provider_cost_confirmed')
    list_filter = ('status', 'tool')
    search_fields = ('user__username', 'idempotency_key', 'related_object')
    inlines = [ReservationInline]
    actions = ['release_selected']

    def has_change_permission(self, request, obj=None):
        return request.method == 'GET' or request.user.has_perm('energy.adjust_energy')

    @admin.action(description='Liberar reservas seleccionadas (sin resultado utilizable)')
    def release_selected(self, request, queryset):
        count = 0
        for operation in queryset.filter(status=EnergyOperation.Status.RESERVED):
            services.release(operation.pk, f'Liberación administrativa por {request.user}.')
            count += 1
        self.message_user(request, f'{count} operación(es) liberadas.')


@admin.register(EnergyPurchasedLot)
class EnergyPurchasedLotAdmin(ReadOnlyAdmin):
    list_display = (
        'created_at', 'wallet', 'product_code', 'quantity', 'available', 'reserved', 'consumed', 'removed',
        'expires_at', 'status',
    )
    list_filter = ('status',)
    actions = ['revert_for_refund']

    def has_change_permission(self, request, obj=None):
        return request.method == 'GET' or request.user.has_perm('energy.adjust_energy')

    @admin.action(description='Revertir lote por devolución confirmada')
    def revert_for_refund(self, request, queryset):
        for lot in queryset:
            try:
                result = services.revert_lot_for_refund(
                    lot.pk, actor=request.user, reason=f'Devolución confirmada revisada por {request.user}.',
                )
            except services.EnergyError as error:
                self.message_user(request, f'Lote {lot.pk}: {error}', messages.ERROR)
            else:
                self.message_user(request, f'Lote {lot.pk}: {result.get_status_display()}.')


@admin.register(EnergyActivityGrant)
class EnergyActivityGrantAdmin(ReadOnlyAdmin):
    list_display = ('created_at', 'user', 'rule', 'event_key', 'day', 'plan', 'requested', 'granted', 'result')
    list_filter = ('result', 'rule', 'plan')
    search_fields = ('user__username', 'event_key')
