from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.users.access import is_platform_admin
from .ranking_services import (
    activate_season,
    close_season,
    recalculate_closed_season,
)
from .models import (
    Insignia,
    InsigniaAuditEvent,
    InsigniaUsuario,
    InsigniaVersion,
    Logro,
    LogroUsuario,
    Mision,
    MisionUsuario,
    RankingAdjustment,
    RankingAuditEvent,
    RankingDefinition,
    RankingEntry,
    RankingParticipation,
    RankingRuleVersion,
    RankingSeason,
)


class PlatformBadgeAuditAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Insignia)
class InsigniaAdmin(PlatformBadgeAuditAdmin):
    list_display = ('nombre', 'category', 'issuer_name', 'organization', 'status', 'visible')
    list_filter = ('category', 'status', 'issue_method', 'visible')
    search_fields = ('nombre', 'organization__name')


@admin.register(InsigniaVersion)
class InsigniaVersionAdmin(PlatformBadgeAuditAdmin):
    list_display = ('badge', 'version', 'rule_type', 'threshold', 'effective_at')
    list_filter = ('rule_type',)
    search_fields = ('badge__nombre',)


@admin.register(InsigniaUsuario)
class InsigniaUsuarioAdmin(PlatformBadgeAuditAdmin):
    list_display = ('usuario', 'insignia', 'status', 'origin', 'fecha_obtenida', 'is_public')
    list_filter = ('status', 'origin', 'is_public', 'insignia__category')
    search_fields = ('usuario__username', 'insignia__nombre')
    date_hierarchy = 'fecha_obtenida'


@admin.register(InsigniaAuditEvent)
class InsigniaAuditEventAdmin(PlatformBadgeAuditAdmin):
    list_display = ('created_at', 'action', 'badge', 'actor')
    list_filter = ('action',)
    search_fields = ('badge__nombre', 'actor__username', 'reason')
    date_hierarchy = 'created_at'
    readonly_fields = (
        'actor',
        'badge',
        'award',
        'action',
        'reason',
        'metadata',
        'created_at',
    )


@admin.register(Logro)
class LogroAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'puntos_recompensa', 'oculto')
    list_editable = ('oculto',)
    list_filter = ('oculto',)


@admin.register(LogroUsuario)
class LogroUsuarioAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'logro', 'fecha_obtenido')
    search_fields = ('usuario__username', 'logro__nombre')


@admin.register(Mision)
class MisionAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'tipo', 'requisito_tipo', 'requisito_cantidad', 'puntos_recompensa', 'activa')
    list_filter = ('tipo', 'activa', 'requisito_tipo')
    list_editable = ('activa',)


@admin.register(MisionUsuario)
class MisionUsuarioAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'mision', 'progreso', 'completada', 'fecha_asignada')
    list_filter = ('completada', 'mision__tipo')
    search_fields = ('usuario__username', 'mision__titulo')


class PlatformRankingAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RankingDefinition)
class RankingDefinitionAdmin(PlatformRankingAdmin):
    list_display = ('name', 'metric', 'scope', 'status', 'updated_at')
    list_filter = ('metric', 'status')
    search_fields = ('name', 'slug', 'description')
    readonly_fields = ('scope', 'created_by', 'created_at', 'updated_at')

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.full_clean()
        super().save_model(request, obj, form, change)

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj and obj.rule_versions.exists():
            readonly.append('metric')
        return readonly


@admin.register(RankingRuleVersion)
class RankingRuleVersionAdmin(PlatformRankingAdmin):
    list_display = ('ranking', 'version', 'metric', 'tie_policy', 'created_at')
    list_filter = ('metric', 'tie_policy')
    search_fields = ('ranking__name', 'formula_description')
    readonly_fields = ('created_by', 'created_at', 'selection_policy', 'formula_description', 'tie_policy')

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user) and obj is None

    def save_model(self, request, obj, form, change):
        if change:
            raise PermissionDenied('Las versiones de reglas publicadas son inmutables.')
        obj.created_by = request.user
        obj.full_clean()
        super().save_model(request, obj, form, change)


