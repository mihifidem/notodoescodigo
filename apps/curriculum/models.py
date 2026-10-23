"""Models for the Curriculum Vitae builder app."""
import re

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Max
from django.conf import settings
from django.utils.text import slugify
from django.utils import timezone


class ProfileTemplate(models.Model):
    class Category(models.TextChoices):
        GENERAL = 'general', 'General'
        THEMATIC = 'thematic', 'Temática'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        PUBLISHED = 'published', 'Publicada'
        ARCHIVED = 'archived', 'Archivada'

    code = models.SlugField(max_length=80)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=12, choices=Category.choices)
    version = models.PositiveIntegerField(default=1)
    schema_version = models.PositiveIntegerField(default=1)
    renderer = models.CharField(max_length=80)
    allowed_plans = models.JSONField(default=list)
    recommended_branch_codes = models.JSONField(default=list, blank=True)
    compatible_branch_codes = models.JSONField(default=list, blank=True)
    default_color = models.CharField(max_length=7, default='#1d4ed8')
    customization_options = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'name', 'version']
        constraints = [
            models.UniqueConstraint(fields=['code', 'version'], name='curr_unique_template_version'),
        ]

    def clean(self):
        if not has_accessible_color_contrast(self.default_color):
            raise ValidationError({'default_color': 'Elige un color #RRGGBB con contraste mínimo 4,5:1 sobre fondo claro.'})
        valid_plans = {'free', 'plus', 'pro'}
        if not isinstance(self.allowed_plans, list) or not self.allowed_plans or not set(self.allowed_plans) <= valid_plans:
            raise ValidationError({'allowed_plans': 'Indica uno o más niveles válidos: FREE, PLUS o PRO.'})
        if self.status == self.Status.PUBLISHED and self.renderer not in PROFILE_TEMPLATE_RENDERERS:
            raise ValidationError({'renderer': 'El renderizador no está registrado en el servidor.'})
        if self.status == self.Status.PUBLISHED and not self.published_at:
            self.published_at = timezone.now()
        latest_version = type(self).objects.filter(code=self.code).exclude(pk=self.pk).aggregate(
            latest=Max('version'),
        )['latest']
        if latest_version is not None and self.version <= latest_version:
            raise ValidationError({'version': 'La nueva versión debe ser superior a las versiones anteriores.'})

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).first()
            if original is not None:
                if self.code != original.code:
                    raise ValidationError({'code': 'El código de plantilla no se puede cambiar.'})
                if self.version != original.version:
                    raise ValidationError({'version': 'La versión existente no se puede cambiar; crea una nueva versión.'})
                if original.status == self.Status.ARCHIVED and self.status != self.Status.ARCHIVED:
                    raise ValidationError({'status': 'Una plantilla archivada no se puede volver a publicar; crea una nueva versión.'})
                if original.status in {self.Status.PUBLISHED, self.Status.ARCHIVED}:
                    immutable = (
                        'name', 'description', 'category', 'schema_version', 'renderer',
                        'allowed_plans', 'recommended_branch_codes', 'compatible_branch_codes',
                        'default_color', 'customization_options',
                    )
                    if any(getattr(self, field) != getattr(original, field) for field in immutable):
                        raise ValidationError(
                            'Una plantilla publicada es inmutable; crea una nueva versión para modificarla.'
                        )
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.name} v{self.version}'


PROFILE_TEMPLATE_RENDERERS = {
    'basic', 'minimalist', 'professional', 'classic',
    'dev-studio', 'conversation', 'showcase', 'crm-workspace', 'superoffer', 'matrix',
}


