from django.contrib import admin
from .models import (
    Certificado,
    CredentialAuditEvent,
    CredentialDefinition,
    CredentialIssuer,
    CredentialReviewRequest,
    CredentialVerification,
)


@admin.register(Certificado)
class CertificadoAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'curso', 'codigo_unico', 'fecha_emision')
    search_fields = ('usuario__username', 'curso__nombre', 'codigo_unico')
    readonly_fields = ('codigo_unico', 'fecha_emision', 'pdf')
    date_hierarchy = 'fecha_emision'


class CredentialCatalogAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.has_perm('certifications.manage_credential_catalog')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('certifications.manage_credential_catalog')

    def has_add_permission(self, request):
        return request.user.has_perm('certifications.manage_credential_catalog')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('certifications.manage_credential_catalog')

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.full_clean()
        super().save_model(request, obj, form, change)


@admin.register(CredentialIssuer)
class CredentialIssuerAdmin(CredentialCatalogAdmin):
    list_display = ('name', 'website', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(CredentialDefinition)
class CredentialDefinitionAdmin(CredentialCatalogAdmin):
    list_display = ('name', 'issuer', 'credential_type', 'area', 'is_validated', 'is_active')
    list_filter = ('credential_type', 'is_validated', 'is_active')
    search_fields = ('name', 'issuer__name', 'area')


class CredentialReviewAuditAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.has_perm('certifications.review_credential')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('certifications.review_credential')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CredentialReviewRequest)
class CredentialReviewRequestAdmin(CredentialReviewAuditAdmin):
    list_display = ('credential', 'version', 'status', 'requested_by', 'assigned_reviewer', 'created_at')
    list_filter = ('status',)
    search_fields = ('credential__name', 'credential__issuer_name', 'requested_by__username')
    readonly_fields = (
        'credential', 'version', 'status', 'requested_by', 'assigned_reviewer', 'created_at', 'updated_at',
    )


@admin.register(CredentialVerification)
class CredentialVerificationAdmin(CredentialReviewAuditAdmin):
    list_display = ('credential', 'version', 'result', 'method', 'reviewer_name', 'reviewed_at')
    list_filter = ('result', 'method')
    search_fields = ('credential__name', 'reviewer_name', 'source')
    readonly_fields = (
        'request', 'credential', 'version', 'reviewer', 'reviewer_name',
        'method', 'source', 'result', 'reason', 'scope', 'reviewed_at',
    )


@admin.register(CredentialAuditEvent)
class CredentialAuditEventAdmin(CredentialReviewAuditAdmin):
    list_display = ('created_at', 'action', 'credential', 'actor')
    list_filter = ('action',)
    search_fields = ('credential__name', 'actor__username', 'reason')
    readonly_fields = ('actor', 'credential', 'review_request', 'action', 'reason', 'metadata', 'created_at')
