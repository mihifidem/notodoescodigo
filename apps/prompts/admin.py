from django.contrib import admin

from .models import (
    CategoriaPrompt,
    HashtagPrompt,
    Prompt,
    PromptFavorito,
    SubcategoriaPrompt,
    ValoracionPrompt,
)


@admin.register(CategoriaPrompt)
class CategoriaPromptAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'slug', 'activa')
    search_fields = ('nombre', 'descripcion')
    list_filter = ('activa',)
    prepopulated_fields = {'slug': ('nombre',)}


@admin.register(SubcategoriaPrompt)
class SubcategoriaPromptAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'slug', 'activa')
    search_fields = ('nombre', 'descripcion', 'categoria__nombre')
    list_filter = ('activa', 'categoria')
    prepopulated_fields = {'slug': ('nombre',)}


@admin.register(HashtagPrompt)
class HashtagPromptAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'slug')
    search_fields = ('nombre',)
    prepopulated_fields = {'slug': ('nombre',)}


def _tiene_variables(obj):
    return obj.tiene_variables


_tiene_variables.short_description = '¿Tiene variables?'
_tiene_variables.boolean = True


@admin.register(Prompt)
class PromptAdmin(admin.ModelAdmin):
    list_display = (
        'titulo',
        'categoria',
        'subcategoria',
        'visibilidad',
        'publicado',
        'destacado',
        _tiene_variables,
        'publicado_en',
    )
    list_filter = ('visibilidad', 'publicado', 'destacado', 'categoria', 'subcategoria')
    search_fields = ('titulo', 'descripcion', 'contenido')
    prepopulated_fields = {'slug': ('titulo',)}
    filter_horizontal = ('hashtags',)
    date_hierarchy = 'publicado_en'
    readonly_fields = ('creado_en', 'actualizado_en')
    fieldsets = (
        (None, {
            'fields': ('titulo', 'slug', 'descripcion', 'contenido', 'variables_json'),
        }),
        ('Clasificacion', {
            'fields': ('categoria', 'subcategoria', 'hashtags'),
        }),
        ('Publicacion', {
            'fields': ('visibilidad', 'publicado', 'destacado', 'publicado_en'),
        }),
        ('Imagenes', {
            'fields': ('imagen_prompt', 'imagen_muestra'),
            'description': 'Imagen del prompt (portada o recurso principal) e imagen de ejemplo real para referencia visual.',
        }),
        ('Auditoria', {
            'fields': ('creado_en', 'actualizado_en'),
            'classes': ('collapse',),
        }),
    )


@admin.register(ValoracionPrompt)
class ValoracionPromptAdmin(admin.ModelAdmin):
    list_display = ('prompt', 'usuario', 'valor', 'creada_en')
    list_filter = ('valor',)
    search_fields = ('prompt__titulo', 'usuario__username')
    date_hierarchy = 'creada_en'


@admin.register(PromptFavorito)
class PromptFavoritoAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'prompt', 'agregado_en')
    search_fields = ('usuario__username', 'prompt__titulo')
    date_hierarchy = 'agregado_en'
