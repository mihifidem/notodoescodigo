"""Validated configuration; accepted duels never read mutable admin rules."""
from copy import deepcopy

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone


DEFAULT_ITEMS = {
    'NUBE': {'amount': 5, 'max_uses': 2, 'cooldown': 10, 'max_per_round': 1},
    'ACELERADOR': {'amount': 5, 'max_uses': 2, 'cooldown': 10, 'max_per_round': 1},
    'MULTIPLICADOR': {'amount': 2, 'max_uses': 2, 'cooldown': 10, 'max_per_round': 1},
    'ESCUDO': {'amount': 2, 'max_uses': 2, 'cooldown': 10, 'max_per_round': 1},
    'POCIMA_TIEMPO': {'amount': 5, 'max_uses': 2, 'cooldown': 10, 'max_per_round': 1, 'max_extra_seconds': 15},
    'BOTIQUIN': {'amount': 20, 'max_uses': 2, 'cooldown': 10, 'max_per_round': 1},
}
DEFAULT_ENGINE = {
    'countdown_seconds': 3, 'question_seconds': 30, 'result_seconds': 3,
    'presence_seconds': 20, 'waiting_seconds': 300, 'max_rounds': 20,
    'healing_windows': ['question_active'],
}


def validate_items(items):
    if not isinstance(items, dict) or set(items) != set(DEFAULT_ITEMS):
        raise ValidationError('Configura exactamente los seis objetos admitidos.')
    for code, values in items.items():
        if not isinstance(values, dict) or set(values) != set(DEFAULT_ITEMS[code]):
            raise ValidationError(f'Parámetros incompletos o desconocidos para {code}.')
        for key, maximum in (
            ('amount', 1000), ('max_uses', 100), ('max_per_round', 100),
            ('cooldown', 3600), ('max_extra_seconds', 300),
        ):
            if key in values and (type(values[key]) is not int or not 0 <= values[key] <= maximum):
                raise ValidationError(f'Valor no válido de {key} para {code}.')
        if values['amount'] < 1 or (code in ('ESCUDO', 'MULTIPLICADOR') and values['amount'] > 8):
            raise ValidationError(f'Efecto fuera de límites para {code}.')


def default_items():
    return deepcopy(DEFAULT_ITEMS)


def default_healing_windows():
    return ['question_active']


def validate_engine(rules):
    for key, lower, upper in (
        ('question_seconds', 5, 300), ('result_seconds', 1, 30),
        ('presence_seconds', 10, 120), ('waiting_seconds', 30, 3600), ('max_rounds', 1, 100),
    ):
        if type(rules[key]) is not int or not lower <= rules[key] <= upper:
            raise ValidationError('La configuración de tiempos del motor no es válida.')
    if type(rules['countdown_seconds']) is not int or rules['countdown_seconds'] != 3:
        raise ValidationError('La cuenta atrás debe ser de tres segundos.')
    if (not isinstance(rules['healing_windows'], list) or not rules['healing_windows']
            or any(value not in ('countdown', 'question_active') for value in rules['healing_windows'])):
        raise ValidationError('La ventana del Botiquín no es válida.')


def current_rules():
    from .models import RuleConfiguration
    config = RuleConfiguration.objects.filter(pk=1).first()
    if config:
        return config.snapshot()
    engine = {**deepcopy(DEFAULT_ENGINE), **getattr(settings, 'DUCKYCLASH_ENGINE_RULES', {})}
    validate_engine(engine)
    items = {
        code: {**values, **getattr(settings, 'DUCKYCLASH_ITEM_RULES', {}).get(code, {})}
        for code, values in default_items().items()
    }
    validate_items(items)
    double_ko = getattr(settings, 'DUCKYCLASH_DOUBLE_KO', 'draw')
    max_tiebreaks = getattr(settings, 'DUCKYCLASH_MAX_TIEBREAKS', 1)
    if double_ko not in ('draw', 'tiebreak') or type(max_tiebreaks) is not int or not 0 <= max_tiebreaks <= 10:
        raise ValidationError('La configuración de desempate no es válida.')
    return {
        'version': 'settings-v1', 'initial_hp': 100, 'engine': engine, 'items': items,
        'double_ko': double_ko, 'max_tiebreaks': max_tiebreaks,
        'max_stake': 1000000, 'min_expiry_seconds': 1, 'max_expiry_seconds': 2592000,
        'ring_access': 'scheduled', 'no_show': 'refund', 'abandonment': 'wait',
        'abandonment_seconds': 300, 'reminder_seconds': 300,
    }


def validate_offer_limits(*, stake, expires_at=None, evaluated_at=None):
    rules = current_rules()
    if type(stake) is not int or not 1 <= stake <= rules['max_stake']:
        raise ValidationError(f"La apuesta debe ser un entero entre 1 y {rules['max_stake']} DuckyCoins.")
    if expires_at is not None and timezone.is_aware(expires_at):
        remaining = (expires_at - (evaluated_at or timezone.now())).total_seconds()
        if not rules['min_expiry_seconds'] <= remaining <= rules['max_expiry_seconds']:
            raise ValidationError('La caducidad no está dentro de los límites configurados.')
