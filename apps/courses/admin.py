from django.contrib import admin
from .models import (
    TipoCurso, TipoRecursoTema, CourseSector, CourseCategory, CourseTag, Curso, Tema, TemaRecurso,
    TemaRecursoVisualizacion, InscripcionCurso, Progreso, Quiz, Pregunta, Respuesta,
    ResultadoQuiz, CourseSettings,
)


@admin.register(TipoCurso)
class TipoCursoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'activo')
    list_filter = ('activo',)
    search_fields = ('nombre', 'descripcion')
    list_editable = ('activo',)


class TemaInline(admin.TabularInline):
    model = Tema
    extra = 1
    fields = ('orden', 'titulo', 'puntos_otorgados')
    ordering = ('orden',)


class RespuestaInline(admin.TabularInline):
    model = Respuesta
    extra = 4
    fields = ('texto', 'es_correcta')


class TemaRecursoInline(admin.TabularInline):
    model = TemaRecurso
    extra = 1
    fields = ('orden', 'titulo', 'tipo_recurso', 'archivo', 'activo')
    ordering = ('orden', 'titulo')


class PreguntaInline(admin.TabularInline):
    model = Pregunta
    extra = 3
    fields = ('orden', 'texto')


@admin.register(CourseCategory)
class CourseCategoryAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'sector', 'slug', 'activa', 'orden')
    list_filter = ('sector', 'activa')
    search_fields = ('nombre', 'slug', 'sector__nombre')
    list_editable = ('activa', 'orden')
    list_select_related = ('sector',)


@admin.register(CourseSector)
class CourseSectorAdmin(admin.ModelAdmin):
    list_display = ('nombre',)
    search_fields = ('nombre',)


@admin.register(CourseTag)
class CourseTagAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'slug', 'activa')
    list_filter = ('activa',)
    search_fields = ('nombre', 'slug')
    list_editable = ('activa',)


@admin.register(CourseSettings)
class CourseSettingsAdmin(admin.ModelAdmin):
    list_display = ('course', 'visibilidad', 'status', 'capacity', 'allow_self_enrollment')
    list_filter = ('visibilidad', 'status', 'allow_self_enrollment')
    search_fields = ('course__nombre', 'seo_title')


@admin.register(Curso)
class CursoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'status', 'activo', 'destacado', 'fecha_inicio', 'fecha_fin', 'total_temas')
    list_filter = ('categoria', 'status', 'activo', 'destacado', 'visibilidad')
    search_fields = ('nombre', 'descripcion', 'codigo', 'categoria__nombre')
    list_editable = ('activo', 'destacado', 'status')
    inlines = [TemaInline]
    date_hierarchy = 'creado_en'

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == 'descripcion':
            from django_prose_editor.widgets import ProseEditorWidget
            from apps.lms.forms import PROSE_EDITOR_CONFIG

            kwargs['widget'] = ProseEditorWidget(preset='configurable', config=PROSE_EDITOR_CONFIG)
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(Tema)
class TemaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'curso', 'orden', 'puntos_otorgados')
    list_filter = ('curso',)
    search_fields = ('titulo', 'curso__nombre')
    ordering = ('curso', 'orden')
    inlines = [TemaRecursoInline]


@admin.register(TipoRecursoTema)
class TipoRecursoTemaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'codigo', 'activo')
    list_filter = ('activo',)
    search_fields = ('nombre', 'codigo')
    list_editable = ('activo',)


@admin.register(TemaRecurso)
class TemaRecursoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'tema', 'tipo_recurso', 'orden', 'activo')
    list_filter = ('tipo_recurso', 'activo', 'tema__curso')
    search_fields = ('titulo', 'tema__titulo', 'tema__curso__nombre')
    ordering = ('tema__curso', 'tema', 'orden')


@admin.register(TemaRecursoVisualizacion)
class TemaRecursoVisualizacionAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'recurso', 'puntos_otorgados', 'vista_en')
    list_filter = ('recurso__tipo_recurso', 'recurso__tema__curso')
    search_fields = ('usuario__username', 'recurso__titulo', 'recurso__tema__titulo')
    date_hierarchy = 'vista_en'


@admin.register(InscripcionCurso)
class InscripcionAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'curso', 'fecha_inscripcion', 'completado')
    list_filter = ('completado', 'curso')
    search_fields = ('usuario__username', 'curso__nombre')
    date_hierarchy = 'fecha_inscripcion'


@admin.register(Progreso)
class ProgresoAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'tema', 'completado', 'fecha_completado')
    list_filter = ('completado',)
    search_fields = ('usuario__username', 'tema__titulo')


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'tema', 'csv_filename', 'puntos_bonus', 'porcentaje_aprobacion')
    search_fields = ('titulo', 'tema__titulo', 'csv_filename')
    fields = ('tema', 'titulo', 'csv_filename', 'porcentaje_aprobacion', 'puntos_bonus')


@admin.register(Pregunta)
class PreguntaAdmin(admin.ModelAdmin):
    list_display = ('texto', 'quiz', 'orden')
    inlines = [RespuestaInline]


@admin.register(ResultadoQuiz)
class ResultadoQuizAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'quiz', 'puntuacion', 'total_preguntas', 'aprobado', 'fecha')
    list_filter = ('aprobado',)
    search_fields = ('usuario__username', 'quiz__titulo')
    date_hierarchy = 'fecha'
