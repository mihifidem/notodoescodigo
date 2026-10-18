from datetime import datetime

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ducky_arenas.battle_challenges import require_player
from apps.ducky_arenas.services import calculate_effective_stats
from .bank import require_balance
from .models import Challenge, ChallengeAgreement, ChallengeOffer, Duel, ParticipantState
from .notifications import challenge_notice, deliver
from .rules import current_rules, validate_offer_limits
from .policies import is_clash_staff


def create_challenge(*, actor, opponent, is_public, category, subcategory, landscape,
                     scheduled_at, expires_at, time_zone, stake, evaluated_at=None):
    require_player(actor)
    if opponent is not None:
        require_player(opponent)
        if actor.pk == opponent.pk:
            raise ValidationError('No puedes retarte a ti mismo.')
    if (is_public and opponent is not None) or (not is_public and opponent is None):
        raise ValidationError('Un reto privado necesita rival; un reto público no lo preasigna.')
    now = timezone.now()
    evaluated_at = now if evaluated_at is None else evaluated_at
    if not isinstance(evaluated_at, datetime) or not timezone.is_aware(evaluated_at) or evaluated_at > now:
        raise ValidationError('La hora de recepción del servidor no es válida.')
    validate_offer_limits(stake=stake, expires_at=expires_at, evaluated_at=evaluated_at)
    challenge = Challenge(
        creator=actor, opponent=opponent, proposed_by=actor,
        is_public=is_public, stake=stake, expires_at=expires_at,
    )
    challenge.full_clean()
    require_balance(actor, stake)
    return persist_challenge(
        challenge=challenge, category=category, subcategory=subcategory, landscape=landscape,
        scheduled_at=scheduled_at, expires_at=expires_at, time_zone=time_zone, stake=stake,
        evaluated_at=evaluated_at,
    )


@transaction.atomic
def persist_challenge(*, challenge, category, subcategory, landscape,
                      scheduled_at, expires_at, time_zone, stake, evaluated_at=None):
    validate_offer_limits(stake=stake, expires_at=expires_at, evaluated_at=evaluated_at)
    require_player(challenge.creator)
    if challenge.opponent_id:
        require_player(challenge.opponent)
    challenge.save()
    ChallengeOffer.objects.create(
        challenge=challenge, version=1, proposed_by=challenge.creator,
        category=category, subcategory=subcategory, landscape=landscape,
        scheduled_at=scheduled_at, expires_at=expires_at, time_zone=time_zone, stake=stake,
    )
    if challenge.opponent_id:
        notify(challenge.opponent, challenge, 'Nuevo reto privado', f'{challenge.creator.get_username()} te ha enviado un reto.')
    return challenge


def notify(recipient, challenge, title, message):
    challenge_notice(recipient, challenge, title, message)


