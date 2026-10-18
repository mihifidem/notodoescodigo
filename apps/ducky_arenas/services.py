import copy
import random
from typing import Dict, Optional

from django.db import transaction
from django.utils import timezone

from .config import ARENA_REWARD_CONFIG, ARENA_TYPES, DUCKY_BASE_EQUIPPED, DUCKY_INITIAL_RATING, DUCKY_TYPES, MAX_LEVEL, get_xp_required_for_level
from .models import ArenaItem, ArenaRewardTransaction, UserArenaInventory, UserProfileArenas


def get_level_for_xp(total_xp: int) -> int:
    level = 1
    while level < MAX_LEVEL and total_xp >= get_xp_required_for_level(level + 1):
        level += 1
    return level


def get_level_progress(total_xp: int):
    current_level = get_level_for_xp(total_xp)
    if current_level >= MAX_LEVEL:
        next_level = MAX_LEVEL
        current_threshold = get_xp_required_for_level(current_level)
        next_threshold = get_xp_required_for_level(MAX_LEVEL)
        xp_into_current_level = max(0, total_xp - current_threshold)
        xp_needed_for_next_level = max(0, next_threshold - total_xp)
        percent = 100 if total_xp >= next_threshold else min(100, (xp_into_current_level / max(1, next_threshold - current_threshold)) * 100)
        return {
            'current_level': current_level,
            'next_level': next_level,
            'xp_into_current_level': xp_into_current_level,
            'xp_needed_for_next_level': xp_needed_for_next_level,
            'progress_percent': round(percent, 2),
        }

    current_threshold = get_xp_required_for_level(current_level)
    next_threshold = get_xp_required_for_level(current_level + 1)
    xp_into_current_level = max(0, total_xp - current_threshold)
    xp_needed_for_next_level = max(0, next_threshold - total_xp)
    progress_percent = 0 if total_xp <= current_threshold else min(100, (xp_into_current_level / max(1, next_threshold - current_threshold)) * 100)

    return {
        'current_level': current_level,
        'next_level': current_level + 1,
        'xp_into_current_level': xp_into_current_level,
        'xp_needed_for_next_level': xp_needed_for_next_level,
        'progress_percent': round(progress_percent, 2),
    }


def calculate_arena_reward(profile: UserProfileArenas, arena_type: str, result: str, difficulty: str, score: int) -> Dict[str, int]:
    arena_type = (arena_type or '').upper()
    result = (result or '').upper()
    difficulty = (difficulty or 'MEDIUM').upper()
    config = ARENA_REWARD_CONFIG.get(arena_type, {})
    if not config:
        raise ValueError('arenaType no válido')

    xp_awarded = int(config.get('completion_xp', 0))
    duck_coins_awarded = int(config.get('completion_coins', 0))

    if result == 'WIN':
        xp_awarded += int(config.get('win_bonus_xp', 0))
        duck_coins_awarded += int(config.get('win_bonus_coins', 0))
    elif result == 'LOSS':
        xp_awarded += int(config.get('completion_xp', 0) * 0.25)
    elif result == 'DRAW':
        xp_awarded += int(config.get('completion_xp', 0) * 0.15)

    difficulty_bonus = config.get('difficulty_bonus', {}).get(difficulty, 0)
    xp_awarded += int(difficulty_bonus)
    score_bonus = int(config.get('score_bonus', 0) * max(0, int(score or 0)) / 100)
    xp_awarded += score_bonus

    return {
        'xp_awarded': xp_awarded,
        'duck_coins_awarded': duck_coins_awarded,
    }


