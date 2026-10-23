"""Models for the certifications app."""
import hashlib
import ipaddress
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.utils import timezone

from .storage import CertificationEvidenceStorage


class CredentialType(models.TextChoices):
    MANUFACTURER = 'manufacturer', 'Certificación de fabricante o proyecto'
    INDEPENDENT = 'independent', 'Certificación de entidad independiente'
    COURSE = 'course', 'Certificado de finalización de curso'
    INTERNAL = 'internal', 'Acreditación propia de Notodoescodigo'


class CredentialVisibility(models.TextChoices):
    PRIVATE = 'private', 'Privada'
    PUBLIC = 'public', 'Pública en el perfil'


class ReviewStatus(models.TextChoices):
    DECLARED = 'declared', 'Declarada'
    PENDING = 'pending', 'Pendiente'
    IN_REVIEW = 'in_review', 'En revisión'
    VERIFIED = 'verified', 'Verificada'
    REJECTED = 'rejected', 'Rechazada'


def _validate_https_url(value):
    if not value:
        return
    URLValidator(schemes=['https'])(value)
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError('Introduce una URL HTTPS pública sin credenciales.')
    host = parsed.hostname.lower().rstrip('.')
    if (
        host in {'localhost', 'localhost.localdomain'}
        or host.endswith(('.localhost', '.local', '.internal'))
    ):
        raise ValidationError('La URL no puede apuntar a un host local.')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValidationError('La URL no puede apuntar a una dirección IP privada o local.')


def _evidence_upload_path(instance, filename):
    return f'{instance.credential_id}/{uuid.uuid4().hex}'


class Certificado(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='certificados'
    )
    curso = models.ForeignKey(
        'courses.Curso', on_delete=models.CASCADE, related_name='certificados'
    )
    codigo_unico = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    fecha_emision = models.DateTimeField(auto_now_add=True)
    pdf = models.FileField(upload_to='certificados/', null=True, blank=True)

    class Meta:
        unique_together = ('usuario', 'curso')
        verbose_name = 'Certificado'
        verbose_name_plural = 'Certificados'
        ordering = ['-fecha_emision']

    def __str__(self):
        return f'{self.usuario.get_full_name() or self.usuario.username} — {self.curso.nombre}'

    def ensure_pdf_exists(self):
        from .utils import generar_y_guardar_certificado

        if not self.pdf or not self.pdf.storage.exists(self.pdf.name):
            generar_y_guardar_certificado(self)

    def get_validation_url(self):
        from django.conf import settings as conf
        return f'{conf.SITE_URL}/certificados/validar/{self.codigo_unico}/'


class CredentialIssuer(models.Model):
    name = models.CharField(max_length=180, unique=True)
    website = models.URLField(blank=True, validators=[_validate_https_url])
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CredentialDefinition(models.Model):
    name = models.CharField(max_length=180)
    issuer = models.ForeignKey(
        CredentialIssuer,
        on_delete=models.PROTECT,
        related_name='definitions',
    )
    credential_type = models.CharField(max_length=20, choices=CredentialType.choices)
    area = models.CharField(max_length=120, blank=True)
    official_information_url = models.URLField(blank=True, validators=[_validate_https_url])
    verification_method = models.CharField(max_length=300, blank=True)
    is_validated = models.BooleanField(default=False)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['issuer__name', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['issuer', 'name', 'credential_type'],
                name='certifications_unique_definition',
            ),
        ]

    def clean(self):
        if self.is_active and (
            not self.is_validated
            or not self.issuer_id
            or not self.issuer.is_active
        ):
            raise ValidationError({
                'is_active': 'Valida la definición y activa su emisor antes de publicarla.',
            })

    def __str__(self):
        return f'{self.name} — {self.issuer.name}'


class UserCredential(models.Model):
    class Origin(models.TextChoices):
        MANUAL = 'manual', 'Declaración manual'
        INTERNAL = 'internal', 'Emisión interna'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='credential_claims',
    )
    definition = models.ForeignKey(
        CredentialDefinition,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_credentials',
    )
    name = models.CharField(max_length=180)
    issuer_name = models.CharField(max_length=180)
    credential_type = models.CharField(max_length=20, choices=CredentialType.choices)
    external_id = models.CharField(max_length=180, blank=True)
    area = models.CharField(max_length=120, blank=True)
    awarded_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    verification_url = models.URLField(blank=True, validators=[_validate_https_url])
    origin = models.CharField(max_length=12, choices=Origin.choices, default=Origin.MANUAL)
    visibility = models.CharField(
        max_length=12,
        choices=CredentialVisibility.choices,
        default=CredentialVisibility.PRIVATE,
    )
    publish_external_id = models.BooleanField(default=False)
    publish_verification_url = models.BooleanField(default=False)
    review_status = models.CharField(
        max_length=12,
        choices=ReviewStatus.choices,
        default=ReviewStatus.DECLARED,
    )
    revision = models.PositiveIntegerField(default=1)
    revoked_on = models.DateField(null=True, blank=True)
    revocation_source = models.CharField(max_length=300, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-pk']
        indexes = [
            models.Index(fields=['user', 'visibility', 'archived_at'], name='cert_credential_owner'),
            models.Index(fields=['review_status', 'updated_at'], name='cert_credential_review'),
        ]
        permissions = [
            ('review_credential', 'Puede revisar credenciales'),
            ('manage_credential_catalog', 'Puede gestionar el catálogo de credenciales'),
        ]

    def clean(self):
        if self.definition_id:
            if (
                not self.definition.is_active
                or not self.definition.is_validated
                or not self.definition.issuer.is_active
            ):
                raise ValidationError({'definition': 'Solo se pueden asociar definiciones activas y validadas.'})
            if self.credential_type != self.definition.credential_type:
                raise ValidationError({'credential_type': 'El tipo no coincide con la definición.'})
        if self.awarded_on and self.expires_on and self.expires_on < self.awarded_on:
            raise ValidationError({'expires_on': 'La caducidad no puede ser anterior a la obtención.'})
        if self.origin == self.Origin.INTERNAL and self.credential_type != CredentialType.INTERNAL:
            raise ValidationError({'credential_type': 'Las emisiones internas deben identificarse como propias.'})
        if self.origin == self.Origin.MANUAL and self.credential_type == CredentialType.INTERNAL:
            raise ValidationError({'credential_type': 'Las acreditaciones internas se leen del registro original de emisión.'})
        if self.revoked_on and not self.revocation_source.strip():
            raise ValidationError({'revocation_source': 'Indica la fuente que confirmó la revocación.'})

    @property
    def validity_status(self):
        if self.revoked_on:
            return 'revoked'
        if not self.expires_on:
            return 'no_expiry'
        if self.expires_on < timezone.localdate():
            return 'expired'
        return 'current'

    @property
    def is_verified_for_current_revision(self):
        return self.review_status == ReviewStatus.VERIFIED and any(
            verification.version == self.revision
            and verification.result == CredentialVerification.Result.VERIFIED
            for verification in self.verifications.all()
        )

    def __str__(self):
        return f'{self.name} — {self.user}'


class CredentialRevision(models.Model):
    credential = models.ForeignKey(
        UserCredential,
        on_delete=models.CASCADE,
        related_name='revisions',
    )
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='credential_revisions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']
        constraints = [
            models.UniqueConstraint(
                fields=['credential', 'version'],
                name='certifications_unique_revision',
            ),
        ]