def pending_offer(*, actor, challenge_id, version, revision, private_only=False, lock=True):
    require_player(actor)
    if (isinstance(version, bool) or not isinstance(version, int) or version < 1
            or isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
        raise ValidationError('La versión y la revisión deben ser enteros válidos.')
    queryset = Challenge.objects.select_for_update() if lock else Challenge.objects.all()
    try:
        challenge = queryset.get(pk=challenge_id)
    except Challenge.DoesNotExist as exc:
        raise ValidationError('El reto ya no está disponible.') from exc
    if private_only and (challenge.is_public or challenge.opponent_id is None):
        raise PermissionDenied('Esta operación solo admite retos privados.')
    if actor.pk == challenge.proposed_by_id:
        raise PermissionDenied('No puedes responder a tu propia propuesta.')
    if challenge.opponent_id is not None and actor.pk not in (challenge.creator_id, challenge.opponent_id):
        raise PermissionDenied('Solo los participantes pueden responder a este reto.')
    require_player(challenge.creator)
    if challenge.opponent_id:
        require_player(challenge.opponent)
    if challenge.status != Challenge.Status.PENDING or challenge.revision != revision:
        raise ValidationError('El reto ha cambiado o ya no está pendiente.')
    offer = challenge.clash_offers.order_by('-version').first()
    if offer is None or offer.version != version:
        raise ValidationError('La versión de la oferta no es la vigente.')
    now = timezone.now()
    if min(offer.expires_at, challenge.expires_at) <= now or offer.scheduled_at <= now:
        raise ValidationError('La oferta ha vencido.')
    return challenge, offer


def update_pending(challenge, revision, **updates):
    now = timezone.now()
    changed = Challenge.objects.filter(
        pk=challenge.pk, status=Challenge.Status.PENDING,
        revision=revision, expires_at__gt=now,
    ).update(**updates, revision=revision + 1, updated_at=now)
    if changed != 1:
        raise ValidationError('Otro jugador ha actualizado el reto o ha vencido. Recarga la pantalla.')
    challenge.refresh_from_db()


def accept_offer(*, actor, challenge_id, version, revision):
    challenge, offer = pending_offer(
        actor=actor, challenge_id=challenge_id, version=version, revision=revision, lock=False,
    )
    require_balance(challenge.creator, offer.stake)
    require_balance(challenge.opponent or actor, offer.stake)
    return confirm_acceptance(actor=actor, challenge_id=challenge_id, version=version, revision=revision)


@transaction.atomic
def confirm_acceptance(*, actor, challenge_id, version, revision):
    # Recheck after bank I/O: neither a stale balance query nor a race claims a slot.
    challenge, offer = pending_offer(
        actor=actor, challenge_id=challenge_id, version=version, revision=revision,
    )
    opponent_id = challenge.opponent_id or actor.pk
    update_pending(
        challenge, revision,
        opponent_id=opponent_id, stake=offer.stake, status=Challenge.Status.ACCEPTED,
    )
    agreement = ChallengeAgreement.objects.create(
        challenge=challenge, offer=offer, creator=challenge.creator,
        opponent=challenge.opponent,
    )
    rules = current_rules()
    duel = Duel.objects.create(
        agreement=agreement, initial_hp=rules['initial_hp'],
        rules_snapshot=rules, rules_version=rules['version'],
        payment_gateway=getattr(settings, 'DUCKYCLASH_PAYMENT_GATEWAY', ''),
    )
    for seat, player in ((1, agreement.creator), (2, agreement.opponent)):
        ParticipantState.objects.create(
            duel=duel, player=player, seat=seat,
            hp=duel.initial_hp, max_hp=duel.initial_hp,
            stats_snapshot=calculate_effective_stats(player.user_profile_arenas),
        )
    for player in (agreement.creator, agreement.opponent):
        notify(player, challenge, 'Reto abierto aceptado' if challenge.is_public else 'Reto aceptado',
               'La oferta ha sido aceptada. El servidor debe verificar ambas reservas antes de confirmar el duelo.')
    from .economy import enqueue_local_reconciliation
    enqueue_local_reconciliation(duel.pk)
    return duel


def counter_offer(*, actor, challenge_id, version, revision, scheduled_at, stake):
    challenge, previous = pending_offer(
        actor=actor, challenge_id=challenge_id, version=version, revision=revision, private_only=True, lock=False,
    )
    validate_offer_limits(stake=stake)
    if previous.scheduled_at == scheduled_at and previous.stake == stake:
        raise ValidationError('Cambia la fecha, la hora o la apuesta para contraofertar.')
    require_balance(actor, stake)
    return persist_counter(
        actor=actor, challenge_id=challenge_id, version=version, revision=revision,
        scheduled_at=scheduled_at, stake=stake,
    )


@transaction.atomic
def persist_counter(*, actor, challenge_id, version, revision, scheduled_at, stake):
    validate_offer_limits(stake=stake)
    challenge, previous = pending_offer(
        actor=actor, challenge_id=challenge_id, version=version, revision=revision, private_only=True,
    )
    offer = ChallengeOffer(
        challenge=challenge, version=previous.version + 1, proposed_by=actor,
        category=previous.category, subcategory=previous.subcategory, landscape=previous.landscape,
        scheduled_at=scheduled_at, expires_at=previous.expires_at,
        time_zone=previous.time_zone, stake=stake,
    )
    offer.full_clean()
    update_pending(challenge, revision, proposed_by_id=actor.pk, stake=stake)
    offer.save()
    recipient = challenge.opponent if actor.pk == challenge.creator_id else challenge.creator
    notify(recipient, challenge, 'Nueva contrapropuesta', f'{actor.get_username()} propone nuevas condiciones (versión {offer.version}).')
    return offer


@transaction.atomic
def decline_offer(*, actor, challenge_id, version, revision):
    challenge, offer = pending_offer(
        actor=actor, challenge_id=challenge_id, version=version, revision=revision, private_only=True,
    )
    update_pending(challenge, revision, status=Challenge.Status.DECLINED)
    notify(offer.proposed_by, challenge, 'Reto rechazado', f'{actor.get_username()} ha rechazado la oferta vigente.')
    return challenge


def owned_pending_offer(*, actor, challenge_id, version, revision, lock=True, allow_staff=False):
    staff_deletion = allow_staff and is_clash_staff(actor)
    if not staff_deletion:
        require_player(actor)
    if (type(version) is not int or version < 1
            or type(revision) is not int or revision < 0):
        raise ValidationError('La versión y la revisión deben ser enteros válidos.')
    queryset = Challenge.objects.select_for_update() if lock else Challenge.objects.all()
    try:
        challenge = queryset.get(pk=challenge_id)
    except Challenge.DoesNotExist as exc:
        raise ValidationError('El reto ya no está disponible.') from exc
    if challenge.creator_id != actor.pk and not staff_deletion:
        raise PermissionDenied('Solo el creador puede gestionar este reto.')
    offer = challenge.clash_offers.order_by('-version').first()
    now = timezone.now()
    if (challenge.status != Challenge.Status.PENDING or challenge.revision != revision
            or offer is None or offer.version != version
            or min(challenge.expires_at, offer.expires_at, offer.scheduled_at) <= now
            or ChallengeAgreement.objects.filter(challenge=challenge).exists()):
        raise ValidationError('El reto ha cambiado, ha vencido o ya está aceptado. Recarga la pantalla.')
    return challenge, offer


def edit_challenge(*, actor, challenge_id, version, revision, category, subcategory, landscape,
                   scheduled_at, expires_at, time_zone, stake, evaluated_at=None):
    owned_pending_offer(
        actor=actor, challenge_id=challenge_id, version=version, revision=revision, lock=False,
    )
    validate_offer_limits(stake=stake, expires_at=expires_at, evaluated_at=evaluated_at)
    require_balance(actor, stake)
    return persist_challenge_edit(
        actor=actor, challenge_id=challenge_id, version=version, revision=revision,
        category=category, subcategory=subcategory, landscape=landscape, scheduled_at=scheduled_at,
        expires_at=expires_at, time_zone=time_zone, stake=stake, evaluated_at=evaluated_at,
    )


@transaction.atomic
def persist_challenge_edit(*, actor, challenge_id, version, revision, category, subcategory, landscape,
                           scheduled_at, expires_at, time_zone, stake, evaluated_at=None):
    challenge, previous = owned_pending_offer(
        actor=actor, challenge_id=challenge_id, version=version, revision=revision,
    )
    validate_offer_limits(stake=stake, expires_at=expires_at, evaluated_at=evaluated_at)
    offer = ChallengeOffer(
        challenge=challenge, version=previous.version + 1, proposed_by=actor,
        category=category, subcategory=subcategory, landscape=landscape,
        scheduled_at=scheduled_at, expires_at=expires_at, time_zone=time_zone, stake=stake,
    )
    offer.full_clean()
    update_pending(challenge, revision, proposed_by_id=actor.pk, stake=stake, expires_at=expires_at)
    offer.save()
    if challenge.opponent_id:
        notify(challenge.opponent, challenge, 'Reto actualizado',
               f'{actor.get_username()} ha cambiado las condiciones. Revisa la versión {offer.version} antes de aceptar.')
    return challenge


@transaction.atomic
def delete_challenge(*, actor, challenge_id, version, revision):
    from django.urls import reverse
    challenge, offer = owned_pending_offer(
        actor=actor, challenge_id=challenge_id, version=version, revision=revision, allow_staff=True,
    )
    # Claim the same revision used by acceptance before removing any pending data.
    update_pending(challenge, revision, status=Challenge.Status.CANCELLED)
    if is_clash_staff(actor):
        from django.contrib.admin.models import DELETION, LogEntry
        from django.contrib.contenttypes.models import ContentType
        LogEntry.objects.log_action(
            user_id=actor.pk, content_type_id=ContentType.objects.get_for_model(Challenge).pk,
            object_id=challenge.pk, object_repr=f'Reto DuckyClash #{challenge.pk}',
            action_flag=DELETION,
            change_message=f'Borrado de reto pendiente; creador={challenge.creator_id}; versión={offer.version}.',
        )
    recipients = {challenge.creator_id, challenge.opponent_id} - {None, actor.pk}
    for recipient_id in recipients:
        deliver(
            recipient_id, f'challenge:{challenge.pk}:deleted:{offer.version}',
            'Reto eliminado', f'{actor.get_username()} ha eliminado el reto pendiente #{challenge.pk}.',
            reverse('duckyclash:invitations_received'),
        )
    challenge.clash_offers.all().delete()
    challenge.delete()