def update_match_stats(profile: UserProfileArenas, result: str):
    result = (result or '').upper()
    if result == 'WIN':
        profile.gamesPlayed = (profile.gamesPlayed or 0) + 1
        profile.wins = (profile.wins or 0) + 1
        profile.currentWinStreak = (profile.currentWinStreak or 0) + 1
        profile.bestWinStreak = max(profile.bestWinStreak or 0, profile.currentWinStreak)
    elif result == 'LOSS':
        profile.gamesPlayed = (profile.gamesPlayed or 0) + 1
        profile.losses = (profile.losses or 0) + 1
        profile.currentWinStreak = 0
    elif result == 'DRAW':
        profile.gamesPlayed = (profile.gamesPlayed or 0) + 1
        profile.draws = (profile.draws or 0) + 1
        profile.currentWinStreak = 0
    elif result == 'COMPLETED':
        profile.gamesPlayed = (profile.gamesPlayed or 0) + 1
    else:
        raise ValueError('resultado no válido')

    profile.lastPlayedAt = timezone.now()
    profile.save(update_fields=['gamesPlayed', 'wins', 'losses', 'draws', 'currentWinStreak', 'bestWinStreak', 'lastPlayedAt'])


def calculate_effective_stats(profile: UserProfileArenas) -> Dict[str, int]:
    if profile is None:
        return {}

    base_stats = copy.deepcopy(profile.stats or {})
    for stat_name in ('attack', 'defense', 'speed', 'logic', 'energy'):
        base_stats[stat_name] = int(base_stats.get(stat_name, 0) or 0)

    equipped = profile.equipped or {}
    for slot_name, item_code in equipped.items():
        if not item_code:
            continue
        try:
            item = ArenaItem.objects.get(code=item_code)
        except ArenaItem.DoesNotExist:
            continue

        for stat_name, bonus in (item.statBonuses or {}).items():
            if stat_name in base_stats:
                base_stats[stat_name] = int(base_stats.get(stat_name, 0)) + int(bonus)

    return base_stats


def calculate_power(profile: UserProfileArenas) -> int:
    effective_stats = calculate_effective_stats(profile)
    if not effective_stats:
        return 0
    return sum(int(value) for value in effective_stats.values())


def get_characteristics(profile: UserProfileArenas):
    stats = calculate_effective_stats(profile)
    # Keep the stored energy key so existing profiles and equipment retain their values.
    labels = (
        ('attack', 'ATK', 'Ataque'),
        ('defense', 'DEF', 'Defensa'),
        ('logic', 'LOG', 'Lógica'),
        ('speed', 'SPE', 'Velocidad'),
        ('energy', 'INT', 'Inteligencia'),
    )
    return [
        {'code': code, 'label': label, 'value': stats[key]}
        for key, code, label in labels
    ]


