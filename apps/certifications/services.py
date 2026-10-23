import os
import re
from pathlib import PurePath

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    CredentialAuditEvent,
    CredentialEvidence,
    CredentialRevision,
    CredentialReviewRequest,
    CredentialType,
    CredentialVerification,
    ReviewStatus,
    UserCredential,
)


REVISION_FIELDS = (
    'definition_id',
    'name',
    'issuer_name',
    'credential_type',
    'area',
    'external_id',
    'awarded_on',
    'expires_on',
    'verification_url',
)
MAGIC_TYPES = {
    'application/pdf': ('.pdf', lambda data: data.startswith(b'%PDF-')),
    'image/png': ('.png', lambda data: data.startswith(b'\x89PNG\r\n\x1a\n')),
    'image/jpeg': ('.jpg', lambda data: data.startswith(b'\xff\xd8\xff')),
}


def _credential_snapshot(credential):
    return {
        'owner_id': credential.user_id,
        'definition_id': credential.definition_id,
        'name': credential.name,
        'issuer_name': credential.issuer_name,
        'credential_type': credential.credential_type,
        'area': credential.area,
        'external_id': credential.external_id,
        'awarded_on': credential.awarded_on.isoformat() if credential.awarded_on else None,
        'expires_on': credential.expires_on.isoformat() if credential.expires_on else None,
        'verification_url': credential.verification_url,
        'version': credential.revision,
    }


def _record_revision(credential, actor):
    CredentialRevision.objects.create(
        credential=credential,
        version=credential.revision,
        snapshot=_credential_snapshot(credential),
        changed_by=actor,
    )


def _audit(actor, credential, action, *, reason='', metadata=None, review_request=None):
    return CredentialAuditEvent.objects.create(
        actor=actor,
        credential=credential,
        review_request=review_request,
        action=action,
        reason=reason,
        metadata=metadata or {},
    )


@transaction.atomic
def save_user_credential(actor, form, credential=None):
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied('Inicia sesión para gestionar tus credenciales.')
    is_new = credential is None
    if is_new:
        credential = form.save(commit=False)
        credential.user = actor
        credential.origin = UserCredential.Origin.MANUAL
        credential.revision = 1
    else:
        credential = UserCredential.objects.select_for_update().get(
            pk=credential.pk,
            user=actor,
            origin=UserCredential.Origin.MANUAL,
            archived_at__isnull=True,
        )
        submitted = form.save(commit=False)
        old_values = {field: getattr(credential, field) for field in REVISION_FIELDS}
        old_visibility = credential.visibility
        for field in (
            'definition',
            'name',
            'issuer_name',
            'credential_type',
            'area',
            'external_id',
            'awarded_on',
            'expires_on',
            'verification_url',
            'visibility',
            'publish_external_id',
            'publish_verification_url',
        ):
            setattr(credential, field, getattr(submitted, field))
        relevant_change = any(
            getattr(credential, field) != old_values[field]
            for field in REVISION_FIELDS
        )
        visibility_changed = (
            old_visibility != credential.visibility
        )
        if relevant_change:
            credential.revision += 1
            credential.review_status = ReviewStatus.DECLARED
            credential.review_requests.filter(
                status__in=[
                    CredentialReviewRequest.Status.PENDING,
                    CredentialReviewRequest.Status.IN_REVIEW,
                ]
            ).update(
                status=CredentialReviewRequest.Status.CANCELLED,
                updated_at=timezone.now(),
            )

    credential.full_clean()
    credential.save()
    if is_new:
        _record_revision(credential, actor)
        _audit(actor, credential, CredentialAuditEvent.Action.CREATED)
    elif relevant_change:
        _record_revision(credential, actor)
        _audit(
            actor,
            credential,
            CredentialAuditEvent.Action.UPDATED,
            reason='Los datos sustantivos cambiaron; las decisiones anteriores no cubren esta versión.',
            metadata={'version': credential.revision},
        )
    elif visibility_changed:
        _audit(
            actor,
            credential,
            CredentialAuditEvent.Action.VISIBILITY_CHANGED,
            metadata={'visibility': credential.visibility},
        )

    uploaded_file = form.cleaned_data.get('evidence_file')
    evidence_type = form.cleaned_data.get('evidence_type')
    if uploaded_file:
        add_evidence(actor, credential, uploaded_file, evidence_type)
    return credential


