"""Admin configuration for the users app."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from .models import (
    AccessAuditEvent,
    AccessMembership,
    AccessPolicyConfiguration,
    AccessRoleAssignment,
    Aula,
    Notificacion,
    Organization,
    OrganizationMembership,
    PurchasedEntitlement,
    RamaProfesional,
    User,
)
from .billing_models import (
    AdministrativeAccessGrant,
    BillingConfiguration,
    BillingEvent,
    BillingOrder,
    BillingOrderLine,
    BillingSubscription,
    Charge,
    ConfirmedPayment,
    Invoice,
    MembershipContract,
    PaymentAttempt,
    PaymentProviderConfiguration,
    PromotionalVoucher,
    Refund,
    ServiceProduct,
    UserVoucher,
)


def _audit_access_change(request, action, obj):
    AccessAuditEvent.objects.create(
        actor=request.user,
        action=action,
        target_type=obj._meta.label_lower,
        target_id=str(obj.pk),
        metadata={
            field: getattr(obj, field)
            for field in ('role', 'plan', 'status', 'key', 'version', 'code', 'source', 'name')
            if hasattr(obj, field)
        },
    )


class AccessAuditAdminMixin:
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        _audit_access_change(request, 'updated' if change else 'created', obj)

    def delete_model(self, request, obj):
        _audit_access_change(request, 'deleted', obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            _audit_access_change(request, 'deleted', obj)
            obj.delete()

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_delete_permission(request, obj)


@admin.register(Aula)
class AulaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'direccion', 'horario', 'total_cursos')
    search_fields = ('nombre', 'direccion', 'horario')
    filter_horizontal = ('cursos',)

    def total_cursos(self, obj):
        return obj.cursos.count()

    total_cursos.short_description = 'Cursos disponibles'


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        'username', 'email', 'first_name', 'last_name',
        'role', 'aula', 'nivel', 'puntos', 'streak', 'is_staff',
    )
    list_filter = ('role', 'nivel', 'is_staff', 'is_active', 'date_joined')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    ordering = ('-date_joined',)

    fieldsets = BaseUserAdmin.fieldsets + (
        ('Tipo de usuario', {'fields': ('role',)}),
        ('Gamificación', {'fields': ('puntos', 'nivel', 'streak', 'fecha_ultimo_acceso', 'avatar', 'bio')}),
        ('Datos academicos', {'fields': ('aula', 'rama_principal')}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Datos personales', {'fields': ('first_name', 'last_name', 'email', 'role')}),
    )

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if not (request.user.is_superuser or request.user.has_perm('users.assign_access_roles')):
            return (*fields, 'role')
        return fields

    def save_model(self, request, obj, form, change):
        original_role = User.objects.filter(pk=obj.pk).values_list('role', flat=True).first() if change else None
        if obj.role != (original_role or 'usuario') and not (
            request.user.is_superuser or request.user.has_perm('users.assign_access_roles')
        ):
            raise PermissionDenied('No tienes permiso para asignar roles de acceso.')
        super().save_model(request, obj, form, change)
        if obj.role != original_role:
            _audit_access_change(request, 'user_role_changed', obj)


@admin.register(RamaProfesional)
class RamaProfesionalAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'order', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'code', 'description')
    ordering = ('order', 'name')

    def _can_manage_catalog(self, request):
        from .access import is_platform_admin

        return is_platform_admin(request.user)

    def has_module_permission(self, request):
        return self._can_manage_catalog(request)

    def has_view_permission(self, request, obj=None):
        return self._can_manage_catalog(request)

    def has_add_permission(self, request):
        return self._can_manage_catalog(request)

    def has_change_permission(self, request, obj=None):
        return self._can_manage_catalog(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return ('code', 'created_at', 'updated_at') if obj else ('created_at', 'updated_at')


@admin.register(Organization)
class OrganizationAdmin(AccessAuditAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'kind', 'is_active', 'created_at')
    list_filter = ('kind', 'is_active')
    search_fields = ('name',)

    def has_module_permission(self, request):
        return request.user.is_superuser or request.user.has_perm('users.manage_access_policy')

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(AccessAuditAdminMixin, admin.ModelAdmin):
    list_display = ('organization', 'user', 'status', 'created_at')
    list_filter = ('status', 'organization__kind')
    search_fields = ('organization__name', 'user__username', 'user__email')
    autocomplete_fields = ('organization', 'user')

    def has_module_permission(self, request):
        return request.user.is_superuser or request.user.has_perm('users.manage_access_policy')

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)


@admin.register(AccessRoleAssignment)
class AccessRoleAssignmentAdmin(AccessAuditAdminMixin, admin.ModelAdmin):
    list_display = ('user', 'role', 'organization', 'is_active', 'assigned_by', 'created_at')
    list_filter = ('role', 'is_active', 'organization__kind')
    search_fields = ('user__username', 'user__email', 'organization__name')
    autocomplete_fields = ('user', 'organization', 'assigned_by')
    readonly_fields = ('assigned_by', 'created_at')

    def _may_assign_roles(self, request):
        return request.user.is_superuser or request.user.has_perm('users.assign_access_roles')

    def has_module_permission(self, request):
        return self._may_assign_roles(request)

    def has_add_permission(self, request):
        return self._may_assign_roles(request)

    def has_change_permission(self, request, obj=None):
        return self._may_assign_roles(request)

    def has_view_permission(self, request, obj=None):
        return self._may_assign_roles(request)

    def save_model(self, request, obj, form, change):
        if obj.role == AccessRoleAssignment.Role.ADMIN and not request.user.is_superuser:
            raise PermissionDenied('Solo un superusuario puede asignar ADMIN.')
        obj.assigned_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(AccessMembership)
class AccessMembershipAdmin(AccessAuditAdminMixin, admin.ModelAdmin):
    list_display = ('user', 'plan', 'status', 'billing_period', 'starts_at', 'ends_at')
    list_filter = ('plan', 'status', 'billing_period')
    search_fields = ('user__username', 'user__email')
    autocomplete_fields = ('user',)

    def _may_manage_memberships(self, request):
        return request.user.is_superuser or request.user.has_perm('users.manage_access_memberships')

    def has_module_permission(self, request):
        return self._may_manage_memberships(request)

    def has_add_permission(self, request):
        return self._may_manage_memberships(request)

    def has_change_permission(self, request, obj=None):
        return self._may_manage_memberships(request)

    def has_view_permission(self, request, obj=None):
        return self._may_manage_memberships(request)


@admin.register(PurchasedEntitlement)
class PurchasedEntitlementAdmin(AccessAuditAdminMixin, admin.ModelAdmin):
    list_display = ('user', 'code', 'source', 'source_reference', 'starts_at', 'expires_at', 'revoked_at')
    list_filter = ('source',)
    search_fields = ('user__username', 'code', 'source_reference', 'idempotency_key')
    autocomplete_fields = ('user',)
    readonly_fields = ('created_at',)

    def _may_issue_entitlements(self, request):
        return request.user.is_superuser or request.user.has_perm('users.issue_purchased_entitlements')

    def has_module_permission(self, request):
        return self._may_issue_entitlements(request)

    def has_add_permission(self, request):
        return self._may_issue_entitlements(request)

    def has_change_permission(self, request, obj=None):
        return self._may_issue_entitlements(request)

    def has_view_permission(self, request, obj=None):
        return self._may_issue_entitlements(request)


@admin.register(AccessPolicyConfiguration)
class AccessPolicyConfigurationAdmin(AccessAuditAdminMixin, admin.ModelAdmin):
    list_display = ('key', 'version', 'status', 'approved_by', 'updated_at')
    list_filter = ('status',)
    search_fields = ('key',)
    readonly_fields = ('created_at', 'updated_at')

    def _may_manage_policy(self, request):
        return request.user.is_superuser or request.user.has_perm('users.manage_access_policy')

    def has_module_permission(self, request):
        return self._may_manage_policy(request)

    def has_add_permission(self, request):
        return self._may_manage_policy(request)

    def has_change_permission(self, request, obj=None):
        return self._may_manage_policy(request)

    def has_view_permission(self, request, obj=None):
        return self._may_manage_policy(request)

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status != AccessPolicyConfiguration.Status.DRAFT:
            return ('key', 'version', 'configuration', 'approved_by', 'created_at', 'updated_at')
        return ('approved_by', 'created_at', 'updated_at')

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        class RequestAwarePolicyForm(form):
            def clean(self):
                cleaned_data = super().clean()
                if (
                    cleaned_data.get('status') == AccessPolicyConfiguration.Status.APPROVED
                    and not self.instance.approved_by_id
                ):
                    self.instance.approved_by = request.user
                return cleaned_data

        return RequestAwarePolicyForm

    def save_model(self, request, obj, form, change):
        previous_status = None
        if change:
            previous_status = AccessPolicyConfiguration.objects.get(pk=obj.pk).status
        if obj.status == AccessPolicyConfiguration.Status.APPROVED and not obj.approved_by_id:
            obj.approved_by = request.user
        if (
            obj.status == AccessPolicyConfiguration.Status.ACTIVE
            and previous_status != AccessPolicyConfiguration.Status.APPROVED
        ):
            raise PermissionDenied('La configuración debe aprobarse en un cambio separado antes de activarse.')
        super().save_model(request, obj, form, change)


@admin.register(AccessAuditEvent)
class AccessAuditEventAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'action', 'target_type', 'target_id')
    list_filter = ('action', 'target_type')
    search_fields = ('actor__username', 'target_type', 'target_id')
    readonly_fields = ('actor', 'action', 'target_type', 'target_id', 'metadata', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'usuario', 'tipo', 'leida', 'creada_en')
    list_filter = ('tipo', 'leida')
    search_fields = ('titulo', 'usuario__username')
    list_editable = ('leida',)
    date_hierarchy = 'creada_en'


class BillingManagedAdmin(admin.ModelAdmin):
    def _may_manage_billing(self, request):
        return request.user.is_superuser or request.user.has_perm('users.manage_billing')

    def has_module_permission(self, request):
        return self._may_manage_billing(request)

    def has_add_permission(self, request):
        return self._may_manage_billing(request)

    def has_change_permission(self, request, obj=None):
        return self._may_manage_billing(request)

    def has_view_permission(self, request, obj=None):
        return self._may_manage_billing(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        BillingEvent.objects.create(
            actor=request.user,
            user=getattr(obj, 'user', None),
            action='admin_updated' if change else 'admin_created',
            object_type=obj._meta.label_lower,
            object_id=str(obj.pk),
            metadata={},
        )


@admin.register(BillingConfiguration)
class BillingConfigurationAdmin(BillingManagedAdmin):
    list_display = ('version', 'status', 'currency', 'approved_by', 'approved_at')
    list_filter = ('status', 'currency')
    readonly_fields = ('approved_by', 'approved_at')

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        class RequestAwareBillingConfigurationForm(form):
            def clean(self):
                cleaned_data = super().clean()
                if cleaned_data.get('status') in {
                    BillingConfiguration.Status.APPROVED,
                    BillingConfiguration.Status.ACTIVE,
                    BillingConfiguration.Status.RETIRED,
                } and not self.instance.approved_by_id:
                    self.instance.approved_by = request.user
                    self.instance.approved_at = timezone.now()
                return cleaned_data

        return RequestAwareBillingConfigurationForm

    def save_model(self, request, obj, form, change):
        if change:
            previous = BillingConfiguration.objects.get(pk=obj.pk)
            if (
                obj.status == BillingConfiguration.Status.ACTIVE
                and previous.status != BillingConfiguration.Status.APPROVED
            ):
                raise PermissionDenied('La configuración debe aprobarse antes, en un cambio separado.')
        super().save_model(request, obj, form, change)


@admin.register(PaymentProviderConfiguration)
class PaymentProviderConfigurationAdmin(BillingManagedAdmin):
    list_display = ('provider', 'enabled', 'environment', 'updated_at')
    list_filter = ('enabled', 'environment', 'provider')
    readonly_fields = ('updated_at',)


@admin.register(AdministrativeAccessGrant)
class AdministrativeAccessGrantAdmin(BillingManagedAdmin):
    list_display = ('user', 'plan', 'status', 'starts_at', 'ends_at', 'granted_by')
    list_filter = ('plan', 'status')
    search_fields = ('user__username', 'user__email', 'reason')
    autocomplete_fields = ('user', 'granted_by', 'revoked_by')
    readonly_fields = ('granted_by', 'created_at')

    def _may_manage_grants(self, request, obj=None):
        return request.user.is_superuser or request.user.has_perm('users.manage_access_grants')

    has_module_permission = _may_manage_grants
    has_add_permission = _may_manage_grants
    has_change_permission = _may_manage_grants
    has_view_permission = _may_manage_grants

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ('user', 'plan', 'starts_at', 'ends_at', 'reason', 'granted_by', 'created_at')
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        if not change:
            obj.granted_by = request.user
        obj.full_clean()
        super().save_model(request, obj, form, change)


@admin.register(ServiceProduct)
class ServiceProductAdmin(BillingManagedAdmin):
    list_display = ('name', 'code', 'is_active', 'base_price', 'currency', 'fulfillment')
    list_filter = ('is_active', 'fulfillment', 'module')
    search_fields = ('name', 'code', 'description')

    def save_model(self, request, obj, form, change):
        obj.full_clean()
        super().save_model(request, obj, form, change)


@admin.register(MembershipContract)
class MembershipContractAdmin(BillingManagedAdmin):
    list_display = ('user', 'plan', 'billing_period', 'status', 'period_ends_at', 'contracted_price')
    list_filter = ('plan', 'billing_period', 'status')
    search_fields = ('user__username', 'user__email', 'provider_reference')
    autocomplete_fields = ('user',)
    readonly_fields = ('created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return tuple(
                field.name for field in MembershipContract._meta.fields
                if field.name not in {'status', 'cancel_at_period_end'}
            )
        return self.readonly_fields


@admin.register(BillingSubscription)
class BillingSubscriptionAdmin(BillingManagedAdmin):
    list_display = ('user', 'status', 'stripe_id', 'cancel_at_period_end', 'period_ends_at')
    list_filter = ('status', 'cancel_at_period_end')
    search_fields = ('user__username', 'stripe_id', 'checkout_id')

    def has_add_permission(self, request):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in BillingSubscription._meta.fields)


@admin.register(BillingEvent)
class BillingEventAdmin(BillingManagedAdmin):
    list_display = ('created_at', 'actor', 'user', 'action', 'object_type', 'object_id')
    list_filter = ('action', 'object_type')
    search_fields = ('user__username', 'object_type', 'object_id', 'idempotency_key')
    readonly_fields = ('actor', 'user', 'action', 'object_type', 'object_id', 'idempotency_key', 'metadata', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BillingOrder)
class BillingOrderAdmin(BillingManagedAdmin):
    list_display = ('pk', 'user', 'kind', 'status', 'currency', 'total', 'created_at')
    list_filter = ('kind', 'status', 'currency')
    search_fields = ('user__username', 'idempotency_key')
    readonly_fields = tuple(field.name for field in BillingOrder._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(BillingOrderLine)
class BillingOrderLineAdmin(BillingManagedAdmin):
    list_display = ('order', 'product_code', 'description', 'quantity', 'line_total')
    search_fields = ('product_code', 'description', 'order__user__username')
    readonly_fields = tuple(field.name for field in BillingOrderLine._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(Charge)
class ChargeAdmin(BillingManagedAdmin):
    list_display = ('user', 'concept', 'status', 'currency', 'total', 'created_at')
    list_filter = ('status', 'currency')
    search_fields = ('user__username', 'concept', 'idempotency_key')
    readonly_fields = tuple(field.name for field in Charge._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(BillingManagedAdmin):
    list_display = ('charge', 'provider', 'method', 'status', 'amount', 'created_at')
    list_filter = ('provider', 'status', 'method')
    readonly_fields = tuple(field.name for field in PaymentAttempt._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(ConfirmedPayment)
class ConfirmedPaymentAdmin(BillingManagedAdmin):
    list_display = ('user', 'provider', 'method', 'amount', 'currency', 'confirmed_at')
    list_filter = ('provider', 'method', 'currency')
    search_fields = ('user__username', 'external_reference', 'idempotency_key')
    readonly_fields = tuple(field.name for field in ConfirmedPayment._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(Invoice)
class InvoiceAdmin(BillingManagedAdmin):
    list_display = ('user', 'series', 'number', 'status', 'total', 'currency', 'issued_at')
    list_filter = ('status', 'currency')
    search_fields = ('user__username', 'number', 'external_reference')
    readonly_fields = tuple(field.name for field in Invoice._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(Refund)
class RefundAdmin(BillingManagedAdmin):
    list_display = ('payment', 'amount', 'currency', 'status', 'created_by', 'created_at')
    list_filter = ('status', 'currency')
    search_fields = ('payment__external_reference', 'reason', 'idempotency_key')
    readonly_fields = tuple(field.name for field in Refund._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(PromotionalVoucher)
class PromotionalVoucherAdmin(BillingManagedAdmin):
    list_display = ('code', 'discount_percent', 'is_active', 'starts_at', 'expires_at', 'global_limit')
    list_filter = ('is_active',)
    search_fields = ('code', 'description')
    readonly_fields = ('created_by', 'created_at')

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.full_clean()
        super().save_model(request, obj, form, change)


@admin.register(UserVoucher)
class UserVoucherAdmin(BillingManagedAdmin):
    list_display = ('user', 'voucher', 'status', 'granted_at', 'expires_at')
    list_filter = ('status', 'voucher')
    search_fields = ('user__username', 'voucher__code', 'reason')
    autocomplete_fields = ('user', 'voucher', 'granted_by', 'charge')
    readonly_fields = ('granted_by', 'granted_at')

    def _may_grant_vouchers(self, request):
        return request.user.is_superuser or request.user.has_perm('users.grant_vouchers')

    has_module_permission = _may_grant_vouchers
    has_add_permission = _may_grant_vouchers
    has_change_permission = _may_grant_vouchers
    has_view_permission = _may_grant_vouchers

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return tuple(field.name for field in UserVoucher._meta.fields)
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        if not change:
            obj.granted_by = request.user
        super().save_model(request, obj, form, change)
