"""Durable ordered public facts. Callers hold the Duel row lock."""

import logging

from asgiref.sync import async_to_sync
from channels.exceptions import ChannelFull
from channels.layers import get_channel_layer
from django.db import connection, transaction
from django.db.models import Max
from redis.exceptions import RedisError

from .models import DuelEvent

logger = logging.getLogger(__name__)


def notify(duel_id):
    layer = get_channel_layer()
    if layer is None:
        logger.error('Duel channel layer is not configured')
        return
    try:
        async_to_sync(layer.group_send)(f'clash.duel.{duel_id}', {'type': 'duel.update'})
    except (ChannelFull, ConnectionError, RedisError):
        logger.exception('Duel realtime hint failed; durable snapshots remain available: %s', duel_id)


def emit(duel, kind, *, actor=None, payload=None):
    if not connection.in_atomic_block:
        raise RuntimeError('Los eventos deben confirmarse junto a su transición.')
    sequence = (duel.events.aggregate(last=Max('sequence'))['last'] or 0) + 1
    event = DuelEvent.objects.create(
        duel=duel, sequence=sequence, kind=kind, actor=actor, payload=payload or {},
    )
    transaction.on_commit(lambda: notify(duel.pk))
    return event
