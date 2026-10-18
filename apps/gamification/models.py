"""Models for the gamification app."""
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone


class Insignia(models.Model):
    TIPO_CHOICES = [
        ('curso', 'Completar Curso'),
        ('tema', 'Completar Tema'),
        ('streak', 'Racha'),
        ('puntos', 'Puntos Acumulados'),
        ('mision', 'Completar Misión'),
        ('especial', 'Especial'),
    ]

    nombre = models.CharField(max_length=100)
    descripcion = models.TextField()
    imagen = models.ImageField(upload_to='insignias/', null=True, blank=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    requisito_valor = models.PositiveIntegerField(
        default=0,
        help_text='Valor numérico del requisito (p.ej. racha de 7 días, 500 puntos, etc.)'
    )
    curso_objetivo = models.ForeignKey(
        'courses.Curso',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='insignias_asociadas',
        help_text='Curso específico que activa esta insignia (opcional).',
    )
    tema_objetivo = models.ForeignKey(
        'courses.Tema',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='insignias_asociadas',
        help_text='Tema específico que activa esta insignia (opcional).',
    )
    visible = models.BooleanField(default=True, help_text='Falso = insignia oculta/sorpresa')

    class Meta:
        verbose_name = 'Insignia'
        verbose_name_plural = 'Insignias'
        ordering = ['tipo', 'requisito_valor']

    def clean(self):
        super().clean()

        if self.tipo == 'curso':
            if not self.curso_objetivo_id:
                raise ValidationError({'curso_objetivo': 'Para insignias de tipo curso, debes seleccionar un curso objetivo.'})
            if self.tema_objetivo_id:
                raise ValidationError({'tema_objetivo': 'Para insignias de tipo curso, no debes seleccionar tema objetivo.'})

        elif self.tipo == 'tema':
            if not self.tema_objetivo_id:
                raise ValidationError({'tema_objetivo': 'Para insignias de tipo tema, debes seleccionar un tema objetivo.'})
            if self.curso_objetivo_id:
                raise ValidationError({'curso_objetivo': 'Para insignias de tipo tema, no debes seleccionar curso objetivo.'})

        else:
            if self.curso_objetivo_id:
                raise ValidationError({'curso_objetivo': 'Este tipo de insignia no admite curso objetivo.'})
            if self.tema_objetivo_id:
                raise ValidationError({'tema_objetivo': 'Este tipo de insignia no admite tema objetivo.'})

    def __str__(self):
        if self.tema_objetivo_id:
            return f'{self.nombre} (tema: {self.tema_objetivo.titulo})'
        if self.curso_objetivo_id:
            return f'{self.nombre} (curso: {self.curso_objetivo.nombre})'
        return self.nombre


class InsigniaUsuario(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='insignias_obtenidas'
    )
    insignia = models.ForeignKey(Insignia, on_delete=models.CASCADE)
    fecha_obtenida = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('usuario', 'insignia')
        verbose_name = 'Insignia de Usuario'
        verbose_name_plural = 'Insignias de Usuarios'
        ordering = ['-fecha_obtenida']

    def __str__(self):
        return f'{self.usuario.username} — {self.insignia.nombre}'


class Logro(models.Model):
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField()
    puntos_recompensa = models.PositiveIntegerField(default=50)
    oculto = models.BooleanField(default=False)
    insignia = models.ForeignKey(
        Insignia, on_delete=models.SET_NULL, null=True, blank=True, related_name='logros'
    )

    class Meta:
        verbose_name = 'Logro'
        verbose_name_plural = 'Logros'

    def __str__(self):
        return self.nombre


class LogroUsuario(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='logros_obtenidos'
    )
    logro = models.ForeignKey(Logro, on_delete=models.CASCADE)
    puntos_asignados = models.PositiveIntegerField(default=0)
    fecha_obtenido = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('usuario', 'logro')
        verbose_name = 'Logro de Usuario'
        verbose_name_plural = 'Logros de Usuarios'

    def __str__(self):
        return f'{self.usuario.username} — {self.logro.nombre}'


class Mision(models.Model):
    TIPO_CHOICES = [
        ('diaria', 'Diaria'),
        ('semanal', 'Semanal'),
    ]
    REQUISITO_CHOICES = [
        ('completar_temas', 'Completar temas'),
        ('quiz_aprobado', 'Aprobar quizzes'),
        ('racha_dias', 'Mantener racha de días'),
        ('puntos_ganados', 'Ganar puntos'),
    ]

    titulo = models.CharField(max_length=100)
    descripcion = models.TextField()
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    puntos_recompensa = models.IntegerField(default=30)
    requisito_tipo = models.CharField(max_length=30, choices=REQUISITO_CHOICES)
    requisito_cantidad = models.PositiveIntegerField(default=1)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Misión'
        verbose_name_plural = 'Misiones'

    def __str__(self):
        return f'[{self.tipo}] {self.titulo}'


class MisionUsuario(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='misiones'
    )
    mision = models.ForeignKey(Mision, on_delete=models.CASCADE, related_name='asignaciones')
    progreso = models.PositiveIntegerField(default=0)
    completada = models.BooleanField(default=False)
    fecha_completada = models.DateTimeField(null=True, blank=True)
    fecha_asignada = models.DateField(auto_now_add=True)

    class Meta:
        verbose_name = 'Misión de Usuario'
        verbose_name_plural = 'Misiones de Usuarios'
        ordering = ['completada', '-fecha_asignada']

    def __str__(self):
        return f'{self.usuario.username} — {self.mision.titulo}'

    def avanzar(self, cantidad=1):
        self.progreso = min(self.progreso + cantidad, self.mision.requisito_cantidad)
        if self.progreso >= self.mision.requisito_cantidad and not self.completada:
            self.completada = True
            self.fecha_completada = timezone.now()
            self.save()
            # Award points
            self.usuario.agregar_puntos(self.mision.puntos_recompensa)
            return True  # Newly completed
        self.save(update_fields=['progreso'])
        return False
