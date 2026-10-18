"""Read-only presentation helpers for the configurable profession catalog."""

from apps.ducky_arenas.models import Profession


ATTRIBUTE_LABELS = {
    'attack': 'Ataque',
    'defense': 'Defensa',
    'speed': 'Velocidad',
    'intelligence': 'Inteligencia',
    'logic': 'Lógica',
    'quack': 'Quack',
}

PRIORITY_LABELS = {
    'low': 'Baja',
    'low_medium': 'Baja-media',
    'medium': 'Media',
    'medium_high': 'Media-alta',
    'high': 'Alta',
    'very_high': 'Muy alta',
}


def attribute_priority_rows(profession: Profession) -> list[dict[str, str]]:
    """Return configured qualitative priorities without inventing point values."""
    recommendations = profession.attribute_recommendations or {}
    return [
        {
            'code': code,
            'label': label,
            'priority': recommendations.get(code, ''),
            'priority_label': PRIORITY_LABELS.get(
                recommendations.get(code),
                'Sin definir',
            ),
        }
        for code, label in ATTRIBUTE_LABELS.items()
    ]


def profession_access_error(profession: Profession, progress_snapshot) -> str:
    """Return a server-side access error for unmet official XP requirements."""
    requirements = profession.access_requirements
    if not isinstance(requirements, dict) or set(requirements) - {'min_xp'}:
        return 'Los requisitos de acceso de esta profesión no son válidos.'
    minimum_xp = requirements.get('min_xp', 0)
    if type(minimum_xp) is not int or minimum_xp < 0:
        return 'Los requisitos de acceso de esta profesión no son válidos.'
    if minimum_xp and (
        progress_snapshot is None or progress_snapshot.xp_total < minimum_xp
    ):
        return f'Esta profesión requiere {minimum_xp} XP oficiales.'
    return ''
