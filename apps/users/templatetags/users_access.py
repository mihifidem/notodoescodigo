from django import template

from apps.users.access import can, effective_plan


register = template.Library()


@register.simple_tag
def can_access(actor, action, resource=None):
    return can(actor, action, resource).allowed


@register.simple_tag
def current_access_plan(actor):
    return effective_plan(actor)
