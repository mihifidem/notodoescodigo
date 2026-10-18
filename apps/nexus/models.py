from decimal import Decimal
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator

User = get_user_model()

ARCHETYPE_CHOICES = [
    ('Strategic Builder', 'Strategic Builder'),
    ('Adaptive Innovator', 'Adaptive Innovator'),
    ('Collaborative Leader', 'Collaborative Leader'),
    ('Future Navigator', 'Future Navigator'),
    ('Digital Transformer', 'Digital Transformer'),
    ('Visionary Creator', 'Visionary Creator'),
    ('Operational Executor', 'Operational Executor'),
    ('Human-Centered Leader', 'Human-Centered Leader'),
    ('Enterprise Architect', 'Enterprise Architect'),
    ('Nexus Mastermind', 'Nexus Mastermind'),
]


class NexusModule(models.Model):
    LAYER_FOUNDATION = 'FOUNDATION'
    LAYER_TALENT = 'TALENT'
    LAYER_HUMAN = 'HUMAN'

    LAYER_CHOICES = [
        (LAYER_FOUNDATION, 'Foundation'),
        (LAYER_TALENT, 'Talent Intelligence'),
        (LAYER_HUMAN, 'Human Intelligence'),
    ]

    LAYER_COLORS = {
        LAYER_FOUNDATION: '#2563EB',
        LAYER_TALENT: '#7C3AED',
        LAYER_HUMAN: '#DC2626',
    }

    code = models.CharField(max_length=50, unique=True, verbose_name='Code')
    name = models.CharField(max_length=100, verbose_name='Name')
    layer = models.CharField(max_length=20, choices=LAYER_CHOICES, verbose_name='Layer')
    description = models.TextField(blank=True, verbose_name='Description')
    weight = models.DecimalField(
        max_digits=5, decimal_places=4,
        default=Decimal('0.0500'),
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('1'))],
        verbose_name='Weight (0-1)',
    )
    color = models.CharField(max_length=7, default='#1B3C59', verbose_name='Color')
    active = models.BooleanField(default=True, verbose_name='Active')
    order = models.PositiveIntegerField(default=0, verbose_name='Order')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Nexus Module'
        verbose_name_plural = 'Nexus Modules'

    def __str__(self):
        return self.name

    @property
    def question_count(self):
        return self.questions.filter(active=True).count()

    @property
    def weight_percent(self):
        return f"{float(self.weight) * 100:.0f}%"

    @property
    def layer_color(self):
        return self.LAYER_COLORS.get(self.layer, '#1B3C59')


class Question(models.Model):
    TYPE_LIKERT = 'LIKERT'
    TYPE_MULTIPLE = 'MULTIPLE'
    TYPE_SCALE = 'SCALE'

    TYPE_CHOICES = [
        (TYPE_LIKERT, 'Likert Scale (1–5)'),
        (TYPE_MULTIPLE, 'Multiple Choice'),
        (TYPE_SCALE, 'Numeric Scale (0–100)'),
    ]

    module = models.ForeignKey(NexusModule, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField(verbose_name='Question Text')
    question_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_LIKERT, verbose_name='Type')
    order = models.PositiveIntegerField(default=0, verbose_name='Order')
    active = models.BooleanField(default=True, verbose_name='Active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order']
        verbose_name = 'Question'
        verbose_name_plural = 'Questions'

    def __str__(self):
        return f'Q{self.order}: {self.text[:60]}'

    @property
    def option_count(self):
        return self.options.count()


class AnswerOption(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='options')
    text = models.CharField(max_length=300, verbose_name='Option Text')
    value = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
        verbose_name='Score Value (0–100)',
    )
    order = models.PositiveIntegerField(default=0, verbose_name='Order')

    class Meta:
        ordering = ['order']
        verbose_name = 'Answer Option'
        verbose_name_plural = 'Answer Options'

    def __str__(self):
        return f'{self.text} ({self.value})'


