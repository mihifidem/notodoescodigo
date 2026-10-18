"""Serializers para la API del blog."""
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from .models import CategoriaBlog, HashtagBlog, PostBlog


class CategoriaBlogSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriaBlog
        fields = "__all__"
        # fields = ('id', 'nombre', 'slug')


class HashtagBlogSerializer(serializers.ModelSerializer):
    class Meta:
        model = HashtagBlog
        fields = ('nombre', 'slug')


class PostBlogListSerializer(serializers.ModelSerializer):
    """Versión ligera para el listado."""
    categoria = CategoriaBlogSerializer(read_only=True)
    hashtags = HashtagBlogSerializer(many=True, read_only=True)
    imagen_url = serializers.SerializerMethodField()
    minutos_lectura = serializers.SerializerMethodField()

    class Meta:
        model = PostBlog
        fields = (
            'id', 'titulo', 'slug', 'resumen', 'categoria', 'hashtags',
            'imagen_url', 'visibilidad', 'publicado', 'destacado',
            'publicado_en', 'puntos_lectura', 'minutos_lectura',
        )

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_imagen_url(self, obj):
        request = self.context.get('request')
        if obj.imagen and request:
            return request.build_absolute_uri(obj.imagen.url)
        return None

    @extend_schema_field(serializers.IntegerField())
    def get_minutos_lectura(self, obj):
        return max(1, round(obj.segundos_objetivo / 60))


class PostBlogDetailSerializer(PostBlogListSerializer):
    """Versión completa para el detalle — incluye contenido privado solo para premium."""
    contenido_publico = serializers.CharField()
    contenido_privado = serializers.SerializerMethodField()
    meta_description = serializers.SerializerMethodField()
    seo_keywords = serializers.SerializerMethodField()

    class Meta(PostBlogListSerializer.Meta):
        fields = PostBlogListSerializer.Meta.fields + (
            'contenido_publico', 'contenido_privado',
            'meta_description', 'seo_keywords',
        )

    def _es_premium(self, usuario):
        """Puede ver contenido_privado: cualquier usuario registrado con rol válido."""
        if not usuario or not usuario.is_authenticated:
            return False
        return usuario.is_staff or getattr(usuario, 'role', '') in {'alumno', 'freemium', 'premium'}

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_contenido_privado(self, obj):
        request = self.context.get('request')
        usuario = getattr(request, 'user', None)
        if obj.contenido_privado and self._es_premium(usuario):
            return obj.contenido_privado
        return None

    @extend_schema_field(serializers.CharField())
    def get_meta_description(self, obj):
        texto = obj.resumen or obj.titulo
        return texto[:157] + '...' if len(texto) > 160 else texto

    @extend_schema_field(serializers.CharField())
    def get_seo_keywords(self, obj):
        return ', '.join(t.nombre for t in obj.hashtags.all())
