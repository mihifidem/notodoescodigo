import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import OperationalError, close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notificacion
from .economy import reconcile_duel
from .engine import DuelEngine, Phase
from .history import participant_metrics, publish_result, summary
from .models import (
    Challenge, CombatSession, Duel, DuelResolution, NotificationReceipt,
    ParticipantState, ProfileResultUpdate, RuleConfiguration,
)
from .notifications import deliver, upcoming_notice
from .questions import submit_answer
from .rules import current_rules
from .services import counter_offer, decline_offer
from .tasks import advance_duel_engines, expire_open_challenges
from . import test_engine as engine_tests


@override_settings(
    DEBUG=True, DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.ProfileBalanceGateway',
    DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.knowledge.LMSKnowledgeGateway',
    DUCKYCLASH_ECOMOTOR_GATEWAY='apps.duckyclash.ecomotor.DevelopmentFakeEcoMotor',
    DUCKYCLASH_PAYMENT_GATEWAY='apps.duckyclash.test_economy.FakePaymentBank',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
    DUCKYCLASH_ENGINE_RULES={}, DUCKYCLASH_ITEM_RULES={},
)
class ClashIntegrationTests(TransactionTestCase):
    create = engine_tests.DuelEngineTests.create
    accept = engine_tests.DuelEngineTests.accept
    publish = engine_tests.DuelEngineTests.publish
    at = engine_tests.DuelEngineTests.at
    begin = engine_tests.DuelEngineTests.begin
    active = engine_tests.DuelEngineTests.active
    use = engine_tests.DuelEngineTests.use
    option = engine_tests.DuelEngineTests.option

    def setUp(self):
        with override_settings(DUCKYCLASH_PAYMENT_GATEWAY=''):
            engine_tests.DuelEngineTests.setUp(self)

    def configure(self, **changes):
        return RuleConfiguration.objects.create(**changes)

    def fresh_duel(self):
        self.duel = self.accept(self.create())
        reconcile_duel(self.duel.pk)
        self.start = self.duel.agreement.offer.scheduled_at
        self.engine = DuelEngine(self.duel.pk)

    def finish(self, draw=False):
        question = self.active()
        ParticipantState.objects.filter(duel=self.duel, player=self.opponent).update(hp=10)
        if draw:
            ParticipantState.objects.filter(duel=self.duel, player=self.creator).update(hp=10)
        with self.at(4):
            submit_answer(actor=self.creator, question_id=question.pk, option_id=self.option(question))
            rival = question.player_options.get(participant__player=self.opponent)
            option = next(opt['id'] for opt in rival.options if opt['text'] == ('A' if draw else 'B'))
            submit_answer(actor=self.opponent, question_id=question.pk, option_id=option)
        with self.at(33):
            self.engine.advance()
        return DuelResolution.objects.get(duel=self.duel)

    def test_private_notifications_follow_offer_versions_and_no_double_delivery(self):
        challenge = self.create()
        received = Notificacion.objects.filter(usuario=self.opponent, titulo='Nuevo reto privado').count()
        offer = counter_offer(
            actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0,
            scheduled_at=self.now + timedelta(hours=3), stake=21,
        )
        self.assertTrue(Notificacion.objects.filter(usuario=self.creator, titulo='Nueva contrapropuesta').exists())
        decline_offer(actor=self.creator, challenge_id=challenge.pk, version=offer.version, revision=1)
        self.assertTrue(Notificacion.objects.filter(usuario=self.opponent, titulo='Reto rechazado').exists())
        self.assertEqual(Notificacion.objects.filter(usuario=self.opponent, titulo='Nuevo reto privado').count(), received)
        key = 'integration:notice'
        self.assertTrue(deliver(self.creator.pk, key, 'Prueba', 'Mensaje', '/clash/'))
        self.assertFalse(deliver(self.creator.pk, key, 'Prueba', 'Mensaje', '/clash/'))
        receipt = NotificationReceipt.objects.get(recipient=self.creator, key=key)
        receipt.notification.delete()
        self.assertFalse(deliver(self.creator.pk, key, 'Prueba', 'Mensaje', '/clash/'))

    def test_open_acceptance_notifies_both_but_not_outsider(self):
        duel = self.accept(self.create(public=True))
        notices = Notificacion.objects.filter(titulo='Reto abierto aceptado', mensaje__contains=f'/clash/challenges/{duel.agreement.challenge_id}/')
        self.assertEqual(set(notices.values_list('usuario_id', flat=True)), {self.creator.pk, self.opponent.pk})
        self.assertFalse(notices.filter(usuario=self.outsider).exists())

    def test_notification_rollback_and_concurrent_retries_do_not_duplicate(self):
        with patch('apps.duckyclash.notifications.Notificacion.objects.create', side_effect=RuntimeError('inbox unavailable')):
            with self.assertRaises(RuntimeError):
                deliver(self.creator.pk, 'rollback', 'Prueba', 'Mensaje', '/clash/')
        self.assertFalse(NotificationReceipt.objects.filter(key='rollback').exists())
        barrier = Barrier(2)
        recipient = self.creator.pk

        def send():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return deliver(recipient, 'race', 'Aviso concurrente', 'Mensaje', '/clash/')
            except OperationalError as exc:
                if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                    raise
                return 'retry'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: send(), range(2)))
        if 'retry' in outcomes:
            deliver(recipient, 'race', 'Aviso concurrente', 'Mensaje', '/clash/')
        self.assertEqual(NotificationReceipt.objects.filter(key='race').count(), 1)
        self.assertEqual(Notificacion.objects.filter(titulo='Aviso concurrente').count(), 1)

    def test_private_and_public_expiry_notify_only_once(self):
        private, public = self.create(), self.create(public=True)
        with patch('django.utils.timezone.now', return_value=self.now + timedelta(hours=1, seconds=1)):
            self.assertEqual(expire_open_challenges(), 2)
            self.assertEqual(expire_open_challenges(), 0)
        self.assertEqual(Notificacion.objects.filter(titulo='Reto caducado').count(), 3)
        self.assertEqual(Challenge.objects.filter(pk__in=[private.pk, public.pk], status='expired').count(), 2)

    def test_reminder_only_when_scheduled_and_reserved_with_frozen_lead_time(self):
        self.configure(reminder_seconds=10)
        with self.at(-60):
            self.assertTrue(upcoming_notice(self.duel.pk))
            self.assertTrue(upcoming_notice(self.duel.pk))
        self.assertEqual(Notificacion.objects.filter(titulo='Tu duelo empieza pronto').count(), 2)
        self.duel.economic_operations.filter(player=self.opponent).update(status='uncertain')
        with self.at(-10):
            self.assertFalse(upcoming_notice(self.duel.pk))

    def test_result_updates_real_profile_once_without_crediting_coins(self):
        before = self.creator.user_profile_arenas.duckCoins
        resolution = self.finish()
        publish_result(resolution)
        self.creator.user_profile_arenas.refresh_from_db()
        self.opponent.user_profile_arenas.refresh_from_db()
        self.assertEqual(self.creator.user_profile_arenas.wins, 1)
        self.assertEqual(self.opponent.user_profile_arenas.losses, 1)
        self.assertEqual(self.creator.user_profile_arenas.gamesPlayed, 1)
        self.assertEqual(self.creator.user_profile_arenas.duckCoins, before)
        self.assertEqual(ProfileResultUpdate.objects.count(), 1)
        self.assertEqual(Notificacion.objects.filter(titulo='Resultado de DuckyClash').count(), 2)
        self.assertEqual(Notificacion.objects.filter(titulo='Liquidación de DuckyClash').count(), 0)

    def test_history_excludes_reserved_or_pending_money_and_reports_confirmed_net(self):
        self.finish()
        own = ParticipantState.objects.filter(player=self.creator)
        self.assertEqual(summary(own, self.creator)['coins_won'], 0)
        self.assertEqual(summary(own, self.creator)['pending_money'], 1)
        with self.at(34):
            reconcile_duel(self.duel.pk)
            reconcile_duel(self.duel.pk)
        totals = summary(own, self.creator)
        self.assertEqual((totals['wins'], totals['answered'], totals['correct']), (1, 1, 1))
        self.assertEqual((totals['coins_won'], totals['coins_lost'], totals['damage_caused']), (20, 0, 10))
        losses = summary(ParticipantState.objects.filter(player=self.opponent), self.opponent)
        self.assertEqual(losses['coins_lost'], 20)
        self.assertEqual(Notificacion.objects.filter(titulo='Liquidación de DuckyClash').count(), 2)
        participant = own.select_related('duel').get()
        self.assertEqual(participant_metrics(participant)['net_coins'], 20)

    def test_history_never_discloses_correctness_of_an_active_answer(self):
        question = self.active()
        with self.at(4):
            submit_answer(actor=self.creator, question_id=question.pk, option_id=self.option(question))
            totals = summary(ParticipantState.objects.filter(player=self.creator), self.creator)
            metrics = participant_metrics(ParticipantState.objects.select_related('duel').get(player=self.creator))
            self.client.force_login(self.creator)
            response = self.client.get(reverse('duckyclash:history'))
        self.assertEqual((totals['answered'], totals['correct']), (1, 0))
        self.assertEqual(metrics['correct'], 0)
        self.assertEqual(response.context['rows'][0]['correct'], 0)
        self.assertNotContains(response, 'PRIVATE-VALIDATION-KEY')

    def test_draw_refund_zero_net_and_profile_draw_once(self):
        resolution = self.finish(draw=True)
        publish_result(resolution)
        with self.at(34):
            reconcile_duel(self.duel.pk)
        self.creator.user_profile_arenas.refresh_from_db()
        self.assertEqual(self.creator.user_profile_arenas.draws, 1)
        totals = summary(ParticipantState.objects.filter(player=self.creator), self.creator)
        self.assertEqual((totals['draws'], totals['coins_won'], totals['coins_lost'], totals['pending_money']), (1, 0, 0, 0))

    def test_new_duels_freeze_hp_timing_items_and_tie_policy_before_admin_changes(self):
        config = self.configure(initial_hp=200, question_seconds=45, double_ko='tiebreak')
        config.item_rules['NUBE']['amount'] = 7
        config.save()
        self.fresh_duel()
        captured = self.duel.rules_snapshot
        config.initial_hp, config.question_seconds, config.double_ko = 300, 90, 'draw'
        config.item_rules['NUBE']['amount'] = 10
        config.save()
        question = self.begin()
        with self.at(1):
            receipt = self.use(question, 'NUBE')
        self.assertEqual(receipt.effect_snapshot['rules']['amount'], 7)
        self.assertEqual(self.duel.initial_hp, 200)
        self.assertEqual(CombatSession.objects.get(duel=self.duel).rules['question_seconds'], 45)
        self.assertEqual((question.closes_at - question.opens_at).total_seconds(), 45)
        self.assertEqual(captured['double_ko'], 'tiebreak')
        self.assertNotEqual(captured['version'], str(config.version))
        self.duel.rules_snapshot = current_rules()
        with self.assertRaises(ValidationError):
            self.duel.save()

    def test_admin_limits_validate_services_and_forms(self):
        self.configure(max_stake=25, max_expiry_seconds=120)
        with self.assertRaises(ValidationError):
            self.create(stake=26, expires_at=self.now + timedelta(seconds=90))
        with self.assertRaises(ValidationError):
            self.create(stake=20)
        with self.assertRaises(ValidationError):
            self.create(expires_at=timezone.now() + timedelta(seconds=90), evaluated_at=False)
        with self.assertRaises(ValidationError):
            self.create(expires_at=timezone.now() + timedelta(seconds=90),
                        evaluated_at=timezone.now() + timedelta(seconds=1))
        valid = self.create(expires_at=timezone.now() + timedelta(seconds=90))
        self.assertEqual(valid.stake, 20)
        from .forms import PrivateChallengeForm
        local = timezone.localtime(self.now + timedelta(hours=2))
        form = PrivateChallengeForm(actor=self.creator, data={
            'opponent': self.opponent.username, 'category': self.category.pk, 'landscape': self.landscape.pk,
            'date': local.date(), 'time': local.time().replace(tzinfo=None), 'stake': 26, 'lifetime': 60,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('stake', form.errors)
        self.assertEqual(form.fields['stake'].widget.attrs['max'], 25)
        self.assertEqual([value for value, _ in form.fields['lifetime'].choices], [1, 2])

    def test_invalid_configuration_rejected_and_singleton_cannot_be_deleted_in_admin(self):
        for changes in (
            {'question_seconds': 4}, {'max_stake': 0}, {'initial_hp': 0},
            {'max_expiry_seconds': 1, 'min_expiry_seconds': 2},
            {'max_expiry_seconds': 90, 'min_expiry_seconds': 61},
            {'item_rules': {'UNKNOWN': {}}}, {'healing_windows': ['finished']},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.configure(**changes)
        config = self.configure()
        self.client.force_login(self.curator)
        response = self.client.post(reverse('admin:duckyclash_ruleconfiguration_delete', args=[config.pk]), {'post': 'yes'})
        self.assertEqual(response.status_code, 403)

    def test_exact_minimum_private_lifetime_uses_server_submission_time(self):
        self.configure(min_expiry_seconds=120, max_expiry_seconds=120)
        local = timezone.localtime(self.now + timedelta(hours=2))
        self.client.force_login(self.creator)
        response = self.client.post(reverse('duckyclash:challenge_create'), {
            'opponent': self.opponent.get_username(), 'category': self.category.pk,
            'subcategory': self.subcategory.pk, 'landscape': self.landscape.pk,
            'date': local.date(), 'time': local.time().replace(tzinfo=None),
            'stake': 20, 'lifetime': 2, 'evaluated_at': '2060-01-01T00:00:00Z',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Challenge.objects.filter(status=Challenge.Status.PENDING).count(), 1)

    def admin_data(self, config):
        data = {field.name: getattr(config, field.name) for field in config._meta.fields
                if field.editable and not field.primary_key}
        for name in ('healing_windows', 'item_rules'):
            data[name] = json.dumps(data[name])
        return data

    def test_admin_requires_rules_permission_and_logs_before_after_with_actor(self):
        config = self.configure()
        self.creator.is_staff = True
        self.creator.save(update_fields=['is_staff'])
        self.creator.user_permissions.add(Permission.objects.get(codename='change_ruleconfiguration'))
        url = reverse('admin:duckyclash_ruleconfiguration_change', args=[config.pk])
        self.client.force_login(self.creator)
        self.assertEqual(self.client.post(url, self.admin_data(config)).status_code, 403)
        self.creator.user_permissions.add(Permission.objects.get(codename='manage_rules'))
        data = self.admin_data(config)
        data['question_seconds'] = 50
        self.assertEqual(self.client.post(url, data).status_code, 302)
        log = LogEntry.objects.get(user=self.creator, object_id=str(config.pk))
        audit = json.loads(log.change_message)[-1]['clash_rules']
        self.assertEqual(audit['before']['engine']['question_seconds'], 30)
        self.assertEqual(audit['after']['engine']['question_seconds'], 50)

    def test_admin_creation_audit_and_partial_save_refresh_rule_version(self):
        config = RuleConfiguration()
        self.client.force_login(self.curator)
        response = self.client.post(reverse('admin:duckyclash_ruleconfiguration_add'), self.admin_data(config))
        self.assertEqual(response.status_code, 302)
        config = RuleConfiguration.objects.get(pk=1)
        audit = json.loads(LogEntry.objects.get(user=self.curator).change_message)[-1]['clash_rules']
        self.assertIsNone(audit['before'])
        self.assertEqual(audit['after']['initial_hp'], 100)
        old_version = config.version
        config.initial_hp = 120
        config.save(update_fields=['initial_hp'])
        config.refresh_from_db()
        self.assertNotEqual(config.version, old_version)
        self.assertEqual(config.initial_hp, 120)

    def test_existing_result_projection_is_recovered_idempotently_by_existing_worker(self):
        Duel.objects.filter(pk=self.duel.pk).update(status=Duel.Status.SETTLEMENT_PENDING)
        self.duel.refresh_from_db()
        DuelResolution.objects.create(duel=self.duel, is_draw=True, reason='Resultado histórico sin proyección')
        advance_duel_engines()
        advance_duel_engines()
        self.creator.user_profile_arenas.refresh_from_db()
        self.assertEqual(self.creator.user_profile_arenas.draws, 1)
        self.assertEqual(ProfileResultUpdate.objects.count(), 1)
        self.assertEqual(Notificacion.objects.filter(titulo='Resultado de DuckyClash').count(), 2)

    def test_historical_projection_preserves_more_recent_profile_activity_and_streak(self):
        profile = self.creator.user_profile_arenas
        profile.lastPlayedAt, profile.currentWinStreak, profile.bestWinStreak = self.now, 3, 5
        profile.save(update_fields=['lastPlayedAt', 'currentWinStreak', 'bestWinStreak'])
        Duel.objects.filter(pk=self.duel.pk).update(status=Duel.Status.SETTLEMENT_PENDING)
        self.duel.refresh_from_db()
        resolution = DuelResolution.objects.create(
            duel=self.duel, is_draw=True, reason='Empate histórico',
            resolved_at=self.now - timedelta(days=1),
        )
        publish_result(resolution)
        profile.refresh_from_db()
        self.assertEqual(profile.lastPlayedAt, self.now)
        self.assertEqual((profile.currentWinStreak, profile.bestWinStreak, profile.draws), (3, 5, 1))

    def test_invalid_historical_profile_is_logged_without_blocking_other_engines(self):
        second = self.accept(self.create())
        reconcile_duel(second.pk)
        Duel.objects.filter(pk=self.duel.pk).update(status=Duel.Status.SETTLEMENT_PENDING)
        self.duel.refresh_from_db()
        resolution = DuelResolution.objects.create(duel=self.duel, is_draw=True, reason='Perfil histórico eliminado')
        self.opponent.user_profile_arenas.delete()
        with self.assertLogs('apps.duckyclash.tasks', level='ERROR') as logs:
            self.assertEqual(advance_duel_engines(), 1)
        self.assertIn('requires review', logs.output[0])
        self.assertFalse(ProfileResultUpdate.objects.filter(resolution=resolution).exists())

    def test_ring_disabled_is_frozen_and_waiting_endpoint_agrees(self):
        config = self.configure(ring_access='disabled')
        self.fresh_duel()
        config.ring_access = 'scheduled'
        config.save()
        with self.at():
            with self.assertRaises(ValidationError):
                self.engine.ready(self.creator)
            self.assertFalse(self.engine.snapshot(self.creator)['unlocked'])
            self.client.force_login(self.creator)
            response = self.client.get(reverse('duckyclash:waiting_state', args=[self.duel.pk]))
        self.assertFalse(response.json()['can_ring'])

    def test_abandonment_refund_uses_frozen_policy_and_has_no_sport_winner(self):
        self.configure(abandonment='refund', abandonment_seconds=30)
        self.fresh_duel()
        self.active()
        with self.at(31):
            self.engine.enter(self.creator)
            self.engine.advance()
            self.assertEqual(CombatSession.objects.get(duel=self.duel).phase, Phase.FINISHED)
            reconcile_duel(self.duel.pk)
        self.duel.refresh_from_db()
        self.assertEqual(self.duel.status, Duel.Status.CANCELED)
        self.assertFalse(DuelResolution.objects.filter(duel=self.duel).exists())
        self.assertFalse(ProfileResultUpdate.objects.exists())

    def test_history_is_private_filtered_and_uses_existing_profile(self):
        self.finish()
        self.client.force_login(self.creator)
        response = self.client.get(reverse('duckyclash:history'), {'category': self.category.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['totals']['wins'], 1)
        self.assertContains(response, self.creator.user_profile_arenas.nickname)
        self.assertNotContains(response, 'PRIVATE-VALIDATION-KEY')
        self.assertEqual(response.context['breakdown'][0]['stats']['answered'], 1)
        self.client.force_login(self.outsider)
        response = self.client.get(reverse('duckyclash:history'))
        self.assertEqual(response.context['totals']['wins'], 0)
        self.assertContains(response, 'Todavía no tienes combates')
        self.client.logout()
        self.assertEqual(self.client.get(reverse('duckyclash:history')).status_code, 302)

    def test_migration_freezes_existing_duel_and_preserves_session_then_reverses(self):
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([('duckyclash', '0005_participantstate_last_seen_at_and_more')])
            old_apps = executor.loader.project_state([('duckyclash', '0005_participantstate_last_seen_at_and_more')]).apps
            OldSession = old_apps.get_model('duckyclash', 'CombatSession')
            engine = {'question_seconds': 48}
            OldSession.objects.create(duel_id=self.duel.pk, rules=engine)
            executor = MigrationExecutor(connection)
            executor.migrate(leaves)
            self.duel.refresh_from_db()
            self.assertEqual(self.duel.rules_snapshot['engine']['question_seconds'], 48)
            self.assertEqual(self.duel.rules_snapshot['engine']['countdown_seconds'], 3)
            self.assertEqual(self.duel.rules_snapshot['version'], 'legacy-freeze-v1')
            self.assertEqual(self.creator.user_profile_arenas.duckCoins, 150)
        finally:
            MigrationExecutor(connection).migrate(leaves)
