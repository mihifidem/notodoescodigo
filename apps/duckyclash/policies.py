from django.core.exceptions import PermissionDenied

from apps.ducky_arenas.battle_challenges import require_player


def is_clash_staff(user):
    return user.is_authenticated and user.is_active and user.is_staff


def require_participant(user, agreement):
    require_player(user)
    if user.pk not in (agreement.creator_id, agreement.opponent_id):
        raise PermissionDenied('Solo los participantes pueden acceder al Ring.')


def can_view_challenge(user, challenge):
    if not user.is_authenticated or not user.is_active:
        return False
    if is_clash_staff(user) or user.pk in (challenge.creator_id, challenge.opponent_id):
        return True
    return (
        challenge.is_public and challenge.opponent_id is None
        and challenge.status == challenge.Status.PENDING and not challenge.is_expired
    )