class Assessment(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_ACTIVE = 'ACTIVE'
    STATUS_CLOSED = 'CLOSED'

    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_CLOSED, 'Closed'),
    ]

    title = models.CharField(max_length=200, verbose_name='Title')
    description = models.TextField(blank=True, verbose_name='Description')
    modules = models.ManyToManyField(NexusModule, related_name='assessments', blank=True, verbose_name='Modules')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT, verbose_name='Status')
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_nexus_assessments',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Assessment'
        verbose_name_plural = 'Assessments'

    def __str__(self):
        return self.title

    @property
    def module_count(self):
        return self.modules.count()

    @property
    def session_count(self):
        return self.sessions.count()

    @property
    def status_class(self):
        return {'DRAFT': 'secondary', 'ACTIVE': 'success', 'CLOSED': 'danger'}.get(self.status, 'secondary')


class AssessmentSession(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_IN_PROGRESS = 'IN_PROGRESS'
    STATUS_COMPLETED = 'COMPLETED'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_COMPLETED, 'Completed'),
    ]

    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name='sessions')
    candidate = models.ForeignKey(User, on_delete=models.CASCADE, related_name='nexus_sessions')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    reliability_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Assessment Session'
        verbose_name_plural = 'Assessment Sessions'

    def __str__(self):
        return f'{self.candidate} – {self.assessment}'

    @property
    def status_class(self):
        return {
            'PENDING': 'warning',
            'IN_PROGRESS': 'info',
            'COMPLETED': 'success',
        }.get(self.status, 'secondary')


class UserAnswer(models.Model):
    session = models.ForeignKey(AssessmentSession, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    answer_option = models.ForeignKey(AnswerOption, on_delete=models.SET_NULL, null=True, blank=True)
    score = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['session', 'question']
        verbose_name = 'User Answer'
        verbose_name_plural = 'User Answers'


class ModuleResult(models.Model):
    session = models.ForeignKey(AssessmentSession, on_delete=models.CASCADE, related_name='module_results')
    module = models.ForeignKey(NexusModule, on_delete=models.CASCADE)
    score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    primary_archetype = models.CharField(max_length=100, blank=True, choices=ARCHETYPE_CHOICES)
    secondary_archetype = models.CharField(max_length=100, blank=True, choices=ARCHETYPE_CHOICES)
    reliability = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    class Meta:
        unique_together = ['session', 'module']
        verbose_name = 'Module Result'
        verbose_name_plural = 'Module Results'

    def __str__(self):
        return f'{self.module.name}: {self.score}'

    @property
    def score_class(self):
        s = float(self.score)
        if s >= 90:
            return 'success'
        elif s >= 75:
            return 'primary'
        elif s >= 60:
            return 'warning'
        return 'danger'


class DNAProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='dna_profile')
    session = models.OneToOneField(AssessmentSession, on_delete=models.SET_NULL, null=True, blank=True)

    nexus_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    primary_archetype = models.CharField(max_length=100, blank=True, choices=ARCHETYPE_CHOICES)
    secondary_archetype = models.CharField(max_length=100, blank=True, choices=ARCHETYPE_CHOICES)
    tertiary_archetype = models.CharField(max_length=100, blank=True, choices=ARCHETYPE_CHOICES)

    # Master Indices
    leadership_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    innovation_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    technology_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    future_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    career_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    executive_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    motivation_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    cognitive_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    emotional_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    reliability = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    vector = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'DNA Profile'
        verbose_name_plural = 'DNA Profiles'

    def __str__(self):
        return f'{self.user} – Nexus Score: {self.nexus_score}'

    @property
    def level(self):
        s = float(self.nexus_score)
        if s >= 90:
            return 'Exceptional Talent'
        elif s >= 80:
            return 'High Potential Talent'
        elif s >= 70:
            return 'Strong Professional'
        elif s >= 60:
            return 'Developing Professional'
        return 'Growth Required'

    @property
    def level_class(self):
        s = float(self.nexus_score)
        if s >= 90:
            return 'success'
        elif s >= 80:
            return 'primary'
        elif s >= 70:
            return 'info'
        elif s >= 60:
            return 'warning'
        return 'danger'

    @property
    def score_gauge_percent(self):
        return int(float(self.nexus_score))


