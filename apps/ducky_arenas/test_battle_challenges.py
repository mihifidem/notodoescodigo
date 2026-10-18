from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .battle_challenges import create_challenge, respond_to_challenge, visible_challenges
from .models import BattleArena, BattleChallenge, UserProfileArenas


class BattleChallengeTests(TestCase):
    def setUp(self):
        self.players = [
            get_user_model().objects.create_user(username=name, password='test1234')
            for name in ('challenger', 'opponent', 'outsider')
        ]
        self.creator, self.opponent, self.outsider = self.players
        for user in self.players:
            UserProfileArenas.objects.create(
                user=user, nickname=user.username, avatarType='GAMER',
                onboardingCompleted=True, duckCoins=100,
            )
        self.url = reverse('ducky_arenas:battle')

    def create(self, public=False, stake=20):
        return create_challenge(
            actor=self.creator, opponent=None if public else self.opponent,
            is_public=public, stake=stake, lifetime=30,
        )

    def respond(self, challenge, actor, action, **kwargs):
        return respond_to_challenge(
            actor=actor, challenge_id=challenge.pk,
            revision=challenge.revision, action=action, **kwargs,
        )

    def test_direct_creation_and_acceptance_do_not_move_money_or_start_combat(self):
        challenge = self.create()
        result = self.respond(challenge, self.opponent, 'accept')
        self.assertEqual(result.status, BattleChallenge.Status.ACCEPTED)
        self.assertEqual(result.opponent, self.opponent)
        self.assertEqual(result.stake, 20)
        self.assertFalse(BattleArena.objects.exists())
        self.assertEqual(list(UserProfileArenas.objects.values_list('duckCoins', flat=True)), [100, 100, 100])
        self.assertEqual(UserProfileArenas.objects.count(), 3)

    def test_public_acceptance_claims_one_opponent(self):
        challenge = self.create(public=True)
        result = self.respond(challenge, self.opponent, 'accept')
        self.assertEqual(result.opponent, self.opponent)
        with self.assertRaises(Http404):
            self.respond(challenge, self.outsider, 'accept')
        result.refresh_from_db()
        self.assertEqual(result.opponent, self.opponent)
        self.assertEqual(result.revision, 1)

    def test_public_counteroffer_becomes_private_negotiation(self):
        challenge = self.create(public=True)
        counter = self.respond(challenge, self.opponent, 'counter', stake=35)
        self.assertEqual(counter.opponent, self.opponent)
        self.assertEqual(counter.proposed_by, self.opponent)
        self.assertEqual(counter.expires_at, challenge.expires_at)
        self.assertFalse(visible_challenges(self.outsider).filter(pk=challenge.pk).exists())
        accepted = self.respond(counter, self.creator, 'accept')
        self.assertEqual(accepted.stake, 35)
        self.assertEqual(accepted.status, BattleChallenge.Status.ACCEPTED)

    def test_counteroffers_require_other_player_and_current_revision(self):
        challenge = self.create()
        counter = self.respond(challenge, self.opponent, 'counter', stake=30)
        with self.assertRaises(PermissionDenied):
            self.respond(counter, self.opponent, 'accept')
        with self.assertRaises(ValidationError):
            self.respond(challenge, self.creator, 'accept')
        next_counter = self.respond(counter, self.creator, 'counter', stake=25)
        accepted = self.respond(next_counter, self.opponent, 'accept')
        self.assertEqual(accepted.stake, 25)
        self.assertEqual(accepted.revision, 3)

    def test_creator_cannot_accept_own_offer_or_public_challenge(self):
        for public in (False, True):
            challenge = self.create(public=public)
            with self.assertRaises(PermissionDenied):
                self.respond(challenge, self.creator, 'accept')

    def test_expiration_boundary_prevents_all_mutations(self):
        challenge = self.create()
        for action in ('accept', 'counter', 'decline', 'cancel'):
            actor = self.creator if action == 'cancel' else self.opponent
            with self.subTest(action=action), patch(
                'apps.ducky_arenas.battle_challenges.timezone.now', return_value=challenge.expires_at,
            ):
                with self.assertRaises(ValidationError):
                    self.respond(challenge, actor, action, stake=40)
        challenge.refresh_from_db()
        self.assertEqual(challenge.revision, 0)
        self.assertEqual(challenge.status, BattleChallenge.Status.PENDING)

    def test_rejection_and_cancellation_are_terminal(self):
        for action, actor, status in (
            ('decline', self.opponent, BattleChallenge.Status.DECLINED),
            ('cancel', self.creator, BattleChallenge.Status.CANCELLED),
        ):
            challenge = self.create()
            result = self.respond(challenge, actor, action)
            self.assertEqual(result.status, status)
            with self.assertRaises(ValidationError):
                self.respond(result, self.opponent, 'accept')
        with self.assertRaises(PermissionDenied):
            self.respond(self.create(public=True), self.outsider, 'decline')
        with self.assertRaises(PermissionDenied):
            self.respond(self.create(), self.opponent, 'cancel')

    def test_service_rejects_invalid_stakes_and_identity(self):
        for stake in (-1, 1.5, True, 1000001):
            with self.subTest(stake=stake), self.assertRaises(ValidationError):
                self.create(stake=stake)
        with self.assertRaises(ValidationError):
            create_challenge(actor=self.creator, opponent=self.creator, is_public=False, stake=1, lifetime=30)
        with self.assertRaises(ValidationError):
            create_challenge(actor=self.creator, opponent=None, is_public=False, stake=1, lifetime=30)
        with self.assertRaises(ValidationError):
            create_challenge(actor=self.creator, opponent=self.opponent, is_public=False, stake=1, lifetime=999)
        challenge = self.create()
        for stake in (-1, 0.5, True, 1000001, 20):
            with self.subTest(stake=stake), self.assertRaises(ValidationError):
                self.respond(challenge, self.opponent, 'counter', stake=stake)
        challenge.refresh_from_db()
        self.assertEqual(challenge.revision, 0)

    def test_private_challenge_cannot_be_read_or_changed_by_third_party(self):
        challenge = self.create()
        self.client.force_login(self.outsider)
        self.assertNotContains(self.client.get(self.url), f'Reto #{challenge.pk} ')
        for action in ('accept', 'counter', 'decline', 'cancel'):
            response = self.client.post(self.url, {
                'action': action, 'challenge_id': challenge.pk, 'revision': 0, 'stake': 10,
            })
            self.assertEqual(response.status_code, 404)

    def test_create_form_validates_and_preserves_input(self):
        self.client.force_login(self.creator)
        data = {'action': 'create', 'visibility': 'direct', 'opponent': 'missing', 'stake': 17, 'lifetime': 30}
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context['challenge_form'].errors['opponent'])
        self.assertEqual(response.context['challenge_form']['stake'].value(), '17')
        self.assertFalse(BattleChallenge.objects.exists())
        data['opponent'] = self.opponent.username
        response = self.client.post(self.url, data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BattleChallenge.objects.get().opponent, self.opponent)
        self.assertContains(response, 'Las monedas no se han reservado ni descontado.')

    def test_public_creation_and_counteroffer_ui(self):
        self.client.force_login(self.creator)
        response = self.client.post(self.url, {
            'action': 'create', 'visibility': 'public', 'stake': 0, 'lifetime': 15,
        })
        self.assertEqual(response.status_code, 302)
        challenge = BattleChallenge.objects.get()
        self.client.force_login(self.opponent)
        self.assertContains(self.client.get(self.url), 'Contraproponer')
        response = self.client.post(self.url, {
            'action': 'counter', 'challenge_id': challenge.pk, 'revision': 0, 'stake': 10,
        })
        self.assertEqual(response.status_code, 302)
        challenge.refresh_from_db()
        self.assertEqual(challenge.proposed_by, self.opponent)
        self.client.force_login(self.creator)
        response = self.client.post(self.url, {
            'action': 'accept', 'challenge_id': challenge.pk, 'revision': 1, 'stake': 999,
            'winner': self.creator.pk, 'status': 'finished',
        })
        self.assertEqual(response.status_code, 302)
        challenge.refresh_from_db()
        self.assertEqual(challenge.stake, 10)
        self.assertEqual(challenge.status, BattleChallenge.Status.ACCEPTED)

    def test_invalid_actions_and_stale_requests_show_errors(self):
        challenge = self.create()
        self.client.force_login(self.opponent)
        for data in (
            {'action': 'counter', 'revision': 0, 'stake': '1.5'},
            {'action': 'accept', 'revision': 99},
            {'action': 'finished', 'revision': 0},
            {'action': 'counter', 'revision': 0},
        ):
            response = self.client.post(self.url, dict(data, challenge_id=challenge.pk))
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, 'No se pudo actualizar el reto.', status_code=400)
        challenge.refresh_from_db()
        self.assertEqual(challenge.revision, 0)

    def test_expired_public_challenges_hidden_and_own_expired_visible(self):
        challenge = self.create(public=True)
        BattleChallenge.objects.filter(pk=challenge.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(visible_challenges(self.outsider).filter(pk=challenge.pk).exists())
        self.client.force_login(self.creator)
        response = self.client.get(self.url)
        self.assertContains(response, 'Caducado')
        self.assertNotContains(response, 'Cancelar reto')

    def test_csrf_authentication_and_inactive_players(self):
        challenge = self.create()
        response = self.client.post(self.url, {'action': 'accept'})
        self.assertEqual(response.status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.opponent)
        self.assertEqual(csrf_client.post(self.url, {
            'action': 'accept', 'challenge_id': challenge.pk, 'revision': 0,
        }).status_code, 403)
        self.creator.is_active = False
        self.creator.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied):
            self.respond(challenge, self.opponent, 'accept')

    def test_listing_is_paginated_and_counterparty_can_reject_counteroffer(self):
        for _ in range(21):
            self.create(public=True)
        self.client.force_login(self.opponent)
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['challenges_page']), 20)
        self.assertContains(response, '?page=2')
        counter = self.respond(self.create(), self.opponent, 'counter', stake=50)
        result = self.respond(counter, self.creator, 'decline')
        self.assertEqual(result.status, BattleChallenge.Status.DECLINED)

    def test_database_enforces_valid_participants_and_accepted_rival(self):
        challenge = self.create(public=True)
        for changes in (
            {'proposed_by': self.outsider},
            {'status': BattleChallenge.Status.ACCEPTED},
            {'opponent': self.creator},
            {'stake': 1000001},
        ):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                BattleChallenge.objects.filter(pk=challenge.pk).update(**changes)

    def test_expiration_during_update_and_losing_version_update_roll_back(self):
        challenge = self.create()
        with patch('apps.ducky_arenas.battle_challenges.timezone.now', side_effect=[
            challenge.expires_at - timedelta(seconds=2),
            challenge.expires_at - timedelta(seconds=1), challenge.expires_at,
        ]):
            with self.assertRaises(ValidationError):
                self.respond(challenge, self.opponent, 'accept')
        with patch('django.db.models.query.QuerySet.update', return_value=0):
            with self.assertRaises(ValidationError):
                self.respond(challenge, self.opponent, 'accept')
        challenge.refresh_from_db()
        self.assertEqual(challenge.status, BattleChallenge.Status.PENDING)
        self.assertEqual(challenge.revision, 0)


class BattleChallengeMigrationTests(TransactionTestCase):
    def test_migration_roundtrip_preserves_existing_profile(self):
        old = [('ducky_arenas', '0005_arenaitem_itemtype_userarenainventory_isequipped_and_more')]
        new = [('ducky_arenas', '0006_battlechallenge')]
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        user = get_user_model().objects.create_user(username='migration_duck')
        try:
            executor.migrate(old)
            apps = executor.loader.project_state(old).apps
            Profile = apps.get_model('ducky_arenas', 'UserProfileArenas')
            profile = Profile.objects.create(user_id=user.pk, nickname='PersistentDucky', duckCoins=123)
            executor = MigrationExecutor(connection)
            executor.migrate(new)
            apps = executor.loader.project_state(new).apps
            self.assertEqual(apps.get_model('ducky_arenas', 'UserProfileArenas').objects.get(pk=profile.pk).duckCoins, 123)
            executor = MigrationExecutor(connection)
            executor.migrate(old)
            self.assertNotIn('ducky_arenas_battlechallenge', connection.introspection.table_names())
            self.assertTrue(Profile.objects.filter(pk=profile.pk, duckCoins=123).exists())
        finally:
            MigrationExecutor(connection).migrate(leaves)
