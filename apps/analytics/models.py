"""Models for the analytics app."""
from django.db import models
from django.conf import settings


class RegistroActividad(models.Model):
    TIPO_CHOICES = [
        ('acceso', 'Acceso al sistema'),
        ('tema_completado', 'Tema completado'),
        ('quiz_completado', 'Quiz completado'),
        ('recurso_visualizado', 'Recurso visualizado'),
        ('insignia_obtenida', 'Insignia obtenida'),
        ('recompensa_canjeada', 'Recompensa canjeada'),
        ('mision_completada', 'Misión completada'),
        ('cv_seccion', 'Sección de CV completada'),
        ('bug_validado', 'Bug validado'),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='actividades'
    )
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    descripcion = models.TextField(blank=True)
    puntos_ganados = models.IntegerField(default=0)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Registro de Actividad'
        verbose_name_plural = 'Registros de Actividad'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['usuario', 'tipo']),
            models.Index(fields=['-timestamp']),
        ]

    def __str__(self):
        return f'{self.usuario.username} — {self.get_tipo_display()}'
