from django.contrib import admin
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.urls import reverse
from django.utils.html import format_html
from apps.courses.models import CourseCategory
from .rules import current_rules
from .policies import is_clash_staff
from .forms import SubcategoryAdminForm

from .models import (
    Challenge, ChallengeAgreement, ChallengeOffer, ChallengeSubcategory, Duel,
    DuelAnswer, DuelEvent, DuelItemUse, DuelQuestion, DuelResolution,
    EconomicOperation, KnowledgePublication, Landscape, ParticipantState, DuelQuestionOptions,
    RoundResolution, HealthChange, CombatSession, RuleConfiguration, NotificationReceipt, ProfileResultUpdate,
    LocalBankReceipt,
    SubcategoryQuestionSource, SubcategoryQuestionLevel,
)


class OperationalAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LocalBankReceipt)
class LocalBankReceiptAdmin(OperationalAdmin):
    list_display = ('id', 'operation', 'outcome', 'hold_state', 'created_at')
    list_filter = ('outcome', 'hold_state')


@admin.register(Challenge)
class ChallengeAdmin(OperationalAdmin):
    list_display = ('id', 'creator', 'opponent', 'status', 'stake', 'expires_at', 'manage_challenge')
    list_filter = ('status', 'is_public')

    def has_module_permission(self, request):
        return is_clash_staff(request.user)

    def has_view_permission(self, request, obj=None):
        return is_clash_staff(request.user)

    @admin.display(description='Gestión')
    def manage_challenge(self, obj):
        return format_html('<a href="{}">Ver reto / borrar pendiente</a>',
                           reverse('duckyclash:challenge_detail', args=[obj.pk]))

    def get_queryset(self, request):
        return super().get_queryset(request).filter(clash_offers__isnull=False).distinct()


@admin.register(Duel)
class DuelAdmin(OperationalAdmin):
    list_display = ('id', 'agreement', 'status', 'bank_mode', 'session_id', 'created_at')
    list_filter = ('status', 'bank_mode')


@admin.register(EconomicOperation)
class EconomicOperationAdmin(OperationalAdmin):
    list_display = ('id', 'duel', 'player', 'kind', 'status', 'amount', 'attempts', 'reconciliation_attempts', 'last_error')
    list_filter = ('kind', 'status')


@admin.register(Landscape)
class LandscapeAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'era', 'active')
    list_filter = ('era', 'active')


@admin.register(ChallengeSubcategory)
class SubcategoryAdmin(admin.ModelAdmin):
    form = SubcategoryAdminForm
    list_display = ('name', 'sector_name', 'category', 'active')
    list_filter = ('category__sector', 'category', 'active')
    search_fields = ('name', 'category__nombre', 'category__sector__nombre')
    list_select_related = ('category__sector',)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        relations = {
            'sector': CourseCategory._meta.get_field('sector').remote_field,
            'category': ChallengeSubcategory._meta.get_field('category').remote_field,
        }
        for name, relation in relations.items():
            if name not in form.base_fields:
                continue
            field = form.base_fields[name]
            related_admin = self.admin_site._registry.get(relation.model)
            widget = field.widget
            if isinstance(widget, RelatedFieldWidgetWrapper):
                widget = widget.widget
            field.widget = RelatedFieldWidgetWrapper(
                widget, relation, self.admin_site,
                can_add_related=bool(related_admin and related_admin.has_add_permission(request)),
            )
        return form

    @admin.display(description='Sector', ordering='category__sector__nombre')
    def sector_name(self, obj):
        return obj.sector


