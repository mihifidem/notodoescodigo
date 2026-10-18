"""Ducky era progression, battle sprites and balanced character statistics."""

from django.core.exceptions import ValidationError

from apps.ducky_arenas.models import CharacterAsset

from .ecomotor import FighterStats
from apps.ducky_arenas.core.progression import ERAS, era_for_xp

SPECIALTIES = {
    'developer': ('DEV', 'Developer'),
    'systems': ('SYS', 'Sistemas'),
    'cybersecurity': ('CYB', 'Ciberseguridad'),
    'gamer': ('GAM', 'Gamer'),
    'data_ia': ('DIA', 'Data IA'),
}

# Every character has 35 points across ATK, DEF, LOG, SPE and QUA.
STAT_PROFILES = {
    'PRE': (10, 8, 5, 7, 5),
    'GRE': (7, 6, 10, 7, 5),
    'ROM': (9, 10, 6, 5, 5),
    'MED': (7, 10, 6, 6, 6),
    'REN': (6, 6, 10, 7, 6),
    'REV': (8, 7, 8, 6, 6),
    'SXX': (7, 7, 7, 8, 6),
    'DEV': (6, 6, 10, 7, 6),
    'SYS': (7, 9, 8, 6, 5),
    'CYB': (7, 10, 8, 5, 5),
    'GAM': (8, 5, 7, 10, 5),
    'DIA': (5, 6, 10, 7, 7),
}


def character_code(user) -> str:
    era = era_for_xp(user.puntos_totales)
    if era.code != 'ACT':
        return era.code
    specialty = SPECIALTIES.get(user.duck_specialty)
    if specialty is None:
        raise ValidationError('Elige una especialidad Ducky para la época actual.')
    return specialty[0]


def character_stats(user) -> FighterStats:
    code = character_code(user)
    atk, defense, logic, speed, qua = STAT_PROFILES[code]
    return FighterStats(atk=atk, defense=defense, logic=logic, speed=speed, qua=qua)


def character_sprite_path(user) -> str:
    code = character_code(user)
    gender = user.genero
    if gender not in {'hombre', 'mujer'}:
        profile = getattr(user, 'user_profile_arenas', None)
        gender = 'mujer' if profile and profile.gender == 'GIRL' else 'hombre'
    variant = CharacterAsset.Variant.GIRL if gender == 'mujer' else CharacterAsset.Variant.BOY
    asset = CharacterAsset.objects.filter(
        era__code=code,
        variant=variant,
        is_available=True,
    ).only('static_image_path').first()
    if asset and asset.static_image_path:
        return asset.static_image_path
    fallback = CharacterAsset.objects.filter(
        era__code='SXX',
        variant=variant,
        is_available=True,
    ).only('static_image_path').first()
    if fallback and fallback.static_image_path:
        return fallback.static_image_path
    suffix = '2' if variant == CharacterAsset.Variant.GIRL else '1'
    return f'duckyarenas/DAR_personajes/DAR_SXX{suffix}.png'