def _validate_uploaded_evidence(uploaded_file):
    maximum = getattr(settings, 'CERTIFICATIONS_EVIDENCE_MAX_BYTES', 0)
    allowed_types = set(getattr(settings, 'CERTIFICATIONS_ALLOWED_EVIDENCE_MIME_TYPES', []))
    if maximum <= 0 or not allowed_types:
        raise ValidationError('La carga de evidencias no está habilitada por configuración.')
    if uploaded_file.size <= 0 or uploaded_file.size > maximum:
        raise ValidationError('El archivo está vacío o supera el límite configurado.')
    uploaded_file.seek(0)
    header = uploaded_file.read(16)
    uploaded_file.seek(0)
    matched = None
    for mime_type in allowed_types:
        entry = MAGIC_TYPES.get(mime_type)
        if entry and entry[1](header):
            matched = (mime_type, entry[0])
            break
    if not matched:
        raise ValidationError('El contenido del archivo no coincide con un formato permitido.')
    name = os.path.basename(uploaded_file.name or 'evidencia').replace('\x00', '')
    stem, extension = os.path.splitext(name)
    expected_extension = matched[1]
    if extension.lower() not in {expected_extension, '.jpeg' if expected_extension == '.jpg' else expected_extension}:
        raise ValidationError('La extensión del archivo no coincide con su contenido.')
    cleaned_name = re.sub(r'[\x00-\x1f\x7f]', '', PurePath(stem).name).strip()
    return matched[0], f'{cleaned_name or "evidencia"}{expected_extension}'


@transaction.atomic
def add_evidence(actor, credential, uploaded_file, evidence_type):
    credential = UserCredential.objects.select_for_update().get(
        pk=credential.pk,
        user=actor,
        origin=UserCredential.Origin.MANUAL,
        archived_at__isnull=True,
    )
    if evidence_type not in CredentialEvidence.EvidenceType.values:
        raise ValidationError('El tipo de evidencia seleccionado no es válido.')
    content_type, original_name = _validate_uploaded_evidence(uploaded_file)
    old_revision = credential.revision
    credential.revision += 1
    credential.review_status = ReviewStatus.DECLARED
    credential.review_requests.filter(
        status__in=[
            CredentialReviewRequest.Status.PENDING,
            CredentialReviewRequest.Status.IN_REVIEW,
        ]
    ).update(status=CredentialReviewRequest.Status.CANCELLED, updated_at=timezone.now())
    credential.save(update_fields=['revision', 'review_status', 'updated_at'])
    evidence = CredentialEvidence.objects.create(
        credential=credential,
        file=uploaded_file,
        original_name=original_name[:180],
        evidence_type=evidence_type,
        content_type=content_type,
        size_bytes=uploaded_file.size,
        sha256=CredentialEvidence.digest(uploaded_file),
        version=credential.revision,
        uploaded_by=actor,
    )
    _record_revision(credential, actor)
    _audit(
        actor,
        credential,
        CredentialAuditEvent.Action.EVIDENCE_ADDED,
        metadata={'evidence_id': evidence.pk, 'previous_version': old_revision},
    )
    return evidence


def _can_review(actor):
    return (
        getattr(actor, 'is_authenticated', False)
        and actor.is_active
        and actor.has_perm('certifications.review_credential')
    )


