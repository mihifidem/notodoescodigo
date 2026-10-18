from django import forms
from django.contrib import admin
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    TipoExamen,
    Evaluacion,
    RubricaEvaluacion,
    CriterioRubrica,
    EntregaEvaluacion,
    CorreccionEvaluacion,
    EventoCorreccion,
    PromptCorreccion,
)
from .tasks import procesar_entrega_evaluacion


class CriterioRubricaInline(admin.TabularInline):
    model = CriterioRubrica
    extra = 1
    fields = ('codigo', 'nombre', 'peso', 'escala_min', 'escala_max', 'obligatorio', 'orden')


@admin.register(TipoExamen)
class TipoExamenAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'codigo', 'activo')
    list_filter = ('activo',)
    search_fields = ('nombre', 'codigo')


class RubricaInline(admin.StackedInline):
    model = RubricaEvaluacion
    extra = 0


class EvaluacionAdminForm(forms.ModelForm):
    class Meta:
        model = Evaluacion
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        alcance = cleaned.get('alcance_tipo')
        if alcance == Evaluacion.ALCANCE_MF:
            cursos = cleaned.get('cursos')
            if not cursos:
                self.add_error('cursos', 'Debes seleccionar al menos un curso para un Módulo Formativo.')
        if alcance == Evaluacion.ALCANCE_UF and not cleaned.get('curso'):
            self.add_error('curso', 'Debes seleccionar un curso para una Unidad Formativa.')
        if alcance == Evaluacion.ALCANCE_UD and not cleaned.get('tema'):
            self.add_error('tema', 'Debes seleccionar un tema para una Unidad Didactica.')
        return cleaned


@admin.register(Evaluacion)
class EvaluacionAdmin(admin.ModelAdmin):
    form = EvaluacionAdminForm
    list_display = ('titulo', 'aula', 'tipo_prueba', 'alcance_tipo', 'estado', 'fecha_prueba')
    list_filter = ('tipo_prueba', 'alcance_tipo', 'estado', 'aula')
    search_fields = ('titulo', 'enunciado', 'criterios_a_valorar')
    inlines = [RubricaInline]
    fieldsets = (
        ('Identificación', {
            'fields': ('titulo', 'aula', 'tipo_prueba', 'estado'),
        }),
        ('Alcance', {
            'fields': (
                'alcance_tipo',
                'tema',
                'curso',
                'cursos',
                'modulo_ref',
            ),
        }),
        ('Contenido', {
            'fields': (
                'enunciado',
                'enunciado_pdf',
                'criterios_evaluacion_pdf',
                'rubrica_pdf',
                'instrucciones',
                'criterios_a_valorar',
                'max_puntuacion',
            ),
        }),
        ('Planificación', {
            'fields': ('fecha_prueba', 'fecha_apertura', 'fecha_cierre'),
            'classes': ('collapse',),
        }),
    )

    class Media:
        js = ('evaluations/js/evaluacion_alcance.js',)


@admin.register(RubricaEvaluacion)
class RubricaEvaluacionAdmin(admin.ModelAdmin):
    list_display = ('evaluacion', 'version', 'nota_maxima', 'umbral_aprobado')
    inlines = [CriterioRubricaInline]