class CurriculumVitae(models.Model):
    """Root container – one per user."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='curriculum',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Curriculum Vitae'
        verbose_name_plural = 'Curriculums Vitae'

    def __str__(self):
        return f'CV de {self.user.get_full_name() or self.user.username}'


# ─── Section 1: Personal info ─────────────────────────────────────────────────

DISPONIBILIDAD_CHOICES = [
    ('inmediata', 'Inmediata'),
    ('parcial', 'Parcial'),
    ('por_acordar', 'Por acordar'),
    ('no_disponible', 'No disponible'),
]


class PersonalInfo(models.Model):
    cv = models.OneToOneField(
        CurriculumVitae, on_delete=models.CASCADE, related_name='personal_info',
    )
    nombre = models.CharField('Nombre', max_length=100)
    apellidos = models.CharField('Apellidos', max_length=150)
    telefono1 = models.CharField('Teléfono 1', max_length=30)
    telefono2 = models.CharField('Teléfono 2', max_length=30, blank=True)
    email_profesional1 = models.EmailField('Email profesional 1')
    email_profesional2 = models.EmailField('Email profesional 2', blank=True)
    ciudad = models.CharField('Ciudad', max_length=100)
    pais = models.CharField('País', max_length=100)
    codigo_postal = models.CharField('Código postal', max_length=20, blank=True)
    fecha_nacimiento = models.DateField('Fecha de nacimiento', null=True, blank=True)
    nacionalidad = models.CharField('Nacionalidad', max_length=100, blank=True)
    carnet_conducir = models.BooleanField('Carnet de conducir', default=False)
    disponibilidad = models.CharField(
        'Disponibilidad', max_length=20, choices=DISPONIBILIDAD_CHOICES, default='inmediata',
    )
    foto = models.ImageField('Foto', upload_to='curriculum/fotos/', null=True, blank=True)

    class Meta:
        verbose_name = 'Datos personales'

    def __str__(self):
        return f'{self.nombre} {self.apellidos}'

    @property
    def nombre_completo(self):
        return f'{self.nombre} {self.apellidos}'


# ─── Section 2: Professional profile ─────────────────────────────────────────

class ProfessionalProfile(models.Model):
    cv = models.OneToOneField(
        CurriculumVitae, on_delete=models.CASCADE, related_name='professional_profile',
    )
    profesion = models.CharField('Profesión / Especialidad', max_length=200)
    objetivo = models.CharField('Objetivo profesional', max_length=300, blank=True)
    resumen = models.TextField('Resumen profesional')

    class Meta:
        verbose_name = 'Perfil profesional'

    def __str__(self):
        return self.profesion


# ─── Section 3: Work experience ───────────────────────────────────────────────

class WorkExperience(models.Model):
    cv = models.ForeignKey(
        CurriculumVitae, on_delete=models.CASCADE, related_name='work_experiences',
    )
    puesto = models.CharField('Puesto', max_length=200)
    empresa = models.CharField('Empresa', max_length=200)
    ubicacion = models.CharField('Ubicación', max_length=200, blank=True)
    fecha_inicio = models.DateField('Fecha inicio')
    fecha_fin = models.DateField('Fecha fin', null=True, blank=True)
    trabajo_actual = models.BooleanField('Trabajo actual', default=False)
    descripcion = models.TextField('Descripción / Responsabilidades', blank=True)
    logros = models.TextField('Logros', blank=True)
    orden = models.PositiveSmallIntegerField('Orden', default=0)

    class Meta:
        verbose_name = 'Experiencia laboral'
        verbose_name_plural = 'Experiencias laborales'
        ordering = ['orden', '-fecha_inicio']

    def __str__(self):
        return f'{self.puesto} en {self.empresa}'

    @property
    def periodo(self):
        fin = 'Actualidad' if self.trabajo_actual else (self.fecha_fin.strftime('%m/%Y') if self.fecha_fin else '')
        return f'{self.fecha_inicio.strftime("%m/%Y")} – {fin}'


# ─── Section 4: Academic education ───────────────────────────────────────────

class Education(models.Model):
    cv = models.ForeignKey(
        CurriculumVitae, on_delete=models.CASCADE, related_name='educations',
    )
    titulo = models.CharField('Título', max_length=200)
    centro = models.CharField('Centro educativo', max_length=200)
    ubicacion = models.CharField('Ubicación', max_length=200, blank=True)
    fecha_inicio = models.DateField('Fecha inicio')
    fecha_fin = models.DateField('Fecha fin', null=True, blank=True)
    en_curso = models.BooleanField('En curso', default=False)
    orden = models.PositiveSmallIntegerField('Orden', default=0)

    class Meta:
        verbose_name = 'Formación académica'
        verbose_name_plural = 'Formaciones académicas'
        ordering = ['orden', '-fecha_inicio']

    def __str__(self):
        return f'{self.titulo} – {self.centro}'

    @property
    def periodo(self):
        fin = 'En curso' if self.en_curso else (self.fecha_fin.strftime('%m/%Y') if self.fecha_fin else '')
        return f'{self.fecha_inicio.strftime("%m/%Y")} – {fin}'


# ─── Section 5: Complementary training ───────────────────────────────────────

TIPO_FORMACION_CHOICES = [
    ('curso', 'Curso'),
    ('certificacion', 'Certificación'),
    ('bootcamp', 'Bootcamp'),
    ('taller', 'Taller'),
    ('otro', 'Otro'),
]


class ComplementaryTraining(models.Model):
    cv = models.ForeignKey(
        CurriculumVitae, on_delete=models.CASCADE, related_name='trainings',
    )
    tipo = models.CharField('Tipo', max_length=20, choices=TIPO_FORMACION_CHOICES, default='curso')
    nombre = models.CharField('Nombre', max_length=200)
    entidad = models.CharField('Entidad / Institución', max_length=200, blank=True)
    fecha = models.DateField('Fecha', null=True, blank=True)
    descripcion = models.CharField('Descripción breve', max_length=300, blank=True)
    orden = models.PositiveSmallIntegerField('Orden', default=0)

    class Meta:
        verbose_name = 'Formación complementaria'
        verbose_name_plural = 'Formaciones complementarias'
        ordering = ['orden', '-fecha']

    def __str__(self):
        return f'{self.get_tipo_display()}: {self.nombre}'


# ─── Section 6: Skills ────────────────────────────────────────────────────────

TIPO_SKILL_CHOICES = [
    ('hard', 'Hard Skill (técnica)'),
    ('soft', 'Soft Skill (blanda)'),
]

CATEGORIA_SKILL_CHOICES = [
    # --- HARD SKILLS ---
    ('h_lenguaje', 'HardSkill_Lenguaje de programación'),
    ('h_framework', 'HardSkill_Framework / Librería'),
    ('h_herramienta', 'HardSkill_Herramienta'),
    ('h_infraestructura', 'HardSkill_Infraestructura y Cloud'),
    ('h_base_datos', 'HardSkill_Bases de Datos'),
    ('h_metodologia', 'HardSkill_Metodologías Técnicas (Agile/QA)'), # Nueva e importante
    ('h_otro', 'HardSkill_Otro'),

    # --- SOFT SKILLS ---
    ('s_liderazgo', 'SoftSkill_Liderazgo y Gestión'),
    ('s_comunicacion', 'SoftSkill_Comunicación e Influencia'),
    ('s_eficacia', 'SoftSkill_Eficacia Personal'),
    ('s_adaptabilidad', 'SoftSkill_Adaptabilidad y Cultura'),
    ('s_negocio', 'SoftSkill_Visión de Negocio y Estrategia'), # Clave para seniors
    ('s_otro', 'SoftSkill_Otro'),

    ('', 'Sin categoría'),
]


class Skill(models.Model):
    cv = models.ForeignKey(
        CurriculumVitae, on_delete=models.CASCADE, related_name='skills',
    )
    tipo = models.CharField('Tipo', max_length=10, choices=TIPO_SKILL_CHOICES, default='hard')
    categoria = models.CharField('Categoría', max_length=20, choices=CATEGORIA_SKILL_CHOICES, blank=True)
    nombre = models.CharField('Habilidad', max_length=100)
    nivel = models.PositiveSmallIntegerField(
        'Nivel (1–5)', null=True, blank=True,
        help_text='1=Básico, 5=Experto',
    )
    orden = models.PositiveSmallIntegerField('Orden', default=0)

    class Meta:
        verbose_name = 'Habilidad'
        verbose_name_plural = 'Habilidades'
        ordering = ['tipo', 'orden', 'nombre']

    def __str__(self):
        return self.nombre


# ─── Section 7: Languages ─────────────────────────────────────────────────────

NIVEL_IDIOMA_CHOICES = [
    ('nativo', 'Nativo'),
    ('C2', 'C2 – Maestría'),
    ('C1', 'C1 – Dominio operativo eficaz'),
    ('B2', 'B2 – Avanzado'),
    ('B1', 'B1 – Intermedio'),
    ('A2', 'A2 – Básico'),
    ('A1', 'A1 – Principiante'),
]


class Language(models.Model):
    cv = models.ForeignKey(
        CurriculumVitae, on_delete=models.CASCADE, related_name='languages',
    )
    idioma = models.CharField('Idioma', max_length=100)
    nivel = models.CharField('Nivel', max_length=10, choices=NIVEL_IDIOMA_CHOICES)
    certificacion = models.CharField('Certificación', max_length=200, blank=True)
    orden = models.PositiveSmallIntegerField('Orden', default=0)

    class Meta:
        verbose_name = 'Idioma'
        verbose_name_plural = 'Idiomas'
        ordering = ['orden']

    def __str__(self):
        return f'{self.idioma} – {self.get_nivel_display()}'


# ─── Section 8: Projects ──────────────────────────────────────────────────────

class Project(models.Model):
    cv = models.ForeignKey(
        CurriculumVitae, on_delete=models.CASCADE, related_name='projects',
    )
    nombre = models.CharField('Nombre del proyecto', max_length=200)
    descripcion = models.TextField('Descripción', blank=True)
    tecnologias = models.CharField('Tecnologías usadas', max_length=300, blank=True)
    link = models.URLField('Enlace (GitHub / web)', blank=True)
    orden = models.PositiveSmallIntegerField('Orden', default=0)

    class Meta:
        verbose_name = 'Proyecto'
        verbose_name_plural = 'Proyectos'
        ordering = ['orden']

    def __str__(self):
        return self.nombre


# ─── Section 9: Achievements ──────────────────────────────────────────────────

TIPO_LOGRO_CHOICES = [
    ('premio', 'Premio'),
    ('merito', 'Mérito académico'),
    ('evento', 'Participación en evento'),
    ('otro', 'Otro'),
]


class Achievement(models.Model):
    cv = models.ForeignKey(
        CurriculumVitae, on_delete=models.CASCADE, related_name='achievements',
    )
    titulo = models.CharField('Título / Descripción', max_length=200)
    tipo = models.CharField('Tipo', max_length=20, choices=TIPO_LOGRO_CHOICES, default='otro')
    descripcion = models.TextField('Detalle', blank=True)
    fecha = models.DateField('Fecha', null=True, blank=True)
    orden = models.PositiveSmallIntegerField('Orden', default=0)

    class Meta:
        verbose_name = 'Logro / Reconocimiento'
        verbose_name_plural = 'Logros y reconocimientos'
        ordering = ['orden', '-fecha']

    def __str__(self):
        return self.titulo


# ─── Section 10: Volunteering ────────────────────────────────────────────────

class Volunteering(models.Model):
    cv = models.ForeignKey(
        CurriculumVitae, on_delete=models.CASCADE, related_name='volunteerings',
    )
    organizacion = models.CharField('Organización', max_length=200)
    funcion = models.CharField('Función / Rol', max_length=200)
    impacto = models.TextField('Impacto', blank=True)
    fecha_inicio = models.DateField('Fecha inicio', null=True, blank=True)
    fecha_fin = models.DateField('Fecha fin', null=True, blank=True)
    en_curso = models.BooleanField('En curso', default=False)
    orden = models.PositiveSmallIntegerField('Orden', default=0)

    class Meta:
        verbose_name = 'Voluntariado'
        verbose_name_plural = 'Voluntariados'
        ordering = ['orden']

    def __str__(self):
        return f'{self.funcion} en {self.organizacion}'


# ─── Section 11: Social networks ─────────────────────────────────────────────

class SocialNetwork(models.Model):
    cv = models.OneToOneField(
        CurriculumVitae, on_delete=models.CASCADE, related_name='social_networks',
    )
    linkedin = models.URLField('LinkedIn', blank=True)
    github = models.URLField('GitHub', blank=True)
    portfolio = models.URLField('Portfolio / Web personal', blank=True)
    twitter = models.URLField('Twitter / X', blank=True)
    otras = models.CharField('Otras redes (describe)', max_length=500, blank=True)

    class Meta:
        verbose_name = 'Redes profesionales'

    def __str__(self):
        return f'Redes de {self.cv}'


# ─── Section 12: Interests ────────────────────────────────────────────────────

class Interest(models.Model):
    cv = models.ForeignKey(
        CurriculumVitae, on_delete=models.CASCADE, related_name='interests',
    )
    nombre = models.CharField('Interés', max_length=100)
    orden = models.PositiveSmallIntegerField('Orden', default=0)

    class Meta:
        verbose_name = 'Interés'
        verbose_name_plural = 'Intereses'
        ordering = ['orden']

    def __str__(self):
        return self.nombre


# ─── Section 13: Other info ───────────────────────────────────────────────────

TELETRABAJO_CHOICES = [
    ('si', 'Sí, 100% remoto'),
    ('parcial', 'Teletrabajo parcial'),
    ('no', 'Presencial'),
    ('indiferente', 'Indiferente'),
]


class OtherInfo(models.Model):
    cv = models.OneToOneField(
        CurriculumVitae, on_delete=models.CASCADE, related_name='other_info',
    )
    disponibilidad_viajar = models.BooleanField('Disponibilidad para viajar', default=False)
    teletrabajo = models.CharField(
        'Disponibilidad de teletrabajo', max_length=20,
        choices=TELETRABAJO_CHOICES, default='indiferente',
    )
    expectativa_salarial = models.CharField('Expectativas salariales', max_length=200, blank=True)
    notas = models.TextField('Notas adicionales', blank=True)

    class Meta:
        verbose_name = 'Otros datos'


# ─── Nexus DNA Entries ────────────────────────────────────────────────────────

class NexusDNAEntry(models.Model):
    """Snapshot of a completed Nexus test session stored in the user's CV."""
    cv = models.ForeignKey(
        CurriculumVitae, on_delete=models.CASCADE, related_name='nexus_entries',
    )
    sesion_id = models.PositiveIntegerField(
        help_text='ID of the original SesionTest (no FK to avoid circular import)',
    )
    test_nombre = models.CharField(max_length=200)
    completado_en = models.DateTimeField()
    nexus_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    primary_archetype = models.CharField(max_length=100, blank=True)
    secondary_archetype = models.CharField(max_length=100, blank=True)
    tertiary_archetype = models.CharField(max_length=100, blank=True)
    leadership_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    innovation_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    technology_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    future_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    career_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    executive_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    motivation_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    cognitive_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    emotional_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    dimensiones = models.JSONField(
        default=list, blank=True,
        help_text='Lista de dimensiones propias del test: [{label, score, color}]',
    )
    version_test = models.CharField(
        max_length=20, blank=True, default='',
        help_text='essential / advanced / premium',
    )
    total_preguntas = models.PositiveSmallIntegerField(null=True, blank=True)
    num_likert      = models.PositiveSmallIntegerField(null=True, blank=True)
    num_abcd        = models.PositiveSmallIntegerField(null=True, blank=True)

    visible_en_cv = models.BooleanField('Visible en CV web', default=True)
    guardado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Entrada Nexus DNA'
        verbose_name_plural = 'Entradas Nexus DNA'
        ordering = ['-completado_en']
        unique_together = [['cv', 'sesion_id']]

    def __str__(self):
        return f'{self.test_nombre} — {self.nexus_score} pts ({self.completado_en.date()})'

    @property
    def indices(self):
        if self.dimensiones:
            return [
                (d['label'], float(d['score']), d.get('color', '#64748B'))
                for d in self.dimensiones
            ]
        return [
            ('Liderazgo',           float(self.leadership_score),  '#DC2626'),
            ('Innovación',          float(self.innovation_score),  '#8B5CF6'),
            ('Mentalidad Digital',  float(self.technology_score),  '#06B6D4'),
            ('Visión de Futuro',    float(self.future_score),      '#22C55E'),
            ('Visión de Negocio',   float(self.career_score),      '#F59E0B'),
            ('Gestión Ejecutiva',   float(self.executive_score),   '#EC4899'),
            ('Motivación',          float(self.motivation_score),  '#2563EB'),
            ('Pensamiento Crítico', float(self.cognitive_score),   '#6366F1'),
            ('Intel. Emocional',    float(self.emotional_score),   '#14B8A6'),
        ]


