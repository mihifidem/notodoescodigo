import hashlib
import json
import uuid
from copy import deepcopy
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.users.access import can
from apps.curriculum.letter_forms import PROFILE_SECTIONS
from apps.curriculum.models import (
    CVProfile, CoverLetter, LetterConfiguration, LetterGeneration, LetterOffer, LetterVersion,
)
from .profiles import snapshot_profile


REVIEW_SALT = 'curriculum.letter-review.v1'
ENERGY_TOOL = 'letters-generate'
UNAVAILABLE_MESSAGE = (
    'Generación no disponible: la herramienta no tiene proveedor IA ni coste energético aprobado en '
    'Energía Ducky. No se ha enviado ningún dato, realizado ningún cargo ni reservado energía. '
    'Puedes editar y descargar tu carta manualmente.'
)


class LetterConflict(Exception):
    pass


class LetterRateLimited(Exception):
    pass


def current_configuration():
    configuration = LetterConfiguration.objects.first()
    if configuration is None:
        raise ValidationError('Falta aplicar la migración de configuración de cartas.')
    return configuration


def require_owner(actor, resource):
    if not can(actor, 'view_owned_resource', resource).allowed:
        raise PermissionDenied('La carta o el perfil no pertenece a tu cuenta activa.')


def generation_availability(configuration, user=None):
    from apps.energy.services import module_gate

    gate = module_gate(user, ENERGY_TOOL)
    return {
        'available': False,
        'reason': gate['reason'],
        'message': gate['message'],
        'energy_cost': gate['energy_cost'],
        'configured': configuration.generation_enabled,
    }


def _lock_actor(actor):
    if not can(actor, 'view_registered_content').allowed:
        raise PermissionDenied('Se requiere una cuenta activa.')
    get_user_model().objects.select_for_update().get(pk=actor.pk)


def _check_frequency(actor, configuration):
    since = timezone.now() - timedelta(minutes=1)
    count = LetterVersion.objects.filter(author=actor, created_at__gte=since).count()
    count += LetterGeneration.objects.filter(user=actor, created_at__gte=since).count()
    if count >= configuration.requests_per_minute:
        raise LetterRateLimited('Demasiadas acciones seguidas. Espera un minuto y vuelve a intentarlo.')


def prepare_review(actor, cleaned_data, configuration):
    profile = cleaned_data['profile']
    require_owner(actor, profile)
    raw = snapshot_profile(profile)
    fields = set(cleaned_data['sections'])
    selected = {key: value for key, value in raw.items() if key in fields}
    payload = {
        'schema_version': 1,
        'user_id': actor.pk,
        'profile_id': profile.pk,
        'profile_name': profile.nombre,
        'branch': profile.rama_principal.name if profile.rama_principal_id else '',
        'profile_updated_at': profile.updated_at.isoformat(),
        'configuration_id': configuration.pk,
        'configuration_version': configuration.version,
        'creation_key': str(uuid.uuid4()),
        'profile_data': selected,
        'title': cleaned_data['title'],
        'offer': {
            'original_text': cleaned_data['offer_text'],
            'url': cleaned_data['offer_url'],
            'position': cleaned_data['position'],
            'company': cleaned_data['company'],
        },
        'options': {
            key: cleaned_data[key]
            for key in ('language', 'tone', 'length', 'recipient', 'highlights', 'notes')
        },
    }
    if len(json.dumps(payload)) > 150000:
        raise ValidationError('Los datos seleccionados son demasiado extensos. Excluye secciones y revisa de nuevo.')
    warnings = []
    if not selected.get('name') or not selected.get('profession'):
        warnings.append('Falta nombre o titular autorizado: podrás continuar con un alcance reducido, sin inventarlos.')
    if not selected.get('experiences'):
        warnings.append('Sin experiencia seleccionada. No se inventarán puestos ni años de experiencia.')
    sections = [
        {'label': label, 'value': json.dumps(selected[key], ensure_ascii=False, indent=2)
         if isinstance(selected[key], list) else selected[key]}
        for key, label in PROFILE_SECTIONS if key in selected
    ]
    return payload, signing.dumps(payload, salt=REVIEW_SALT, compress=True), warnings, sections


def read_review(token, actor):
    try:
        payload = signing.loads(token, salt=REVIEW_SALT, max_age=1800)
    except signing.BadSignature as error:
        raise ValidationError('La revisión ha caducado o no es válida. Revisa los datos de nuevo.') from error
    if payload['user_id'] != actor.pk:
        raise PermissionDenied('La revisión pertenece a otra cuenta.')
    return payload


