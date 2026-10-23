from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class PersonalGoal(models.Model):
    class TrackingType(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        AUTOMATIC = 'automatic', 'Automático'

    class SourceMetric(models.TextChoices):
        COMPLETED_LESSONS = 'completed_lessons', 'Temas de curso completados'
        COMPLETED_COURSES = 'completed_courses', 'Cursos completados'
        COMPLETED_NEXUS_TESTS = 'completed_nexus_tests', 'Evaluaciones Nexus completadas'
        ACTIVITY_ENTRIES = 'activity_entries', 'Actividades registradas'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Activo'
        COMPLETED = 'completed', 'Completado'
        ARCHIVED = 'archived', 'Archivado'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='personal_goals',
    )
    title = models.CharField(max_length=140)
    description = models.TextField(blank=True)
    tracking_type = models.CharField(
        max_length=12,
        choices=TrackingType.choices,
        default=TrackingType.MANUAL,
    )
    source_metric = models.CharField(
        max_length=32,
        choices=SourceMetric.choices,
        blank=True,
    )
    target_label = models.CharField(max_length=80, blank=True)
    current_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    target_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    target_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['status', 'target_date', '-updated_at', '-pk']
        indexes = [
            models.Index(fields=['user', 'status', 'target_date'], name='users_goal_owner_state'),
        ]
        permissions = [('manage_personal_goals', 'Puede administrar sus propios objetivos del dashboard')]

    def clean(self):
        if self.tracking_type == self.TrackingType.MANUAL:
            if self.source_metric:
                raise ValidationError({'source_metric': 'Un objetivo manual no utiliza una métrica automática.'})
            if (self.current_value is None) != (self.target_value is None):
                raise ValidationError('Un objetivo manual medible requiere avance y objetivo.')
        elif self.tracking_type == self.TrackingType.AUTOMATIC:
            if not self.source_metric:
                raise ValidationError({'source_metric': 'Selecciona una fuente de datos disponible.'})
            if self.current_value is not None:
                raise ValidationError({'current_value': 'El avance automático se calcula desde su fuente.'})
            if self.target_value is None:
                raise ValidationError({'target_value': 'Un objetivo automático requiere un valor objetivo.'})

        if self.status == self.Status.COMPLETED and not self.completed_at:
            raise ValidationError({'completed_at': 'Un objetivo completado necesita fecha de finalización.'})
        if self.status != self.Status.COMPLETED and self.completed_at:
            raise ValidationError({'completed_at': 'Solo objetivos completados guardan fecha de finalización.'})
        if self.status == self.Status.ARCHIVED and not self.archived_at:
            raise ValidationError({'archived_at': 'Un objetivo archivado necesita fecha de archivo.'})
        if self.status != self.Status.ARCHIVED and self.archived_at:
            raise ValidationError({'archived_at': 'Solo objetivos archivados guardan fecha de archivo.'})

    @property
    def progress_percent(self):
        if self.current_value is None or not self.target_value:
            return None
        return min(100, int(self.current_value / self.target_value * 100))

    def __str__(self):
        return self.title
