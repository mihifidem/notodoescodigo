import logging
import re

from django.core.exceptions import ImproperlyConfigured

from .models import InternalPrompt


logger = logging.getLogger(__name__)
_PLACEHOLDER = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)\}')


def get_internal_prompt(key):
    try:
        return InternalPrompt.objects.get(key=key, active=True)
    except InternalPrompt.DoesNotExist as exc:
        logger.error('Missing active internal AI prompt: %s', key)
        raise ImproperlyConfigured(f'Falta el prompt interno activo: {key}') from exc


def render_internal_prompt(key, **values):
    prompt = get_internal_prompt(key)
    prompt.full_clean()
    if set(values) != set(prompt.variables):
        logger.error('Invalid template variables for internal AI prompt: %s', key)
        raise ImproperlyConfigured(f'Variables incorrectas para el prompt interno: {key}')
    return _PLACEHOLDER.sub(
        lambda match: str(values[match.group(1)]) if match.group(1) in values else match.group(0),
        prompt.content,
    )
