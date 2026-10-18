"""Server-side destination resolution and portal authorization."""

from django.core.exceptions import ValidationError
from django.urls import NoReverseMatch, reverse

from apps.ducky_arenas.models import GameDestination, Portal


def resolve_destination_url(destination):
    if destination.status != GameDestination.Status.AVAILABLE:
        raise ValidationError('El destino todavía no está disponible.')
    namespace, separator, _route = destination.entry_url_name.partition(':')
    if not separator or namespace != destination.owner_app:
        raise ValidationError('La ruta del destino no está configurada correctamente.')
    try:
        return reverse(destination.entry_url_name)
    except NoReverseMatch as exc:
        raise ValidationError('La ruta del destino no está disponible.') from exc


def validate_access_requirements(requirements, user):
    if not isinstance(requirements, dict):
        raise ValidationError('Los requisitos de acceso no son válidos.')
    unsupported = set(requirements) - {'min_total_points'}
    if unsupported:
        raise ValidationError('Hay requisitos de acceso todavía no integrados.')
    minimum = requirements.get('min_total_points')
    if minimum is not None:
        if type(minimum) is not int or minimum < 0:
            raise ValidationError('El requisito de puntos no es válido.')
        if user is None:
            raise ValidationError('Se requiere una sesión autenticada.')
        if user.puntos_totales < minimum:
            raise ValidationError('Aún no cumples los requisitos de este portal.')


def requirements_are_met(requirements, user):
    try:
        validate_access_requirements(requirements, user)
    except ValidationError:
        return False
    return True


def authorize_portal(portal_id, era_code, x, y, user):
    portal = (
        Portal.objects.select_related(
            'world_map',
            'world_map__era',
            'destination',
        )
        .filter(
            code=portal_id,
            world_map__era__code=era_code,
        )
        .first()
    )
    if (
        portal is None
        or not portal.is_active
        or not portal.world_map.is_available
        or portal.world_map.era_id is None
        or not portal.world_map.era.is_available
    ):
        raise ValidationError('Este portal todavía no está disponible.')
    if any(value is None for value in (
        portal.x,
        portal.y,
        portal.area_width,
        portal.area_height,
    )):
        raise ValidationError('El área de interacción de este portal no está configurada.')
    player_x = float(x)
    player_y = float(y)
    if not (
        float(portal.x) <= player_x <= float(portal.x + portal.area_width)
        and float(portal.y) <= player_y <= float(portal.y + portal.area_height)
    ):
        raise ValidationError('Acércate al portal antes de interactuar.')
    validate_access_requirements(portal.access_requirements, user)
    validate_access_requirements(portal.destination.access_requirements, user)
    return portal, resolve_destination_url(portal.destination)