@admin.register(RankingSeason)
class RankingSeasonAdmin(PlatformRankingAdmin):
    list_display = ('name', 'ranking', 'starts_on', 'ends_on', 'status', 'calculated_at')
    list_filter = ('status', 'ranking')
    search_fields = ('name', 'ranking__name')
    readonly_fields = ('status', 'calculated_at', 'closed_at', 'created_by', 'created_at')
    actions = (
        'activate_prepared_seasons',
        'close_active_seasons',
        'recalculate_closed_seasons',
    )

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user) and (obj is None or obj.status == RankingSeason.Status.PREPARED)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.full_clean()
        super().save_model(request, obj, form, change)

    @admin.action(description='Activar temporadas preparadas dentro de sus fechas')
    def activate_prepared_seasons(self, request, queryset):
        for season in queryset:
            try:
                activate_season(request.user, season)
            except (PermissionDenied, ValidationError) as exc:
                detail = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
                self.message_user(request, f'{season}: {detail}', level=messages.ERROR)
            else:
                self.message_user(request, f'Temporada activada: {season}', level=messages.SUCCESS)

    @admin.action(description='Cerrar temporadas activas cuyo periodo haya finalizado')
    def close_active_seasons(self, request, queryset):
        for season in queryset:
            try:
                close_season(request.user, season)
            except (PermissionDenied, ValidationError) as exc:
                detail = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
                self.message_user(request, f'{season}: {detail}', level=messages.ERROR)
            else:
                self.message_user(request, f'Temporada cerrada y guardada: {season}', level=messages.SUCCESS)

    @admin.action(description='Recalcular temporadas cerradas (requiere motivo)')
    def recalculate_closed_seasons(self, request, queryset):
        from django.template.response import TemplateResponse

        if 'apply' not in request.POST:
            return self._render_recalculation_form(request, queryset, '')
        reason = request.POST.get('reason', '').strip()
        if not reason:
            return self._render_recalculation_form(
                request,
                queryset,
                'Indica un motivo verificable para el recálculo.',
            )
        for season in queryset:
            try:
                recalculate_closed_season(request.user, season, reason=reason)
            except (PermissionDenied, ValidationError) as exc:
                detail = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
                self.message_user(request, f'{season}: {detail}', level=messages.ERROR)
            else:
                self.message_user(request, f'Temporada recalculada con auditoría: {season}', level=messages.SUCCESS)

    def _render_recalculation_form(self, request, queryset, reason_error):
        from django.template.response import TemplateResponse

        return TemplateResponse(
            request,
            'admin/gamification/rankingseason/recalculate_confirmation.html',
            {
                **self.admin_site.each_context(request),
                'opts': self.model._meta,
                'seasons': queryset,
                'selected_ids': queryset.values_list('pk', flat=True),
                'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
                'reason_error': reason_error,
            },
        )


@admin.register(RankingParticipation)
class RankingParticipationAdmin(PlatformBadgeAuditAdmin):
    list_display = ('ranking', 'alias', 'is_active', 'updated_at')
    list_filter = ('ranking', 'is_active')
    search_fields = ('alias', 'ranking__name')


@admin.register(RankingEntry)
class RankingEntryAdmin(PlatformBadgeAuditAdmin):
    list_display = ('season', 'position', 'alias', 'score', 'calculated_at')
    list_filter = ('season__ranking', 'season')
    search_fields = ('alias', 'season__name')


@admin.register(RankingAuditEvent)
class RankingAuditEventAdmin(PlatformBadgeAuditAdmin):
    list_display = ('created_at', 'action', 'ranking', 'season', 'actor')
    list_filter = ('action',)
    search_fields = ('ranking__name', 'actor__username', 'reason')
    readonly_fields = ('actor', 'ranking', 'season', 'action', 'reason', 'metadata', 'created_at')


@admin.register(RankingAdjustment)
class RankingAdjustmentAdmin(PlatformRankingAdmin):
    list_display = ('source_model', 'source_record_id', 'status', 'reviewed_by', 'reviewed_at')
    list_filter = ('status', 'source_model')
    search_fields = ('source_record_id', 'reason')
    readonly_fields = ('reviewed_by', 'reviewed_at', 'created_at')

    def save_model(self, request, obj, form, change):
        previous_status = None
        if change:
            previous_status = RankingAdjustment.objects.get(pk=obj.pk).status
        if obj.status != RankingAdjustment.Status.PENDING:
            obj.reviewed_by = request.user
            obj.reviewed_at = timezone.now()
        else:
            obj.reviewed_by = None
            obj.reviewed_at = None
        obj.full_clean()
        super().save_model(request, obj, form, change)
        RankingAuditEvent.objects.create(
            actor=request.user,
            action=RankingAuditEvent.Action.RESULT_REVIEWED,
            reason=obj.reason,
            metadata={
                'source_model': obj.source_model,
                'source_record_id': obj.source_record_id,
                'previous_status': previous_status,
                'status': obj.status,
            },
        )
