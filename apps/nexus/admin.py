from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from apps.users.access import is_platform_admin
from .models import (
    NexusModule, Question, AnswerOption, Assessment,
    AssessmentSession, UserAnswer, ModuleResult, DNAProfile,
    TestPlantilla, SesionTest, RecursoTest, InformeTest,
    DimensionDefinicion, DimensionRango,
)


class AnswerOptionInline(admin.TabularInline):
    model = AnswerOption
    extra = 3
    fields = ['text', 'value', 'order']


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ['text', 'question_type', 'order', 'active']
    show_change_link = True


@admin.register(NexusModule)
class NexusModuleAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'layer', 'weight_percent', 'question_count', 'active', 'order']
    list_filter = ['layer', 'active']
    search_fields = ['name', 'code']
    ordering = ['order', 'name']
    inlines = [QuestionInline]
    list_editable = ['active', 'order']


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'module', 'question_type', 'order', 'option_count', 'active']
    list_filter = ['module__layer', 'question_type', 'active']
    search_fields = ['text', 'module__name']
    inlines = [AnswerOptionInline]
    list_editable = ['order', 'active']


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ['title', 'status', 'module_count', 'session_count', 'created_by', 'created_at']
    list_filter = ['status']
    search_fields = ['title']
    filter_horizontal = ['modules']
    readonly_fields = ['created_at', 'updated_at']


class UserAnswerInline(admin.TabularInline):
    model = UserAnswer
    extra = 0
    readonly_fields = ['question', 'answer_option', 'score', 'answered_at']


class ModuleResultInline(admin.TabularInline):
    model = ModuleResult
    extra = 0
    readonly_fields = ['module', 'score', 'primary_archetype', 'secondary_archetype', 'reliability']


@admin.register(AssessmentSession)
class AssessmentSessionAdmin(admin.ModelAdmin):
    list_display = ['candidate', 'assessment', 'status', 'reliability_score', 'completed_at', 'created_at']
    list_filter = ['status', 'assessment']
    search_fields = ['candidate__username', 'candidate__email']
    inlines = [ModuleResultInline]
    readonly_fields = ['created_at', 'started_at', 'completed_at']


@admin.register(DNAProfile)
class DNAProfileAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'nexus_score', 'level', 'primary_archetype',
        'leadership_score', 'innovation_score', 'technology_score', 'reliability', 'updated_at',
    ]
    list_filter = ['primary_archetype']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['updated_at']


class RecursoTestInline(admin.TabularInline):
    model = RecursoTest
    extra = 1
    fields = ['tipo', 'titulo', 'descripcion', 'archivo', 'duracion_o_paginas', 'url_externa', 'orden', 'activo']


@admin.register(TestPlantilla)
class TestPlantillaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'nivel_acceso', 'activo', 'es_demo', 'total_sesiones', 'sesiones_completadas', 'creado_por', 'creado_en']
    list_filter = ['activo', 'es_demo', 'nivel_acceso']
    search_fields = ['nombre']
    list_editable = ['nivel_acceso', 'activo', 'es_demo']
    readonly_fields = ['creado_en', 'actualizado_en']
    inlines = [RecursoTestInline]


@admin.register(RecursoTest)
class RecursoTestAdmin(admin.ModelAdmin):
    list_display = ['titulo', 'test', 'tipo', 'activo', 'orden', 'creado_en']
    list_filter = ['tipo', 'activo', 'test']
    search_fields = ['titulo', 'test__nombre']
    list_editable = ['activo', 'orden']


class InformeTestInline(admin.TabularInline):
    model = InformeTest
    extra = 0
    fields = ['tipo', 'creado_en']
    readonly_fields = ['tipo', 'creado_en']


@admin.register(SesionTest)
class SesionTestAdmin(admin.ModelAdmin):
    list_display = ['alumno', 'test', 'version_test', 'estado', 'nexus_score', 'completado_en', 'creado_en']
    list_filter = ['estado', 'version_test', 'test']
    search_fields = ['alumno__username', 'alumno__email', 'test__nombre']
    readonly_fields = ['creado_en', 'completado_en', 'resultados_json']
    inlines = [InformeTestInline]