class CredentialEvidence(models.Model):
    class EvidenceType(models.TextChoices):
        CERTIFICATE = 'certificate', 'Certificado'
        TRANSCRIPT = 'transcript', 'Expediente o constancia'
        ISSUER_CONFIRMATION = 'issuer_confirmation', 'Confirmación del emisor'
        OTHER = 'other', 'Otra evidencia'

    credential = models.ForeignKey(
        UserCredential,
        on_delete=models.PROTECT,
        related_name='evidences',
    )
    file = models.FileField(
        upload_to=_evidence_upload_path,
        storage=CertificationEvidenceStorage(),
    )
    original_name = models.CharField(max_length=180)
    evidence_type = models.CharField(max_length=24, choices=EvidenceType.choices)
    content_type = models.CharField(max_length=80)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    version = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='credential_evidences',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    @classmethod
    def digest(cls, uploaded_file):
        digest = hashlib.sha256()
        for chunk in uploaded_file.chunks():
            digest.update(chunk)
        uploaded_file.seek(0)
        return digest.hexdigest()


class CredentialReviewRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        IN_REVIEW = 'in_review', 'En revisión'
        COMPLETED = 'completed', 'Resuelta'
        CANCELLED = 'cancelled', 'Cancelada por cambio de versión'

    credential = models.ForeignKey(
        UserCredential,
        on_delete=models.PROTECT,
        related_name='review_requests',
    )
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='credential_review_requests',
    )
    assigned_reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='assigned_credential_reviews',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['credential', 'version'],
                condition=models.Q(status__in=['pending', 'in_review']),
                name='certifications_one_open_review',
            ),
        ]


class CredentialVerification(models.Model):
    class Method(models.TextChoices):
        MANUAL_EVIDENCE = 'manual_evidence', 'Revisión manual de evidencia'
        ISSUER_CONFIRMATION = 'issuer_confirmation', 'Confirmación documentada del emisor'
        INTERNAL_RECORD = 'internal_record', 'Comprobación de registro interno'

    class Result(models.TextChoices):
        VERIFIED = 'verified', 'Verificada'
        REJECTED = 'rejected', 'No se pudo verificar'

    request = models.OneToOneField(
        CredentialReviewRequest,
        on_delete=models.PROTECT,
        related_name='verification',
    )
    credential = models.ForeignKey(
        UserCredential,
        on_delete=models.PROTECT,
        related_name='verifications',
    )
    version = models.PositiveIntegerField()
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='credential_verifications',
    )
    reviewer_name = models.CharField(max_length=180)
    method = models.CharField(max_length=24, choices=Method.choices)
    source = models.CharField(max_length=500, blank=True)
    result = models.CharField(max_length=12, choices=Result.choices)
    reason = models.TextField()
    scope = models.CharField(max_length=300)
    reviewed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-reviewed_at', '-pk']


class CredentialAuditEvent(models.Model):
    class Action(models.TextChoices):
        CREATED = 'created', 'Credencial declarada'
        UPDATED = 'updated', 'Credencial modificada'
        VISIBILITY_CHANGED = 'visibility_changed', 'Visibilidad modificada'
        EVIDENCE_ADDED = 'evidence_added', 'Evidencia adjuntada'
        REVIEW_REQUESTED = 'review_requested', 'Revisión solicitada'
        REVIEW_CLAIMED = 'review_claimed', 'Revisión asignada'
        REVIEW_DECIDED = 'review_decided', 'Revisión resuelta'
        ARCHIVED = 'archived', 'Credencial archivada'
        REVOKED = 'revoked', 'Revocación confirmada'

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='credential_audit_events',
    )
    credential = models.ForeignKey(
        UserCredential,
        on_delete=models.SET_NULL,
        null=True,
        related_name='audit_events',
    )
    review_request = models.ForeignKey(
        CredentialReviewRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_events',
    )
    action = models.CharField(max_length=24, choices=Action.choices)
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
