from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

from apps.lms import routing as lms_routing
from apps.duckyclash import routing as clash_routing

application = ProtocolTypeRouter({
    'http': get_asgi_application(),
    'websocket': AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(
                lms_routing.websocket_urlpatterns + clash_routing.websocket_urlpatterns,
            )
        )
    ),
})
