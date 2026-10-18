"""Persistence helpers for exploration state, independent from Phaser."""

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.ducky_arenas.models import (
    HistoricalEra,
    PlayerMapCheckpoint,
    PlayerEraProgress,
    UserProfileArenas,
    WorldMap,
)

from .exploration import safe_spawn_position, validate_position
from .progression import get_era


class ExplorationStateConflict(Exception):
    """Raised when another request has already changed the player's state."""


class ExplorationStateRateLimited(Exception):
    """Raised when an exploration checkpoint arrives before its configured interval."""

    def __init__(self, retry_after):
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__('Espera un momento antes de volver a guardar la posición.')


@dataclass(frozen=True)
class MapTransitionOutcome:
    profile: UserProfileArenas
    restored_checkpoint: bool


def _coordinate(value, field_name, maximum):
    if isinstance(value, bool):
        raise ValidationError(f'{field_name} debe ser un número válido.')
    try:
        coordinate = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f'{field_name} debe ser un número válido.') from exc

    if (
        not coordinate.is_finite()
        or coordinate < 0
        or coordinate > Decimal(maximum)
    ):
        raise ValidationError('La posición está fuera de los límites del mapa.')
    return coordinate.quantize(Decimal('0.01'))


def _checkpoint_interval_seconds():
    interval_ms = getattr(settings, 'DUCKY_ARENAS_CHECKPOINT_INTERVAL_MS', 5000)
    if type(interval_ms) is not int or interval_ms < 1000:
        raise ValidationError('La frecuencia de guardado del mapa no está configurada.')
    return interval_ms / 1000


def _copy_checkpoint(profile, locked_profile):
    for field in (
        'current_map',
        'position_x',
        'position_y',
        'facing_direction',
        'last_access',
        'exploration_version',
        'updatedAt',
    ):
        setattr(profile, field, getattr(locked_profile, field))


@transaction.atomic
def persist_exploration_state(
    profile,
    world_map,
    x,
    y,
    direction,
    expected_version,
    allow_rapid=False,
):
    """Save a world-coordinate checkpoint using optimistic version control."""
    if type(expected_version) is not int or expected_version < 0:
        raise ValidationError('La versión del estado no es válida.')
    if direction not in UserProfileArenas.ExplorationDirection.values:
        raise ValidationError('La dirección del personaje no es válida.')

    if not isinstance(world_map, WorldMap):
        try:
            world_map = WorldMap.objects.select_related('era').get(pk=world_map)
        except (WorldMap.DoesNotExist, TypeError, ValueError) as exc:
            raise ValidationError('El mapa solicitado no existe.') from exc
    elif world_map.era_id is None:
        raise ValidationError('El mapa solicitado no tiene una época válida.')
    if (
        not world_map.is_available
        or world_map.era_id is None
        or not world_map.era.is_available
    ):
        raise ValidationError('El mapa solicitado no está disponible.')

    state = validate_position(
        profile.user,
        world_map.era.code,
        x,
        y,
        world_map=world_map,
    )
    coordinate_x = _coordinate(state['x'], 'x', world_map.width)
    coordinate_y = _coordinate(state['y'], 'y', world_map.height)

    locked_profile = UserProfileArenas.objects.select_for_update().select_related(
        'current_map',
    ).get(pk=profile.pk)
    if locked_profile.exploration_version != expected_version:
        raise ExplorationStateConflict(
            'El estado de exploración cambió en otra solicitud; vuelve a cargarlo.'
        )
    if locked_profile.current_map_id not in (None, world_map.pk):
        raise ValidationError('Usa la transición autorizada para cambiar de mapa.')
    if locked_profile.current_map_id is None and world_map.era.code != 'PRE':
        raise ValidationError('El primer mapa de exploración debe ser Prehistoria.')

    unchanged = (
        locked_profile.current_map_id == world_map.pk
        and locked_profile.position_x == coordinate_x
        and locked_profile.position_y == coordinate_y
        and locked_profile.facing_direction == direction
    )
    if unchanged:
        PlayerMapCheckpoint.objects.update_or_create(
            profile=locked_profile,
            world_map=world_map,
            defaults={
                'position_x': coordinate_x,
                'position_y': coordinate_y,
                'facing_direction': direction,
            },
        )
        _copy_checkpoint(profile, locked_profile)
        return profile

    now = timezone.now()
    interval = _checkpoint_interval_seconds()
    if not allow_rapid and locked_profile.last_access is not None:
        elapsed = (now - locked_profile.last_access).total_seconds()
        if elapsed < interval:
            raise ExplorationStateRateLimited(interval - elapsed)

    updated = UserProfileArenas.objects.filter(
        pk=locked_profile.pk,
        exploration_version=expected_version,
        current_map_id=locked_profile.current_map_id,
    ).update(
        current_map=world_map,
        position_x=coordinate_x,
        position_y=coordinate_y,
        facing_direction=direction,
        last_access=now,
        exploration_version=F('exploration_version') + 1,
        updatedAt=now,
    )
    if updated != 1:
        raise ExplorationStateConflict(
            'El estado de exploración cambió en otra solicitud; vuelve a cargarlo.'
        )
    PlayerMapCheckpoint.objects.update_or_create(
        profile=locked_profile,
        world_map=world_map,
        defaults={
            'position_x': coordinate_x,
            'position_y': coordinate_y,
            'facing_direction': direction,
        },
    )

    profile.refresh_from_db(
        fields=(
            'current_map',
            'position_x',
            'position_y',
            'facing_direction',
            'last_access',
            'exploration_version',
            'updatedAt',
        )
    )
    return profile