# =============================================================================
# TESTS BASADOS EN PLANTILLA HTML — el admin sube un archivo .html
# =============================================================================

class TestPlantilla(models.Model):
    """
    Test definido por el admin como un archivo HTML/Django template.
    El archivo se renderiza con render() pasando el user autenticado en el contexto.
    """
    nombre = models.CharField(max_length=200, verbose_name='Nombre del test')
    descripcion = models.TextField(blank=True, verbose_name='Descripción')
    archivo_html = models.FileField(
        upload_to='nexus/tests/',
        verbose_name='Archivo HTML del test',
        help_text='Template Django (.html) con el test. Dispondrá de: user, test, sesion, url_enviar.',
    )
    NIVEL_ESSENTIAL = 'essential'
    NIVEL_ADVANCED  = 'advanced'
    NIVEL_PREMIUM   = 'premium'
    NIVEL_CHOICES   = [
        (NIVEL_ESSENTIAL, 'Essential (todos los usuarios)'),
        (NIVEL_ADVANCED,  'Advanced (requiere login)'),
        (NIVEL_PREMIUM,   'Premium (requiere membresía)'),
    ]

    nivel_acceso = models.CharField(
        max_length=20,
        choices=NIVEL_CHOICES,
        default=NIVEL_ADVANCED,
        verbose_name='Nivel de acceso',
        help_text='Quién puede realizar este test.',
    )
    activo = models.BooleanField(default=False, verbose_name='Activo (visible para alumnos)')
    es_demo = models.BooleanField(
        default=False,
        verbose_name='Demo gratuito (sin login)',
        help_text='Si está activado, cualquier usuario puede realizar este test sin registrarse.',
    )
    creado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tests_plantilla_creados',
        verbose_name='Creado por',
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creado_en']
        verbose_name = 'Test (Plantilla HTML)'
        verbose_name_plural = 'Tests (Plantillas HTML)'

    def __str__(self):
        return self.nombre

    @property
    def total_sesiones(self):
        return self.sesiones.count()

    @property
    def sesiones_completadas(self):
        return self.sesiones.filter(estado=SesionTest.COMPLETADO).count()


class SesionTest(models.Model):
    """Sesión de un alumno realizando un TestPlantilla."""
    PENDIENTE = 'PENDIENTE'
    EN_PROGRESO = 'EN_PROGRESO'
    COMPLETADO = 'COMPLETADO'

    ESTADO_CHOICES = [
        (PENDIENTE, 'Pendiente'),
        (EN_PROGRESO, 'En progreso'),
        (COMPLETADO, 'Completado'),
    ]

    test = models.ForeignKey(TestPlantilla, on_delete=models.CASCADE, related_name='sesiones')
    alumno = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sesiones_nexus')
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=PENDIENTE)

    # Resultados devueltos por el HTML del test (JSON libre)
    resultados_json = models.JSONField(
        default=dict, blank=True,
        verbose_name='Resultados (JSON)',
        help_text='El test HTML envía sus resultados aquí via POST (campo resultados_json).',
    )
    nexus_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    VERSION_ESSENTIAL = 'essential'
    VERSION_ADVANCED  = 'advanced'
    VERSION_PREMIUM   = 'premium'
    VERSION_CHOICES = [
        (VERSION_ESSENTIAL, 'Essential'),
        (VERSION_ADVANCED,  'Advanced'),
        (VERSION_PREMIUM,   'Premium'),
    ]
    version_test = models.CharField(
        max_length=20, choices=VERSION_CHOICES, blank=True, default='',
        verbose_name='Variante del test',
        help_text='Essential / Advanced / Premium seleccionado al realizar el test.',
    )

    guardado_en_cv = models.BooleanField(
        default=False,
        verbose_name='Guardado en perfil CV',
        help_text='True si el alumno eligió volcar los resultados en su curriculum.',
    )

    iniciado_en = models.DateTimeField(null=True, blank=True)
    completado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado_en']
        verbose_name = 'Sesión de Test'
        verbose_name_plural = 'Sesiones de Test'

    def __str__(self):
        return f'{self.alumno} — {self.test}'