class DuckyArenasProfileService:
    @staticmethod
    def get_profile_for_user(user) -> Optional[UserProfileArenas]:
        try:
            return UserProfileArenas.objects.get(user_id=user.pk)
        except UserProfileArenas.DoesNotExist:
            return None

    @staticmethod
    def requires_onboarding(user) -> bool:
        profile = DuckyArenasProfileService.get_profile_for_user(user)
        return profile is None or not profile.onboardingCompleted

    @staticmethod
    def serialize_profile(profile: Optional[UserProfileArenas]):
        if profile is None:
            return None
        return {
            'id': profile.id,
            'userId': profile.user_id,
            'nickname': profile.nickname,
            'level': profile.level,
            'xp': profile.xp,
            'prestigeLevel': profile.prestigeLevel,
            'avatarType': profile.avatarType,
            'gender': profile.gender,
            'evolutionStage': profile.evolutionStage,
            'equipped': profile.equipped,
            'stats': profile.stats,
            'duckCoins': profile.duckCoins,
            'arenaTokens': profile.arenaTokens,
            'rating': profile.rating,
            'rank': profile.rank,
            'gamesPlayed': profile.gamesPlayed,
            'wins': profile.wins,
            'losses': profile.losses,
            'draws': profile.draws,
            'currentWinStreak': profile.currentWinStreak,
            'bestWinStreak': profile.bestWinStreak,
            'selectedBadgeId': profile.selectedBadgeId,
            'selectedFrameId': profile.selectedFrameId,
            'activeSetId': profile.activeSetId,
            'onboardingCompleted': profile.onboardingCompleted,
            'onboardingCompletedAt': profile.onboardingCompletedAt.isoformat() if profile.onboardingCompletedAt else None,
            'createdAt': profile.createdAt.isoformat(),
            'updatedAt': profile.updatedAt.isoformat(),
            'lastPlayedAt': profile.lastPlayedAt.isoformat() if profile.lastPlayedAt else None,
        }

    @staticmethod
    def initial_profile_payload(nickname: str, avatar_type: str, gender: str = 'BOY') -> Dict[str, object]:
        avatar_type = avatar_type.upper()
        gender = (gender or 'BOY').upper()
        if gender not in dict(UserProfileArenas.Gender.choices):
            raise ValueError('gender no válido')
        config = DUCKY_TYPES[avatar_type]
        return {
            'nickname': nickname,
            'avatarType': avatar_type,
            'gender': gender,
            'level': 1,
            'xp': 0,
            'prestigeLevel': 0,
            'evolutionStage': 'BASE',
            'equipped': copy.deepcopy(DUCKY_BASE_EQUIPPED),
            'stats': copy.deepcopy(config['baseStats']),
            'duckCoins': 0,
            'arenaTokens': 0,
            'rating': DUCKY_INITIAL_RATING,
            'rank': 'UNRANKED',
            'gamesPlayed': 0,
            'wins': 0,
            'losses': 0,
            'draws': 0,
            'currentWinStreak': 0,
            'bestWinStreak': 0,
            'selectedBadgeId': None,
            'selectedFrameId': None,
            'activeSetId': None,
            'onboardingCompleted': True,
            'onboardingCompletedAt': timezone.now(),
        }

    @classmethod
    @transaction.atomic
    def complete_onboarding(cls, user, nickname: str, avatar_type: str, gender: str = 'BOY'):
        avatar_type = avatar_type.upper()
        if avatar_type not in DUCKY_TYPES:
            raise ValueError('avatarType no válido')
        if avatar_type != 'PREHISTORIC':
            raise ValueError('El Ducky debe empezar en la época prehistórica.')
        if gender not in ('BOY', 'GIRL'):
            raise ValueError('El género Ducky no es válido.')

        profile = cls.get_profile_for_user(user)
        payload = cls.initial_profile_payload(nickname, avatar_type, gender)

        user.genero = 'hombre' if gender == 'BOY' else 'mujer'
        user.save(update_fields=['genero'])
        if profile is None:
            profile = UserProfileArenas.objects.create(user=user, **payload)
        else:
            for field, value in payload.items():
                setattr(profile, field, value)
            profile.save()

        return profile

    @staticmethod
    def get_inventory_for_profile(profile: UserProfileArenas):
        if profile is None:
            return []
        return list(
            UserArenaInventory.objects.filter(profile=profile)
            .select_related('item')
            .order_by('item__rarity', 'item__name')
        )

    @staticmethod
    def equip_item(profile: UserProfileArenas, item_code: str):
        if profile is None or not item_code:
            return False

        try:
            inventory_entry = UserArenaInventory.objects.select_related('item').get(profile=profile, item__code=item_code)
        except UserArenaInventory.DoesNotExist:
            return False

        item = inventory_entry.item
        if item.avatarType and item.avatarType != profile.avatarType:
            return False

        slot_key = item.slot.lower()
        equipped = dict(profile.equipped or {})
        equipped[slot_key] = item.code
        profile.equipped = equipped
        profile.save(update_fields=['equipped'])
        return True

    @staticmethod
    def unequip_item(profile: UserProfileArenas, slot: str):
        if profile is None:
            return False

        slot_key = (slot or '').lower()
        if not slot_key:
            return False

        equipped = dict(profile.equipped or {})
        if slot_key in equipped:
            equipped[slot_key] = None
            profile.equipped = equipped
            profile.save(update_fields=['equipped'])
            return True
        return False