@transaction.atomic
def create_letter(actor, token):
    payload = read_review(token, actor)
    _lock_actor(actor)
    previous = CoverLetter.objects.filter(
        user=actor, creation_key=payload['creation_key'],
    ).first()
    if previous:
        return previous
    configuration = current_configuration()
    if not configuration.available:
        raise ValidationError('La creación de cartas está desactivada. Tus documentos siguen accesibles.')
    if configuration.pk != payload['configuration_id']:
        raise LetterConflict('La configuración ha cambiado. Revisa de nuevo antes de crear la carta.')
    _check_frequency(actor, configuration)
    profile = CVProfile.objects.filter(pk=payload['profile_id'], user=actor).exclude(
        status=CVProfile.Status.ARCHIVED,
    ).first()
    if profile is None:
        raise ValidationError('El perfil seleccionado ya no está disponible. Selecciona otro explícitamente.')
    offer = LetterOffer.objects.create(**payload['offer'])
    letter = CoverLetter.objects.create(
        user=actor, profile=profile, offer=offer, title=payload['title'],
        language=payload['options']['language'], tone=payload['options']['tone'],
        length=payload['options']['length'], reviewed_snapshot=payload,
        creation_key=payload['creation_key'], configuration=configuration,
    )
    LetterVersion.objects.create(letter=letter, number=1, content='', author=actor)
    return letter


@transaction.atomic
def save_version(letter, actor, *, title, content, expected_revision):
    _lock_actor(actor)
    require_owner(actor, letter)
    if letter.status == CoverLetter.Status.ARCHIVED:
        raise LetterConflict('Restaura la carta antes de editarla.')
    _check_frequency(actor, current_configuration())
    if len(content) > current_configuration().max_content_length or not content.strip():
        raise ValidationError('El contenido está vacío o supera el límite configurado.')
    updated = CoverLetter.objects.filter(
        pk=letter.pk, user=actor, revision=expected_revision, status=CoverLetter.Status.DRAFT,
    ).update(title=title, revision=expected_revision + 1, updated_at=timezone.now())
    if not updated:
        raise LetterConflict('Otra pestaña ha cambiado la carta. Tu texto no se ha guardado; cópialo antes de recargar.')
    previous = letter.versions.first()
    return LetterVersion.objects.create(
        letter=letter, number=expected_revision + 1, content=content, author=actor, previous=previous,
    )


@transaction.atomic
def change_status(letter, actor, expected_revision, status):
    _lock_actor(actor)
    require_owner(actor, letter)
    if not CoverLetter.objects.filter(
        pk=letter.pk, user=actor, revision=expected_revision,
    ).update(status=status, revision=expected_revision + 1, updated_at=timezone.now()):
        raise LetterConflict('La carta ha cambiado. Recarga antes de repetir la acción.')


@transaction.atomic
def duplicate_letter(letter, actor, expected_revision):
    _lock_actor(actor)
    require_owner(actor, letter)
    source = CoverLetter.objects.get(pk=letter.pk, user=actor)
    if source.revision != expected_revision:
        raise LetterConflict('La carta ha cambiado. Recarga antes de duplicarla.')
    configuration = current_configuration()
    if not configuration.available:
        raise ValidationError('La creación de cartas está desactivada.')
    _check_frequency(actor, configuration)
    offer = source.offer
    copy_offer = LetterOffer.objects.create(
        original_text=offer.original_text, url=offer.url, company=offer.company, position=offer.position,
    )
    clone = CoverLetter.objects.create(
        user=actor, profile=source.profile, offer=copy_offer, configuration=source.configuration,
        title=f'Copia de {source.title}'[:200], language=source.language, tone=source.tone,
        length=source.length, reviewed_snapshot=deepcopy(source.reviewed_snapshot),
    )
    LetterVersion.objects.create(
        letter=clone, number=1, content=source.versions.first().content, author=actor,
    )
    return clone


@transaction.atomic
def delete_letter(letter, actor, expected_revision):
    _lock_actor(actor)
    require_owner(actor, letter)
    source = CoverLetter.objects.get(pk=letter.pk, user=actor)
    if source.revision != expected_revision:
        raise LetterConflict('La carta ha cambiado. Recarga antes de eliminarla.')
    # Blocked operations have no reservation, provider job or financial movement to reconcile.
    if source.operations.exclude(status='blocked').exists():
        raise ValidationError('Hay operaciones pendientes de conciliación. Archiva la carta mientras se resuelven.')
    source.delete()


@transaction.atomic
def request_generation(letter, actor, *, key, expected_revision):
    _lock_actor(actor)
    require_owner(actor, letter)
    fingerprint = hashlib.sha256(
        f'letter.generate.v1:{letter.pk}:{expected_revision}'.encode(),
    ).hexdigest()
    previous = LetterGeneration.objects.filter(user=actor, idempotency_key=key).first()
    if previous:
        if previous.request_fingerprint != fingerprint:
            raise LetterConflict('La clave de la operación ya pertenece a otra solicitud.')
        return previous
    source = CoverLetter.objects.get(pk=letter.pk, user=actor)
    if source.revision != expected_revision or source.status == CoverLetter.Status.ARCHIVED:
        raise LetterConflict('La carta ha cambiado o está archivada. Revisa antes de generar.')
    configuration = current_configuration()
    _check_frequency(actor, configuration)
    availability = generation_availability(configuration, actor)
    return LetterGeneration.objects.create(
        user=actor, letter=source, idempotency_key=key, request_fingerprint=fingerprint,
        error_code=availability['reason'], configuration_version=source.configuration.version,
        input_snapshot=source.reviewed_snapshot,
    )
