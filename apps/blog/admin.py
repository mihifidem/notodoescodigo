from django.contrib import admin
from django.utils.html import format_html

from .models import (
    CategoriaBlog,
    HashtagBlog,
    LecturaPostUsuario,
    PostBlog,
    SubcategoriaBlog,
    ValoracionPost,
)


@admin.register(CategoriaBlog)
class CategoriaBlogAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'slug', 'activa')
    search_fields = ('nombre', 'descripcion')
    list_filter = ('activa',)
    prepopulated_fields = {'slug': ('nombre',)}


@admin.register(SubcategoriaBlog)
class SubcategoriaBlogAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'slug', 'activa')
    search_fields = ('nombre', 'descripcion', 'categoria__nombre')
    list_filter = ('activa', 'categoria')
    prepopulated_fields = {'slug': ('nombre',)}


@admin.register(HashtagBlog)
class HashtagBlogAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'slug')
    search_fields = ('nombre',)
    prepopulated_fields = {'slug': ('nombre',)}


def _tiene_imagen(obj):
    return bool(obj.imagen)


_tiene_imagen.short_description = 'Imagen'
_tiene_imagen.boolean = True


def accion_generar_posts(modeladmin, request, queryset):
    """Acción de admin: lanza el command generar_posts."""
    from django.core.management import call_command
    from django.contrib import messages as dj_messages
    try:
        call_command('generar_posts', max=3, categoria='Tecnología')
        dj_messages.success(request, '✅ Generación de posts lanzada. Revisa los borradores.')
    except Exception as exc:
        dj_messages.error(request, f'❌ Error al generar posts: {exc}')


accion_generar_posts.short_description = '🤖 Generar posts automáticamente (IA)'


@admin.register(PostBlog)
class PostBlogAdmin(admin.ModelAdmin):
    list_display = (
        'titulo',
        'categoria',
        'visibilidad',
        'publicado',
        'destacado',
        _tiene_imagen,
        'publicado_en',
        'puntos_lectura',
    )
    list_filter = ('publicado', 'destacado', 'visibilidad', 'categoria', 'subcategoria')
    search_fields = ('titulo', 'resumen', 'contenido_publico', 'contenido_privado')
    prepopulated_fields = {'slug': ('titulo',)}
    filter_horizontal = ('hashtags',)
    date_hierarchy = 'publicado_en'
    actions = [accion_generar_posts]
    readonly_fields = ('creado_en', 'actualizado_en', 'vista_previa_imagen')
    fieldsets = (
        (None, {
            'fields': ('titulo', 'slug', 'resumen', 'categoria', 'subcategoria', 'hashtags'),
        }),
        ('Contenido', {
            'fields': ('contenido_publico', 'contenido_privado', 'codigo_style'),
        }),
        ('Publicación', {
            'fields': ('visibilidad', 'publicado', 'destacado', 'publicado_en'),
        }),
        ('Métricas', {
            'fields': ('puntos_lectura', 'segundos_lectura_requeridos'),
        }),
        ('Imagen', {
            'fields': ('imagen', 'vista_previa_imagen'),
        }),
        ('Auditoría', {
            'fields': ('creado_en', 'actualizado_en'),
            'classes': ('collapse',),
        }),
    )

    def vista_previa_imagen(self, obj):
        if obj.imagen:
            return format_html(
                '<img src="{}" style="max-height:200px;border-radius:8px;" />',
                obj.imagen.url,
            )
        return 'Sin imagen'

    vista_previa_imagen.short_description = 'Vista previa'


@admin.register(ValoracionPost)
class ValoracionPostAdmin(admin.ModelAdmin):
    list_display = ('post', 'usuario', 'valor', 'creada_en')
    list_filter = ('valor',)
    search_fields = ('post__titulo', 'usuario__username')
    date_hierarchy = 'creada_en'


@admin.register(LecturaPostUsuario)
class LecturaPostUsuarioAdmin(admin.ModelAdmin):
    list_display = ('post', 'usuario', 'iniciada_en', 'completada_en', 'puntos_otorgados')
    list_filter = ('puntos_otorgados',)
    search_fields = ('post__titulo', 'usuario__username')
    date_hierarchy = 'iniciada_en'
