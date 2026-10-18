from django.contrib import admin
from .models import RegistroActividad


@admin.register(RegistroActividad)
class RegistroActividadAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'tipo', 'puntos_ganados', 'timestamp')
    list_filter = ('tipo',)
    search_fields = ('usuario__username', 'descripcion')
    date_hierarchy = 'timestamp'
    readonly_fields = ('usuario', 'tipo', 'descripcion', 'puntos_ganados', 'timestamp')