# ─── CV Profile ───────────────────────────────────────────────────────────────

TEMPLATE_CHOICES = [
    ('minimalist', 'Minimalista'),
    ('tech', 'Tech (VSCode style)'),
    ('normal', 'Clásico'),
]


from apps.cv_templates.models import TemplateCV

def has_accessible_color_contrast(color):
    if not re.fullmatch(r'#[0-9A-Fa-f]{6}', color or ''):
        return False

    def channel(value):
        value = int(value, 16) / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(color[index:index + 2]) for index in (1, 3, 5))
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return 1.05 / (luminance + 0.05) >= 4.5


class CVProfile(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        PUBLISHED = 'published', 'Publicado'
        ARCHIVED = 'archived', 'Archivado'

    """A curated subset of the master CV, published at a unique URL."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cv_profiles',
    )
    cv = models.ForeignKey(
        CurriculumVitae, on_delete=models.CASCADE, related_name='profiles',
    )
    rama_principal = models.ForeignKey(
        'users.RamaProfesional',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='cv_profiles',
        verbose_name='Rama profesional de este perfil',
    )
    profile_template = models.ForeignKey(
        ProfileTemplate,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='profiles',
        verbose_name='Plantilla de diseño',
    )
    published_template = models.ForeignKey(
        ProfileTemplate,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='published_profiles',
        verbose_name='Plantilla publicada',
    )
    published_template_version = models.PositiveIntegerField(default=1)
    published_brand_color = models.CharField(max_length=7, default='#1d4ed8')
    template_version = models.PositiveIntegerField(default=1)
    template_config = models.JSONField(default=dict, blank=True)
    brand_color = models.CharField('Color de marca', max_length=7, default='#1d4ed8')
    display_name = models.CharField('Nombre visible', max_length=200, blank=True)
    professional_headline = models.CharField('Titular profesional', max_length=220, blank=True)
    presentation = models.TextField('Presentación profesional', blank=True)
    profile_location = models.CharField('Ubicación pública', max_length=200, blank=True)
    contact_email = models.EmailField('Email de contacto del perfil', blank=True)
    contact_phone = models.CharField('Teléfono de contacto del perfil', max_length=40, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    revision = models.PositiveIntegerField(default=1)
    published_snapshot = models.JSONField(default=dict, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    show_email = models.BooleanField('Mostrar email', default=False)
    show_phone = models.BooleanField('Mostrar teléfono', default=False)
    show_location = models.BooleanField('Mostrar ubicación', default=True)
    show_photo = models.BooleanField('Mostrar fotografía', default=False)
    nombre = models.CharField('Nombre del perfil', max_length=200,
                              help_text='Ej: "Desarrollador Frontend", "Data Analyst"')
    slug = models.SlugField(
        'URL slug', max_length=100,
        help_text='Ej: OscarBurgos_frontend → opentowork.es/OscarBurgos_frontend',
    )
    template = models.CharField('Plantilla', max_length=20, choices=TEMPLATE_CHOICES, default='minimalist')
    templatecv = models.ForeignKey(TemplateCV, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='Template personalizado', help_text='Elige un template personalizado de la tabla de templates')
    is_public = models.BooleanField('Perfil público', default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Section visibility flags
    mostrar_perfil_profesional = models.BooleanField('Mostrar perfil profesional', default=True)
    mostrar_redes = models.BooleanField('Mostrar redes sociales', default=True)
    mostrar_intereses = models.BooleanField('Mostrar intereses', default=True)
    mostrar_otros = models.BooleanField('Mostrar otros datos', default=False)
    mostrar_voluntariado = models.BooleanField('Mostrar voluntariado', default=True)
    mostrar_logros = models.BooleanField('Mostrar logros', default=True)

    # Selected M2M items
    experiencias = models.ManyToManyField(WorkExperience, blank=True, verbose_name='Experiencias',
                                          related_name='profiles')
    educaciones = models.ManyToManyField(Education, blank=True, verbose_name='Formación académica',
                                         related_name='profiles')
    formaciones = models.ManyToManyField(ComplementaryTraining, blank=True,
                                         verbose_name='Formación complementaria', related_name='profiles')
    habilidades = models.ManyToManyField(Skill, blank=True, verbose_name='Habilidades', related_name='profiles')
    idiomas = models.ManyToManyField(Language, blank=True, verbose_name='Idiomas', related_name='profiles')
    proyectos = models.ManyToManyField(Project, blank=True, verbose_name='Proyectos', related_name='profiles')
    logros = models.ManyToManyField(Achievement, blank=True, verbose_name='Logros', related_name='profiles')
    voluntariados = models.ManyToManyField(Volunteering, blank=True, verbose_name='Voluntariados',
                                           related_name='profiles')
    intereses = models.ManyToManyField(Interest, blank=True, verbose_name='Intereses', related_name='profiles')

    class Meta:
        verbose_name = 'Perfil de CV'
        verbose_name_plural = 'Perfiles de CV'
        unique_together = [['user', 'slug']]
        ordering = ['-created_at']

    def _generate_unique_slug(self, base_slug=None):
        if self.user_id is None:
            candidate = slugify(base_slug or self.nombre or 'perfil').strip('-') or 'perfil'
            return candidate

        candidate = slugify(base_slug or self.nombre or 'perfil').strip('-') or 'perfil'
        final_slug = candidate
        suffix = 2
        queryset = CVProfile.objects.filter(user_id=self.user_id, slug__iexact=final_slug)
        if self.pk:
            queryset = queryset.exclude(pk=self.pk)

        while queryset.exists():
            final_slug = f'{candidate}-{suffix}'
            queryset = CVProfile.objects.filter(user_id=self.user_id, slug__iexact=final_slug)
            if self.pk:
                queryset = queryset.exclude(pk=self.pk)
            suffix += 1
        return final_slug

    def save(self, *args, **kwargs):
        if self.user_id is None:
            raise ValueError('CVProfile.user is required before saving.')
        if self.rama_principal_id and not self.rama_principal.is_active:
            previous_branch_id = None
            if self.pk:
                previous_branch_id = type(self).objects.filter(pk=self.pk).values_list(
                    'rama_principal_id',
                    flat=True,
                ).first()
            if previous_branch_id != self.rama_principal_id:
                from django.core.exceptions import ValidationError

                raise ValidationError({
                    'rama_principal': 'La rama seleccionada está desactivada y no admite nuevas asignaciones.',
                })
        if not has_accessible_color_contrast(self.brand_color):
            raise ValidationError({'brand_color': 'Elige un color #RRGGBB con contraste mínimo 4,5:1 sobre fondo claro.'})
        if self.profile_template_id:
            self.template_version = self.profile_template.version
        if self.pk:
            previous_revision = type(self).objects.filter(pk=self.pk).values_list(
                'revision',
                flat=True,
            ).first()
            if previous_revision is not None and self.revision < previous_revision:
                raise ValidationError({'revision': 'La revisión del perfil no puede retroceder.'})

        base_slug = (self.slug or self.nombre or 'perfil').strip() or 'perfil'
        self.slug = self._generate_unique_slug(base_slug)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.nombre} ({self.slug})'

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('curriculum_public_cv', kwargs={'slug': self.slug})


class ProfileVisit(models.Model):
    profile = models.ForeignKey(CVProfile, on_delete=models.CASCADE, related_name='visits')
    visited_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-visited_at']
        indexes = [models.Index(fields=['profile', 'visited_at'], name='curr_cvvisit_profile_date')]


class CVProfileRevision(models.Model):
    profile = models.ForeignKey(CVProfile, on_delete=models.CASCADE, related_name='revisions')
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    template = models.ForeignKey(ProfileTemplate, on_delete=models.PROTECT, related_name='profile_revisions')
    template_version = models.PositiveIntegerField()
    brand_color = models.CharField(max_length=7)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-revision']
        constraints = [
            models.UniqueConstraint(fields=['profile', 'revision'], name='curr_unique_profile_revision'),
        ]


from .letter_models import (  # noqa: E402,F401
    CoverLetter, LetterConfiguration, LetterGeneration, LetterOffer, LetterVersion,
)
from .ats_models import (  # noqa: E402,F401
    ATSComparison, ATSConfiguration, ATSOperation, ATSReport, ATSSource,
)
