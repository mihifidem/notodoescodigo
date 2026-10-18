from django.contrib import admin

from .models import AccesoEnlaceUsuario, EnlaceImportante


@admin.register(EnlaceImportante)
class EnlaceImportanteAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'categoria', 'url', 'activo', 'creado_por', 'creado_en')
    list_filter = ('categoria', 'activo', 'creado_en')
    search_fields = ('titulo', 'categoria', 'url', 'comentario')
    readonly_fields = ('creado_en',)

    def save_model(self, request, obj, form, change):
        if not obj.creado_por_id:
            obj.creado_por = request.user
        super().save_model(request, obj, form, change)


@admin.register(AccesoEnlaceUsuario)
class AccesoEnlaceUsuarioAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'enlace', 'accedido_en')
    search_fields = ('usuario__username', 'enlace__titulo')
    readonly_fields = ('accedido_en',)
