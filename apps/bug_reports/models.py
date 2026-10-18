from django.conf import settings
from django.db import models


class BugReport(models.Model):
    ESTADO_PENDIENTE = 'pendiente'
    ESTADO_VALIDADO = 'validado'
    ESTADO_RECHAZADO = 'rechazado'

    ESTADO_CHOICES = [
        (ESTADO_PENDIENTE, 'Pendiente'),
        (ESTADO_VALIDADO, 'Validado'),
        (ESTADO_RECHAZADO, 'Rechazado'),
    ]

    alumno = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bugs_reportados',
    )
    titulo = models.CharField(max_length=180)
    descripcion = models.TextField()
    pasos_reproduccion = models.TextField(blank=True)
    url_afectada = models.CharField(max_length=255, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=ESTADO_PENDIENTE)
    puntos_premio = models.PositiveIntegerField(default=10)
    puntos_asignados = models.BooleanField(default=False)
    comentarios_admin = models.TextField(blank=True)
    validado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='bugs_validados',
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    validado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Reporte de bug'
        verbose_name_plural = 'Reportes de bugs'
        ordering = ['-creado_en']

    def __str__(self):
        return f'{self.titulo} ({self.get_estado_display()})'

    def save(self, *args, **kwargs):
        """Auto-assign points once when the report is validated."""
        puntos_asignados_en_esta_guardada = False
        if (
            self.estado == self.ESTADO_VALIDADO
            and not self.puntos_asignados
            and self.puntos_premio > 0
            and self.alumno_id
        ):
            self.alumno.agregar_puntos(self.puntos_premio)
            self.puntos_asignados = True
            puntos_asignados_en_esta_guardada = True

        if puntos_asignados_en_esta_guardada and kwargs.get('update_fields') is not None:
            update_fields = set(kwargs['update_fields'])
            update_fields.add('puntos_asignados')
            kwargs['update_fields'] = list(update_fields)

        result = super().save(*args, **kwargs)

        if puntos_asignados_en_esta_guardada:
            from apps.analytics.models import RegistroActividad

            RegistroActividad.objects.create(
                usuario=self.alumno,
                tipo='bug_validado',
                descripcion=f'Bug validado: {self.titulo}',
                puntos_ganados=self.puntos_premio,
            )

        return result