class RecursoTest(models.Model):
    """Recursos adicionales (vídeos, PDFs, podcasts…) asociados a un TestPlantilla."""

    TIPO_VIDEO       = 'video'
    TIPO_PDF         = 'pdf'
    TIPO_PRESENTACION = 'presentacion'
    TIPO_AUDIO       = 'audio'
    TIPO_INFOGRAFIA  = 'infografia'
    TIPO_DOCUMENTO   = 'documento'
    TIPO_ENLACE      = 'enlace'

    TIPO_CHOICES = [
        (TIPO_VIDEO,        'Vídeo'),
        (TIPO_PDF,          'PDF'),
        (TIPO_PRESENTACION, 'Presentación'),
        (TIPO_AUDIO,        'Audio / Podcast'),
        (TIPO_INFOGRAFIA,   'Infografía'),
        (TIPO_DOCUMENTO,    'Documento'),
        (TIPO_ENLACE,       'Enlace externo'),
    ]

    # Icono Bootstrap por tipo
    TIPO_ICON = {
        TIPO_VIDEO:        'bi-play-circle-fill',
        TIPO_PDF:          'bi-file-earmark-pdf-fill',
        TIPO_PRESENTACION: 'bi-easel-fill',
        TIPO_AUDIO:        'bi-mic-fill',
        TIPO_INFOGRAFIA:   'bi-image-fill',
        TIPO_DOCUMENTO:    'bi-file-earmark-text-fill',
        TIPO_ENLACE:       'bi-link-45deg',
    }

    TIPO_COLOR = {
        TIPO_VIDEO:        '#EF4444',
        TIPO_PDF:          '#DC2626',
        TIPO_PRESENTACION: '#F59E0B',
        TIPO_AUDIO:        '#8B5CF6',
        TIPO_INFOGRAFIA:   '#06B6D4',
        TIPO_DOCUMENTO:    '#2563EB',
        TIPO_ENLACE:       '#10B981',
    }

    test        = models.ForeignKey(
        TestPlantilla, on_delete=models.CASCADE,
        related_name='recursos', verbose_name='Test',
    )
    tipo        = models.CharField(max_length=20, choices=TIPO_CHOICES, verbose_name='Tipo')
    titulo      = models.CharField(max_length=200, verbose_name='Título')
    descripcion = models.TextField(blank=True, verbose_name='Descripción')
    archivo     = models.FileField(
        upload_to='nexus/recursos/', blank=True, null=True,
        verbose_name='Archivo',
        help_text='PDF, MP3, imagen, etc.',
    )
    duracion_o_paginas = models.CharField(
        max_length=50, blank=True, default='',
        verbose_name='Duración / páginas',
        help_text='Ej.: 4:30 (vídeo) o 12 páginas (PDF).',
    )
    url_externa = models.URLField(
        blank=True, verbose_name='URL externa',
        help_text='YouTube, Spotify, Google Slides, etc.',
    )
    orden       = models.PositiveIntegerField(default=0, verbose_name='Orden')
    activo      = models.BooleanField(default=True, verbose_name='Visible para alumnos')
    creado_en   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['orden', 'creado_en']
        verbose_name = 'Recurso del test'
        verbose_name_plural = 'Recursos del test'

    def __str__(self):
        return f'[{self.get_tipo_display()}] {self.titulo}'

    @property
    def icono(self):
        return self.TIPO_ICON.get(self.tipo, 'bi-paperclip')

    @property
    def color(self):
        return self.TIPO_COLOR.get(self.tipo, '#64748B')

    @property
    def url(self):
        """Devuelve la URL del recurso (archivo subido o URL externa)."""
        if self.archivo:
            return self.archivo.url
        return self.url_externa

    @property
    def estado_class(self):
        return {
            self.PENDIENTE: 'warning',
            self.EN_PROGRESO: 'info',
            self.COMPLETADO: 'success',
        }.get(self.estado, 'secondary')