@transaction.atomic
def transition_exploration_map(
    profile,
    target_map,
    source_era_code,
    x,
    y,
    direction,
    expected_version,
):
    """Persist a validated source checkpoint and atomically switch to an unlocked map."""
    if type(expected_version) is not int or expected_version < 0:
        raise ValidationError('La versión del estado no es válida.')
    if direction not in UserProfileArenas.ExplorationDirection.values:
        raise ValidationError('La dirección del personaje no es válida.')
    if not isinstance(target_map, WorldMap):
        raise ValidationError('El mapa de destino no existe.')

    locked_profile = UserProfileArenas.objects.select_for_update().select_related(
        'current_map',
        'current_map__era',
    ).get(pk=profile.pk)
    if locked_profile.exploration_version != expected_version:
        raise ExplorationStateConflict(
            'El estado de exploración cambió en otra solicitud; vuelve a cargarlo.'
        )

    try:
        source_era = get_era(source_era_code)
    except ValidationError:
        raise
    if locked_profile.current_map_id is not None:
        if (
            locked_profile.current_map.era_id is None
            or locked_profile.current_map.era.code != source_era.code
        ):
            raise ExplorationStateConflict(
                'El mapa de origen ya no coincide con el estado guardado.'
            )
        source_map = locked_profile.current_map
    else:
        if source_era.code != 'PRE':
            raise ValidationError('El primer mapa de exploración debe ser Prehistoria.')
        source_map = WorldMap.objects.select_related('era').filter(
            era__code='PRE',
            is_available=True,
            era__is_available=True,
        ).first()
        if source_map is None:
            raise ValidationError('El mapa inicial no está disponible.')

    source_state = validate_position(
        profile.user,
        source_era.code,
        x,
        y,
        world_map=source_map,
    )

    if target_map.era_id is None or not target_map.is_available or not target_map.era.is_available:
        raise ValidationError('El mapa de destino no está disponible.')
    target_era = get_era(target_map.era.code)
    from .progression import unlocked_eras_for_user

    if target_era not in unlocked_eras_for_user(profile.user):
        raise ValidationError('No tienes permiso para explorar esa época.')

    restored_checkpoint = False
    try:
        target_checkpoint = PlayerMapCheckpoint.objects.get(
            profile=locked_profile,
            world_map=target_map,
        )
    except PlayerMapCheckpoint.DoesNotExist:
        target_checkpoint = None

    if (
        target_checkpoint is not None
        and target_checkpoint.facing_direction
        not in UserProfileArenas.ExplorationDirection.values
    ):
        target_checkpoint = None

    if target_checkpoint is not None:
        try:
            target_state = validate_position(
                profile.user,
                target_era.code,
                target_checkpoint.position_x,
                target_checkpoint.position_y,
                world_map=target_map,
            )
        except ValidationError:
            target_checkpoint = None
        else:
            restored_checkpoint = True

    if target_checkpoint is None:
        spawn = safe_spawn_position(target_map)
        target_state = validate_position(
            profile.user,
            target_era.code,
            spawn['x'],
            spawn['y'],
            world_map=target_map,
        )
        target_direction = UserProfileArenas.ExplorationDirection.DOWN
    else:
        target_direction = target_checkpoint.facing_direction

    source_x = _coordinate(source_state['x'], 'x', source_map.width)
    source_y = _coordinate(source_state['y'], 'y', source_map.height)
    PlayerMapCheckpoint.objects.update_or_create(
        profile=locked_profile,
        world_map=source_map,
        defaults={
            'position_x': source_x,
            'position_y': source_y,
            'facing_direction': direction,
        },
    )
    target_x = _coordinate(target_state['x'], 'x', target_map.width)
    target_y = _coordinate(target_state['y'], 'y', target_map.height)
    now = timezone.now()
    updated = UserProfileArenas.objects.filter(
        pk=locked_profile.pk,
        exploration_version=expected_version,
        current_map_id=locked_profile.current_map_id,
    ).update(
        current_map=target_map,
        position_x=target_x,
        position_y=target_y,
        facing_direction=target_direction,
        last_access=now,
        exploration_version=F('exploration_version') + 1,
        updatedAt=now,
    )
    if updated != 1:
        raise ExplorationStateConflict(
            'El estado de exploración cambió en otra solicitud; vuelve a cargarlo.'
        )
    profile.refresh_from_db(
        fields=(
            'current_map',
            'position_x',
            'position_y',
            'facing_direction',
            'last_access',
            'exploration_version',
            'updatedAt',
        )
    )
    PlayerMapCheckpoint.objects.update_or_create(
        profile=locked_profile,
        world_map=target_map,
        defaults={
            'position_x': target_x,
            'position_y': target_y,
            'facing_direction': target_direction,
        },
    )
    return MapTransitionOutcome(
        profile=profile,
        restored_checkpoint=restored_checkpoint,
    )


