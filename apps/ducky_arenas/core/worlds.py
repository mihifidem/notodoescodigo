"""Data-driven map and character assets; absent sprites remain unavailable."""

from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.ducky_arenas.models import CharacterAsset, Portal, WorldMap

from .destinations import requirements_are_met, resolve_destination_url
from .exploration import safe_spawn_position
from .progression import configured_eras

MAP_WIDTH = 1672
MAP_HEIGHT = 941

# The images were visually checked individually; no walk cycle is inferred.
DIRECTIONAL_SPRITE_INDEXES = {
    'up': 0,
    'left': 1,
    'down': 2,
    'right': 7,
}

def map_catalog():
    era_codes = {era.code for era in configured_eras()}
    return {
        world_map.era.code: {
            'code': world_map.era.code,
            'label': world_map.era.name,
            'map_url': static(world_map.static_path),
            'width': world_map.width,
            'height': world_map.height,
        }
        for world_map in WorldMap.objects.select_related('era').filter(
            era__isnull=False,
            era__is_available=True,
            is_available=True,
        )
        if world_map.era.code in era_codes
    }


def _character_asset(profile, era):
    if profile is None:
        return None

    variant = (
        profile.selected_character.variant
        if profile.selected_character_id
        and profile.selected_character.era.code == era.code
        and profile.selected_character.is_available
        else profile.Gender.BOY if profile.gender == profile.Gender.BOY
        else profile.Gender.GIRL
    )
    character = (
        profile.selected_character
        if profile.selected_character_id
        and profile.selected_character.era.code == era.code
        and profile.selected_character.is_available
        else CharacterAsset.objects.filter(
            era__code=era.code,
            variant=variant,
            is_available=True,
        ).first()
    )
    return character


def _character_config(character, era):
    if character is None:
        return None

    if era.code != 'PRE' or character.variant != CharacterAsset.Variant.BOY:
        return None

    resources = character.movement_resources
    if not isinstance(resources, list):
        raise ValidationError('La configuración de sprites del personaje no es válida.')
    if len(resources) <= max(DIRECTIONAL_SPRITE_INDEXES.values()):
        return None

    directions = {
        direction: static(resources[index])
        for direction, index in DIRECTIONAL_SPRITE_INDEXES.items()
    }
    return {
        'kind': 'directional-sprites',
        'directions': directions,
        'walk_cycle_available': False,
    }


def _portal_config(world_map, user):
    portals = []
    for portal in Portal.objects.select_related('destination').filter(
        world_map=world_map,
    ).order_by('display_order', 'name'):
        geometry_available = all(
            value is not None
            for value in (portal.x, portal.y, portal.area_width, portal.area_height)
        )
        route_available = False
        if portal.is_active and geometry_available:
            try:
                resolve_destination_url(portal.destination)
            except ValidationError:
                pass
            else:
                route_available = True

        configured = portal.is_active and geometry_available and route_available
        requirements_met = (
            requirements_are_met(portal.access_requirements, user)
            and requirements_are_met(portal.destination.access_requirements, user)
        )
        portals.append({
            'portal_id': portal.code,
            'name': portal.name,
            'x': float(portal.x) if portal.x is not None else None,
            'y': float(portal.y) if portal.y is not None else None,
            'width': float(portal.area_width) if portal.area_width is not None else None,
            'height': float(portal.area_height) if portal.area_height is not None else None,
            'display_order': portal.display_order,
            'status': (
                'available' if configured and requirements_met
                else 'locked' if configured
                else 'pending'
            ),
            'launch_url': (
                reverse('ducky_arenas:portal_launch', kwargs={'portal_id': portal.code})
                if configured
                else None
            ),
        })
    return portals


def world_config(era, profile, position=None):
    try:
        world_map = WorldMap.objects.select_related('era').get(
            era__code=era.code,
            era__is_available=True,
            is_available=True,
        )
    except WorldMap.DoesNotExist as exc:
        raise ValidationError('El mapa de esa época no está disponible.') from exc

    if not isinstance(world_map.collision_config, list):
        raise ValidationError('La configuración de colisiones del mapa no es válida.')

    spawn = safe_spawn_position(world_map)
    character_asset = _character_asset(profile, era)
    character = _character_config(character_asset, era)
    landscape_paths = world_map.era.resource_config.get('landscapes', [])
    if not isinstance(landscape_paths, list) or any(
        not isinstance(path, str) for path in landscape_paths
    ):
        raise ValidationError('La configuración de paisajes de la época no es válida.')
    landscapes = [
        static(path)
        for path in landscape_paths
        if finders.find(path)
    ]

    if character is None:
        sprite_notice = 'No hay sprite de movimiento disponible para este personaje y época.'
    else:
        sprite_notice = (
            'El personaje cambia de orientación; no hay fotogramas suficientes '
            'para animar el ciclo de caminar.'
        )

    if profile is not None and profile.current_map_id == world_map.pk:
        initial_direction = profile.facing_direction
    else:
        initial_direction = 'down'

    return {
        'era': era.code,
        'label': era.label,
        'map': {
            'code': world_map.code,
            'url': static(world_map.static_path),
            'width': world_map.width,
            'height': world_map.height,
        },
        'player': {
            'x': (
                position['x'] if position is not None
                else spawn['x']
            ),
            'y': (
                position['y'] if position is not None
                else spawn['y']
            ),
            'direction': initial_direction,
            'version': profile.exploration_version if profile is not None else 0,
            'character': character,
            'portrait_url': (
                static(character_asset.static_image_path)
                if character_asset and character_asset.static_image_path
                else ''
            ),
        },
        'landscapes': landscapes,
        'checkpoint_interval_ms': settings.DUCKY_ARENAS_CHECKPOINT_INTERVAL_MS
        if hasattr(settings, 'DUCKY_ARENAS_CHECKPOINT_INTERVAL_MS')
        else 5000,
        'collision_zones': world_map.collision_config,
        'portals': _portal_config(
            world_map,
            profile.user if profile is not None and profile.user_id else None,
        ),
        'debug_allowed': settings.DEBUG,
        'sprite_notice': sprite_notice,
    }