@admin.register(EntregaEvaluacion)
class EntregaEvaluacionAdmin(admin.ModelAdmin):
    list_display = (
        'evaluacion',
        'alumno',
        'estado',
        'solicita_revision_exhaustiva',
        'intento_numero',
        'fecha_entrega',
        'procesada_en',
        'boton_corregir_ia',
    )
    list_filter = ('estado', 'solicita_revision_exhaustiva', 'evaluacion')
    search_fields = ('alumno__username', 'evaluacion__titulo')
    readonly_fields = ('boton_corregir_ia',)
    actions = ('lanzar_correccion_ia', 'marcar_revision_docente')

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                '<int:pk>/corregir-ia/',
                self.admin_site.admin_view(self.corregir_ia_view),
                name='evaluations_entregaevaluacion_corregir_ia',
            ),
        ]
        return custom + urls

    def corregir_ia_view(self, request, pk):
        entrega = get_object_or_404(EntregaEvaluacion, pk=pk)
        # Forzar correccion IA desde admin, incluso si antes estaba marcada para revision docente.
        entrega.estado = EntregaEvaluacion.ESTADO_PENDIENTE
        entrega.solicita_revision_exhaustiva = False
        entrega.motivo_revision_exhaustiva = ''
        entrega.save(update_fields=['estado', 'solicita_revision_exhaustiva', 'motivo_revision_exhaustiva'])
        try:
            procesar_entrega_evaluacion.delay(entrega.pk)
            self.message_user(request, f'Corrección IA encolada para la entrega #{pk}.')
        except Exception:
            # Celery/Redis no disponible: ejecutar de forma síncrona
            procesar_entrega_evaluacion.run(entrega.pk)
            self.message_user(request, f'Corrección IA completada (modo síncrono) para la entrega #{pk}.')
        return redirect(
            reverse('admin:evaluations_entregaevaluacion_change', args=[pk])
        )

    @admin.display(description='Corrección IA')
    def boton_corregir_ia(self, obj):
        if not obj.pk:
            return '-'
        url = reverse('admin:evaluations_entregaevaluacion_corregir_ia', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}" style="white-space:nowrap;">▶ Corregir con IA</a>',
            url,
        )

    @admin.action(description='Lanzar correccion automatica IA')
    def lanzar_correccion_ia(self, request, queryset):
        updated = 0
        for entrega in queryset:
            entrega.estado = EntregaEvaluacion.ESTADO_PENDIENTE
            entrega.solicita_revision_exhaustiva = False
            entrega.motivo_revision_exhaustiva = ''
            entrega.save(update_fields=['estado', 'solicita_revision_exhaustiva', 'motivo_revision_exhaustiva'])
            try:
                procesar_entrega_evaluacion.delay(entrega.pk)
            except Exception:
                procesar_entrega_evaluacion.run(entrega.pk)
            updated += 1
        self.message_user(request, f'Se lanzaron {updated} correcciones automáticas.')

    @admin.action(description='Marcar para revision exhaustiva docente')
    def marcar_revision_docente(self, request, queryset):
        total = queryset.update(
            estado=EntregaEvaluacion.ESTADO_REVISION,
            solicita_revision_exhaustiva=True,
        )
        self.message_user(request, f'Se marcaron {total} entregas para revision docente.')


@admin.register(PromptCorreccion)
class PromptCorreccionAdmin(admin.ModelAdmin):
    list_display = ('scope', 'version', 'activo', 'actualizado_en', 'descripcion_corta')
    list_filter = ('scope', 'activo')
    search_fields = ('version', 'descripcion', 'system_prompt')
    list_editable = ('activo',)
    readonly_fields = ('creado_en', 'actualizado_en')
    fieldsets = (
        (None, {
            'fields': ('scope', 'version', 'activo', 'descripcion'),
        }),
        ('Prompt de sistema', {
            'fields': ('system_prompt',),
            'description': (
                'Este texto se envía como mensaje de sistema a la IA antes de la corrección. '
                'Activa un prompt para que sea el que se use en nuevas correcciones de ese tipo.'
            ),
        }),
        ('Auditoría', {
            'fields': ('creado_en', 'actualizado_en'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='Descripción')
    def descripcion_corta(self, obj):
        return (obj.descripcion or '')[:80] or '—'


@admin.register(CorreccionEvaluacion)
class CorreccionEvaluacionAdmin(admin.ModelAdmin):
    list_display = (
        'entrega',
        'tipo_correccion',
        'puntuacion_total',
        'confianza_modelo',
        'requiere_revision_humana',
        'modelo_ia',
        'revisado_por',
    )
    list_filter = ('tipo_correccion', 'requiere_revision_humana', 'modelo_ia')
    readonly_fields = ('creada_en', 'prompt_sistema_usado')
    fields = (
        'entrega',
        'tipo_correccion',
        'puntuacion_total',
        'puntuacion_por_criterio',
        'feedback_global',
        'feedback_detallado',
        'evidencias',
        'plan_mejora',
        'confianza_modelo',
        'modelo_ia',
        'prompt_version',
        'prompt_sistema_usado',
        'requiere_revision_humana',
        'revisado_por',
        'observaciones_docente',
        'fecha_revision_docente',
        'creada_en',
    )

    def save_model(self, request, obj, form, change):
        if obj.tipo_correccion == CorreccionEvaluacion.TIPO_DOCENTE and not obj.revisado_por_id:
            obj.revisado_por = request.user
            obj.fecha_revision_docente = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(EventoCorreccion)
class EventoCorreccionAdmin(admin.ModelAdmin):
    list_display = ('entrega', 'evento', 'creado_en')
    list_filter = ('evento',)
    search_fields = ('entrega__alumno__username', 'entrega__evaluacion__titulo', 'evento')
