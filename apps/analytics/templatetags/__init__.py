from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Return dict[key], useful for dict lookups in templates."""
    return dictionary.get(key)
