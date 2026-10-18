from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TipoExamen(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    codigo = models.CharField(max_length=30, unique=True)
    descripcion = models.TextField(blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Tipo de examen'
        verbose_name_plural = 'Tipos de examen'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Evaluacion(models.Model):
    # Ambito jerarquico de la evaluacion
    ALCANCE_MODULO = 'MF'
    ALCANCE_UF = 'UF'
    ALCANCE_UD = 'UD'

    ALCANCE_MF = ALCANCE_MODULO
    ALCANCE_CHOICES = [
        (ALCANCE_UD, 'UD - Unidad Didactica'),
        (ALCANCE_UF, 'UF - Unidad Formativa'),
        (ALCANCE_MF, 'MF - Modulo Formativo'),
    ]

    TIPO_OBJ_TEST = 'OBJ_TEST'
    TIPO_OBJ_REDACCION = 'OBJ_REDACCION'
    TIPO_PRACTICO_CODIGO = 'PRACTICO_CODIGO'
    TIPO_PRACTICO_REDACCION = 'PRACTICO_REDACCION'
    TIPO_PRUEBA_CHOICES = [
        (TIPO_OBJ_TEST, 'Objetiva tipo test'),
        (TIPO_OBJ_REDACCION, 'Objetiva tipo redaccion'),
        (TIPO_PRACTICO_CODIGO, 'Practico tipo creacion de codigo'),
        (TIPO_PRACTICO_REDACCION, 'Practico tipo redaccion'),
    ]

    ESTADO_BORRADOR = 'BORRADOR'
    ESTADO_PUBLICADA = 'PUBLICADA'
    ESTADO_CERRADA = 'CERRADA'
    ESTADO_CHOICES = [
        (ESTADO_BORRADOR, 'Borrador'),
        (ESTADO_PUBLICADA, 'Publicada'),
        (ESTADO_CERRADA, 'Cerrada'),
    ]

    tipo_examen = models.ForeignKey(
        TipoExamen,
        on_delete=models.PROTECT,
        related_name='evaluaciones',
        null=True,
        blank=True,
    )
    titulo = models.CharField(max_length=200)
    aula = models.ForeignKey(
        'users.Aula',
        on_delete=models.PROTECT,
        related_name='evaluaciones',
        null=True,
        blank=True,
    )
    alcance_tipo = models.CharField(max_length=15, choices=ALCANCE_CHOICES)
    tipo_prueba = models.CharField(max_length=20, choices=TIPO_PRUEBA_CHOICES, default=TIPO_OBJ_TEST)
    tema = models.ForeignKey(
        'courses.Tema',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='evaluaciones',
        verbose_name='Tema (UD)',
    )
    curso = models.ForeignKey(
        'courses.Curso',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='evaluaciones_uf',
        verbose_name='Curso (Unidad Formativa)',
    )
    cursos = models.ManyToManyField(
        'courses.Curso',
        blank=True,
        related_name='evaluaciones_modulo',
        verbose_name='Cursos (Módulo Formativo)',
    )
    modulo_ref = models.CharField(
        max_length=100,
        blank=True,
        help_text='Referencia externa opcional del módulo (código, nombre externo…).',
    )
    enunciado = models.TextField(blank=True)
    enunciado_pdf = models.FileField(upload_to='evaluaciones/enunciados/%Y/%m/', blank=True)
    criterios_evaluacion_pdf = models.FileField(upload_to='evaluaciones/criterios/%Y/%m/', blank=True)
    rubrica_pdf = models.FileField(upload_to='evaluaciones/rubricas/%Y/%m/', blank=True)
    instrucciones = models.TextField(blank=True)
    criterios_a_valorar = models.TextField(blank=True)
    clave_respuestas = models.JSONField(
        blank=True,
        null=True,
        default=None,
        help_text=(
            'Solo para tests objetivos (OBJ_TEST). '
            'Diccionario JSON con la clave de corrección: {"1":"D","2":"C","3":"A",...}. '
            'Si se rellena, la puntuación se calcula en Python de forma exacta '
            '(aciertos × valor − errores × penalización) sin depender de la IA.'
        ),
    )
    max_puntuacion = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('10.00'))
    fecha_prueba = models.DateTimeField(null=True, blank=True)
    fecha_apertura = models.DateTimeField(null=True, blank=True)
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default=ESTADO_BORRADOR)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Evaluacion'
        verbose_name_plural = 'Evaluaciones'
        ordering = ['-creada_en']

    def __str__(self):
        return self.titulo

    @property
    def esta_abierta(self):
        now = timezone.now()
        if self.estado != self.ESTADO_PUBLICADA:
            return False
        if self.fecha_apertura and now < self.fecha_apertura:
            return False
        if self.fecha_cierre and now > self.fecha_cierre:
            return False
        return True

    # Alcances que requieren un tema FK
    ALCANCES_CON_TEMA = {ALCANCE_UD}
    # Alcances que requieren un curso FK (UF) o varios cursos M2M (MODULO)
    ALCANCES_CON_CURSO = {ALCANCE_UF}
    ALCANCES_CON_CURSOS = {ALCANCE_MF}

    @staticmethod
    def _is_pdf(file_field):
        if not file_field:
            return False
        return str(file_field.name).lower().endswith('.pdf')

    def clean(self):
        super().clean()
        if not self.aula_id:
            raise ValidationError({'aula': 'Debes asignar la evaluacion a un aula.'})
        if self.alcance_tipo in self.ALCANCES_CON_TEMA and not self.tema_id:
            raise ValidationError({'tema': 'Debes asociar un tema cuando el ambito es UD.'})
        if self.alcance_tipo in self.ALCANCES_CON_CURSO and not self.curso_id:
            raise ValidationError({'curso': 'Debes seleccionar un curso para una Unidad Formativa (UF).'})
        if self.alcance_tipo in self.ALCANCES_CON_CURSOS and not self.pk:
            # Validacion para altas en admin/API: en creacion se comprueba en form/serializer
            pass
        if self.alcance_tipo != self.ALCANCE_UD and self.tema_id:
            raise ValidationError({'tema': 'Solo debes seleccionar tema cuando el ambito es UD.'})
        if self.alcance_tipo != self.ALCANCE_UF and self.curso_id:
            raise ValidationError({'curso': 'Solo debes seleccionar curso cuando el ambito es UF.'})
        if self.enunciado_pdf and not self._is_pdf(self.enunciado_pdf):
            raise ValidationError({'enunciado_pdf': 'El enunciado debe subirse en formato PDF.'})
        if self.criterios_evaluacion_pdf and not self._is_pdf(self.criterios_evaluacion_pdf):
            raise ValidationError({'criterios_evaluacion_pdf': 'El criterio de evaluacion debe subirse en PDF.'})
        if self.rubrica_pdf and not self._is_pdf(self.rubrica_pdf):
            raise ValidationError({'rubrica_pdf': 'La rubrica debe subirse en PDF.'})
        if self.fecha_apertura and self.fecha_cierre and self.fecha_cierre <= self.fecha_apertura:
            raise ValidationError({'fecha_cierre': 'La fecha de cierre debe ser posterior a la de apertura.'})