@admin.register(InformeTest)
class InformeTestAdmin(admin.ModelAdmin):
    list_display = ['sesion', 'tipo', 'creado_en']
    list_filter = ['tipo']
    search_fields = ['sesion__alumno__username', 'sesion__test__nombre']
    readonly_fields = ['creado_en']


class DimensionRangoInline(admin.TabularInline):
    model = DimensionRango
    extra = 5
    fields = ['rango_min', 'rango_max', 'titulo', 'color_nivel', 'explicacion', 'recomendacion']


@admin.register(DimensionDefinicion)
class DimensionDefinicionAdmin(admin.ModelAdmin):
    list_display  = ['label', 'test_tipo', 'orden']
    list_filter   = ['test_tipo']
    search_fields = ['label']
    list_editable = ['orden']
    ordering      = ['test_tipo', 'orden', 'label']
    inlines       = [DimensionRangoInline]
    fieldsets = [
        (None, {'fields': ['test_tipo', 'label', 'icono', 'orden']}),
        ('Descripción general', {'fields': ['descripcion']}),
    ]


# Editorial permissions do not grant access to private assessments or reports.
for private_model in (AssessmentSession, DNAProfile, SesionTest, InformeTest):
    admin.site.unregister(private_model)


from .models import NexusAIOperation, NexusEditorialEvent, NexusFamily, NexusModality, NexusVersion


class EditorialAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return is_platform_admin(request.user) and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user) and super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user) and super().has_change_permission(request, obj)

    def has_add_permission(self, request):
        return is_platform_admin(request.user) and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(NexusFamily)
class NexusFamilyAdmin(EditorialAdmin):
    list_display = ['name', 'code', 'active', 'order']
    list_filter = ['active']

    def get_readonly_fields(self, request, obj=None):
        return ['code'] if obj else []


@admin.register(NexusModality)
class NexusModalityAdmin(NexusFamilyAdmin):
    def has_add_permission(self, request):
        return False


@admin.register(NexusVersion)
class NexusVersionAdmin(EditorialAdmin):
    list_display = ['title', 'family', 'mode', 'version', 'status', 'is_fixture', 'published_at']
    list_filter = ['family', 'mode', 'status', 'is_fixture']
    readonly_fields = ['snapshot', 'published_at', 'created_at', 'updated_at']
    actions = None

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.published_at:
            return [field.name for field in obj._meta.fields if field.name not in ('status', 'change_reason')]
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        previous = NexusVersion.objects.get(pk=obj.pk) if change else None
        changed_publication = obj.status in ('published', 'archived') and (
            previous is None or previous.status != obj.status
        )
        if changed_publication and not request.user.has_perm('nexus.publish_nexusversion'):
            raise PermissionDenied('Se necesita permiso específico de publicación Nexus.')
        obj.save(editorial_actor=request.user)

    def get_form(self, request, obj=None, **kwargs):
        parent_form = super().get_form(request, obj, **kwargs)

        class EditorialForm(parent_form):
            def clean(self):
                cleaned = super().clean()
                if cleaned.get('status') in ('published', 'archived'):
                    if not request.user.has_perm('nexus.publish_nexusversion'):
                        raise ValidationError('Necesitas el permiso específico de publicación.')
                    if not cleaned.get('change_reason'):
                        raise ValidationError('Indica un motivo de publicación o archivo.')
                return cleaned
        return EditorialForm


@admin.register(NexusEditorialEvent)
class NexusEditorialEventAdmin(EditorialAdmin):
    list_display = ['version', 'actor', 'action', 'created_at']
    readonly_fields = ['version', 'actor', 'action', 'reason', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(NexusAIOperation)
class NexusAIOperationAdmin(EditorialAdmin):
    list_display = ['id', 'task', 'status', 'error', 'created_at']
    fields = ['id', 'task', 'status', 'error', 'created_at']
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
