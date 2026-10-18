"""Validation for map-relative exploration coordinates."""

import math
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from apps.ducky_arenas.models import WorldMap

from .progression import get_era, unlocked_eras_for_user


def _decimal_coordinate(value, field_name, maximum):
    if isinstance(value, bool):
        raise ValidationError(f'{field_name} debe ser un número válido.')
    try:
        coordinate = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f'{field_name} debe ser un número válido.') from exc
    if (
        not coordinate.is_finite()
        or coordinate < 0
        or coordinate > maximum
    ):
        raise ValidationError('La posición está fuera de los límites del mapa.')
    return coordinate.quantize(Decimal('0.01'))


def _valid_zone_point(zone):
    mode = zone.get('mode', 'obstacle')
    shape = zone.get('type', 'rect')
    if mode not in {'obstacle', 'building-entry', 'walkable'}:
        return None

    if shape == 'rect':
        values = (zone.get('x'), zone.get('y'), zone.get('width'), zone.get('height'))
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise ValidationError('La configuración de colisiones del mapa no es válida.')
        x, y, width, height = values
        if (
            not all(_finite_number(value) for value in values)
            or width <= 0
            or height <= 0
        ):
            raise ValidationError('La configuración de colisiones del mapa no es válida.')
        return ('rect', mode, (x, y, width, height))

    if shape == 'polygon':
        points = zone.get('points')
        if (
            not isinstance(points, list)
            or len(points) < 3
            or any(
                not isinstance(point, (list, tuple))
                or len(point) != 2
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not _finite_number(value)
                    for value in point
                )
                for point in points
            )
        ):
            raise ValidationError('La configuración de colisiones del mapa no es válida.')
        return ('polygon', mode, points)

    return None


def _finite_number(value):
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _point_in_polygon(x, y, points):
    inside = False
    previous_x, previous_y = points[-1]
    for current_x, current_y in points:
        if (current_y > y) != (previous_y > y):
            crossing_x = (
                (previous_x - current_x) * (y - current_y)
                / (previous_y - current_y)
                + current_x
            )
            if x < crossing_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def position_is_safe(world_map, x, y):
    if not isinstance(world_map.collision_config, list):
        raise ValidationError('La configuración de colisiones del mapa no es válida.')

    parsed_zones = []
    for zone in world_map.collision_config:
        if not isinstance(zone, dict):
            raise ValidationError('La configuración de colisiones del mapa no es válida.')
        parsed = _valid_zone_point(zone)
        if parsed is not None:
            parsed_zones.append(parsed)

    inside_walkable = [
        _zone_contains(x, y, shape, geometry)
        for shape, mode, geometry in parsed_zones
        if mode == 'walkable'
    ]
    if inside_walkable and not any(inside_walkable):
        return False

    return not any(
        _zone_contains(x, y, shape, geometry)
        for shape, mode, geometry in parsed_zones
        if mode in {'obstacle', 'building-entry'}
    )


def _zone_contains(x, y, shape, geometry):
    if shape == 'rect':
        left, top, width, height = geometry
        return left <= x <= left + width and top <= y <= top + height
    return _point_in_polygon(x, y, geometry)


def safe_spawn_position(world_map):
    configured_x = (
        float(world_map.spawn_x) * world_map.width
        if world_map.spawn_x is not None
        else world_map.width / 2
    )
    configured_y = (
        float(world_map.spawn_y) * world_map.height
        if world_map.spawn_y is not None
        else world_map.height * 0.78
    )
    candidates = (
        (configured_x, configured_y),
        (world_map.width / 2, world_map.height / 2),
        (world_map.width * 0.1, world_map.height * 0.1),
        (world_map.width * 0.9, world_map.height * 0.1),
        (world_map.width * 0.1, world_map.height * 0.9),
        (world_map.width * 0.9, world_map.height * 0.9),
    )
    for x, y in candidates:
        if position_is_safe(world_map, x, y):
            return {'x': round(x, 2), 'y': round(y, 2)}
    raise ValidationError('El mapa no tiene un punto de aparición seguro configurado.')


def validate_position(user, era_code, x, y, world_map=None):
    era = get_era(era_code)
    if era not in unlocked_eras_for_user(user):
        raise ValidationError('No tienes permiso para explorar esa época.')

    if world_map is None:
        world_map = WorldMap.objects.select_related('era').filter(
            era__code=era.code,
            era__is_available=True,
            is_available=True,
        ).first()
    elif not isinstance(world_map, WorldMap):
        world_map = WorldMap.objects.select_related('era').filter(
            pk=world_map,
            era__code=era.code,
            era__is_available=True,
            is_available=True,
        ).first()
    if (
        world_map is None
        or world_map.era_id is None
        or world_map.era.code != era.code
        or not world_map.is_available
        or not world_map.era.is_available
    ):
        raise ValidationError('El mapa solicitado no está disponible.')

    coordinate_x = _decimal_coordinate(x, 'x', Decimal(world_map.width))
    coordinate_y = _decimal_coordinate(y, 'y', Decimal(world_map.height))
    if not position_is_safe(world_map, float(coordinate_x), float(coordinate_y)):
        raise ValidationError('La posición está dentro de una zona no transitable.')

    return {
        'era': era.code,
        'x': float(coordinate_x),
        'y': float(coordinate_y),
        'world_map': world_map,
    }
