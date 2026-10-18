from django.contrib import admin
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
