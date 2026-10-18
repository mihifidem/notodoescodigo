"""Presentation helpers for attributes read from the EcoMotor adapter."""


CHARACTERISTIC_LABELS = (
    ('attack', 'ATK', 'Ataque'),
    ('defense', 'DEF', 'Defensa'),
    ('speed', 'SPE', 'Velocidad'),
    ('intelligence', 'INT', 'Inteligencia'),
    ('logic', 'LOG', 'Lógica'),
    ('quack', 'QUA', 'Quack'),
)


def official_characteristics(progress_snapshot):
    values = progress_snapshot.attributes if progress_snapshot is not None else None
    return [
        {
            'code': code,
            'label': label,
            'value': values[key] if values is not None else '—',
        }
        for key, code, label in CHARACTERISTIC_LABELS
    ]
