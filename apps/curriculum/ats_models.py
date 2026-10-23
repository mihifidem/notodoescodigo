import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


ATS_RULES = ('sections', 'contacts', 'links', 'dates')


def default_rules():
    return list(ATS_RULES)


class ATSConfiguration(models.Model):
    version = models.PositiveIntegerField(unique=True)
    enabled = models.BooleanField(default=True)
    criteria_version = models.CharField(max_length=30, default='text-rules-v1', editable=False)
    enabled_rules = models.JSONField(default=default_rules, blank=True)
    max_pdf_bytes = models.PositiveIntegerField(
        default=5 * 1024 * 1024, validators=[MinValueValidator(1024), MaxValueValidator(5 * 1024 * 1024)],
    )
    max_pages = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(1), MaxValueValidator(30)],
    )
    max_text_chars = models.PositiveIntegerField(
        default=100000, validators=[MinValueValidator(1000), MaxValueValidator(100000)],
    )
    processing_seconds = models.PositiveSmallIntegerField(
        default=15, validators=[MinValueValidator(1), MaxValueValidator(30)],
    )
    requests_per_minute = models.PositiveSmallIntegerField(
        default=6, validators=[MinValueValidator(1), MaxValueValidator(30)],
    )
    detail_entitlement = models.SlugField(max_length=100, blank=True)
    comparison_entitlement = models.SlugField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']
        default_permissions = ('add', 'view')
        verbose_name = 'Configuración de verificación CV'
        verbose_name_plural = 'Configuraciones de verificación CV'

    def clean(self):
        if not isinstance(self.enabled_rules, list) or not set(self.enabled_rules) <= set(ATS_RULES):
            raise ValidationError({'enabled_rules': 'Reglas admitidas: sections, contacts, links, dates.'})

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Crea una versión nueva; no se reescriben criterios históricos.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'Verificar CV v{self.version}'


class ATSSource(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    profile = models.ForeignKey('curriculum.CVProfile', null=True, on_delete=models.SET_NULL)
    profile_revision = models.PositiveIntegerField(null=True)
    source_kind = models.CharField(max_length=20)
    source_label = models.CharField(max_length=250)
    original_pdf = models.BinaryField(null=True, editable=False)
    original_hash = models.CharField(max_length=64)
    extracted_text = models.TextField()
    extraction_method = models.CharField(max_length=40)
    limitations = models.JSONField(default=list)
    configuration = models.ForeignKey(ATSConfiguration, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ['-created_at']


class ATSReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    source = models.ForeignKey(ATSSource, on_delete=models.CASCADE, related_name='reports')
    configuration = models.ForeignKey(ATSConfiguration, on_delete=models.PROTECT)
    reviewed_text = models.TextField()
    content_hash = models.CharField(max_length=64)
    offer_text = models.TextField(blank=True)
    offer_url = models.URLField(max_length=2000, blank=True)
    scope = models.CharField(max_length=20)
    access_basis = models.CharField(max_length=150)
    status = models.CharField(max_length=20, default='complete')
    result = models.JSONField()
    archived = models.BooleanField(default=False)
    idempotency_key = models.UUIDField()
    request_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at'], name='curr_ats_owner_date')]
        constraints = [
            models.UniqueConstraint(fields=['user', 'idempotency_key'], name='curr_ats_report_key'),
        ]

    def __str__(self):
        return f'{self.source.title} — {self.created_at:%Y-%m-%d %H:%M} — {self.scope}'


class ATSComparison(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    before = models.ForeignKey(ATSReport, on_delete=models.CASCADE, related_name='comparisons_before')
    after = models.ForeignKey(ATSReport, on_delete=models.CASCADE, related_name='comparisons_after')
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=['user', 'before', 'after'], name='curr_ats_compare_pair'),
        ]


class ATSOperation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    report = models.ForeignKey(ATSReport, null=True, on_delete=models.SET_NULL)
    idempotency_key = models.UUIDField()
    task = models.CharField(max_length=30)
    status = models.CharField(max_length=20, default='blocked', editable=False)
    error_code = models.CharField(max_length=50, default='energy_integration_pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ('view',)
        constraints = [
            models.UniqueConstraint(fields=['user', 'idempotency_key'], name='curr_ats_operation_key'),
        ]
