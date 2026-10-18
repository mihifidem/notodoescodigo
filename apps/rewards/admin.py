from django.contrib import admin
from .models import Recompensa, CanjeRecompensa, Seccion, PuntosSeccionUsuario


@admin.register(Recompensa)
class RecompensaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'puntos_necesarios', 'stock', 'activa')
    list_filter = ('activa',)
    search_fields = ('nombre',)
    list_editable = ('activa', 'stock')


@admin.register(CanjeRecompensa)
class CanjeAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'recompensa', 'puntos_gastados', 'estado', 'fecha_canje')
    list_filter = ('estado',)
    search_fields = ('usuario__username', 'recompensa__nombre')
    list_editable = ('estado',)
    date_hierarchy = 'fecha_canje'


@admin.register(Seccion)
class SeccionAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'activa', 'creada_en')
    list_filter = ('activa',)
    search_fields = ('nombre', 'descripcion')
    list_editable = ('activa',)


@admin.register(PuntosSeccionUsuario)
class PuntosSeccionUsuarioAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'seccion', 'puntos_otorgados', 'otorgado_en')
    list_filter = ('seccion', 'otorgado_en')
    search_fields = ('usuario__username', 'seccion__nombre', 'notas')
    list_editable = ('puntos_otorgados',)
    date_hierarchy = 'otorgado_en'
