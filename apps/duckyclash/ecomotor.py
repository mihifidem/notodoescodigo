"""Damage contract; no real EcoMotor API was found in this repository."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


class EcoMotorUnavailable(Exception):
    pass


@dataclass(frozen=True)
class FighterStats:
    atk: int
    defense: int
    logic: int
    speed: int
    qua: int


@dataclass(frozen=True)
class FighterResult:
    player_id: int
    hp: int
    max_hp: int
    correct: bool
    elapsed_ms: int
    modifiers: tuple[str, ...]
    stats: FighterStats


@dataclass(frozen=True)
class DamageRequest:
    key: str
    duel: str
    round: int
    difficulty: int
    fighters: tuple[FighterResult, FighterResult]


@dataclass(frozen=True)
class DamageResult:
    request: DamageRequest
    damage: tuple[int, int]
    engine_version: str


class EcoMotorGateway(ABC):
    @abstractmethod
    def calculate(self, request: DamageRequest) -> DamageResult:
        """Damage received in seat order, using both results simultaneously.

        A stable key and identical input must return identical results.
        No HP mutations or money effects. Echo the verified input, use bounded
        network timeouts, and raise EcoMotorUnavailable for temporary failures.
        Unsupported difficulty/time/modifiers must be explicitly documented.
        """


def validate_result(request: DamageRequest, result: DamageResult):
    if (not isinstance(result, DamageResult) or result.request != request
            or not isinstance(result.damage, tuple) or len(result.damage) != 2
            or any(type(value) is not int or not 0 <= value <= 1000000 for value in result.damage)
            or not isinstance(result.engine_version, str) or not result.engine_version.strip()
            or len(result.engine_version) > 200):
        raise EcoMotorUnavailable('Resultado de EcoMotor no verificable.')


class DevelopmentFakeEcoMotor(EcoMotorGateway):
    """Example ONLY: a correct answer deals 25 damage, independent of speed/stats."""

    def calculate(self, request):
        if not settings.DEBUG:
            raise EcoMotorUnavailable('El EcoMotor fake está prohibido fuera de desarrollo/pruebas.')
        return DamageResult(
            request=request,
            damage=(25 if request.fighters[1].correct else 0, 25 if request.fighters[0].correct else 0),
            engine_version='development-fake-25-v1',
        )


def get_ecomotor(path=None) -> EcoMotorGateway:
    path = getattr(settings, 'DUCKYCLASH_ECOMOTOR_GATEWAY', '') if path is None else path
    if not path:
        raise EcoMotorUnavailable('EcoMotor no está configurado.')
    try:
        gateway = import_string(path)()
    except (ImportError, AttributeError) as exc:
        logger.error('Invalid EcoMotor gateway configuration')
        raise EcoMotorUnavailable('EcoMotor no está disponible.') from exc
    if not isinstance(gateway, EcoMotorGateway):
        raise EcoMotorUnavailable('El adaptador no implementa el contrato de EcoMotor.')
    return gateway
