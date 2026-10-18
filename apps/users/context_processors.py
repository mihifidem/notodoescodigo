"""Context processors for the users app."""
from .notifications import platform_notifications


def notificaciones_no_leidas(request):
    """Inject unread notification count into every template context."""
    if request.user.is_authenticated:
        count = platform_notifications(request.user).filter(leida=False).count()
        return {'notificaciones_count': count}
    return {'notificaciones_count': 0}