class InformeTest(models.Model):
    """Informe generado (base o IA) asociado a una sesión de test."""
    TIPO_BASE = 'base'
    TIPO_AI_PERSONAL = 'ai_personal'
    TIPO_AI_COMPLETO = 'ai_completo'

    TIPO_CHOICES = [
        (TIPO_BASE, 'Básico (9,99€)'),
        (TIPO_AI_PERSONAL, 'Advanced (19,99€)'),
        (TIPO_AI_COMPLETO, 'Premium (49,00€)'),
    ]

    PRECIOS = {
        TIPO_BASE: Decimal('9.99'),
        TIPO_AI_PERSONAL: Decimal('19.99'),
        TIPO_AI_COMPLETO: Decimal('49.00'),
    }

    TIPO_ICON = {
        TIPO_BASE: 'bi-file-earmark-bar-graph',
        TIPO_AI_PERSONAL: 'bi-stars',
        TIPO_AI_COMPLETO: 'bi-file-earmark-richtext',
    }

    sesion = models.ForeignKey(SesionTest, on_delete=models.CASCADE, related_name='informes')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    contenido_html = models.TextField(verbose_name='Contenido HTML del informe')
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado_en']
        verbose_name = 'Informe de test'
        verbose_name_plural = 'Informes de test'
        unique_together = ['sesion', 'tipo']

    def __str__(self):
        return f'{self.sesion} — {self.get_tipo_display()}'

    @property
    def icono(self):
        return self.TIPO_ICON.get(self.tipo, 'bi-file-earmark')

    @property
    def precio(self):
        return self.PRECIOS.get(self.tipo, self.PRECIOS[self.TIPO_AI_PERSONAL])

    @property
    def precio_cents(self):
        return int((self.precio * 100).quantize(Decimal('1')))


class InformePago(models.Model):
    """Registro de pago para desbloquear un informe Nexus."""
    METODO_STRIPE = 'stripe'
    METODO_PAYPAL = 'paypal'
    METODO_CHOICES = [
        (METODO_STRIPE, 'Stripe'),
        (METODO_PAYPAL, 'PayPal'),
    ]

    ESTADO_PENDIENTE = 'pendiente'
    ESTADO_PAGADO = 'pagado'
    ESTADO_CANCELADO = 'cancelado'
    ESTADO_FALLIDO = 'fallido'
    ESTADO_CHOICES = [
        (ESTADO_PENDIENTE, 'Pendiente'),
        (ESTADO_PAGADO, 'Pagado'),
        (ESTADO_CANCELADO, 'Cancelado'),
        (ESTADO_FALLIDO, 'Fallido'),
    ]

    informe = models.ForeignKey(InformeTest, on_delete=models.CASCADE, related_name='pagos')
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pagos_informe')
    metodo = models.CharField(max_length=20, choices=METODO_CHOICES, default=METODO_STRIPE)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=ESTADO_PENDIENTE)
    monto = models.DecimalField(max_digits=10, decimal_places=2, default=19.99)
    moneda = models.CharField(max_length=10, default='EUR')
    external_id = models.CharField(max_length=200, blank=True, default='')
    provider_response = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    pagado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-creado_en']
        verbose_name = 'Pago de informe'
        verbose_name_plural = 'Pagos de informe'

    def __str__(self):
        return f'{self.usuario} — {self.informe} — {self.get_estado_display()}'

    @property
    def esta_pagado(self):
        return self.estado == self.ESTADO_PAGADO