@transaction.atomic
def synchronize_era_progress(profile, xp_total, visited_era_code=None):
    """Record server-authorized unlocks and visits without storing XP."""
    if type(xp_total) is not int or xp_total < 0:
        raise ValidationError('Los puntos acumulados deben ser un entero no negativo.')

    now = timezone.now()
    locked_profile = UserProfileArenas.objects.select_for_update().get(pk=profile.pk)
    previously_unlocked_ids = set(
        PlayerEraProgress.objects.filter(
            profile=locked_profile,
            unlocked_at__isnull=False,
        ).values_list('era_id', flat=True)
    )
    earned_eras = list(
        HistoricalEra.objects.filter(required_xp__lte=xp_total).order_by('order')
    )
    newly_unlocked_eras = [
        era for era in earned_eras if era.pk not in previously_unlocked_ids
    ]
    newly_unlocked_ids = {era.pk for era in earned_eras}
    unlocked_ids = newly_unlocked_ids | previously_unlocked_ids
    allowed_codes = set(
        HistoricalEra.objects.filter(pk__in=unlocked_ids).values_list('code', flat=True)
    )
    if visited_era_code is not None and visited_era_code not in allowed_codes:
        raise ValidationError('No tienes permiso para explorar esa época.')
    if visited_era_code is not None and not HistoricalEra.objects.filter(
        code=visited_era_code,
        is_available=True,
        maps__is_available=True,
    ).exists():
        raise ValidationError('El mapa de esa época no está disponible.')

    PlayerEraProgress.objects.bulk_create(
        [
            PlayerEraProgress(
                profile=locked_profile,
                era=era,
                unlocked_at=now,
                first_visited_at=now if era.code == visited_era_code else None,
                last_visited_at=now if era.code == visited_era_code else None,
            )
            for era in newly_unlocked_eras
        ],
        ignore_conflicts=True,
    )
    progress = PlayerEraProgress.objects.filter(
        profile=locked_profile,
        era_id__in=unlocked_ids,
    )
    progress.filter(unlocked_at__isnull=True).update(
        unlocked_at=now,
        updated_at=now,
    )
    if visited_era_code is not None:
        PlayerEraProgress.objects.filter(
            profile=locked_profile,
            era__code=visited_era_code,
        ).update(
            first_visited_at=Coalesce('first_visited_at', Value(now)),
            last_visited_at=now,
            updated_at=now,
        )


def pending_evolution_notices(profile):
    return (
        PlayerEraProgress.objects.filter(
            profile=profile,
            unlocked_at__isnull=False,
            evolution_notified_at__isnull=True,
        )
        .exclude(era__code='PRE')
        .select_related('era')
        .order_by('era__order')
    )


@transaction.atomic
def acknowledge_evolution_notice(profile, era_code):
    if not isinstance(era_code, str):
        raise ValidationError('La época de la notificación no es válida.')

    normalized_code = era_code.upper()
    progress = PlayerEraProgress.objects.select_for_update().filter(
        profile=profile,
        era__code=normalized_code,
        unlocked_at__isnull=False,
    )
    if not progress.exists():
        raise ValidationError('La época no está desbloqueada para este jugador.')

    return progress.filter(evolution_notified_at__isnull=True).update(
        evolution_notified_at=timezone.now(),
        updated_at=timezone.now(),
    ) == 1
