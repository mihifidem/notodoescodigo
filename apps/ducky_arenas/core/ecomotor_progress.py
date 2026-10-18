"""Read-only adapter for EcoMotor-owned player progression and attributes."""

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.dateparse import parse_datetime

logger = logging.getLogger(__name__)

ATTRIBUTE_CODES = ('attack', 'defense', 'speed', 'intelligence', 'logic', 'quack')


class EcoMotorProgressUnavailable(Exception):
    """EcoMotor progression cannot be read or its response is invalid."""


@dataclass(frozen=True)
class ProgressSnapshot:
    user_id: str
    xp_total: int
    attributes: dict[str, int] | None
    revision: int
    updated_at: datetime
    simulated: bool = False


class EcoMotorProgressProvider(Protocol):
    def get_player_progress(self, user_id: str) -> ProgressSnapshot | Mapping[str, object]:
        """Return the current authoritative XP and optional attribute snapshot."""


def _cache_key(user_id):
    provider = getattr(settings, 'DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER', '') or 'unconfigured'
    provider_id = hashlib.sha256(provider.encode('utf-8')).hexdigest()[:12]
    return f'ducky-arenas:ecomotor-progress:{provider_id}:{user_id}'


def _unavailable_key(user_id):
    return f'{_cache_key(user_id)}:unavailable'


def invalidate_progress_snapshot(user_id):
    cache.delete(_cache_key(user_id))
    cache.delete(_unavailable_key(user_id))


def _normalize_snapshot(raw, expected_user_id):
    if isinstance(raw, ProgressSnapshot):
        values = {
            'user_id': raw.user_id,
            'xp_total': raw.xp_total,
            'attributes': raw.attributes,
            'revision': raw.revision,
            'updated_at': raw.updated_at,
            'simulated': raw.simulated,
        }
    elif isinstance(raw, Mapping):
        values = raw
    else:
        raise EcoMotorProgressUnavailable('EcoMotor devolvió una respuesta no válida.')

    user_id = str(values.get('user_id', ''))
    xp_total = values.get('xp_total')
    revision = values.get('revision')
    attributes = values.get('attributes')
    updated_at = values.get('updated_at')
    simulated = values.get('simulated', False)

    if user_id != expected_user_id:
        raise EcoMotorProgressUnavailable('La respuesta de EcoMotor corresponde a otro usuario.')
    if type(xp_total) is not int or xp_total < 0:
        raise EcoMotorProgressUnavailable('EcoMotor devolvió una cantidad de XP no válida.')
    if type(revision) is not int or revision < 0:
        raise EcoMotorProgressUnavailable('EcoMotor devolvió una revisión no válida.')
    if type(simulated) is not bool:
        raise EcoMotorProgressUnavailable('EcoMotor devolvió un estado de simulación no válido.')

    if attributes is not None:
        if not isinstance(attributes, Mapping) or set(attributes) != set(ATTRIBUTE_CODES):
            raise EcoMotorProgressUnavailable('EcoMotor no devolvió los seis atributos esperados.')
        if any(type(attributes[code]) is not int or attributes[code] < 0 for code in ATTRIBUTE_CODES):
            raise EcoMotorProgressUnavailable('EcoMotor devolvió atributos no válidos.')
        attributes = {code: attributes[code] for code in ATTRIBUTE_CODES}

    if isinstance(updated_at, str):
        updated_at = parse_datetime(updated_at)
    if not isinstance(updated_at, datetime) or timezone.is_naive(updated_at):
        raise EcoMotorProgressUnavailable('EcoMotor devolvió una fecha de actualización no válida.')

    return ProgressSnapshot(
        user_id=user_id,
        xp_total=xp_total,
        attributes=attributes,
        revision=revision,
        updated_at=updated_at,
        simulated=simulated,
    )


def _cached_snapshot(user_id):
    cached = cache.get(_cache_key(user_id))
    if cached is None:
        return None
    try:
        return _normalize_snapshot(cached, user_id)
    except EcoMotorProgressUnavailable:
        cache.delete(_cache_key(user_id))
        return None