@transaction.atomic
def request_verification(actor, credential):
    credential = UserCredential.objects.select_for_update().get(
        pk=credential.pk,
        user=actor,
        origin=UserCredential.Origin.MANUAL,
        archived_at__isnull=True,
    )
    if credential.review_status == ReviewStatus.VERIFIED and credential.is_verified_for_current_revision:
        raise ValidationError('Esta versión ya está verificada.')
    if not credential.verification_url and not credential.evidences.filter(version=credential.revision).exists():
        raise ValidationError('Adjunta evidencia vigente o aporta un enlace para revisión manual.')
    existing = credential.review_requests.filter(
        version=credential.revision,
        status__in=[
            CredentialReviewRequest.Status.PENDING,
            CredentialReviewRequest.Status.IN_REVIEW,
        ],
    ).first()
    if existing:
        return existing
    try:
        with transaction.atomic():
            review = CredentialReviewRequest.objects.create(
                credential=credential,
                version=credential.revision,
                requested_by=actor,
            )
    except IntegrityError:
        return credential.review_requests.get(
            version=credential.revision,
            status__in=[
                CredentialReviewRequest.Status.PENDING,
                CredentialReviewRequest.Status.IN_REVIEW,
            ],
        )
    credential.review_status = ReviewStatus.PENDING
    credential.save(update_fields=['review_status', 'updated_at'])
    _audit(
        actor,
        credential,
        CredentialAuditEvent.Action.REVIEW_REQUESTED,
        review_request=review,
        metadata={'version': review.version},
    )
    return review


def _require_reviewer(actor, credential):
    if not _can_review(actor):
        raise PermissionDenied('No tienes permiso para revisar credenciales.')
    if actor.pk == credential.user_id:
        raise PermissionDenied('No puedes revisar tu propia credencial.')


@transaction.atomic
def claim_review(actor, review_request):
    review = CredentialReviewRequest.objects.select_for_update().select_related(
        'credential'
    ).get(pk=review_request.pk)
    _require_reviewer(actor, review.credential)
    if review.status == CredentialReviewRequest.Status.IN_REVIEW:
        if review.assigned_reviewer_id == actor.pk:
            return review
        raise ValidationError('La solicitud ya está asignada a otra persona revisora.')
    if review.status != CredentialReviewRequest.Status.PENDING:
        raise ValidationError('La solicitud ya no está pendiente.')
    if review.version != review.credential.revision:
        review.status = CredentialReviewRequest.Status.CANCELLED
        review.save(update_fields=['status', 'updated_at'])
        return review
    review.status = CredentialReviewRequest.Status.IN_REVIEW
    review.assigned_reviewer = actor
    review.save(update_fields=['status', 'assigned_reviewer', 'updated_at'])
    review.credential.review_status = ReviewStatus.IN_REVIEW
    review.credential.save(update_fields=['review_status', 'updated_at'])
    _audit(
        actor,
        review.credential,
        CredentialAuditEvent.Action.REVIEW_CLAIMED,
        review_request=review,
    )
    return review


