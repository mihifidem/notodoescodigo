import json
import uuid
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.db import transaction
from django.test import TransactionTestCase, override_settings
from django.test import Client

from .models import DuelAnswer, DuelEvent, DuelItemUse
from .routing import websocket_urlpatterns
from . import test_engine as engine_tests


@override_settings(
    DEBUG=True,
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.ProfileBalanceGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.knowledge.LMSKnowledgeGateway',
    DUCKYCLASH_ECOMOTOR_GATEWAY='apps.duckyclash.ecomotor.DevelopmentFakeEcoMotor',
    DUCKYCLASH_DOUBLE_KO='draw', DUCKYCLASH_MAX_TIEBREAKS=1, DUCKYCLASH_ENGINE_RULES={},
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class RealtimeTests(TransactionTestCase):
    create = engine_tests.DuelEngineTests.create
    accept = engine_tests.DuelEngineTests.accept
    publish = engine_tests.DuelEngineTests.publish
    at = engine_tests.DuelEngineTests.at
    begin = engine_tests.DuelEngineTests.begin
    active = engine_tests.DuelEngineTests.active
    option = engine_tests.DuelEngineTests.option
    use = engine_tests.DuelEngineTests.use

    def setUp(self):
        engine_tests.DuelEngineTests.setUp(self)
        cache.clear()

    def socket(self, actor, duel_id=None):
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns), f'/ws/clash/duels/{duel_id or self.duel.pk}/',
        )
        communicator.scope['user'] = actor
        return communicator

    async def state(self, socket):
        for _ in range(30):
            message = await socket.receive_json_from(timeout=3)
            if 'state' in message:
                return message['state']
        self.fail('No se recibió snapshot.')

    async def acknowledgement(self, socket):
        for _ in range(30):
            message = await socket.receive_json_from(timeout=3)
            if message.get('type') == 'ack':
                return message
        self.fail('No se recibió confirmación.')

    def test_authentication_room_and_origin_restrictions(self):
        async def run():
            for actor, room, code in (
                (AnonymousUser(), self.duel.pk, 4401),
                (self.outsider, self.duel.pk, 4403),
                (self.creator, self.duel.pk + 999, 4403),
            ):
                socket = self.socket(actor, room)
                self.assertEqual(await socket.connect(), (False, code))
                await socket.disconnect()
            from config.routing import application
            socket = WebsocketCommunicator(application, f'/ws/clash/duels/{self.duel.pk}/',
                                           headers=[(b'origin', b'https://malicious.invalid')])
            self.assertFalse((await socket.connect())[0])
            await socket.disconnect()
        async_to_sync(run)()

    @override_settings(ALLOWED_HOSTS=['localhost'])
    def test_existing_asgi_session_middleware_authenticates_real_cookie(self):
        client = Client()
        client.force_login(self.creator)
        from django.conf import settings
        headers = [
            (b'origin', b'http://localhost'),
            (b'cookie', f'{settings.SESSION_COOKIE_NAME}={client.session.session_key}'.encode()),
        ]
        async def run():
            from config.routing import application
            socket = WebsocketCommunicator(application, f'/ws/clash/duels/{self.duel.pk}/', headers=headers)
            self.assertTrue((await socket.connect())[0])
            self.assertEqual((await self.state(socket))['phase'], 'waiting_players')
            await socket.disconnect()
        async_to_sync(run)()

    def test_broadcast_hint_is_sent_only_after_commit_not_on_rollback(self):
        from .events import emit
        with patch('apps.duckyclash.events.notify') as notify:
            with transaction.atomic():
                emit(self.duel, 'player.left', actor=self.creator)
                notify.assert_not_called()
            notify.assert_called_once_with(self.duel.pk)
            notify.reset_mock()
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    emit(self.duel, 'player.left', actor=self.creator)
                    raise ValueError('rollback')
            notify.assert_not_called()

    def test_reconnect_snapshot_never_repeats_countdown_item_consumption(self):
        question = self.begin()
        action = str(uuid.uuid4())
        async def run():
            for _ in range(2):
                socket = self.socket(self.creator)
                self.assertTrue((await socket.connect())[0])
                state = await self.state(socket)
                self.assertIsNone(state['question'])
                await socket.send_json_to({'type': 'item', 'question_id': question.pk,
                                           'code': 'NUBE', 'action_id': action})
                await self.state(socket)
                await socket.disconnect()
        with self.at(1):
            async_to_sync(run)()
        self.assertEqual(DuelItemUse.objects.count(), 1)

    def test_cloud_target_receives_no_options_or_correction(self):
        question = self.begin()
        with self.at(1):
            self.use(question, 'NUBE')
        with self.at(3):
            self.engine.advance()
            async def run():
                socket = self.socket(self.opponent)
                self.assertTrue((await socket.connect())[0])
                state = await self.state(socket)
                self.assertEqual(state['question']['options'], [])
                serialized = json.dumps(state)
                for secret in ('correct_answers', 'validation', 'is_correct', 'original_options'):
                    self.assertNotIn(secret, serialized)
                await socket.disconnect()
            async_to_sync(run)()

    def test_websocket_presence_opens_question_without_worker(self):
        question = self.begin()

        async def run():
            socket = self.socket(self.creator)
            self.assertTrue((await socket.connect())[0])
            self.assertEqual((await self.state(socket))['phase'], 'countdown')
            await socket.send_json_to({'type': 'presence'})
            await self.acknowledgement(socket)
            state = await self.state(socket)
            self.assertEqual(state['phase'], 'question_active')
            self.assertEqual(state['question']['id'], question.pk)
            self.assertEqual(len(state['question']['options']), 4)
            self.assertNotIn('correct_answers', json.dumps(state))
            await socket.disconnect()

        with self.at(3):
            async_to_sync(run)()
        self.assertEqual(self.duel.questions.count(), 1)

    def test_second_websocket_answer_resolves_before_deadline(self):
        from .questions import submit_answer
        from .models import CombatSession
        question = self.active()
        opponent_option = question.player_options.get(participant__player=self.opponent).options[0]['id']
        with self.at(4):
            submit_answer(actor=self.creator, question_id=question.pk, option_id=self.option(question))

            async def run():
                socket = self.socket(self.opponent)
                self.assertTrue((await socket.connect())[0])
                await self.state(socket)
                await socket.send_json_to({
                    'type': 'answer', 'question_id': question.pk, 'option_id': opponent_option,
                })
                await self.acknowledgement(socket)
                state = await self.state(socket)
                self.assertEqual(state['phase'], 'round_result')
                await socket.disconnect()

            async_to_sync(run)()
        self.assertEqual(CombatSession.objects.get(duel=self.duel).phase, 'round_result')
        self.assertEqual(question.damage_resolution.status, 'applied')
        self.assertEqual(question.answers.count(), 2)

    def test_answer_repeated_and_delayed_is_immutable(self):
        question = self.active()
        option = self.option(question)
        async def send_answer(offset, selected):
            socket = self.socket(self.creator)
            await socket.connect()
            await self.state(socket)
            await socket.send_json_to({'type': 'answer', 'question_id': question.pk, 'option_id': selected})
            message = await socket.receive_json_from()
            self.assertEqual(message['type'], offset)
            await socket.disconnect()
        with self.at(5):
            async_to_sync(send_answer)('ack', option)
            async_to_sync(send_answer)('ack', option)
            async_to_sync(send_answer)('error', self.option(question, 'B'))
        with self.at(99):
            async_to_sync(send_answer)('ack', option)
        self.assertEqual(DuelAnswer.objects.count(), 1)
        self.assertEqual(DuelEvent.objects.filter(kind='answer.locked').count(), 1)

    def test_revocation_is_checked_on_incoming_and_outgoing_events(self):
        async def run(push):
            socket = self.socket(self.creator)
            await socket.connect()
            await self.state(socket)
            await database_sync_to_async(get_user_model().objects.filter(pk=self.creator.pk).update)(is_active=False)
            if push:
                from channels.layers import get_channel_layer
                await get_channel_layer().group_send(f'clash.duel.{self.duel.pk}', {'type': 'duel.update'})
            else:
                await socket.send_json_to({'type': 'sync', 'after': 0})
            self.assertEqual((await socket.receive_output(timeout=3))['code'], 4403)
            await socket.disconnect()
            await database_sync_to_async(get_user_model().objects.filter(pk=self.creator.pk).update)(is_active=True)
        async_to_sync(run)(False)
        async_to_sync(run)(True)

    def test_invalid_types_targets_cursors_size_binary_and_frequency(self):
        async def run():
            socket = self.socket(self.creator)
            await socket.connect()
            await self.state(socket)
            for message in (
                {'type': 'ready', 'target_id': self.opponent.pk},
                {'type': 'sync', 'after': True},
                {'type': 'answer', 'question_id': True, 'option_id': 'x'},
                {'type': ['ready']}, [], {'type': 'finished'},
            ):
                await socket.send_json_to(message)
                self.assertEqual((await socket.receive_json_from())['code'], 'invalid_message')
            await socket.send_to(text_data='x' * 2049)
            self.assertEqual((await socket.receive_output())['code'], 4400)
            await socket.disconnect()
            socket = self.socket(self.creator)
            await socket.connect()
            await self.state(socket)
            await socket.send_to(bytes_data=b'bad')
            self.assertEqual((await socket.receive_output())['code'], 4400)
            await socket.disconnect()
            socket = self.socket(self.creator)
            await socket.connect()
            await self.state(socket)
            for _ in range(20):
                await socket.send_json_to({})
                await socket.receive_json_from()
            await socket.send_json_to({})
            self.assertEqual((await socket.receive_output())['code'], 4429)
            await socket.disconnect()
        async_to_sync(run)()

    def test_cross_room_question_is_rejected_and_channel_payload_is_ignored(self):
        async def run():
            socket = self.socket(self.creator)
            await socket.connect()
            await self.state(socket)
            from channels.layers import get_channel_layer
            await get_channel_layer().group_send(f'clash.duel.{self.duel.pk}', {
                'type': 'duel.update', 'options': ['SECRET'], 'correct_answers': ['SECRET'],
            })
            self.assertNotIn('SECRET', json.dumps(await self.state(socket)))
            await socket.send_json_to({'type': 'answer', 'question_id': 999999, 'option_id': 'x'})
            self.assertEqual((await socket.receive_output())['code'], 4403)
            await socket.disconnect()
        async_to_sync(run)()

    def test_worker_database_updates_are_recovered_without_shared_channel_layer(self):
        self.begin()
        async def run():
            socket = self.socket(self.creator)
            await socket.connect()
            first = await self.state(socket)
            self.assertEqual(first['phase'], 'countdown')
            with self.at(3):
                await database_sync_to_async(self.engine.advance)()
                for _ in range(5):
                    state = await self.state(socket)
                    if state['phase'] == 'question_active':
                        break
                self.assertEqual(state['phase'], 'question_active')
            await socket.disconnect()
        with self.at(1):
            async_to_sync(run)()

    def test_logged_out_session_is_revoked_on_next_snapshot(self):
        client = Client()
        client.force_login(self.creator)
        from importlib import import_module
        from django.conf import settings
        session = import_module(settings.SESSION_ENGINE).SessionStore(session_key=client.session.session_key)
        async def run():
            socket = self.socket(self.creator)
            socket.scope['session'] = session
            await socket.connect()
            await self.state(socket)
            await database_sync_to_async(client.logout)()
            from channels.layers import get_channel_layer
            await get_channel_layer().group_send(f'clash.duel.{self.duel.pk}', {'type': 'duel.update'})
            self.assertEqual((await socket.receive_output(timeout=3))['code'], 4403)
            await socket.disconnect()
        async_to_sync(run)()

    def test_ready_enter_leave_and_cursor_recovery_are_authorized(self):
        async def run():
            socket = self.socket(self.creator)
            await socket.connect()
            await self.state(socket)
            for kind in ('enter', 'ready', 'presence', 'leave'):
                await socket.send_json_to({'type': kind})
                await self.acknowledgement(socket)
                await self.state(socket)
            await socket.send_json_to({'type': 'sync', 'after': 0})
            await self.acknowledgement(socket)
            state = await self.state(socket)
            self.assertTrue(state['events'])
            self.assertEqual([e['sequence'] for e in state['events']], sorted(e['sequence'] for e in state['events']))
            await socket.disconnect()
        with self.at():
            async_to_sync(run)()
        self.assertEqual(DuelEvent.objects.filter(kind='player.ready').count(), 1)

    def test_new_answer_after_deadline_is_rejected(self):
        question = self.active()
        async def run():
            socket = self.socket(self.creator)
            await socket.connect()
            await self.state(socket)
            await socket.send_json_to({'type': 'answer', 'question_id': question.pk,
                                       'option_id': self.option_id})
            self.assertEqual((await socket.receive_json_from())['code'], 'action_rejected')
            await socket.disconnect()
        self.option_id = self.option(question)
        with self.at(33):
            async_to_sync(run)()
        self.assertFalse(DuelAnswer.objects.exists())

    def test_user_limit_is_shared_between_connections(self):
        async def run():
            sockets = [self.socket(self.creator), self.socket(self.creator)]
            for socket in sockets:
                await socket.connect()
                await self.state(socket)
            for socket in sockets:
                for _ in range(19):
                    await socket.send_json_to({})
                    self.assertEqual((await socket.receive_json_from())['code'], 'invalid_message')
            await sockets[1].send_json_to({})
            self.assertEqual((await sockets[1].receive_output())['code'], 4429)
            for socket in sockets:
                await socket.disconnect()
        async_to_sync(run)()
