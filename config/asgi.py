"""ASGI config for mihi_training project."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

application = get_asgi_application()

from config.routing import application as routed_application  # noqa: E402

application = routed_application