@admin.register(RuleConfiguration)
class RuleConfigurationAdmin(admin.ModelAdmin):
    readonly_fields = ('version', 'updated_at')
    fieldsets = (
        ('Combate', {'fields': ('initial_hp', 'question_seconds', 'result_seconds', 'max_rounds',
                               'double_ko', 'max_tiebreaks', 'healing_windows')}),
        ('Ofertas y avisos', {'fields': ('max_stake', 'min_expiry_seconds', 'max_expiry_seconds', 'reminder_seconds')}),
        ('Acceso y ausencias', {'fields': ('ring_access', 'waiting_seconds', 'presence_seconds',
                                         'no_show', 'abandonment', 'abandonment_seconds')}),
        ('Objetos', {'fields': ('item_rules',),
                     'description': 'Por código: amount, max_uses, max_per_round, cooldown; POCIMA_TIEMPO también max_extra_seconds. Solo afecta a duelos nuevos.'}),
        ('Versión', {'fields': ('version', 'updated_at')}),
    )

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and request.user.has_perm('duckyclash.manage_rules')

    def has_add_permission(self, request):
        return (super().has_add_permission(request) and request.user.has_perm('duckyclash.manage_rules')
                and not RuleConfiguration.objects.exists())

    def has_delete_permission(self, request, obj=None):
        return False

    def get_changeform_initial_data(self, request):
        rules = current_rules()
        return {**rules['engine'], **{key: value for key, value in rules.items()
                if key not in ('version', 'engine', 'items')}, 'item_rules': rules['items']}

    def save_model(self, request, obj, form, change):
        request.clash_rules_before = RuleConfiguration.objects.get(pk=obj.pk).snapshot() if change else None
        super().save_model(request, obj, form, change)

    def log_change(self, request, obj, message):
        super().log_change(request, obj, [
            *message, {'clash_rules': {'before': request.clash_rules_before, 'after': obj.snapshot()}},
        ])

    def log_addition(self, request, obj, message):
        super().log_addition(request, obj, [*message, {'clash_rules': {'before': None, 'after': obj.snapshot()}}])


class KnowledgeApprovalAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    has_add_permission = has_module_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission


@admin.register(KnowledgePublication)
class KnowledgePublicationAdmin(KnowledgeApprovalAdmin):
    list_display = ('version', 'category', 'subcategory', 'approved_by', 'active')
    list_filter = ('active', 'category')


class SubcategoryQuestionLevelInline(admin.TabularInline):
    model = SubcategoryQuestionLevel
    extra = 1
    fields = ('reference', 'bank', 'active', 'edit_questions')
    readonly_fields = ('edit_questions',)

    @admin.display(description='Preguntas e importación CSV')
    def edit_questions(self, obj):
        if not obj or not obj.bank_id:
            return 'Guarda el nivel para abrir el editor del banco.'
        return format_html(
            '<a href="{}">Gestionar preguntas del LMS</a>',
            reverse('lms:knowledge_bank', kwargs={'course_id': obj.bank.course_id, 'bank_id': obj.bank_id}),
        )


@admin.register(SubcategoryQuestionSource)
class SubcategoryQuestionSourceAdmin(KnowledgeApprovalAdmin):
    list_display = ('subcategory', 'active', 'approved_by')
    list_filter = ('active', 'subcategory__category')
    search_fields = ('subcategory__name', 'levels__reference', 'levels__bank__title')
    readonly_fields = ('approved_by', 'created_at', 'updated_at')
    fields = ('subcategory', 'active', 'approved_by', 'created_at', 'updated_at')
    list_select_related = ('subcategory', 'approved_by')
    inlines = [SubcategoryQuestionLevelInline]

    def save_model(self, request, obj, form, change):
        obj.approved_by = request.user
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            instance.approved_by = request.user
            instance.save()
        formset.save_m2m()


@admin.register(DuelQuestion)
class DuelQuestionAdmin(OperationalAdmin):
    exclude = ('snapshot',)


@admin.register(DuelAnswer)
class DuelAnswerAdmin(OperationalAdmin):
    exclude = ('is_correct',)


@admin.register(DuelQuestionOptions)
class DuelQuestionOptionsAdmin(OperationalAdmin):
    exclude = ('evidence',)


@admin.register(RoundResolution)
class RoundResolutionAdmin(OperationalAdmin):
    exclude = ('request_payload', 'response_payload')
    list_display = ('question', 'status', 'attempts', 'outcome', 'last_error')


for model in (
    ChallengeOffer, ChallengeAgreement, ParticipantState,
    DuelItemUse, DuelEvent, DuelResolution, HealthChange, CombatSession,
    NotificationReceipt, ProfileResultUpdate,
):
    admin.site.register(model, OperationalAdmin)
