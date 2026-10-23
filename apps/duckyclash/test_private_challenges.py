from datetime import timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import CourseCategory
from apps.ducky_arenas.models import BattleChallenge, UserProfileArenas
from apps.users.models import Notificacion

from .bank import BankGateway, BankUnavailable, ProfileBalanceGateway, get_bank_gateway
from .forms import PrivateChallengeForm
from .models import ChallengeAgreement, ChallengeOffer, ChallengeSubcategory, Duel, EconomicOperation, Landscape
from .selectors import resolve_player, search_players
from .services import accept_offer, counter_offer, create_challenge, decline_offer


class FakeBankGateway(BankGateway):
    balances = {}
    calls = []
    failure = None

    def available_balance(self, player):
        self.calls.append(player.pk)
        if self.failure:
            raise BankUnavailable(self.failure)
        return self.balances[player.pk]


@override_settings(
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.test_private_challenges.FakeBankGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class PrivateChallengeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.creator, self.opponent, self.outsider = [
            User.objects.create_user(**{User.USERNAME_FIELD: name}, password='test1234', email=f'{name}@example.com')
            for name in ('private_creator', 'private_opponent', 'private_outsider')
        ]
        for player in (self.creator, self.opponent, self.outsider):
            UserProfileArenas.objects.create(user=player, nickname=player.get_username(), onboardingCompleted=True, duckCoins=200)
        FakeBankGateway.balances = {player.pk: 100 for player in (self.creator, self.opponent, self.outsider)}
        FakeBankGateway.calls = []
        FakeBankGateway.failure = None
        self.category = CourseCategory.objects.create(nombre='Reto privado')
        self.subcategory = ChallengeSubcategory.objects.create(category=self.category, name='Fundamentos')
        from .characters import era_for_xp
        self.landscape = Landscape.objects.create(
            code='private-ring', name='Paisaje privado', era=era_for_xp(self.creator.puntos_totales).code,
        )
        self.schedule = (timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)
        self.expiry = timezone.now() + timedelta(hours=1)
        self.create_url = reverse('duckyclash:challenge_create')
        self.client.force_login(self.creator)

    def create(self):
        return create_challenge(
            actor=self.creator, opponent=self.opponent, is_public=False,
            category=self.category, subcategory=self.subcategory, landscape=self.landscape,
            scheduled_at=self.schedule, expires_at=self.expiry, time_zone='Europe/Madrid', stake=20,
        )

    def post_data(self, **changes):
        local = timezone.localtime(self.schedule, ZoneInfo('Europe/Madrid'))
        data = {
            'opponent': self.opponent.get_username(), 'category': self.category.pk,
            'subcategory': self.subcategory.pk, 'landscape': self.landscape.pk,
            'date': local.date().isoformat(), 'time': local.strftime('%H:%M'), 'stake': 20, 'lifetime': 60,
        }
        data.update(changes)
        return data

    def action(self, challenge, name='accept', **changes):
        data = {'version': challenge.clash_offers.count(), 'revision': challenge.revision}
        data.update(changes)
        return self.client.post(reverse(f'duckyclash:challenge_{name}', args=[challenge.pk]), data)

    def counter(self, challenge, actor, **changes):
        data = dict(actor=actor, challenge_id=challenge.pk, version=challenge.clash_offers.count(),
                    revision=challenge.revision, scheduled_at=self.schedule + timedelta(hours=1), stake=30)
        data.update(changes)
        result = counter_offer(**data)
        challenge.refresh_from_db()
        return result

    def test_search_uses_real_identity_and_only_eligible_players(self):
        response = self.client.get(self.create_url, {'q': 'private_op'})
        self.assertContains(response, self.opponent.get_username())
        self.assertNotContains(response, self.opponent.email)
        self.assertEqual(list(search_players(self.creator, 'private_creator')), [])
        self.assertEqual(resolve_player(self.creator, self.opponent.get_username()), self.opponent)
        self.opponent.is_active = False
        self.opponent.save()
        self.assertEqual(list(search_players(self.creator, 'private_op')), [])
        self.opponent.is_active = True
        self.opponent.save()
        UserProfileArenas.objects.filter(user=self.opponent).update(onboardingCompleted=False)
        self.assertIsNone(resolve_player(self.creator, self.opponent.get_username()))

    def test_lookup_respects_custom_username_field(self):
        User = get_user_model()
        with patch.object(User, 'USERNAME_FIELD', 'email'):
            self.assertEqual(resolve_player(self.creator, self.opponent.email), self.opponent)
            form = PrivateChallengeForm(self.post_data(opponent=self.opponent.email), actor=self.creator)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data['opponent'], self.opponent)
            form = PrivateChallengeForm(self.post_data(opponent=self.creator.email), actor=self.creator)
            self.assertFalse(form.is_valid())

    def test_creation_notifies_only_recipient_and_does_not_reserve(self):
        response = self.client.post(self.create_url, self.post_data())
        challenge = BattleChallenge.objects.get()
        self.assertRedirects(response, reverse('duckyclash:challenge_detail', args=[challenge.pk]))
        offer = challenge.clash_offers.get()
        self.assertFalse(challenge.is_public)
        self.assertEqual(offer.scheduled_at, self.schedule)
        self.assertEqual(FakeBankGateway.calls, [self.creator.pk])
        self.assertEqual(Notificacion.objects.count(), 1)
        notice = Notificacion.objects.get()
        self.assertEqual(notice.usuario_id, self.opponent.pk)
        self.assertIn(reverse('duckyclash:challenge_detail', args=[challenge.pk]), notice.mensaje)
        self.assertFalse(Duel.objects.exists())
        self.assertFalse(EconomicOperation.objects.exists())
        self.assertEqual(list(UserProfileArenas.objects.values_list('duckCoins', flat=True)), [200] * 3)

    def test_invalid_creation_identity_catalog_amount_and_dates(self):
        other = CourseCategory.objects.create(nombre='Otra')
        cases = [
            {'opponent': self.creator.get_username()}, {'opponent': 'unknown-user'},
            {'category': other.pk}, {'stake': 0}, {'stake': '2.5'},
            {'date': '2000-01-01'}, {'time': '25:00'}, {'lifetime': 999},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                response = self.client.post(self.create_url, self.post_data(**changes))
                self.assertEqual(response.status_code, 400)
                self.assertFalse(BattleChallenge.objects.exists())
                self.assertFalse(Notificacion.objects.exists())

    def test_insufficient_bank_balance_not_profile_points_blocks_creation(self):
        self.creator.puntos = 100000
        self.creator.save()
        FakeBankGateway.balances[self.creator.pk] = 10
        response = self.client.post(self.create_url, self.post_data())
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'suficientes DuckyCoins', status_code=400)
        self.assertFalse(BattleChallenge.objects.exists())

    def test_bank_unavailable_or_invalid_never_defaults_to_success(self):
        for error in ('Banco no disponible',):
            FakeBankGateway.failure = error
            response = self.client.post(self.create_url, self.post_data())
            self.assertContains(response, error, status_code=503)
            self.assertFalse(BattleChallenge.objects.exists())
        FakeBankGateway.failure = None
        for invalid in (-1, 10.5, True, '100'):
            FakeBankGateway.balances[self.creator.pk] = invalid
            response = self.client.post(self.create_url, self.post_data())
            self.assertContains(response, 'saldo no válido', status_code=503)
            self.assertFalse(BattleChallenge.objects.exists())
        with override_settings(DUCKYCLASH_BANK_GATEWAY=''):
            with self.assertRaises(BankUnavailable):
                get_bank_gateway()

    def test_local_profile_adapter_is_read_only(self):
        gateway = ProfileBalanceGateway()
        self.assertEqual(gateway.available_balance(self.creator), 200)
        self.assertEqual(gateway.available_balance(self.creator), 200)
        self.assertEqual(self.creator.user_profile_arenas.duckCoins, 200)
        self.assertFalse(EconomicOperation.objects.exists())

    def test_invalid_bank_configuration_is_explicit(self):
        for path in ('does.not.exist.Gateway', 'builtins.dict'):
            with self.subTest(path=path), override_settings(DUCKYCLASH_BANK_GATEWAY=path):
                with self.assertRaises(BankUnavailable):
                    get_bank_gateway()

    def test_battle_links_clash_without_legacy_mutation_controls(self):
        challenge = self.create()
        self.client.force_login(self.opponent)
        response = self.client.get(reverse('ducky_arenas:battle'))
        self.assertContains(response, reverse('duckyclash:home'))
        self.assertNotContains(response, f'name="challenge_id" value="{challenge.pk}"')

    def test_bank_failure_on_accept_and_counter_leaves_pending_offer(self):
        challenge = self.create()
        self.client.force_login(self.opponent)
        FakeBankGateway.failure = 'Consulta bancaria interrumpida'
        self.assertContains(self.action(challenge), 'Consulta bancaria interrumpida', status_code=503)
        local = timezone.localtime(self.schedule + timedelta(hours=1), ZoneInfo('Europe/Madrid'))
        response = self.action(
            challenge, 'counter', date=local.date().isoformat(), time=local.strftime('%H:%M'), stake=30,
        )
        self.assertContains(response, 'Consulta bancaria interrumpida', status_code=503)
        challenge.refresh_from_db()
        self.assertEqual(challenge.revision, 0)
        self.assertEqual(challenge.clash_offers.count(), 1)
        self.assertEqual(Notificacion.objects.count(), 1)
        self.assertFalse(Duel.objects.exists())

    def test_permissions_for_private_detail_and_all_mutations(self):
        challenge = self.create()
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('duckyclash:challenge_detail', args=[challenge.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse('duckyclash:home')), f'Reto #{challenge.pk}')
        for action in ('accept', 'decline', 'counter'):
            self.assertEqual(self.action(challenge, action).status_code, 404)
        for service in (accept_offer, decline_offer):
            with self.assertRaises(PermissionDenied):
                service(actor=self.outsider, challenge_id=challenge.pk, version=1, revision=0)
        with self.assertRaises(PermissionDenied):
            self.counter(challenge, self.outsider)
        challenge.refresh_from_db()
        self.assertEqual(challenge.status, BattleChallenge.Status.PENDING)
        self.client.force_login(self.creator)
        self.assertEqual(self.action(challenge).status_code, 403)

    def test_post_only_csrf_login_and_incomplete_profile(self):
        challenge = self.create()
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get(self.create_url).status_code, 302)
        client.force_login(self.opponent)
        for action in ('accept', 'decline', 'counter'):
            url = reverse(f'duckyclash:challenge_{action}', args=[challenge.pk])
            self.assertEqual(client.get(url).status_code, 405)
            self.assertEqual(client.post(url, {'version': 1, 'revision': 0}).status_code, 403)
        self.assertEqual(client.post(self.create_url, self.post_data()).status_code, 403)
        client.get(reverse('duckyclash:challenge_detail', args=[challenge.pk]))
        token = client.cookies['csrftoken'].value
        response = client.post(
            reverse('duckyclash:challenge_accept', args=[challenge.pk]),
            {'version': 1, 'revision': 0, 'csrfmiddlewaretoken': token},
        )
        self.assertEqual(response.status_code, 302)
        UserProfileArenas.objects.filter(user=self.opponent).update(onboardingCompleted=False)
        self.assertEqual(client.get(self.create_url).status_code, 302)

    def test_counteroffer_preserves_terms_history_expiry_and_supports_rounds(self):
        challenge = self.create()
        original = challenge.clash_offers.get()
        second = self.counter(challenge, self.opponent)
        third = self.counter(challenge, self.creator, stake=25, scheduled_at=self.schedule + timedelta(hours=2))
        self.assertEqual([o.version for o in challenge.clash_offers.order_by('version')], [1, 2, 3])
        original.refresh_from_db()
        self.assertEqual(original.stake, 20)
        for offer in (second, third):
            self.assertEqual(offer.category_id, original.category_id)
            self.assertEqual(offer.subcategory_id, original.subcategory_id)
            self.assertEqual(offer.landscape_id, original.landscape_id)
            self.assertEqual(offer.expires_at, original.expires_at)
            self.assertEqual(offer.time_zone, original.time_zone)
        self.assertEqual(challenge.expires_at, original.expires_at)
        duel = accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=3, revision=2)
        self.assertEqual(duel.agreement.offer_id, third.pk)
        self.assertEqual(duel.status, Duel.Status.PENDING_RESERVATION)
        self.assertFalse(EconomicOperation.objects.exists())

    def test_counter_ui_ignores_forged_category_landscape_and_expiry(self):
        challenge = self.create()
        self.client.force_login(self.opponent)
        other = Landscape.objects.create(code='other', name='Otro')
        local = timezone.localtime(self.schedule + timedelta(hours=3), ZoneInfo('Europe/Madrid'))
        response = self.action(
            challenge, 'counter', date=local.date().isoformat(), time=local.strftime('%H:%M'),
            stake=30, category=99999, landscape=other.pk, expires_at='2099-01-01',
        )
        self.assertEqual(response.status_code, 302)
        challenge.refresh_from_db()
        second = challenge.clash_offers.get(version=2)
        self.assertEqual(second.landscape_id, self.landscape.pk)
        self.assertEqual(second.expires_at, self.expiry)
        self.client.force_login(self.creator)
        detail = self.client.get(reverse('duckyclash:challenge_detail', args=[challenge.pk]))
        self.assertContains(detail, 'Historial de ofertas')
        self.assertContains(detail, 'Contraofertar')
        self.assertContains(detail, 'versión 2')

    def test_counters_must_change_terms_fit_deadline_and_have_balance(self):
        challenge = self.create()
        for changes in (
            {'scheduled_at': self.schedule, 'stake': 20},
            {'scheduled_at': self.expiry},
            {'stake': 0}, {'stake': 101},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.counter(challenge, self.opponent, **changes)
        self.assertEqual(challenge.clash_offers.count(), 1)
        self.assertEqual(challenge.revision, 0)

    def test_acceptance_rechecks_both_balances_and_never_reserves(self):
        challenge = self.create()
        self.client.force_login(self.opponent)
        for player in (self.creator, self.opponent):
            FakeBankGateway.balances[player.pk] = 0
            response = self.action(challenge)
            self.assertContains(response, 'suficientes DuckyCoins', status_code=409)
            self.assertFalse(Duel.objects.exists())
            FakeBankGateway.balances[player.pk] = 100
        response = self.action(challenge)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Duel.objects.get().status, Duel.Status.PENDING_RESERVATION)
        self.assertEqual(ChallengeAgreement.objects.count(), 1)
        self.assertEqual(Notificacion.objects.count(), 3)
        self.assertFalse(EconomicOperation.objects.exists())
        self.assertEqual(list(UserProfileArenas.objects.values_list('duckCoins', flat=True)), [200] * 3)

    def test_rejection_terminal_and_allowed_without_bank(self):
        challenge = self.create()
        self.client.force_login(self.opponent)
        FakeBankGateway.failure = 'Banco caído'
        response = self.action(challenge, 'decline')
        self.assertEqual(response.status_code, 302)
        challenge.refresh_from_db()
        self.assertEqual(challenge.status, BattleChallenge.Status.DECLINED)
        self.assertFalse(Duel.objects.exists())
        self.assertEqual(Notificacion.objects.filter(usuario=self.creator, titulo='Reto rechazado').count(), 1)
        self.assertEqual(self.action(challenge).status_code, 409)

    def test_creator_can_decline_counter_and_proposer_cannot_respond(self):
        challenge = self.create()
        self.counter(challenge, self.opponent)
        self.client.force_login(self.opponent)
        self.assertEqual(self.action(challenge, 'decline').status_code, 403)
        self.client.force_login(self.creator)
        self.assertEqual(self.action(challenge, 'decline').status_code, 302)
        challenge.refresh_from_db()
        self.assertEqual(challenge.status, BattleChallenge.Status.DECLINED)

    def test_old_version_and_double_click_cannot_duplicate_agreements_or_notices(self):
        challenge = self.create()
        self.counter(challenge, self.opponent)
        self.client.force_login(self.creator)
        self.assertEqual(self.action(challenge, version=1, revision=0).status_code, 409)
        count = Notificacion.objects.count()
        self.assertEqual(self.action(challenge).status_code, 302)
        self.assertEqual(self.action(challenge).status_code, 409)
        self.assertEqual(Duel.objects.count(), 1)
        self.assertEqual(Notificacion.objects.count(), count + 2)

    def test_expired_offers_cannot_be_accepted_rejected_or_countered(self):
        challenge = self.create()
        self.client.force_login(self.opponent)
        with patch('apps.duckyclash.services.timezone.now', return_value=self.expiry):
            self.assertEqual(self.action(challenge).status_code, 409)
            self.assertEqual(self.action(challenge, 'decline').status_code, 409)
            with self.assertRaises(ValidationError):
                self.counter(challenge, self.opponent)
        self.assertEqual(challenge.clash_offers.count(), 1)
        self.assertFalse(Duel.objects.exists())

    def test_expiry_during_balance_query_and_lost_cas_roll_back(self):
        challenge = self.create()
        with patch('django.db.models.query.QuerySet.update', return_value=0):
            with self.assertRaises(ValidationError):
                accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        self.assertFalse(Duel.objects.exists())
        with patch('apps.duckyclash.services.require_balance'):
            with patch('apps.duckyclash.services.timezone.now', side_effect=[timezone.now(), self.expiry]):
                with self.assertRaises(ValidationError):
                    accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        challenge.refresh_from_db()
        self.assertEqual(challenge.status, BattleChallenge.Status.PENDING)
        self.assertEqual(Notificacion.objects.count(), 1)

    def test_counter_failure_rolls_back_revision_and_notifications(self):
        challenge = self.create()
        with patch.object(ChallengeOffer, 'save', side_effect=ValidationError('Error de oferta')):
            with self.assertRaises(ValidationError):
                self.counter(challenge, self.opponent)
        challenge.refresh_from_db()
        self.assertEqual(challenge.revision, 0)
        self.assertEqual(challenge.proposed_by_id, self.creator.pk)
        self.assertEqual(Notificacion.objects.count(), 1)

    def test_ambiguous_and_nonexistent_local_time_rejected(self):
        for date in ('2027-03-28', '2027-10-31'):
            form = PrivateChallengeForm(self.post_data(date=date, time='02:30'), actor=self.creator)
            self.assertFalse(form.is_valid())
            self.assertIn('time', form.errors)

    def test_invalid_action_forms_are_explicit_and_no_mutation(self):
        challenge = self.create()
        self.client.force_login(self.opponent)
        self.assertEqual(self.action(challenge, version='invalid').status_code, 400)
        self.assertEqual(self.action(challenge, 'counter', date='bad', time='bad', stake=0).status_code, 400)
        self.assertFalse(Duel.objects.exists())
        self.assertEqual(challenge.clash_offers.count(), 1)

    def test_sqlite_lock_is_conflict_and_other_database_errors_surface(self):
        challenge = self.create()
        self.client.force_login(self.opponent)
        with patch('apps.duckyclash.views.accept_offer', side_effect=OperationalError('database is locked')):
            self.assertContains(self.action(challenge), 'Recarga', status_code=409)
        with patch('apps.duckyclash.views.accept_offer', side_effect=OperationalError('unexpected db failure')):
            with self.assertRaises(OperationalError):
                self.action(challenge)
