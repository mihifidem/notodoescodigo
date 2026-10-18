from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, local
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ducky_arenas.models import BattleChallenge
from apps.users.models import Notificacion

from .bank import BankUnavailable
from .forms import OpenChallengeForm
from .models import ChallengeAgreement, Duel, EconomicOperation
from .selectors import open_challenges
from .services import accept_offer, counter_offer, create_challenge, decline_offer
from .tasks import expire_open_challenges
from .test_private_challenges import FakeBankGateway, PrivateChallengeTests


@override_settings(
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.test_private_challenges.FakeBankGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class OpenChallengeTests(TestCase):
    setUp = PrivateChallengeTests.setUp
    post_data = PrivateChallengeTests.post_data

    def create(self, **changes):
        params = dict(
            actor=self.creator, opponent=None, is_public=True,
            category=self.category, subcategory=self.subcategory, landscape=self.landscape,
            scheduled_at=self.schedule, expires_at=self.expiry, time_zone='Europe/Madrid', stake=20,
        )
        params.update(changes)
        return create_challenge(**params)

    def accept(self, actor, challenge):
        return accept_offer(actor=actor, challenge_id=challenge.pk, version=1, revision=0)

    def test_create_with_explicit_expiry_and_no_recipient(self):
        self.assertContains(self.client.get(reverse('duckyclash:open_create')), 'Fecha de vencimiento')
        local_expiry = timezone.localtime(self.expiry).replace(second=0, microsecond=0)
        data = self.post_data(expiry_date=local_expiry.date().isoformat(), expiry_time=local_expiry.strftime('%H:%M'))
        # Extra fields cannot turn this into a private challenge.
        response = self.client.post(reverse('duckyclash:open_create'), data)
        self.assertEqual(response.status_code, 302)
        challenge = BattleChallenge.objects.get()
        self.assertTrue(challenge.is_public)
        self.assertIsNone(challenge.opponent_id)
        self.assertEqual(challenge.expires_at, local_expiry)
        self.assertEqual(challenge.clash_offers.get().version, 1)
        self.assertFalse(Notificacion.objects.exists())

    def test_expiry_form_validation_and_ambiguous_times(self):
        data = self.post_data(expiry_date='2000-01-01', expiry_time='12:00')
        self.assertFalse(OpenChallengeForm(data, actor=self.creator).is_valid())
        data.update(expiry_date=(self.schedule + timedelta(days=1)).date().isoformat())
        self.assertFalse(OpenChallengeForm(data, actor=self.creator).is_valid())
        data.update(date='2027-11-01', time='12:00', expiry_date='2027-10-31', expiry_time='02:30')
        form = OpenChallengeForm(data, actor=self.creator)
        self.assertFalse(form.is_valid())
        self.assertIn('expiry_time', form.errors)
        self.assertNotIn('opponent', form.fields)
        self.assertNotIn('lifetime', form.fields)

    def test_panel_only_available_before_job_and_filters(self):
        available = self.create()
        expired = self.create()
        BattleChallenge.objects.filter(pk=expired.pk).update(expires_at=timezone.now())
        private = create_challenge(
            actor=self.creator, opponent=self.opponent, is_public=False, category=self.category,
            subcategory=self.subcategory, landscape=self.landscape, scheduled_at=self.schedule,
            expires_at=self.expiry, time_zone='Europe/Madrid', stake=20,
        )
        accepted = self.create()
        self.accept(self.opponent, accepted)
        self.assertEqual(list(open_challenges().values_list('pk', flat=True)), [available.pk])
        self.client.force_login(self.outsider)
        url = reverse('duckyclash:open_list')
        response = self.client.get(url)
        self.assertContains(response, reverse('duckyclash:open_accept', args=[available.pk]))
        for hidden in (expired, private, accepted):
            self.assertNotContains(response, reverse('duckyclash:open_accept', args=[hidden.pk]))
        self.assertContains(response, 'data-open-countdown')
        self.assertContains(response, 'open_challenges.js')
        self.assertEqual(len(self.client.get(url, {'max_stake': 19}).context['cards']), 0)
        self.assertEqual(len(self.client.get(url, {'category': self.category.pk}).context['cards']), 1)
        self.assertEqual(len(self.client.get(url, {'date': timezone.localtime(self.schedule).date().isoformat()}).context['cards']), 1)
        self.assertEqual(len(self.client.get(url, {'max_stake': 'bad'}).context['cards']), 0)

    def test_self_acceptance_rejected_at_http_and_service(self):
        challenge = self.create()
        response = self.client.get(reverse('duckyclash:open_list'))
        self.assertNotContains(response, reverse('duckyclash:open_accept', args=[challenge.pk]))
        with self.assertRaises(PermissionDenied):
            self.accept(self.creator, challenge)
        self.assertEqual(self.client.post(
            reverse('duckyclash:open_accept', args=[challenge.pk]), {'version': 1, 'revision': 0},
        ).status_code, 403)

    def test_panel_excludes_unavailable_creator_and_closed_challenges(self):
        challenge = self.create()
        for status in (BattleChallenge.Status.DECLINED, BattleChallenge.Status.CANCELLED, BattleChallenge.Status.EXPIRED):
            BattleChallenge.objects.filter(pk=challenge.pk).update(status=status)
            self.assertFalse(open_challenges().exists())
        BattleChallenge.objects.filter(pk=challenge.pk).update(status=BattleChallenge.Status.PENDING)
        self.creator.is_active = False
        self.creator.save(update_fields=['is_active'])
        self.assertFalse(open_challenges().exists())

    def test_pagination_preserves_filters(self):
        for _ in range(13):
            self.create()
        response = self.client.get(reverse('duckyclash:open_list'), {'max_stake': 20})
        self.assertEqual(len(response.context['cards']), 12)
        self.assertContains(response, '?max_stake=20&amp;page=2')
        response = self.client.get(reverse('duckyclash:open_list'), {'max_stake': 20, 'page': 2})
        self.assertEqual(len(response.context['cards']), 1)

    def test_direct_acceptance_no_negotiation_or_forged_terms(self):
        challenge = self.create()
        self.client.force_login(self.opponent)
        response = self.client.post(
            reverse('duckyclash:open_accept', args=[challenge.pk]),
            {'version': 1, 'revision': 0, 'stake': 1, 'landscape': 999, 'scheduled_at': '2099-01-01'},
        )
        self.assertEqual(response.status_code, 302)
        challenge.refresh_from_db()
        self.assertEqual(challenge.opponent_id, self.opponent.pk)
        self.assertEqual(challenge.stake, 20)
        self.assertEqual(challenge.clash_offers.count(), 1)
        self.assertEqual(Duel.objects.get().status, Duel.Status.PENDING_RESERVATION)
        self.assertFalse(open_challenges().exists())
        self.assertFalse(EconomicOperation.objects.exists())
        with self.assertRaises(ValidationError):
            self.accept(self.opponent, challenge)
        self.assertEqual(ChallengeAgreement.objects.count(), 1)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('duckyclash:challenge_detail', args=[challenge.pk])).status_code, 403)
        response = self.client.post(
            reverse('duckyclash:open_accept', args=[challenge.pk]), {'version': 1, 'revision': 0},
        )
        self.assertEqual(response.status_code, 409)
        self.assertNotContains(response, 'private_opponent', status_code=409)

    def test_no_public_counter_or_decline(self):
        challenge = self.create()
        with self.assertRaises(PermissionDenied):
            counter_offer(
                actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0,
                scheduled_at=self.schedule + timedelta(hours=1), stake=30,
            )
        with self.assertRaises(PermissionDenied):
            decline_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        self.client.force_login(self.opponent)
        for action in ('counter', 'decline', 'accept'):
            self.assertEqual(self.client.post(
                reverse(f'duckyclash:challenge_{action}', args=[challenge.pk]), {'version': 1, 'revision': 0},
            ).status_code, 404)

    def test_expiry_job_idempotent_and_never_changes_accepted_or_legacy(self):
        expired = self.create()
        accepted = self.create()
        self.accept(self.opponent, accepted)
        private = create_challenge(
            actor=self.creator, opponent=self.opponent, is_public=False, category=self.category,
            subcategory=None, landscape=self.landscape, scheduled_at=self.schedule,
            expires_at=self.expiry, time_zone='Europe/Madrid', stake=20,
        )
        legacy = BattleChallenge.objects.create(
            creator=self.creator, proposed_by=self.creator, is_public=True, stake=1,
            expires_at=self.expiry,
        )
        now = self.expiry
        with patch('apps.duckyclash.tasks.timezone.now', return_value=now):
            self.assertEqual(expire_open_challenges.run(), 2)
            self.assertEqual(expire_open_challenges.run(), 0)
        expired.refresh_from_db()
        self.assertEqual(expired.status, BattleChallenge.Status.EXPIRED)
        self.assertTrue(expired.is_expired)
        self.assertEqual(expired.revision, 1)
        private.refresh_from_db()
        self.assertEqual(private.status, BattleChallenge.Status.EXPIRED)
        for record in (accepted, legacy):
            record.refresh_from_db()
            self.assertNotEqual(record.status, BattleChallenge.Status.EXPIRED)
        self.assertFalse(open_challenges().filter(pk=expired.pk).exists())

    def test_late_acceptance_and_stale_revision_fail_without_job(self):
        challenge = self.create()
        with self.assertRaises(ValidationError):
            accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=1)
        with patch('apps.duckyclash.services.timezone.now', return_value=self.expiry):
            with self.assertRaises(ValidationError):
                self.accept(self.opponent, challenge)
        self.assertFalse(ChallengeAgreement.objects.exists())

    def test_bank_failure_does_not_claim_and_other_candidate_can_accept(self):
        challenge = self.create()
        FakeBankGateway.failure = 'Banco no disponible'
        with self.assertRaises(BankUnavailable):
            self.accept(self.opponent, challenge)
        challenge.refresh_from_db()
        self.assertIsNone(challenge.opponent_id)
        FakeBankGateway.failure = None
        duel = self.accept(self.outsider, challenge)
        self.assertEqual(duel.agreement.opponent_id, self.outsider.pk)

    def test_post_only_and_csrf(self):
        challenge = self.create()
        url = reverse('duckyclash:open_accept', args=[challenge.pk])
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get(reverse('duckyclash:open_list')).status_code, 302)
        client.force_login(self.opponent)
        self.assertEqual(client.get(url).status_code, 405)
        self.assertEqual(client.post(url, {'version': 1, 'revision': 0}).status_code, 403)
        client.get(reverse('duckyclash:open_list'))
        response = client.post(url, {
            'version': 1, 'revision': 0, 'csrfmiddlewaretoken': client.cookies['csrftoken'].value,
        })
        self.assertEqual(response.status_code, 302)


