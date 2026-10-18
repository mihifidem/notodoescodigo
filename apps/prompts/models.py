import re

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg
from django.utils import timezone
from django.utils.text import slugify


def _auto_label(nombre):
    if not nombre:
        return 'Variable'
    return nombre.replace('_', ' ').replace('-', ' ').strip().title()


def normalize_variables(variables):
    """Normaliza definiciones de variables a un formato interno consistente."""
    if not variables:
        return {}

    raw_items = []
    if isinstance(variables, dict):
        raw_items = list(variables.items())
    elif isinstance(variables, list):
        for item in variables:
            if isinstance(item, dict):
                if 'nombre' in item or 'name' in item:
                    nombre = item.get('nombre') or item.get('name')
                    raw_items.append((nombre, item))
                else:
                    raw_items.extend(item.items())
            elif isinstance(item, tuple) and len(item) == 2:
                raw_items.append((item[0], item[1]))
    else:
        return {}

    normalized = {}
    for nombre, config in raw_items:
        if not isinstance(nombre, str) or not nombre.strip():
            continue
        nombre = nombre.strip()

        if isinstance(config, dict):
            definition = dict(config)
        elif isinstance(config, str):
            definition = {'default': config}
        else:
            definition = {}

        var_type = str(
            definition.get('type')
            or definition.get('tipo')
            or 'text'
        ).lower()
        legacy_aliases = {
            'texto': 'text',
            'string': 'text',
            'textarea': 'textarea',
            'text-area': 'textarea',
            'opcion': 'select',
            'opciones': 'select',
            'choice': 'select',
            'select': 'select',
            'imagen': 'image',
        }
        var_type = legacy_aliases.get(var_type, var_type)
        if var_type not in {'text', 'textarea', 'select', 'image'}:
            var_type = 'text'

        label = (
            definition.get('label')
            or definition.get('descripcion')
            or definition.get('title')
            or _auto_label(nombre)
        )
        default = (
            definition.get('default', None)
            if 'default' in definition else
            definition.get('valor_defecto')
            if 'valor_defecto' in definition else
            definition.get('value', '')
            if 'value' in definition else
            ''
        )
        options = definition.get('options') or definition.get('opciones') or []

        if var_type == 'select':
            if not isinstance(options, list):
                options = []
            normalized_options = []
            for option in options:
                if isinstance(option, dict):
                    option_label = option.get('label') or option.get('value') or option.get('nombre') or ''
                    option_value = option.get('value') or option.get('nombre') or option_label
                elif isinstance(option, (str, int, float)):
                    option_label = str(option)
                    option_value = str(option)
                else:
                    continue
                normalized_options.append({
                    'label': str(option_label),
                    'value': str(option_value),
                })

            if normalized_options:
                valid_values = [item['value'] for item in normalized_options]
                if default not in valid_values:
                    default = normalized_options[0]['value']
            else:
                default = ''

            normalized[nombre] = {
                'type': 'select',
                'label': str(label),
                'default': str(default),
                'options': normalized_options,
            }
        else:
            default_value = '' if default is None else str(default)
            normalized[nombre] = {
                'type': var_type,
                'label': str(label),
                'default': default_value,
            }

    return normalized


def extract_variables(template):
    if not template:
        return []
    matches = re.findall(
        r'\{\{\s*([A-Za-z0-9_\-]+)\s*\}\}|\{\s*([A-Za-z0-9_\-]+)\s*\}|\[\s*([A-Za-z0-9_\-]+)\s*\]',
        template,
    )
    names = []
    for groups in matches:
        nombre = next((group for group in groups if group), '').strip()
        if nombre and nombre not in names:
            names.append(nombre)
    return names


def render_prompt(template, values):
    if not template:
        return ''
    if not isinstance(values, dict):
        values = {}

    rendered = template
    for variable in extract_variables(template):
        valor = values.get(variable)
        if valor is None:
            continue
        valor_texto = str(valor)
        rendered = rendered.replace(f'{{{{{variable}}}}}', valor_texto)
        rendered = rendered.replace(f'{{{variable}}}', valor_texto)
        rendered = rendered.replace(f'[{variable}]', valor_texto)
        rendered = rendered.replace(f'[ {variable} ]', valor_texto)
        rendered = rendered.replace(f'[ {variable}]', valor_texto)
        rendered = rendered.replace(f'[{variable} ]', valor_texto)
    return rendered


class CategoriaPrompt(models.Model):
    nombre = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    descripcion = models.TextField(blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Categoria de prompts'
        verbose_name_plural = 'Categorias de prompts'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nombre)
        super().save(*args, **kwargs)


