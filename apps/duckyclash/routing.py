from django.urls import path

from .consumers import DuelConsumer

websocket_urlpatterns = [
    path('ws/clash/duels/<int:duel_id>/', DuelConsumer.as_asgi()),
]
