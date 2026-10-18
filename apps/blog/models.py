

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg
from django.utils import timezone
from django.utils.text import slugify


class CategoriaBlog(models.Model):
    nombre = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    descripcion = models.TextField(blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Categoria del blog'
        verbose_name_plural = 'Categorias del blog'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nombre)
        super().save(*args, **kwargs)


class SubcategoriaBlog(models.Model):
    categoria = models.ForeignKey(
        CategoriaBlog,
        on_delete=models.CASCADE,
        related_name='subcategorias',
    )
    nombre = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    descripcion = models.TextField(blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Subcategoria del blog'
        verbose_name_plural = 'Subcategorias del blog'
        ordering = ['categoria__nombre', 'nombre']
        constraints = [
            models.UniqueConstraint(
                fields=['categoria', 'slug'],
                name='uniq_subcategoria_blog_categoria_slug',
            )
        ]

    def __str__(self):
        return f'{self.categoria.nombre} / {self.nombre}'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nombre)
        super().save(*args, **kwargs)


class HashtagBlog(models.Model):
    nombre = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=80, unique=True)

    class Meta:
        verbose_name = 'Hashtag'
        verbose_name_plural = 'Hashtags'
        ordering = ['nombre']

    def __str__(self):
        return f'#{self.nombre}'

    def save(self, *args, **kwargs):
        if self.nombre.startswith('#'):
            self.nombre = self.nombre[1:]
        if not self.slug:
            self.slug = slugify(self.nombre)
        super().save(*args, **kwargs)


class PostBlog(models.Model):
    VISIBILIDAD_PUBLICA = 'publico'
    VISIBILIDAD_SEMIPUBLICA = 'semipublico'
    VISIBILIDAD_PRIVADA = 'privado'

    VISIBILIDAD_CHOICES = [
        (VISIBILIDAD_PUBLICA, 'Publico total'),
        (VISIBILIDAD_SEMIPUBLICA, 'Semipublico'),
        (VISIBILIDAD_PRIVADA, 'Privado membresia premium'),
    ]

    titulo = models.CharField(max_length=220)
    slug = models.SlugField(max_length=240, unique=True)
    resumen = models.TextField(blank=True)
    contenido_publico = models.TextField(help_text='Texto visible para todos los roles.')
    contenido_privado = models.TextField(
        blank=True,
        help_text='Texto solo visible para membresia premium.',
    )
    codigo_style = models.TextField(
        blank=True,
        help_text='CSS personalizado para el contenido HTML del post.',
    )
    categoria = models.ForeignKey(
        CategoriaBlog,
        on_delete=models.PROTECT,
        related_name='posts',
    )
    subcategoria = models.ForeignKey(
        SubcategoriaBlog,
        on_delete=models.PROTECT,
        related_name='posts',
        null=True,
        blank=True,
    )
    hashtags = models.ManyToManyField(HashtagBlog, blank=True, related_name='posts')
    imagen = models.ImageField(upload_to='blog/', null=True, blank=True)
    visibilidad = models.CharField(
        max_length=20,
        choices=VISIBILIDAD_CHOICES,
        default=VISIBILIDAD_PUBLICA,
    )
    publicado = models.BooleanField(default=True)
    destacado = models.BooleanField(default=False)
    publicado_en = models.DateTimeField(default=timezone.now)
    puntos_lectura = models.PositiveIntegerField(default=10)
    segundos_lectura_requeridos = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Si esta vacio, se calcula automaticamente por numero de palabras.',
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Post del blog'
        verbose_name_plural = 'Posts del blog'
        ordering = ['-destacado', '-publicado_en']

    def __str__(self):
        return self.titulo

    @property
    def total_palabras(self) -> int:
        texto = f'{self.contenido_publico} {self.contenido_privado}'.strip()
        if not texto:
            return 0
        return len(texto.split())

    @property
    def segundos_objetivo(self) -> int:
        if self.segundos_lectura_requeridos:
            return self.segundos_lectura_requeridos
        # Estimacion a 180 palabras por minuto.
        estimado = int((self.total_palabras / 180) * 60)
        return max(estimado, 15)

    @property
    def rating_promedio(self):
        data = self.valoraciones.aggregate(promedio=Avg('valor'))
        return data['promedio'] or 0


class ValoracionPost(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='valoraciones_posts',
    )
    post = models.ForeignKey(
        PostBlog,
        on_delete=models.CASCADE,
        related_name='valoraciones',
    )
    valor = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Valoracion de post'
        verbose_name_plural = 'Valoraciones de posts'
        ordering = ['-creada_en']
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'post'],
                name='uniq_valoracion_post_usuario_post',
            )
        ]

    def __str__(self):
        return f'{self.usuario.username} - {self.post.titulo} ({self.valor})'


class LecturaPostUsuario(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lecturas_posts',
    )
    post = models.ForeignKey(
        PostBlog,
        on_delete=models.CASCADE,
        related_name='lecturas',
    )
    iniciada_en = models.DateTimeField(default=timezone.now)
    completada_en = models.DateTimeField(null=True, blank=True)
    puntos_otorgados = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Lectura de post'
        verbose_name_plural = 'Lecturas de posts'
        ordering = ['-iniciada_en']
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'post'],
                name='uniq_lectura_post_usuario_post',
            )
        ]

    def __str__(self):
        return f'{self.usuario.username} - {self.post.titulo}'