def grant_arena_rewards(profile: UserProfileArenas, arena_type: str, result: str, difficulty: str, score: int, source_id: str, reward_context: Optional[Dict[str, object]] = None):
    reward_context = reward_context or {}
    arena_type = (arena_type or '').upper()
    result = (result or '').upper()
    difficulty = (difficulty or 'MEDIUM').upper()

    if not source_id:
        raise ValueError('sourceId es obligatorio')
    if arena_type not in ARENA_TYPES:
        raise ValueError('arenaType no válido')
    if result not in {'WIN', 'LOSS', 'DRAW', 'COMPLETED'}:
        raise ValueError('result no válido')

    with transaction.atomic():
        profile = UserProfileArenas.objects.select_for_update().get(pk=profile.pk)
        existing = ArenaRewardTransaction.objects.filter(profile=profile, arenaType=arena_type, sourceId=source_id).first()
        if existing:
            return {
                'xp_awarded': existing.xpAwarded,
                'duck_coins_awarded': existing.duckCoinsAwarded,
                'items_awarded': [],
                'previous_level': existing.previousLevel,
                'new_level': existing.newLevel,
                'levels_gained': max(0, existing.newLevel - existing.previousLevel),
                'level_up': existing.newLevel > existing.previousLevel,
                'already_processed': True,
            }

        reward = calculate_arena_reward(profile, arena_type, result, difficulty, score)
        previous_level = int(profile.level or 1)
        total_xp = int(profile.xp or 0) + int(reward['xp_awarded'])
        new_level = max(previous_level, get_level_for_xp(total_xp))
        if new_level > MAX_LEVEL:
            new_level = MAX_LEVEL
        total_xp = min(total_xp, get_xp_required_for_level(MAX_LEVEL) + 5000)

        profile.xp = total_xp
        profile.level = new_level
        profile.duckCoins = max(0, int(profile.duckCoins) + int(reward['duck_coins_awarded']))
        update_match_stats(profile, result)

        items_awarded = []
        item_code = reward_context.get('item_code')
        if item_code:
            item = ArenaItem.objects.filter(code=item_code, isActive=True).first()
            if item:
                inventory, _ = UserArenaInventory.objects.get_or_create(profile=profile, item=item, defaults={'quantity': 0})
                inventory.quantity += 1
                inventory.save(update_fields=['quantity'])
                items_awarded.append(item.code)
        else:
            config = ARENA_REWARD_CONFIG.get(arena_type, {})
            for drop in config.get('drop_pool', []):
                if profile.level < int(drop.get('min_level', 1)):
                    continue
                if drop.get('avatar_type') and profile.avatarType != drop['avatar_type']:
                    continue
                if random.random() <= float(drop.get('probability', 0)):
                    item = ArenaItem.objects.filter(code=drop['item_code'], isActive=True).first()
                    if item:
                        inventory, _ = UserArenaInventory.objects.get_or_create(profile=profile, item=item, defaults={'quantity': 0})
                        inventory.quantity += 1
                        inventory.save(update_fields=['quantity'])
                        items_awarded.append(item.code)
                        break

        profile.save(update_fields=['xp', 'level', 'duckCoins', 'gamesPlayed', 'wins', 'losses', 'draws', 'currentWinStreak', 'bestWinStreak', 'lastPlayedAt'])

        reward_transaction = ArenaRewardTransaction.objects.create(
            profile=profile,
            arenaType=arena_type,
            sourceId=source_id,
            xpAwarded=reward['xp_awarded'],
            duckCoinsAwarded=reward['duck_coins_awarded'],
            previousLevel=previous_level,
            newLevel=new_level,
        )

        return {
            'xp_awarded': reward['xp_awarded'],
            'duck_coins_awarded': reward['duck_coins_awarded'],
            'items_awarded': items_awarded,
            'previous_level': previous_level,
            'new_level': new_level,
            'levels_gained': max(0, new_level - previous_level),
            'level_up': new_level > previous_level,
            'already_processed': False,
            'transaction_id': reward_transaction.pk,
        }