@transaction.atomic
def decide_review(actor, review_request, *, method, result, source, reason):
    review = CredentialReviewRequest.objects.select_for_update().select_related(
        'credential'
    ).get(pk=review_request.pk)
    credential = UserCredential.objects.select_for_update().get(pk=review.credential_id)
    _require_reviewer(actor, credential)
    if review.status != CredentialReviewRequest.Status.IN_REVIEW or review.assigned_reviewer_id != actor.pk:
        raise PermissionDenied('Toma la solicitud antes de registrar una decisión.')
    if review.version != credential.revision:
        raise ValidationError('Los datos cambiaron después de la solicitud; crea una revisión nueva.')
    if not reason.strip():
        raise ValidationError('El motivo y alcance de la revisión son obligatorios.')
    if method == CredentialVerification.Method.ISSUER_CONFIRMATION and not source.strip():
        raise ValidationError('Indica la fuente de la confirmación del emisor.')
    if (
        method == CredentialVerification.Method.MANUAL_EVIDENCE
        and not credential.evidences.filter(version=review.version).exists()
    ):
        raise ValidationError('La revisión manual de evidencia requiere un archivo de esta versión.')
    if method not in {
        CredentialVerification.Method.MANUAL_EVIDENCE,
        CredentialVerification.Method.ISSUER_CONFIRMATION,
    }:
        raise ValidationError('El método de verificación no está disponible.')
    if result not in CredentialVerification.Result.values:
        raise ValidationError('La decisión seleccionada no es válida.')

    CredentialVerification.objects.create(
        request=review,
        credential=credential,
        version=review.version,
        reviewer=actor,
        reviewer_name=actor.get_full_name() or actor.get_username(),
        method=method,
        source=source.strip(),
        result=result,
        reason=reason.strip(),
        scope=(
            'Confirmación documentada del emisor.'
            if method == CredentialVerification.Method.ISSUER_CONFIRMATION
            else 'Revisión manual de la evidencia aportada; no es una comprobación directa del emisor.'
        ),
    )
    review.status = CredentialReviewRequest.Status.COMPLETED
    review.save(update_fields=['status', 'updated_at'])
    credential.review_status = (
        ReviewStatus.VERIFIED
        if result == CredentialVerification.Result.VERIFIED
        else ReviewStatus.REJECTED
    )
    credential.save(update_fields=['review_status', 'updated_at'])
    _audit(
        actor,
        credential,
        CredentialAuditEvent.Action.REVIEW_DECIDED,
        review_request=review,
        reason=reason.strip(),
        metadata={'result': result, 'method': method, 'version': review.version},
    )
    return review.verification


@transaction.atomic
def archive_credential(actor, credential):
    credential = UserCredential.objects.select_for_update().get(
        pk=credential.pk,
        user=actor,
        origin=UserCredential.Origin.MANUAL,
        archived_at__isnull=True,
    )
    credential.archived_at = timezone.now()
    credential.visibility = 'private'
    credential.save(update_fields=['archived_at', 'visibility', 'updated_at'])
    credential.review_requests.filter(
        status__in=[
            CredentialReviewRequest.Status.PENDING,
            CredentialReviewRequest.Status.IN_REVIEW,
        ]
    ).update(status=CredentialReviewRequest.Status.CANCELLED, updated_at=timezone.now())
    _audit(actor, credential, CredentialAuditEvent.Action.ARCHIVED)
    return credential


@transaction.atomic
def record_revocation(actor, credential, *, revoked_on, source, reason):
    credential = UserCredential.objects.select_for_update().get(pk=credential.pk)
    _require_reviewer(actor, credential)
    if not revoked_on or not source.strip() or not reason.strip():
        raise ValidationError('La fecha, fuente y motivo de la revocación son obligatorios.')
    credential.review_requests.filter(
        status__in=[
            CredentialReviewRequest.Status.PENDING,
            CredentialReviewRequest.Status.IN_REVIEW,
        ]
    ).update(status=CredentialReviewRequest.Status.CANCELLED, updated_at=timezone.now())
    credential.revoked_on = revoked_on
    credential.revocation_source = source.strip()
    credential.save(update_fields=['revoked_on', 'revocation_source', 'updated_at'])
    _audit(
        actor,
        credential,
        CredentialAuditEvent.Action.REVOKED,
        reason=reason.strip(),
        metadata={'revoked_on': revoked_on.isoformat(), 'source': source.strip()},
    )
    return credential


def may_download_evidence(actor, evidence):
    if not getattr(actor, 'is_authenticated', False) or not actor.is_active:
        return False
    if evidence.credential.user_id == actor.pk:
        return True
    return (
        _can_review(actor)
        and evidence.credential.review_requests.filter(
            assigned_reviewer=actor,
            status__in=[
                CredentialReviewRequest.Status.IN_REVIEW,
                CredentialReviewRequest.Status.COMPLETED,
            ],
            version=evidence.version,
        ).exists()
    )