@override_settings(DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.test_private_challenges.FakeBankGateway', DUCKYCLASH_PAYMENT_GATEWAY='')
class OpenChallengeConcurrencyTests(TransactionTestCase):
    setUp = PrivateChallengeTests.setUp
    create = OpenChallengeTests.create

    def test_creation_bank_check_is_outside_transaction(self):
        def balance(player, stake):
            self.assertFalse(connection.in_atomic_block)

        with patch('apps.duckyclash.services.require_balance', side_effect=balance):
            self.create()
        self.assertEqual(BattleChallenge.objects.count(), 1)

    def test_expiry_during_bank_wait_cannot_be_resurrected(self):
        challenge = self.create()

        def balance(player, stake):
            self.assertFalse(connection.in_atomic_block)
            with patch('apps.duckyclash.tasks.timezone.now', return_value=self.expiry):
                expire_open_challenges.run()

        with patch('apps.duckyclash.services.require_balance', side_effect=balance):
            with self.assertRaises(ValidationError):
                accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        challenge.refresh_from_db()
        self.assertEqual(challenge.status, BattleChallenge.Status.EXPIRED)
        self.assertIsNone(challenge.opponent_id)
        self.assertFalse(Duel.objects.exists())

    def test_bank_wait_does_not_hold_transaction_and_first_confirmation_wins(self):
        challenge = self.create()
        winner = []

        def bank_wait(player, stake):
            self.assertFalse(connection.in_atomic_block)
            if not winner:
                winner.append(True)
                with patch('apps.duckyclash.services.require_balance'):
                    winner.append(accept_offer(
                        actor=self.outsider, challenge_id=challenge.pk, version=1, revision=0,
                    ))

        with patch('apps.duckyclash.services.require_balance', side_effect=bank_wait):
            with self.assertRaises(PermissionDenied):
                accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        self.assertEqual(Duel.objects.count(), 1)
        self.assertEqual(ChallengeAgreement.objects.get().opponent_id, self.outsider.pk)
        self.assertEqual(Notificacion.objects.count(), 2)

    def test_two_database_connections_cannot_confirm_two_candidates(self):
        challenge = self.create()
        barrier = Barrier(2)

        # Both threads share one thread-aware bank callback and separate DB connections.
        thread_state = local()

        def synchronized_balance(player, stake):
            self.assertFalse(connection.in_atomic_block)
            thread_state.calls = getattr(thread_state, 'calls', 0) + 1
            if thread_state.calls == 1:
                barrier.wait(timeout=15)

        def run(player):
            close_old_connections()
            try:
                return accept_offer(actor=player, challenge_id=challenge.pk, version=1, revision=0).pk
            except (ValidationError, PermissionDenied):
                return None
            except OperationalError as exc:
                if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
                    raise
                return None
            finally:
                close_old_connections()

        with patch('apps.duckyclash.services.require_balance', side_effect=synchronized_balance):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(run, [self.opponent, self.outsider]))
        self.assertLessEqual(sum(result is not None for result in outcomes), 1)
        if connection.vendor != 'sqlite':
            self.assertEqual(sum(result is not None for result in outcomes), 1)
        # SQLite may reject both writers; a fresh explicit request can then win.
        if not Duel.objects.exists():
            accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        self.assertEqual(Duel.objects.count(), 1)
        self.assertEqual(ChallengeAgreement.objects.count(), 1)
        self.assertEqual(Notificacion.objects.count(), 2)
