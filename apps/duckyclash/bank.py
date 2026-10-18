import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth import get_user_model
from django.utils.module_loading import import_string

from apps.ducky_arenas.models import UserProfileArenas

logger = logging.getLogger(__name__)


class BankUnavailable(Exception):
    """The balance cannot be verified; the operation must not proceed."""


class BankGateway(ABC):
    @abstractmethod
    def available_balance(self, player: AbstractBaseUser) -> int:
        """Return available integer DuckyCoins, not a reservation confirmation."""


@dataclass(frozen=True)
class BankRequest:
    key: str
    duel: str
    kind: str
    amount: int
    player: str = ''
    winner: str = ''
    reservation_keys: tuple[str, ...] = ()
    reservation_references: tuple[str, ...] = ()


class BankOutcome(str, Enum):
    CONFIRMED = 'confirmed'
    REJECTED = 'rejected'
    UNKNOWN = 'unknown'
    NOT_FOUND = 'not_found'


@dataclass(frozen=True)
class BankResult:
    request: BankRequest
    outcome: BankOutcome
    reference: str = ''


class TransactionalBankGateway(BankGateway):
    """Contract, not a fabricated HTTP API.

    Keys deduplicate mutations permanently, including concurrent/in-flight calls.
    Lookup is authoritative by key; NOT_FOUND permits resubmitting that same key,
    but does NOT prove that a delayed previous request can never arrive.
    REJECTED is terminal and guarantees that operation cannot subsequently apply.
    Confirmed holds remain active until release or atomic settlement of both holds.
    Results echo the full verified request, not merely an HTTP success code.
    Adapters must bound network timeouts and translate transport errors to
    BankUnavailable; neither errors nor responses may contain credentials.
    """

    @abstractmethod
    def reserve(self, request: BankRequest) -> BankResult:
        """Hold request.amount for request.player, or definitively reject."""

    @abstractmethod
    def release(self, request: BankRequest) -> BankResult:
        """Release the referenced hold once."""

    @abstractmethod
    def settle(self, request: BankRequest) -> BankResult:
        """Atomically consume BOTH holds and pay the full pot to request.winner."""

    @abstractmethod
    def lookup(self, request: BankRequest) -> BankResult:
        """Reconcile the original operation, including unknown timeout results."""


def get_payment_gateway(path: str | None = None) -> TransactionalBankGateway:
    path = getattr(settings, 'DUCKYCLASH_PAYMENT_GATEWAY', '') if path is None else path
    if not path:
        raise BankUnavailable('Las reservas de DuckyBank no están configuradas.')
    try:
        gateway = import_string(path)()
    except (ImportError, AttributeError) as exc:
        logger.error('Invalid DuckyClash payment gateway configuration')
        raise BankUnavailable('La integración económica no está disponible.') from exc
    if not isinstance(gateway, TransactionalBankGateway):
        logger.error('DuckyClash payment gateway must implement TransactionalBankGateway')
        raise BankUnavailable('La integración económica no implementa el contrato requerido.')
    return gateway


class ProfileBalanceGateway(BankGateway):
    """Explicit local, read-only integration; no bank holds or money transfers."""

    def available_balance(self, player: AbstractBaseUser) -> int:
        try:
            return UserProfileArenas.objects.values_list('duckCoins', flat=True).get(user_id=player.pk)
        except UserProfileArenas.DoesNotExist as exc:
            raise BankUnavailable('No se ha podido consultar el saldo del jugador.') from exc


class UserPointsBalanceGateway(BankGateway):
    """Spendable points are the local DuckyCoins; cumulative points are not money."""

    def available_balance(self, player: AbstractBaseUser) -> int:
        User = get_user_model()
        try:
            return User.objects.values_list('puntos', flat=True).get(pk=player.pk)
        except User.DoesNotExist as exc:
            raise BankUnavailable('No se ha podido consultar el saldo del jugador.') from exc


def get_bank_gateway() -> BankGateway:
    if getattr(settings, 'DUCKYCLASH_PAYMENT_GATEWAY', ''):
        return get_payment_gateway()
    path = getattr(settings, 'DUCKYCLASH_BANK_GATEWAY', '')
    if not path:
        logger.error('DuckyClash balance gateway is not configured')
        raise BankUnavailable('La consulta de saldo no está configurada. Inténtalo más tarde.')
    try:
        gateway = import_string(path)()
    except (ImportError, AttributeError) as exc:
        logger.exception('Invalid DuckyClash balance gateway configuration')
        raise BankUnavailable('La consulta de saldo no está disponible.') from exc
    if not isinstance(gateway, BankGateway):
        logger.error('DuckyClash balance gateway does not implement BankGateway')
        raise BankUnavailable('La consulta de saldo no está disponible.')
    return gateway


def require_balance(player: AbstractBaseUser, stake: int) -> None:
    balance = get_bank_gateway().available_balance(player)
    if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
        logger.error('DuckyClash balance gateway returned an invalid balance')
        raise BankUnavailable('El banco ha devuelto un saldo no válido.')
    if balance < stake:
        from django.core.exceptions import ValidationError
        raise ValidationError(
            f'{player.get_username()} no dispone de suficientes DuckyCoins para esta apuesta.'
        )
