from django.core.exceptions import ValidationError
from django.db import models


class ProximaActualizacion(models.Model):
    class Estado(models.TextChoices):
        ANALISIS = 'analisis', 'Análisis'
        DESARROLLO = 'desarrollo', 'Desarrollo'
        TEST = 'test', 'Test'
        PRODUCCION = 'produccion', 'Producción'

    referencia = models.CharField('Referencia', max_length=80, unique=True)
    titulo = models.CharField('Título', max_length=180)
    descripcion = models.TextField('Descripción')
    equipo = models.CharField('Equipo responsable', max_length=180)
    fecha_inicio = models.DateField('Fecha de inicio')
    fecha_fin = models.DateField('Fecha de fin')
    estado = models.CharField(
        'Estado', max_length=20, choices=Estado.choices, default=Estado.ANALISIS,
    )

    class Meta:
        verbose_name = 'Próxima actualización'
        verbose_name_plural = 'Próximas actualizaciones'
        ordering = ['-fecha_inicio', 'referencia']
        constraints = [
            models.CheckConstraint(
                check=models.Q(fecha_fin__gte=models.F('fecha_inicio')),
                name='updates_fin_no_anterior_inicio',
            ),
            models.CheckConstraint(
                check=models.Q(estado__in=['analisis', 'desarrollo', 'test', 'produccion']),
                name='updates_estado_valido',
            ),
        ]

    def __str__(self):
        return f'{self.referencia} — {self.titulo}'

    def clean(self):
        super().clean()
        if self.fecha_inicio and self.fecha_fin and self.fecha_fin < self.fecha_inicio:
            raise ValidationError({
                'fecha_fin': 'La fecha de fin no puede ser anterior a la fecha de inicio.',
            })
