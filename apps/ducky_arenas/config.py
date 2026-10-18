from django.db import models


class EquipmentSlot(models.TextChoices):
    PANTS = 'PANTS', 'Pantalón'
    TOP = 'TOP', 'Camiseta / sudadera / túnica'
    SHOES = 'SHOES', 'Calzado'
    VEST = 'VEST', 'Chaleco / armadura'
    BACK = 'BACK', 'Capa / mochila'
    HEAD = 'HEAD', 'Gorra / casco / gorro'
    EYES = 'EYES', 'Gafas / visor / VR'
    DEVICE = 'DEVICE', 'Portátil / dispositivo'


REQUIRED_EVOLUTION_SLOTS = (
    EquipmentSlot.PANTS,
    EquipmentSlot.TOP,
    EquipmentSlot.SHOES,
    EquipmentSlot.VEST,
    EquipmentSlot.BACK,
    EquipmentSlot.HEAD,
    EquipmentSlot.EYES,
    EquipmentSlot.DEVICE,
)

EQUIPMENT_PROGRESS_ORDER = [slot for slot in EquipmentSlot]

MAX_LEVEL = 50

ARENA_TYPES = ['QUIZ', 'BATTLE', 'CHALLENGE', 'ESCAPE', 'HACK']


def get_xp_required_for_level(level):
    if level <= 1:
        return 0
    return int(75 * (level - 1) + 25 * (level - 1) ** 2)


LEVEL_CONFIG = {level: get_xp_required_for_level(level) for level in range(1, MAX_LEVEL + 1)}

ARENA_REWARD_CONFIG = {
    'QUIZ': {
        'completion_xp': 50,
        'completion_coins': 10,
        'win_bonus_xp': 25,
        'win_bonus_coins': 8,
        'difficulty_bonus': {'EASY': 0, 'MEDIUM': 0, 'HARD': 15},
        'score_bonus': 1,
        'drop_pool': [
            {'item_code': 'cyber_hoodie_01', 'probability': 0.15, 'min_level': 1, 'avatar_type': 'CYBERSECURITY'},
            {'item_code': 'gamer_headset_01', 'probability': 0.10, 'min_level': 1, 'avatar_type': 'GAMER'},
        ],
    },
    'BATTLE': {
        'completion_xp': 70,
        'completion_coins': 14,
        'win_bonus_xp': 35,
        'win_bonus_coins': 12,
        'difficulty_bonus': {'EASY': 5, 'MEDIUM': 12, 'HARD': 18},
        'score_bonus': 1,
        'drop_pool': [
            {'item_code': 'roman_helmet_01', 'probability': 0.12, 'min_level': 1, 'avatar_type': 'ROMAN'},
        ],
    },
    'CHALLENGE': {
        'completion_xp': 60,
        'completion_coins': 12,
        'win_bonus_xp': 20,
        'win_bonus_coins': 7,
        'difficulty_bonus': {'EASY': 5, 'MEDIUM': 10, 'HARD': 18},
        'score_bonus': 1,
        'drop_pool': [
            {'item_code': 'reward_bag_01', 'probability': 0.20, 'min_level': 1, 'avatar_type': 'GAMER'},
        ],
    },
    'ESCAPE': {
        'completion_xp': 90,
        'completion_coins': 18,
        'win_bonus_xp': 30,
        'win_bonus_coins': 10,
        'difficulty_bonus': {'EASY': 8, 'MEDIUM': 14, 'HARD': 22},
        'score_bonus': 1,
        'drop_pool': [
            {'item_code': 'systems_visor_01', 'probability': 0.12, 'min_level': 1, 'avatar_type': 'SYSTEMS'},
        ],
    },
    'HACK': {
        'completion_xp': 95,
        'completion_coins': 20,
        'win_bonus_xp': 40,
        'win_bonus_coins': 15,
        'difficulty_bonus': {'EASY': 10, 'MEDIUM': 18, 'HARD': 26},
        'score_bonus': 1,
        'drop_pool': [
            {'item_code': 'developer_laptop_01', 'probability': 0.18, 'min_level': 1, 'avatar_type': 'DEVELOPER'},
        ],
    },
}

DUCKY_BASE_EQUIPPED = {
    'head': None,
    'face': None,
    'torso': None,
    'protection': None,
    'legs': None,
    'feet': None,
    'back': None,
    'neck': None,
    'device': None,
    'artifact': None,
}

DUCKY_INITIAL_RATING = 1000

DUCKY_TYPES = {
    'PREHISTORIC': {
        'label': 'Prehistórico',
        'description': 'Un Ducky robusto y primitivo, impulsado por fuerza y supervivencia.',
        'accent': '#d97706',
        'avatarFallback': 'img/DA_PRE_avatar.png',
        'avatarVideo': 'img/DA_PRE_avatar.mp4',
        'baseStats': {'attack': 12, 'defense': 11, 'speed': 8, 'logic': 6, 'energy': 9},
    },
    'ROMAN': {
        'label': 'Romano',
        'description': 'Equilibrio ofensivo y estrategia con presencia imponente.',
        'avatarFallback': 'img/DA_ROM_avatar.png',
        'avatarVideo': 'img/DA_ROM_avatar.mp4',
        'accent': '#b45309',
        'baseStats': {'attack': 11, 'defense': 10, 'speed': 7, 'logic': 9, 'energy': 8},
    },
    'MILITARY': {
        'label': 'Militar',
        'description': 'Preparado para el combate, la disciplina y la resistencia.',
        'avatarFallback': 'img/DA_MIL_avatar.png',
        'avatarVideo': 'img/DA_MIL_avatar.mp4',
        'accent': '#a16207',
        'baseStats': {'attack': 13, 'defense': 12, 'speed': 7, 'logic': 7, 'energy': 8},
    },
    'GAMER': {
        'label': 'Gamer',
        'description': 'Creativo, rápido y orientado a la adaptabilidad competitiva.',
        'avatarFallback': 'img/DA_GAM_avatar.png',
        'avatarVideo': 'img/DA_GAM_avatar.mp4',
        'accent': '#7c3aed',
        'baseStats': {'attack': 9, 'defense': 8, 'speed': 12, 'logic': 10, 'energy': 11},
    },
    'DEVELOPER': {
        'label': 'Developer',
        'description': 'Lógica pura, precisión de ejecución y enfoque técnico.',
        'avatarFallback': 'img/DA_DEV_avatar.png',
        'avatarVideo': 'img/DA_DEV_avatar.mp4',
        'accent': '#1d4ed8',
        'baseStats': {'attack': 8, 'defense': 7, 'speed': 9, 'logic': 14, 'energy': 10},
    },
    'CYBERSECURITY': {
        'label': 'Cyberseguridad',
        'description': 'Observa, analiza y responde con control total del entorno.',
        'avatarFallback': 'img/DA_CYB_avatar.png',
        'avatarVideo': 'img/DA_CYB_avatar.mp4',
        'accent': '#0f766e',
        'baseStats': {'attack': 10, 'defense': 11, 'speed': 9, 'logic': 13, 'energy': 9},
    },
    'SYSTEMS': {
        'label': 'Systems',
        'description': 'Resiliente, analítico y diseñado para maximizar rendimiento.',
        'avatarFallback': 'img/DA_SYS_avatar.png',
        'avatarVideo': 'img/DA_SYS_avatar.mp4',
        'accent': '#475569',
        'baseStats': {'attack': 9, 'defense': 10, 'speed': 8, 'logic': 12, 'energy': 13},
    },
}

DUCKY_ALLOWED_AVATAR_TYPES = tuple(DUCKY_TYPES.keys())
