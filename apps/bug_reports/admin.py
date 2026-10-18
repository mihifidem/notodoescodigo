from django.contrib import admin

from .models import BugReport


@admin.register(BugReport)
class BugReportAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'alumno', 'estado', 'puntos_premio', 'puntos_asignados', 'creado_en')
    list_filter = ('estado', 'puntos_asignados', 'creado_en')
    search_fields = ('titulo', 'descripcion', 'alumno__username', 'alumno__email')
    readonly_fields = ('creado_en', 'actualizado_en', 'validado_en', 'puntos_asignados')
