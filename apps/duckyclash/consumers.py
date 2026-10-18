"""Private transport: channel messages contain hints, never question options."""

import asyncio
import json
import logging
from collections import deque
from importlib import import_module
from time import monotonic
from types import SimpleNamespace

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.cache import cache
from django.conf import settings
from django.contrib.auth import get_user
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, transaction
from redis.exceptions import RedisError

from .engine import DuelEngine, require_engine_access
from .events import emit
from .items import apply_item
from .knowledge import KnowledgeUnavailable
from .models import Duel
from .questions import submit_answer

logger = logging.getLogger(__name__)


class DuelConsumer(AsyncWebsocketConsumer):
    async def close(self, code=None, reason=None):
        self.active = False
        await super().close(code=code, reason=reason)

    async def connect(self):
        self.duel_id = self.scope['url_route']['kwargs']['duel_id']
        self.group_name = f'clash.duel.{self.duel_id}'
        self.cursor = 0
        self.requests = deque()
        self.joined = False
        self.poller = None
        self.active = True
        self.delivery_lock = asyncio.Lock()
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return
        try:
            await self.authorize()
        except (PermissionDenied, Duel.DoesNotExist):
            await self.close(code=4403)
            return
        except (OperationalError, RedisError):
            logger.exception('Duel socket authorization unavailable: %s', self.duel_id)
            await self.close(code=1013)
            return
        try:
            if not await self.shared_limit():
                await self.close(code=4429)
                return
        except RedisError:
            logger.exception('Duel socket connection limiter unavailable')
            await self.close(code=1013)
            return
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        self.joined = True
        await self.accept()
        await self.deliver('connected')
        self.poller = asyncio.create_task(self.poll_state())

    async def disconnect(self, close_code):
        logger.debug('Duel socket disconnected: duel=%s code=%s', self.duel_id, close_code)
        self.active = False
        if self.poller:
            self.poller.cancel()
            try:
                await self.poller
            except asyncio.CancelledError:
                pass
        if self.joined:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            try:
                await self.leave()
            except (PermissionDenied, Duel.DoesNotExist):
                pass  # Revoked players cannot append events.
            except OperationalError:
                logger.exception('Duel socket leave could not be persisted: %s', self.duel_id)

    @database_sync_to_async
    def authorize(self):
        return self.checked_actor()

    def checked_actor(self):
        duel = Duel.objects.select_related('agreement').get(pk=self.duel_id)
        if 'session' in self.scope:
            store = import_module(settings.SESSION_ENGINE).SessionStore(
                session_key=self.scope['session'].session_key,
            )
            authenticated = get_user(SimpleNamespace(session=store))
            if not authenticated.is_authenticated or authenticated.pk != self.scope['user'].pk:
                raise PermissionDenied('La sesión ha caducado.')
        return require_engine_access(self.scope['user'], duel)

    @database_sync_to_async
    def leave(self):
        with transaction.atomic():
            duel = Duel.objects.select_for_update().select_related('agreement').get(pk=self.duel_id)
            actor = self.checked_actor()
            emit(duel, 'player.left', actor=actor)
        # Presence expires normally: closing one tab must not disconnect another.

    @database_sync_to_async
    def shared_limit(self):
        key = f'clash:socket-rate:{self.scope["user"].pk}'
        if cache.add(key, 1, timeout=10):
            return True
        try:
            return cache.incr(key) <= 40
        except ValueError:
            return False  # Expired between add/incr; retry in the next window.

    async def receive(self, text_data=None, bytes_data=None):
        try:
            await self.authorize()
        except (PermissionDenied, Duel.DoesNotExist):
            await self.close(code=4403)
            return
        except (OperationalError, RedisError):
            logger.exception('Duel socket action authorization unavailable: %s', self.duel_id)
            await self.close(code=1013)
            return
        now = monotonic()
        while self.requests and self.requests[0] <= now - 10:
            self.requests.popleft()
        self.requests.append(now)
        try:
            allowed = len(self.requests) <= 20 and await self.shared_limit()
        except RedisError:
            logger.exception('Duel socket rate limiter unavailable')
            await self.close(code=1013)
            return
        if not allowed:
            await self.close(code=4429)
            return
        if bytes_data is not None or text_data is None or len(text_data.encode('utf-8')) > 2048:
            await self.close(code=4400)
            return
        try:
            message = json.loads(text_data)
            self.validate_message(message)
        except (ValueError, TypeError, RecursionError, ValidationError):
            await self.send_json({'type': 'error', 'code': 'invalid_message'})
            return
        try:
            await self.execute(message)
            async with self.delivery_lock:
                if message['type'] == 'sync':
                    self.cursor = message['after']
                await self.send_json({'type': 'ack', 'action': message['type']})
                await self.deliver_locked('state')
        except (PermissionDenied, Duel.DoesNotExist):
            self.active = False
            await self.close(code=4403)
        except ValidationError:
            await self.send_json({'type': 'error', 'code': 'action_rejected'})
        except KnowledgeUnavailable:
            logger.warning('Duel socket provider unavailable: %s', self.duel_id)
            await self.send_json({'type': 'error', 'code': 'provider_unavailable'})
        except IntegrityError:
            logger.warning('Concurrent duel socket action: %s', self.duel_id)
            await self.send_json({'type': 'error', 'code': 'action_conflict'})
        except OperationalError:
            logger.exception('Duel socket database action failed: %s', self.duel_id)
            await self.send_json({'type': 'error', 'code': 'state_unavailable'})

    @staticmethod
    def validate_message(message):
        fields = {
            'sync': {'type', 'after'}, 'enter': {'type'}, 'leave': {'type'},
            'presence': {'type'}, 'ready': {'type'},
            'answer': {'type', 'question_id', 'option_id'},
            'item': {'type', 'question_id', 'code', 'action_id'},
        }
        if not isinstance(message, dict) or not isinstance(message.get('type'), str):
            raise ValidationError('Mensaje no válido.')
        kind = message['type']
        if kind not in fields or set(message) != fields[kind]:
            raise ValidationError('Campos no admitidos.')
        if kind == 'sync' and (type(message['after']) is not int or not 0 <= message['after'] < 10**18):
            raise ValidationError('Cursor no válido.')
        if kind in ('answer', 'item'):
            if type(message['question_id']) is not int or not 0 < message['question_id'] < 10**18:
                raise ValidationError('Pregunta no válida.')
            for field in fields[kind] - {'type', 'question_id'}:
                if not isinstance(message[field], str) or not 1 <= len(message[field]) <= 80:
                    raise ValidationError('Acción no válida.')

    @database_sync_to_async
    def execute(self, message):
        duel = Duel.objects.select_related('agreement').get(pk=self.duel_id)
        actor = self.checked_actor()
        engine = DuelEngine(self.duel_id)
        kind = message['type']
        if kind == 'ready':
            engine.ready(actor)
            try:
                engine.advance()
            except ValidationError:
                logger.exception('Readiness committed; duel advancement requires recovery: %s', self.duel_id)
        elif kind in ('enter', 'presence'):
            engine.enter(actor)
            engine.advance()
        elif kind == 'leave':
            with transaction.atomic():
                duel = Duel.objects.select_for_update().get(pk=self.duel_id)
                emit(duel, 'player.left', actor=actor)
        elif kind in ('answer', 'item'):
            if not duel.questions.filter(pk=message['question_id']).exists():
                raise PermissionDenied('La pregunta pertenece a otra sala.')
            if kind == 'answer':
                submit_answer(actor=actor, question_id=message['question_id'], option_id=message['option_id'])
                try:
                    engine.advance()
                except ValidationError:
                    logger.exception('Answer committed; duel advancement requires recovery: %s', self.duel_id)
            else:
                apply_item(actor=actor, question_id=message['question_id'],
                           code=message['code'], action_id=message['action_id'])
        # sync is read-only; snapshot rechecks authorization before serialization.

    @database_sync_to_async
    def snapshot(self):
        # Includes current session validity, not just the identity cached on connect.
        self.checked_actor()
        return DuelEngine(self.duel_id).snapshot(self.scope['user'], after=self.cursor)

    async def deliver(self, kind):
        async with self.delivery_lock:
            await self.deliver_locked(kind)

    async def deliver_locked(self, kind):
        if not self.active:
            return
        try:
            state = await self.snapshot()
        except (PermissionDenied, Duel.DoesNotExist):
            self.active = False
            await self.close(code=4403)
            return
        except (OperationalError, RedisError, ValidationError):
            logger.exception('Duel socket snapshot failed: %s', self.duel_id)
            await self.send_json({'type': 'error', 'code': 'state_unavailable'})
            return
        self.cursor = state['cursor']
        await self.send_json({'type': kind, 'state': state})

    async def duel_update(self, event):
        # Ignore channel payloads: each recipient gets freshly authorized private data.
        if event.get('type') == 'duel.update':
            await self.deliver('state')

    async def poll_state(self):
        # Existing layer is in-memory. Recover worker commits across processes too.
        try:
            while self.active:
                await asyncio.sleep(1)
                await self.deliver('state')
        except asyncio.CancelledError:
            raise

    async def send_json(self, payload):
        await self.send(text_data=json.dumps(payload))
