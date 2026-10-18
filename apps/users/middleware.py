"""Middleware that updates the user's daily streak on every request."""
from django.utils import timezone


class StreakMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            today = timezone.now().date()
            if request.user.fecha_ultimo_acceso != today:
                request.user.actualizar_streak()
        return self.get_response(request)