class RubricaEvaluacion(models.Model):
    evaluacion = models.OneToOneField(
        Evaluacion,
        on_delete=models.CASCADE,
        related_name='rubrica',
    )
    version = models.CharField(max_length=30, default='1.0')
    nota_maxima = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('10.00'))
    umbral_aprobado = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('5.00'))

    class Meta:
        verbose_name = 'Rubrica de evaluacion'
        verbose_name_plural = 'Rubricas de evaluacion'

    def __str__(self):
        return f'Rubrica {self.version} - {self.evaluacion.titulo}'


class CriterioRubrica(models.Model):
    rubrica = models.ForeignKey(
        RubricaEvaluacion,
        on_delete=models.CASCADE,
        related_name='criterios',
    )
    codigo = models.CharField(max_length=50)
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True)
    peso = models.DecimalField(max_digits=5, decimal_places=2, help_text='Peso en porcentaje (0-100).')
    escala_min = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    escala_max = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('10.00'))
    obligatorio = models.BooleanField(default=True)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Criterio de rubrica'
        verbose_name_plural = 'Criterios de rubrica'
        ordering = ['orden', 'id']
        unique_together = ('rubrica', 'codigo')

    def __str__(self):
        return f'{self.codigo} - {self.nombre}'


class EntregaEvaluacion(models.Model):
    ESTADO_PENDIENTE = 'PENDIENTE'
    ESTADO_PROCESANDO = 'PROCESANDO'
    ESTADO_CORREGIDA = 'CORREGIDA'
    ESTADO_REVISION = 'REVISION_DOCENTE'
    ESTADO_ERROR = 'ERROR'
    ESTADO_CHOICES = [
        (ESTADO_PENDIENTE, 'Pendiente de correccion'),
        (ESTADO_PROCESANDO, 'Procesando'),
        (ESTADO_CORREGIDA, 'Corregida'),
        (ESTADO_REVISION, 'Revision docente'),
        (ESTADO_ERROR, 'Error'),
    ]

    evaluacion = models.ForeignKey(
        Evaluacion,
        on_delete=models.CASCADE,
        related_name='entregas',
    )
    alumno = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='entregas_evaluacion',
    )
    archivo_respuesta = models.FileField(upload_to='evaluaciones/entregas/%Y/%m/')
    texto_extraido = models.TextField(blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=ESTADO_PENDIENTE)
    solicita_revision_exhaustiva = models.BooleanField(default=False)
    motivo_revision_exhaustiva = models.TextField(blank=True)
    hash_archivo = models.CharField(max_length=64, blank=True)
    intento_numero = models.PositiveIntegerField(default=1)
    fecha_entrega = models.DateTimeField(auto_now_add=True)
    procesada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Entrega de evaluacion'
        verbose_name_plural = 'Entregas de evaluacion'
        ordering = ['-fecha_entrega']
        unique_together = ('evaluacion', 'alumno', 'intento_numero')

    def __str__(self):
        return f'{self.alumno} - {self.evaluacion} (intento {self.intento_numero})'


