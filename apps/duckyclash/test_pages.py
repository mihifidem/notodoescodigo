from datetime import timedelta
from unittest.mock import patch

from django.contrib.staticfiles import finders
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import test_private_challenges as private_tests
from . import test_knowledge as knowledge_tests
from .models import Duel, DuelResolution, EconomicOperation
from .services import accept_offer, create_challenge


@override_settings(
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.test_private_challenges.FakeBankGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class ClashPageTests(TestCase):
    setUp = private_tests.PrivateChallengeTests.setUp
    create = private_tests.PrivateChallengeTests.create
    counter = private_tests.PrivateChallengeTests.counter
    post_data = private_tests.PrivateChallengeTests.post_data

    def duel(self):
        challenge = self.create()
        return accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)

    def test_pages_empty_navigation_and_login(self):
        for name in ('home', 'create', 'invitations_received', 'invitations_sent', 'counteroffers', 'duel_list'):
            url = reverse(f'duckyclash:{name}')
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'aria-label="DuckyClash"')
            self.assertContains(response, 'pages.js')
            self.assertEqual(Client().get(url).status_code, 302)
        self.assertNotContains(self.client.get(reverse('duckyclash:home')), 'combate todavía no está disponible')

    def test_invitation_folders_isolate_participants_and_current_responder(self):
        challenge = self.create()
        self.assertContains(self.client.get(reverse('duckyclash:invitations_sent')), f'Reto #{challenge.pk}')
        self.assertNotContains(self.client.get(reverse('duckyclash:invitations_received')), f'Reto #{challenge.pk}')
        self.client.force_login(self.opponent)
        self.assertContains(self.client.get(reverse('duckyclash:invitations_received')), f'Reto #{challenge.pk}')
        self.counter(challenge, self.opponent)
        self.assertNotContains(self.client.get(reverse('duckyclash:counteroffers')), f'Reto #{challenge.pk}')
        self.client.force_login(self.creator)
        self.assertContains(self.client.get(reverse('duckyclash:counteroffers')), f'Reto #{challenge.pk}')
        with patch('django.utils.timezone.now', return_value=challenge.expires_at + timedelta(seconds=1)):
            self.assertNotContains(self.client.get(reverse('duckyclash:counteroffers')), f'Reto #{challenge.pk}')
        self.client.force_login(self.outsider)
        for folder in ('invitations_received', 'invitations_sent', 'counteroffers'):
            self.assertNotContains(self.client.get(reverse(f'duckyclash:{folder}')), f'Reto #{challenge.pk}')

    def test_detail_waiting_results_and_state_are_private(self):
        duel = self.duel()
        urls = [reverse(f'duckyclash:{name}', args=[duel.pk])
                for name in ('duel_detail', 'waiting', 'results', 'waiting_state')]
        self.client.force_login(self.outsider)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(Client().get(url).status_code, 302)
        self.assertNotContains(self.client.get(reverse('duckyclash:duel_list')), f'Duelo #{duel.pk}')
        for actor in (self.creator, self.opponent):
            self.client.force_login(actor)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_waiting_reuses_real_avatar_and_requires_time_and_both_reservations(self):
        duel = self.duel()
        profile = self.creator.user_profile_arenas
        profile.nickname = 'Ducky reconocido'
        profile.avatarType = 'DEVELOPER'
        profile.save()
        url = reverse('duckyclash:waiting', args=[duel.pk])
        state_url = reverse('duckyclash:waiting_state', args=[duel.pk])
        response = self.client.get(url)
        self.assertContains(response, 'Ducky reconocido')
        self.assertContains(response, 'waiting-player-initial')
        self.assertContains(response, self.landscape.name)
        self.assertContains(response, 'aria-disabled="true"')
        self.assertContains(response, f'datetime="{duel.agreement.offer.scheduled_at.isoformat()}"')
        self.assertFalse(response.context['can_ring'])
        self.assertIsNone(response.context['opens_at'])
        for actor in (self.creator, self.opponent):
            EconomicOperation.objects.create(
                duel=duel, player=actor, amount=20, kind=EconomicOperation.Kind.RESERVE,
                status=EconomicOperation.Status.CONFIRMED, bank_reference=f'test-hold-{actor.pk}',
            )
        Duel.objects.filter(pk=duel.pk).update(status=Duel.Status.SCHEDULED)
        with patch('django.utils.timezone.now', return_value=self.schedule - timedelta(seconds=1)):
            state = self.client.get(state_url).json()
            self.assertFalse(state['can_ring'])
            self.assertEqual(state['opens_at'], self.schedule.isoformat())
            self.assertEqual(state['server_time'], (self.schedule - timedelta(seconds=1)).isoformat())
            confirmed = self.client.get(url)
            self.assertEqual(confirmed.context['opens_at'], self.schedule)
            self.assertContains(confirmed, 'Reto confirmado. Apertura del Ring en:')
            self.assertContains(confirmed, f'data-opens-at="{timezone.localtime(self.schedule).isoformat()}"')
        with patch('django.utils.timezone.now', return_value=self.schedule):
            self.assertTrue(self.client.get(url).context['can_ring'])
            self.assertTrue(self.client.get(state_url).json()['can_ring'])
            duel.economic_operations.filter(player=self.opponent).update(
                status=EconomicOperation.Status.UNCERTAIN, bank_reference='',
            )
            self.assertFalse(self.client.get(state_url).json()['can_ring'])
            self.assertIsNone(self.client.get(state_url).json()['opens_at'])
        self.assertFalse(duel.questions.exists())

    def test_waiting_page_uses_existing_prehistoric_gifs(self):
        duel = self.duel()
        response = self.client.get(reverse('duckyclash:waiting', args=[duel.pk]))

        self.assertContains(response, 'DAR_PRE1_V.gif')
        self.assertContains(response, 'DAR_PRE2_V.gif')
        self.assertContains(response, 'class="waiting-player"', count=2)
        self.assertContains(response, 'class="waiting-player-details"', count=2)
        self.assertContains(response, '<h2 class="h5">', count=2)
        self.assertNotContains(response, '<video')
        self.assertIsNotNone(finders.find('duckyarenas/DAR_personajes/DAR_PRE1_V.gif'))
        self.assertIsNotNone(finders.find('duckyarenas/DAR_personajes/DAR_PRE2_V.gif'))

    def test_waiting_uses_platform_profile_avatar_above_player_name(self):
        duel = self.duel()
        self.opponent.avatar = 'avatars/waiting-profile.png'
        self.opponent.save(update_fields=['avatar'])
        response = self.client.get(reverse('duckyclash:waiting', args=[duel.pk]))
        self.assertContains(response, f'src="{self.opponent.avatar.url}"')
        self.assertContains(response, 'waiting-player-avatar', count=1)
        self.assertContains(response, 'waiting-player-initial', count=1)
        self.assertNotContains(response, 'DA_DEV_avatar.png')

    def test_opening_countdown_is_removed_on_cancellation_and_disabled_ring_access(self):
        duel = self.duel()
        for actor in (self.creator, self.opponent):
            EconomicOperation.objects.create(
                duel=duel, player=actor, amount=20, kind=EconomicOperation.Kind.RESERVE,
                status=EconomicOperation.Status.CONFIRMED, bank_reference=f'test-hold-{actor.pk}',
            )
        Duel.objects.filter(pk=duel.pk).update(status=Duel.Status.SCHEDULED)
        state_url = reverse('duckyclash:waiting_state', args=[duel.pk])
        self.assertIsNotNone(self.client.get(state_url).json()['opens_at'])
        Duel.objects.filter(pk=duel.pk).update(rules_snapshot={**duel.rules_snapshot, 'ring_access': 'disabled'})
        self.assertIsNone(self.client.get(state_url).json()['opens_at'])
        Duel.objects.filter(pk=duel.pk).update(status=Duel.Status.CANCELED)
        state = self.client.get(state_url).json()
        self.assertIsNone(state['opens_at'])
        self.assertTrue(state['is_terminal'])

    def test_results_do_not_equate_victory_to_payment(self):
        duel = self.duel()
        Duel.objects.filter(pk=duel.pk).update(status=Duel.Status.SETTLEMENT_PENDING, bank_mode=Duel.BankMode.PAYOUT)
        duel.refresh_from_db()
        DuelResolution.objects.create(duel=duel, winner=self.creator, reason='Resultado validado')
        url = reverse('duckyclash:results', args=[duel.pk])
        response = self.client.get(url)
        self.assertContains(response, f'Victoria de {self.creator.get_username()}')
        self.assertContains(response, 'no confirma pagos')
        self.assertNotContains(response, 'Liquidación bancaria confirmada')
        state = self.client.get(reverse('duckyclash:waiting_state', args=[duel.pk])).json()
        self.assertTrue(state['is_terminal'])
        self.assertFalse(state['can_ring'])
        self.assertIsNone(state['opens_at'])
        self.assertEqual(set(state), {'can_ring', 'is_terminal', 'status', 'server_time', 'opens_at'})
        Duel.objects.filter(pk=duel.pk).update(status=Duel.Status.FINISHED)
        self.assertContains(self.client.get(url), 'Liquidación bancaria confirmada')

    def test_active_result_and_invalid_filters_are_explicit(self):
        duel = self.duel()
        self.assertContains(self.client.get(reverse('duckyclash:results', args=[duel.pk])),
                            'El combate todavía no ha terminado')
        response = self.client.get(reverse('duckyclash:duel_list'), {'status': 'invented'})
        self.assertContains(response, 'El estado seleccionado no es válido')
        self.assertFalse(response.context['page_obj'].object_list.exists())
        response = self.client.post(self.create_url, self.post_data(stake=0))
        self.assertContains(response, 'No se han guardado los cambios', status_code=400)
        self.assertContains(response, 'data-clash-errors', status_code=400)

    def test_availability_is_bounded_public_only_and_no_store(self):
        public = create_challenge(
            actor=self.creator, opponent=None, is_public=True,
            category=self.category, subcategory=self.subcategory, landscape=self.landscape,
            scheduled_at=self.schedule, expires_at=self.expiry, time_zone='Europe/Madrid', stake=20,
        )
        private = self.create()
        url = reverse('duckyclash:open_state')
        self.client.force_login(self.outsider)
        response = self.client.get(url, {'ids': f'{public.pk},{private.pk}'})
        self.assertEqual(response.json()['available'], [{'id': public.pk, 'remaining': response.json()['available'][0]['remaining']}])
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        for invalid in ('', 'abc', '1.2', '١', '1,' * 13, '9' * 20):
            self.assertEqual(self.client.get(url, {'ids': invalid}).status_code, 400)
        accept_offer(actor=self.opponent, challenge_id=public.pk, version=1, revision=0)
        self.assertEqual(self.client.get(url, {'ids': str(public.pk)}).json()['available'], [])
        self.assertEqual(Client().get(url, {'ids': str(public.pk)}).status_code, 302)

    def test_availability_excludes_expired_without_background_job(self):
        public = create_challenge(
            actor=self.creator, opponent=None, is_public=True,
            category=self.category, subcategory=self.subcategory, landscape=self.landscape,
            scheduled_at=self.schedule, expires_at=self.expiry, time_zone='Europe/Madrid', stake=20,
        )
        with patch('django.utils.timezone.now', return_value=self.expiry):
            response = self.client.get(reverse('duckyclash:open_state'), {'ids': str(public.pk)})
            self.assertEqual(response.json()['available'], [])


@override_settings(
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.ProfileBalanceGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    DUCKYCLASH_KNOWLEDGE_GATEWAY='apps.duckyclash.knowledge.LMSKnowledgeGateway',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class PageQuestionPrivacyTests(TestCase):
    setUp = knowledge_tests.KnowledgeTests.setUp
    create = knowledge_tests.KnowledgeTests.create
    accept = knowledge_tests.KnowledgeTests.accept
    publish = knowledge_tests.KnowledgeTests.publish

    def test_new_pages_never_serialize_private_correction_or_question(self):
        question = knowledge_tests.KnowledgeTests.prepare(self)
        self.client.force_login(self.creator)
        for name in ('duel_detail', 'waiting', 'results', 'waiting_state'):
            response = self.client.get(reverse(f'duckyclash:{name}', args=[self.duel.pk]))
            self.assertNotContains(response, 'PRIVATE-EXPLANATION-SECRET')
            self.assertNotContains(response, 'PRIVATE-VALIDATION-KEY')
            self.assertNotContains(response, 'correct_answers')
            self.assertNotContains(response, question.snapshot['prompt'])