def _get_provider():
    provider_path = getattr(settings, 'DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER', '')
    if not provider_path:
        raise EcoMotorProgressUnavailable('No hay un adaptador de progresión EcoMotor configurado.')
    try:
        provider_factory = import_string(provider_path)
    except (ImportError, AttributeError) as exc:
        raise EcoMotorProgressUnavailable('El adaptador de progresión EcoMotor no se puede cargar.') from exc

    provider = provider_factory()
    if getattr(provider, 'is_simulator', False) and not settings.DEBUG:
        raise EcoMotorProgressUnavailable('La simulación de EcoMotor está deshabilitada fuera de desarrollo.')
    if not callable(getattr(provider, 'get_player_progress', None)):
        raise EcoMotorProgressUnavailable('El adaptador no implementa get_player_progress(user_id).')
    return provider


def get_progress_snapshot(user, *, force_refresh=False):
    """Return a validated, cached read; never derive XP from local Arena or user fields."""
    user_id = str(user.pk)
    cached = _cached_snapshot(user_id)
    if cached is not None and not force_refresh:
        return cached
    if force_refresh:
        cache.delete(_unavailable_key(user_id))

    if cache.get(_unavailable_key(user_id)):
        if cached is not None:
            return cached
        raise EcoMotorProgressUnavailable('La lectura de EcoMotor está temporalmente no disponible.')

    failure_ttl = max(5, min(
        getattr(settings, 'DUCKY_ARENAS_ECOMOTOR_CACHE_SECONDS', 30),
        15,
    ))
    try:
        provider = _get_provider()
        raw = provider.get_player_progress(user_id)
        snapshot = _normalize_snapshot(raw, user_id)
    except (EcoMotorProgressUnavailable, TimeoutError, ConnectionError) as exc:
        cache.set(_unavailable_key(user_id), True, timeout=failure_ttl)
        logger.warning('EcoMotor progression read unavailable for user %s: %s', user_id, exc)
        if cached is not None:
            return cached
        if isinstance(exc, EcoMotorProgressUnavailable):
            raise
        raise EcoMotorProgressUnavailable('La lectura de EcoMotor ha fallado.') from exc

    if cached is not None and (
        snapshot.revision < cached.revision
        or (
            snapshot.revision == cached.revision
            and (
                snapshot.xp_total != cached.xp_total
                or snapshot.attributes != cached.attributes
            )
        )
    ):
        cache.set(_unavailable_key(user_id), True, timeout=failure_ttl)
        logger.warning(
            'Rejected stale or conflicting EcoMotor progression revision %s for user %s; cached revision is %s.',
            snapshot.revision,
            user_id,
            cached.revision,
        )
        return cached

    cache.set(
        _cache_key(user_id),
        {
            'user_id': snapshot.user_id,
            'xp_total': snapshot.xp_total,
            'attributes': snapshot.attributes,
            'revision': snapshot.revision,
            'updated_at': snapshot.updated_at.isoformat(),
            'simulated': snapshot.simulated,
        },
        timeout=getattr(settings, 'DUCKY_ARENAS_ECOMOTOR_CACHE_SECONDS', 30),
    )
    cache.delete(_unavailable_key(user_id))
    return snapshot


def get_progress_snapshot_or_none(user, *, force_refresh=False):
    try:
        return get_progress_snapshot(user, force_refresh=force_refresh)
    except EcoMotorProgressUnavailable:
        return None


class DevelopmentProgressionSimulator:
    """Explicit development-only fixture provider; it never reads user XP fields."""

    is_simulator = True

    def get_player_progress(self, user_id):
        if not settings.DEBUG:
            raise EcoMotorProgressUnavailable('La simulación solo se permite con DEBUG activo.')
        configured = getattr(settings, 'DUCKY_ARENAS_ECOMOTOR_SIMULATED_PROGRESS', {})
        if not isinstance(configured, Mapping):
            raise EcoMotorProgressUnavailable('La configuración de simulación EcoMotor no es válida.')
        fixture = configured.get(user_id, configured.get('default', {}))
        if not isinstance(fixture, Mapping):
            raise EcoMotorProgressUnavailable('El perfil simulado de EcoMotor no es válido.')
        return {
            'user_id': user_id,
            'xp_total': fixture.get('xp_total', 0),
            'attributes': fixture.get('attributes'),
            'revision': fixture.get('revision', 0),
            'updated_at': timezone.now(),
            'simulated': True,
        }