class SubcategoriaPrompt(models.Model):
    categoria = models.ForeignKey(
        CategoriaPrompt,
        on_delete=models.CASCADE,
        related_name='subcategorias',
    )
    nombre = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    descripcion = models.TextField(blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Subcategoria de prompts'
        verbose_name_plural = 'Subcategorias de prompts'
        ordering = ['categoria__nombre', 'nombre']
        constraints = [
            models.UniqueConstraint(
                fields=['categoria', 'slug'],
                name='uniq_subcategoria_prompt_categoria_slug',
            )
        ]

    def __str__(self):
        return f'{self.categoria.nombre} / {self.nombre}'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nombre)
        super().save(*args, **kwargs)


class HashtagPrompt(models.Model):
    nombre = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=80, unique=True)

    class Meta:
        verbose_name = 'Hashtag de prompt'
        verbose_name_plural = 'Hashtags de prompts'
        ordering = ['nombre']

    def __str__(self):
        return f'#{self.nombre}'

    def save(self, *args, **kwargs):
        if self.nombre.startswith('#'):
            self.nombre = self.nombre[1:]
        if not self.slug:
            self.slug = slugify(self.nombre)
        super().save(*args, **kwargs)


class Prompt(models.Model):
    VISIBILIDAD_PUBLICA = 'publico'
    VISIBILIDAD_SEMIPUBLICA = 'semipublico'
    VISIBILIDAD_PRIVADA = 'privado'

    VISIBILIDAD_CHOICES = [
        (VISIBILIDAD_PUBLICA, 'Publico total'),
        (VISIBILIDAD_SEMIPUBLICA, 'Semipublico'),
        (VISIBILIDAD_PRIVADA, 'Privado - membresia premium'),
    ]

    titulo = models.CharField(max_length=220)
    slug = models.SlugField(max_length=240, unique=True)
    descripcion = models.TextField(
        blank=True,
        help_text='Descripcion breve del prompt y para que sirve.',
    )
    contenido = models.TextField(
        help_text='El texto del prompt. Usa {nombre_variable} para partes rellenables.',
    )
    variables_json = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Definicion de variables: '
            '[{"nombre": "tema", "descripcion": "El tema a tratar", "valor_defecto": "marketing"}]'
        ),
    )
    categoria = models.ForeignKey(
        CategoriaPrompt,
        on_delete=models.PROTECT,
        related_name='prompts',
    )
    subcategoria = models.ForeignKey(
        SubcategoriaPrompt,
        on_delete=models.PROTECT,
        related_name='prompts',
        null=True,
        blank=True,
    )
    hashtags = models.ManyToManyField(HashtagPrompt, blank=True, related_name='prompts')
    visibilidad = models.CharField(
        max_length=20,
        choices=VISIBILIDAD_CHOICES,
        default=VISIBILIDAD_PUBLICA,
    )
    imagen_prompt = models.ImageField(
        upload_to='prompts/imagenes/',
        blank=True,
        null=True,
        help_text='Imagen principal del prompt o portada del recurso.',
    )
    imagen_muestra = models.ImageField(
        upload_to='prompts/muestras/',
        blank=True,
        null=True,
        help_text='Imagen de ejemplo real para guiar la creacion o edicion de imagen.',
    )
    publicado = models.BooleanField(default=True)
    destacado = models.BooleanField(default=False)
    publicado_en = models.DateTimeField(default=timezone.now)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Prompt'
        verbose_name_plural = 'Prompts'
        ordering = ['-destacado', '-publicado_en']

    def __str__(self):
        return self.titulo

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.titulo)
            slug = base
            n = 1
            while Prompt.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{n}'
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def tiene_variables(self):
        return bool(self.nombres_variables)

    @property
    def nombres_variables(self):
        return extract_variables(self.contenido)

    @property
    def variables_definidas(self):
        return normalize_variables(self.variables_json or {})

    @property
    def rating_promedio(self):
        data = self.valoraciones.aggregate(promedio=Avg('valor'))
        return data['promedio'] or 0


class ValoracionPrompt(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='valoraciones_prompts',
    )
    prompt = models.ForeignKey(
        Prompt,
        on_delete=models.CASCADE,
        related_name='valoraciones',
    )
    valor = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Valoracion de prompt'
        verbose_name_plural = 'Valoraciones de prompts'
        ordering = ['-creada_en']
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'prompt'],
                name='uniq_valoracion_prompt_usuario_prompt',
            )
        ]

    def __str__(self):
        return f'{self.usuario.username} - {self.prompt.titulo} ({self.valor})'


class PromptFavorito(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='prompts_favoritos',
    )
    prompt = models.ForeignKey(
        Prompt,
        on_delete=models.CASCADE,
        related_name='favoritos',
    )
    agregado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Prompt favorito'
        verbose_name_plural = 'Prompts favoritos'
        ordering = ['-agregado_en']
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'prompt'],
                name='uniq_favorito_prompt_usuario_prompt',
            )
        ]

    def __str__(self):
        return f'{self.usuario.username} - {self.prompt.titulo}'
