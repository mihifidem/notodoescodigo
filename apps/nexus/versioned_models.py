import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction


class NexusFamily(models.Model):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Familias Nexus'

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if original.code != self.code:
                raise ValidationError('El código de familia es inmutable.')
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class NexusVersion(models.Model):
    class Mode(models.TextChoices):
        BASIC = 'basic', 'Basic'
        ESSENTIAL = 'essential', 'Essential'
        ADVANCED = 'advanced', 'Advanced'
        PREMIUM = 'premium', 'Premium'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        REVIEW = 'review', 'En revisión'
        PUBLISHED = 'published', 'Publicada'
        ARCHIVED = 'archived', 'Archivada'

    code = models.SlugField(max_length=80)
    family = models.ForeignKey(NexusFamily, on_delete=models.PROTECT, related_name='versions')
    assessment = models.ForeignKey('nexus.Assessment', on_delete=models.PROTECT, related_name='versions')
    version = models.PositiveIntegerField()
    mode = models.CharField(max_length=12, choices=Mode.choices)
    title = models.CharField(max_length=200)
    purpose = models.TextField(blank=True)
    audience = models.TextField(blank=True)
    instructions = models.TextField(blank=True)
    authorship = models.TextField(blank=True)
    provenance = models.TextField(blank=True)
    authorization = models.TextField(blank=True)
    foundation = models.TextField(blank=True)
    validation_evidence = models.TextField(blank=True)
    limitations = models.TextField(blank=True)
    reviewed_on = models.DateField(null=True, blank=True)
    editor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    own_content = models.BooleanField(default=True)
    is_fixture = models.BooleanField(default=False)
    duration_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    entitlement_code = models.SlugField(max_length=100, blank=True)
    definition = models.JSONField(default=dict, blank=True)
    snapshot = models.JSONField(default=dict, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    change_reason = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['family__order', 'code', '-version']
        constraints = [
            models.UniqueConstraint(fields=['code', 'version'], name='nexus_version_code'),
        ]
        permissions = [('publish_nexusversion', 'Puede publicar versiones editoriales Nexus')]

    def clean(self):
        if self.status == self.Status.PUBLISHED and not self.published_at:
            from .versioned_services import editorial_snapshot
            editorial_snapshot(self)

    @transaction.atomic
    def save(self, *args, **kwargs):
        original = None
        actor = kwargs.pop('editorial_actor', None)
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if original.code != self.code or original.version != self.version:
                raise ValidationError('Código y versión son inmutables; crea otra versión.')
            if original.published_at:
                protected = [
                    field.attname for field in self._meta.concrete_fields
                    if field.name not in ('status', 'updated_at', 'change_reason')
                ]
                if any(getattr(original, key) != getattr(self, key) for key in protected):
                    raise ValidationError('Una versión publicada es inmutable. Crea una nueva versión.')
                if self.status not in (self.Status.PUBLISHED, self.Status.ARCHIVED):
                    raise ValidationError('Una versión publicada solo puede archivarse.')
        if self.status == self.Status.PUBLISHED and not self.published_at:
            from django.utils import timezone
            from .versioned_services import editorial_snapshot
            self.full_clean(exclude=['snapshot'])
            self.snapshot = editorial_snapshot(self)
            self.published_at = timezone.now()
        result = super().save(*args, **kwargs)
        if original is None or original.status != self.status or not self.published_at:
            NexusEditorialEvent.objects.create(
                version=self, actor=actor or self.editor, action=self.status,
                reason=self.change_reason or 'Guardado de borrador editorial.',
            )
        return result

    def __str__(self):
        return f'{self.title} · {self.get_mode_display()} · v{self.version}'

    def get_mode_display(self):
        return NexusModality.objects.get(code=self.mode).name


class NexusModality(models.Model):
    code = models.CharField(max_length=12, choices=NexusVersion.Mode.choices, unique=True)
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'code']
        verbose_name_plural = 'Modalidades Nexus'

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.get(pk=self.pk).code != self.code:
            raise ValidationError('El código de modalidad es inmutable.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class NexusEditorialEvent(models.Model):
    version = models.ForeignKey(NexusVersion, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=30)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ('view',)


class NexusGoalLink(models.Model):
    session = models.ForeignKey('nexus.AssessmentSession', on_delete=models.CASCADE)
    dimension = models.CharField(max_length=80)
    goal = models.OneToOneField('users.PersonalGoal', on_delete=models.CASCADE)

    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=['session', 'dimension'], name='nexus_goal_dimension')]


class NexusAIOperation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey('nexus.AssessmentSession', null=True, on_delete=models.SET_NULL)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    key = models.UUIDField()
    task = models.CharField(max_length=20)
    status = models.CharField(max_length=20, default='blocked')
    error = models.CharField(max_length=60, default='energy_integration_pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ('view',)
        constraints = [models.UniqueConstraint(fields=['user', 'key'], name='nexus_ai_operation_key')]
