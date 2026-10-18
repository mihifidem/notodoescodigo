from django.contrib import admin
from .models import Insignia, InsigniaUsuario, Logro, LogroUsuario, Mision, MisionUsuario


@admin.register(Insignia)
class InsigniaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'tipo', 'curso_objetivo', 'tema_objetivo', 'requisito_valor', 'visible')
    list_filter = ('tipo', 'visible')
    search_fields = ('nombre', 'curso_objetivo__nombre', 'tema_objetivo__titulo')
    list_editable = ('visible',)


@admin.register(InsigniaUsuario)
class InsigniaUsuarioAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'insignia', 'fecha_obtenida')
    list_filter = ('insignia__tipo',)
    search_fields = ('usuario__username', 'insignia__nombre')
    date_hierarchy = 'fecha_obtenida'


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