class CorreccionEvaluacion(models.Model):
    TIPO_IA = 'IA'
    TIPO_DOCENTE = 'DOCENTE_EXHAUSTIVA'
    TIPO_CORRECCION_CHOICES = [
        (TIPO_IA, 'Automatica por IA'),
        (TIPO_DOCENTE, 'Revision exhaustiva docente'),
    ]

    entrega = models.OneToOneField(
        EntregaEvaluacion,
        on_delete=models.CASCADE,
        related_name='correccion',
    )
    tipo_correccion = models.CharField(max_length=20, choices=TIPO_CORRECCION_CHOICES, default=TIPO_IA)
    puntuacion_total = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    puntuacion_por_criterio = models.JSONField(default=dict, blank=True)
    feedback_global = models.TextField(blank=True)
    feedback_detallado = models.JSONField(default=list, blank=True)
    evidencias = models.JSONField(default=list, blank=True)
    plan_mejora = models.JSONField(default=list, blank=True)
    confianza_modelo = models.DecimalField(max_digits=4, decimal_places=3, default=Decimal('0.000'))
    modelo_ia = models.CharField(max_length=80, blank=True)
    prompt_version = models.CharField(max_length=30, default='1.0')
    prompt_sistema_usado = models.TextField(blank=True)
    requiere_revision_humana = models.BooleanField(default=False)
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='correcciones_docentes',
    )
    observaciones_docente = models.TextField(blank=True)
    fecha_revision_docente = models.DateTimeField(null=True, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Correccion de evaluacion'
        verbose_name_plural = 'Correcciones de evaluacion'

    def __str__(self):
        return f'Correccion #{self.pk} - Entrega #{self.entrega_id}'


class PromptCorreccion(models.Model):
    """
    Prompt de sistema almacenado en BD para la corrección IA.
    Permite editar y versionar el prompt sin tocar código.
    """
    SCOPE_DEFAULT = 'DEFAULT'
    SCOPE_OBJ_TEST = 'OBJ_TEST'
    SCOPE_OBJ_REDACCION = 'OBJ_REDACCION'
    SCOPE_PRACTICO_CODIGO = 'PRACTICO_CODIGO'
    SCOPE_PRACTICO_REDACCION = 'PRACTICO_REDACCION'
    SCOPE_CHOICES = [
        (SCOPE_DEFAULT, 'Por defecto (cualquier tipo)'),
        (SCOPE_OBJ_TEST, 'Objetiva tipo test'),
        (SCOPE_OBJ_REDACCION, 'Objetiva tipo redaccion'),
        (SCOPE_PRACTICO_CODIGO, 'Practico tipo codigo'),
        (SCOPE_PRACTICO_REDACCION, 'Practico tipo redaccion'),
    ]

    scope = models.CharField(
        max_length=25,
        choices=SCOPE_CHOICES,
        default=SCOPE_DEFAULT,
        verbose_name='Aplica a',
    )
    version = models.CharField(max_length=30, default='1.0')
    descripcion = models.TextField(
        blank=True,
        help_text='Notas internas sobre este prompt (cambios, razon, etc.).',
    )
    system_prompt = models.TextField(verbose_name='Prompt de sistema')
    activo = models.BooleanField(
        default=False,
        help_text='Solo un prompt activo por scope. Al activar este se desactivan los demas del mismo scope.',
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Prompt de correccion'
        verbose_name_plural = 'Prompts de correccion'
        ordering = ['scope', '-creado_en']

    def __str__(self):
        return f'[{self.scope}] v{self.version} ({"ACTIVO" if self.activo else "inactivo"})'

    def save(self, *args, **kwargs):
        if self.activo:
            # Desactivar los demas del mismo scope
            PromptCorreccion.objects.filter(scope=self.scope, activo=True).exclude(pk=self.pk).update(activo=False)
        super().save(*args, **kwargs)


class EventoCorreccion(models.Model):
    entrega = models.ForeignKey(
        EntregaEvaluacion,
        on_delete=models.CASCADE,
        related_name='eventos',
    )
    evento = models.CharField(max_length=80)
    payload = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Evento de correccion'
        verbose_name_plural = 'Eventos de correccion'
        ordering = ['-creado_en']

    def __str__(self):
        return f'{self.evento} - Entrega #{self.entrega_id}'
