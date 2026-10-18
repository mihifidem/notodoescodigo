"""Read-only era progression backed by EcoMotor and the configured era catalog."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from apps.ducky_arenas.models import HistoricalEra, UserProfileArenas

from .ecomotor_progress import get_progress_snapshot_or_none


@dataclass(frozen=True)
class Era:
    code: str
    label: str
    minimum_xp: int


ERAS = (
    # Compatibility catalog for callers outside Duki Arenas Core. Core reads the
    # current requirements from HistoricalEra, not these default values.
    Era('PRE', 'Prehistoria', 0),
    Era('GRE', 'Grecia', 1000),
    Era('ROM', 'Roma', 2500),
    Era('MED', 'Medieval', 5000),
    Era('REN', 'Renacimiento', 9000),
    Era('REV', 'Revolución industrial', 15000),
    Era('SXX', 'Siglo XX', 22000),
    Era('ACT', 'Actualidad', 30000),
)


_NO_SNAPSHOT = object()


def configured_eras() -> tuple[Era, ...]:
    records = HistoricalEra.objects.order_by('order')
    configured = tuple(
        Era(code=row.code, label=row.name, minimum_xp=row.required_xp)
        for row in records
    )
    return configured or ERAS


def era_for_xp(xp: int) -> Era:
    if type(xp) is not int or xp < 0:
        raise ValidationError('Los XP acumulados deben ser un entero no negativo.')
    eras = configured_eras()
    return next(
        (era for era in reversed(eras) if xp >= era.minimum_xp),
        eras[0],
    )


def current_era_for_user(user, progress_snapshot=_NO_SNAPSHOT) -> Era:
    if progress_snapshot is _NO_SNAPSHOT:
        progress_snapshot = get_progress_snapshot_or_none(user)
    if progress_snapshot is not None:
        return era_for_xp(progress_snapshot.xp_total)

    unlocked = unlocked_eras_for_user(user, progress_snapshot=None)
    try:
        profile = user.user_profile_arenas
    except UserProfileArenas.DoesNotExist:
        profile = None
    if profile is not None and profile.current_map_id and profile.current_map.era_id:
        saved_era = next(
            (era for era in unlocked if era.code == profile.current_map.era.code),
            None,
        )
        if saved_era is not None:
            return saved_era
    if unlocked:
        return max(unlocked, key=lambda era: era.minimum_xp)
    return configured_eras()[0]


def unlocked_eras_for_user(user, progress_snapshot=_NO_SNAPSHOT) -> tuple[Era, ...]:
    if progress_snapshot is _NO_SNAPSHOT:
        progress_snapshot = get_progress_snapshot_or_none(user)
    xp = progress_snapshot.xp_total if progress_snapshot is not None else None
    try:
        profile = user.user_profile_arenas
    except UserProfileArenas.DoesNotExist:
        profile = None
    recorded_codes = (
        set(
            profile.era_progress.filter(
                unlocked_at__isnull=False,
            ).values_list('era__code', flat=True)
        )
        if profile is not None
        else set()
    )
    return tuple(
        era
        for era in configured_eras()
        if (
            (xp is not None and xp >= era.minimum_xp)
            or era.code in recorded_codes
            or (xp is None and era.code == 'PRE')
        )
    )


def next_era_for_xp(xp: int) -> Era | None:
    if type(xp) is not int or xp < 0:
        raise ValidationError('Los XP acumulados deben ser un entero no negativo.')
    return next(
        (era for era in configured_eras() if era.minimum_xp > xp),
        None,
    )


def get_era(code: str) -> Era:
    if not isinstance(code, str):
        raise ValidationError('La época solicitada no existe.')
    normalized = code.upper()
    for era in configured_eras():
        if era.code == normalized:
            return era
    raise ValidationError('La época solicitada no existe.')
