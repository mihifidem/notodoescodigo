from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import BattleChallenge, UserProfileArenas


def require_player(user):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied
    if not UserProfileArenas.objects.filter(user=user, onboardingCompleted=True).exists():
        raise PermissionDenied('Crea tu Ducky antes de gestionar retos.')


def visible_challenges(user):
    return BattleChallenge.objects.filter(
        Q(creator=user) | Q(opponent=user) |
        Q(is_public=True, opponent__isnull=True, status=BattleChallenge.Status.PENDING, expires_at__gt=timezone.now()),
    ).select_related('creator', 'opponent', 'proposed_by')


@transaction.atomic
def create_challenge(*, actor, opponent, is_public, stake, lifetime):
    require_player(actor)
    if opponent is not None:
        require_player(opponent)
        if actor.pk == opponent.pk:
            raise ValidationError('No puedes retarte a ti mismo.')
    if (is_public and opponent is not None) or (not is_public and opponent is None):
        raise ValidationError('Selecciona un rival solo para los retos directos.')
    if isinstance(stake, bool) or not isinstance(stake, int) or not 0 <= stake <= 1000000:
        raise ValidationError('La apuesta debe ser un entero entre 0 y 1000000 DuckyCoins.')
    if lifetime not in (15, 30, 60):
        raise ValidationError('Selecciona una caducidad válida.')
    return BattleChallenge.objects.create(
        creator=actor, opponent=opponent, proposed_by=actor, is_public=is_public,
        stake=stake, expires_at=timezone.now() + timedelta(minutes=lifetime),
    )


@transaction.atomic
def respond_to_challenge(*, actor, challenge_id, revision, action, stake=None):
    require_player(actor)
    challenge = get_object_or_404(visible_challenges(actor).select_for_update(), pk=challenge_id)
    if challenge.clash_offers.exists():
        raise ValidationError('Este reto pertenece a DuckyClash. No se puede modificar desde Battle Arenas.')
    now = timezone.now()
    if challenge.status != BattleChallenge.Status.PENDING:
        raise ValidationError('Este reto ya se ha resuelto.')
    if challenge.expires_at <= now:
        raise ValidationError('El reto ha caducado. Crea uno nuevo.')
    if challenge.revision != revision:
        raise ValidationError('El reto ha cambiado. Revisa la propuesta actual antes de responder.')
    if action != 'cancel':
        require_player(challenge.creator)
        if challenge.opponent is not None:
            require_player(challenge.opponent)
    if action == 'cancel':
        if challenge.creator_id != actor.pk:
            raise PermissionDenied('Solo el creador puede cancelar el reto.')
        updates = {'status': BattleChallenge.Status.CANCELLED}
    else:
        if challenge.proposed_by_id == actor.pk:
            raise PermissionDenied('No puedes responder a tu propia propuesta.')
        if challenge.opponent_id is None:
            if action == 'decline':
                raise PermissionDenied('No puedes rechazar un reto público de otro jugador.')
        elif actor.pk not in (challenge.creator_id, challenge.opponent_id):
            raise PermissionDenied
        if action == 'accept':
            updates = {'status': BattleChallenge.Status.ACCEPTED}
        elif action == 'decline':
            updates = {'status': BattleChallenge.Status.DECLINED}
        elif action == 'counter':
            if isinstance(stake, bool) or not isinstance(stake, int) or not 0 <= stake <= 1000000:
                raise ValidationError('La contrapropuesta debe ser un entero entre 0 y 1000000 DuckyCoins.')
            if stake == challenge.stake:
                raise ValidationError('La contrapropuesta debe ser distinta de la apuesta actual.')
            updates = {'stake': stake, 'proposed_by_id': actor.pk}
        else:
            raise ValidationError('Acción de reto no válida.')
        if challenge.opponent_id is None:
            updates['opponent_id'] = actor.pk
    now = timezone.now()
    updates.update(revision=challenge.revision + 1, updated_at=now)
    changed = BattleChallenge.objects.filter(
        pk=challenge.pk, revision=revision, status=BattleChallenge.Status.PENDING,
        expires_at__gt=now,
    ).update(**updates)
    if changed != 1:
        raise ValidationError('Otro jugador ha actualizado el reto. Recarga la pantalla.')
    challenge.refresh_from_db()
    return challenge
