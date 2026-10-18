from django.contrib.auth import get_user_model
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from .models import Challenge, ChallengeOffer


def available_players(actor):
    return get_user_model().objects.filter(
        is_active=True, user_profile_arenas__onboardingCompleted=True,
    ).exclude(pk=actor.pk)


def search_players(actor, query):
    User = get_user_model()
    return available_players(actor).filter(
        **{f'{User.USERNAME_FIELD}__icontains': query},
    ).order_by(User.USERNAME_FIELD)[:20]


def resolve_player(actor, username):
    # Keep Arenas' exact identity lookup, using the configured identity field.
    return available_players(actor).filter(
        **{get_user_model().USERNAME_FIELD: username},
    ).first()


def private_challenges(user):
    return Challenge.objects.filter(
        Q(creator=user) | Q(opponent=user),
        is_public=False, clash_offers__isnull=False,
    ).select_related('creator', 'opponent', 'proposed_by').distinct()


def open_challenges():
    now = timezone.now()
    latest = ChallengeOffer.objects.filter(challenge_id=OuterRef('pk')).order_by('-version')
    return Challenge.objects.annotate(
        current_category_id=Subquery(latest.values('category_id')[:1]),
        current_scheduled_at=Subquery(latest.values('scheduled_at')[:1]),
    ).filter(
        is_public=True, opponent__isnull=True, status=Challenge.Status.PENDING,
        expires_at__gt=now, creator__is_active=True,
        creator__user_profile_arenas__onboardingCompleted=True,
        clash_offers__pk=Subquery(latest.values('pk')[:1]), clash_offers__expires_at__gt=now,
        clash_offers__scheduled_at__gt=now,
    ).select_related('creator').prefetch_related(
        'clash_offers__category', 'clash_offers__subcategory', 'clash_offers__landscape',
    )