# =============================================================================
# DIMENSIONES — Definiciones y rangos de puntuación gestionados por el admin
# =============================================================================

class DimensionDefinicion(models.Model):
    """Definición general de una dimensión de un test Nexus."""

    TEST_TIPO_CHOICES = [
        ('',               'Todos los tests (genérico)'),
        ('techmind',       'TechMind'),
        ('careernav',      'CareerNavigator'),
        ('behaviormatrix', 'BehaviorMatrix'),
        ('conflictsync',   'ConflictSync'),
        ('decisioncore',   'DecisionCore'),
        ('identitymap',    'IdentityMap'),
    ]

    test_tipo   = models.CharField(
        max_length=30, choices=TEST_TIPO_CHOICES, blank=True, default='',
        verbose_name='Test', help_text='Deja vacío para dimensiones comunes a varios tests.',
    )
    label       = models.CharField(
        max_length=200, verbose_name='Nombre de la dimensión',
        help_text='Debe coincidir exactamente con el label del JSON de resultados.',
    )
    descripcion = models.TextField(
        verbose_name='Descripción general',
        help_text='Explica qué mide esta dimensión y por qué importa.',
    )
    icono       = models.CharField(
        max_length=60, blank=True, default='bi-bar-chart-line',
        verbose_name='Icono (Bootstrap Icons)',
        help_text='Ej: bi-cpu, bi-lightbulb, bi-graph-up',
    )
    orden       = models.PositiveSmallIntegerField(default=0, verbose_name='Orden')

    class Meta:
        verbose_name = 'Dimensión'
        verbose_name_plural = 'Dimensiones'
        unique_together = [['test_tipo', 'label']]
        ordering = ['test_tipo', 'orden', 'label']

    def __str__(self):
        prefix = dict(self.TEST_TIPO_CHOICES).get(self.test_tipo, self.test_tipo)
        return f'{prefix} — {self.label}' if self.test_tipo else self.label

    def rango_para_score(self, score):
        """Devuelve el DimensionRango que cubre el score dado."""
        return self.rangos.filter(rango_min__lte=score, rango_max__gte=score).first()


class DimensionRango(models.Model):
    """Explicación detallada para un rango de puntuación de una dimensión."""

    COLOR_CHOICES = [
        ('#22C55E', 'Verde — Excelente'),
        ('#84CC16', 'Verde lima — Bueno'),
        ('#F59E0B', 'Amarillo — Moderado'),
        ('#F97316', 'Naranja — Bajo'),
        ('#EF4444', 'Rojo — Muy bajo'),
        ("#C715F3", 'Morado — Nivel Máximo'),  
    ]

    dimension     = models.ForeignKey(
        DimensionDefinicion, on_delete=models.CASCADE,
        related_name='rangos', verbose_name='Dimensión',
    )
    rango_min     = models.PositiveSmallIntegerField(verbose_name='Score mínimo (0–100)')
    rango_max     = models.PositiveSmallIntegerField(verbose_name='Score máximo (0–100)')
    titulo        = models.CharField(max_length=100, verbose_name='Título del nivel',
                                     help_text='Ej: En desarrollo, Medio, Alto, Experto')
    explicacion   = models.TextField(verbose_name='Explicación detallada',
                                     help_text='Qué significa obtener este score en esta dimensión.')
    recomendacion = models.TextField(
        blank=True, verbose_name='Recomendación de mejora',
        help_text='Acciones concretas para subir el score. Dejar vacío si no aplica.',
    )
    color_nivel   = models.CharField(
        max_length=7, choices=COLOR_CHOICES, default='#64748B',
        verbose_name='Color del nivel',
    )

    class Meta:
        verbose_name = 'Rango de score'
        verbose_name_plural = 'Rangos de score'
        ordering = ['rango_min']

    def __str__(self):
        return f'{self.dimension.label} [{self.rango_min}–{self.rango_max}]: {self.titulo}'
