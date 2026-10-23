from django.contrib import admin

from .models import ProximaActualizacion


@admin.register(ProximaActualizacion)
class ProximaActualizacionAdmin(admin.ModelAdmin):
    list_display = ('referencia', 'titulo', 'equipo', 'fecha_inicio', 'fecha_fin', 'estado')
    list_filter = ('estado', 'equipo', 'fecha_inicio', 'fecha_fin')
    search_fields = ('referencia', 'titulo', 'descripcion', 'equipo')
    date_hierarchy = 'fecha_inicio'
    fields = ('referencia', 'titulo', 'descripcion', 'equipo', 'fecha_inicio', 'fecha_fin', 'estado')
