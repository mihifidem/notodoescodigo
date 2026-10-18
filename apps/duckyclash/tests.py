from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import CourseCategory, Curso
from apps.ducky_arenas.battle_challenges import respond_to_challenge
from apps.ducky_arenas.models import ArenaItem, BattleChallenge, UserArenaInventory, UserProfileArenas
from apps.lms.models import Question, QuestionBank, QuestionVersion

from .models import (
    Challenge, ChallengeAgreement, ChallengeOffer, ChallengeSubcategory, Duel,
    DuelAnswer, DuelEvent, DuelItemUse, DuelQuestion, DuelResolution,
    EconomicOperation, Landscape, ParticipantState,
)
from .services import accept_offer, create_challenge


@override_settings(
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.bank.ProfileBalanceGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class ClashFoundationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.creator, self.opponent, self.outsider = [
            User.objects.create_user(**{User.USERNAME_FIELD: name}, password='test1234')
            for name in ('clash_creator', 'clash_opponent', 'clash_outsider')
        ]
        for user in (self.creator, self.opponent, self.outsider):
            UserProfileArenas.objects.create(
                user=user, nickname=user.get_username(), duckCoins=150,
                onboardingCompleted=True,
            )
        self.category = CourseCategory.objects.create(nombre='Clash Python')
        self.subcategory = ChallengeSubcategory.objects.create(category=self.category, name='Fundamentos')
        self.landscape = Landscape.objects.create(code='test-ring', name='Ring de pruebas')
        self.now = timezone.now()

    def create(self, public=False, **changes):
        params = dict(
            actor=self.creator, opponent=None if public else self.opponent,
            is_public=public, category=self.category, subcategory=self.subcategory,
            landscape=self.landscape, scheduled_at=self.now + timedelta(hours=2),
            expires_at=self.now + timedelta(hours=1), time_zone='Europe/Madrid', stake=20,
        )
        params.update(changes)
        return create_challenge(**params)

    def accept(self, challenge):
        return accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)

    def question(self, duel):
        course = Curso.objects.create(
            nombre='Clash course', slug='clash-course', descripcion='Curso de pruebas',
            fecha_inicio=timezone.localdate(), creado_por=self.creator,
        )
        bank = QuestionBank.objects.create(course=course, title='Preguntas Clash', owner=self.creator)
        question = Question.objects.create(bank=bank, title='Pregunta', status=Question.Status.PUBLISHED)
        version = QuestionVersion.objects.create(
            question=question, prompt='¿Cuánto es 1 + 1?', options=['1', '2'],
            correct_answers=['2'], is_published=True,
        )
        return DuelQuestion.objects.create(
            duel=duel, number=1, version=version,
            opens_at=timezone.now() - timedelta(minutes=1),
            closes_at=timezone.now() + timedelta(minutes=1),
            snapshot={'prompt': version.prompt, 'correct_answers': ['2']},
        )

    def assert_db_rejects(self, operation):
        with self.assertRaises(IntegrityError), transaction.atomic():
            operation()

    def test_proxy_reuses_user_profile_and_challenge_table(self):
        challenge = self.create()
        self.assertEqual(Challenge._meta.db_table, BattleChallenge._meta.db_table)
        self.assertEqual(BattleChallenge.objects.count(), 1)
        self.assertEqual(challenge.creator.user_profile_arenas.duckCoins, 150)
        self.assertEqual(UserProfileArenas.objects.count(), 3)
        self.assertEqual(challenge.clash_offers.get().category_id, self.category.pk)

    def test_creation_validates_identity_stake_time_and_zone_atomically(self):
        for changes in (
            {'opponent': self.creator}, {'opponent': None},
            {'stake': 0}, {'stake': -1}, {'stake': True}, {'stake': 1.5},
            {'scheduled_at': self.now - timedelta(minutes=1)},
            {'expires_at': self.now + timedelta(hours=3)},
            {'time_zone': 'Not/AZone'},
            {'scheduled_at': timezone.make_naive(self.now + timedelta(hours=2))},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.create(**changes)
            self.assertFalse(BattleChallenge.objects.exists())

    def test_cross_category_and_inactive_catalog_rejected(self):
        other = CourseCategory.objects.create(nombre='Otra categoría')
        with self.assertRaises(ValidationError):
            self.create(category=other)
        self.landscape.active = False
        self.landscape.save()
        with self.assertRaises(ValidationError):
            self.create()

    def test_offer_immutable_and_database_constraints(self):
        challenge = self.create()
        offer = challenge.clash_offers.get()
        offer.stake = 25
        with self.assertRaises(ValidationError):
            offer.save()
        self.assert_db_rejects(lambda: ChallengeOffer.objects.filter(pk=offer.pk).update(stake=0))
        self.assert_db_rejects(lambda: ChallengeOffer.objects.filter(pk=offer.pk).update(expires_at=offer.scheduled_at))
        with self.assertRaises(ValidationError):
            ChallengeOffer.objects.create(
                challenge=challenge, version=1, proposed_by=self.creator, category=self.category,
                landscape=self.landscape, scheduled_at=offer.scheduled_at,
                expires_at=offer.expires_at, stake=20,
            )

    def test_acceptance_creates_dormant_duel_and_no_economic_changes(self):
        duel = self.accept(self.create())
        self.assertEqual(duel.status, Duel.Status.PENDING_RESERVATION)
        self.assertEqual(duel.participants.count(), 2)
        self.assertEqual(list(duel.participants.values_list('hp', flat=True)), [100, 100])
        self.assertFalse(EconomicOperation.objects.exists())
        self.assertEqual(list(UserProfileArenas.objects.values_list('duckCoins', flat=True)), [150] * 3)
        with self.assertRaises(ValidationError):
            self.accept(duel.agreement.challenge)

    def test_public_first_acceptance_claims_only_one_opponent(self):
        challenge = self.create(public=True)
        duel = self.accept(challenge)
        self.assertEqual(duel.agreement.opponent_id, self.opponent.pk)
        with self.assertRaises(PermissionDenied):
            accept_offer(actor=self.outsider, challenge_id=challenge.pk, version=1, revision=0)
        self.assertEqual(Duel.objects.count(), 1)

    def test_wrong_actor_stale_version_and_expiration_rejected(self):
        challenge = self.create()
        for actor, version, revision, error in (
            (self.creator, 1, 0, PermissionDenied),
            (self.outsider, 1, 0, PermissionDenied),
            (self.opponent, 2, 0, ValidationError),
            (self.opponent, 1, 9, ValidationError),
        ):
            with self.assertRaises(error):
                accept_offer(actor=actor, challenge_id=challenge.pk, version=version, revision=revision)
        with patch('apps.duckyclash.services.timezone.now', return_value=challenge.expires_at):
            with self.assertRaises(ValidationError):
                self.accept(challenge)
        self.assertFalse(ChallengeAgreement.objects.exists())

    def test_legacy_actions_cannot_overwrite_versioned_offer(self):
        challenge = self.create()
        with self.assertRaisesMessage(ValidationError, 'DuckyClash'):
            respond_to_challenge(
                actor=self.opponent, challenge_id=challenge.pk, revision=0, action='counter', stake=35,
            )
        challenge.refresh_from_db()
        self.assertEqual(challenge.stake, 20)

    def test_agreement_is_immutable_and_participants_must_match(self):
        duel = self.accept(self.create())
        agreement = duel.agreement
        agreement.opponent = self.outsider
        with self.assertRaises(ValidationError):
            agreement.save()
        self.assert_db_rejects(lambda: ChallengeAgreement.objects.filter(pk=agreement.pk).update(opponent=self.creator))
        with self.assertRaises(ProtectedError):
            agreement.challenge.delete()
        with self.assertRaises(ProtectedError):
            self.creator.delete()

    def test_hp_seats_and_participant_integrity(self):
        duel = self.accept(self.create())
        state = duel.participants.get(player=self.creator)
        for hp in (-1, 101):
            state.hp = hp
            with self.assertRaises(ValidationError):
                state.save()
            self.assert_db_rejects(lambda: ParticipantState.objects.filter(pk=state.pk).update(hp=hp))
        state.hp = 100
        state.player = self.outsider
        with self.assertRaises(ValidationError):
            state.save()
        self.assert_db_rejects(lambda: ParticipantState.objects.filter(pk=state.pk).update(seat=3))
        with self.assertRaises(ValidationError):
            ParticipantState.objects.create(duel=duel, player=self.creator, seat=1)
        self.assert_db_rejects(lambda: Duel.objects.filter(pk=duel.pk).update(initial_hp=0))

    def test_activation_unavailable_without_real_services(self):
        duel = self.accept(self.create())
        for status in (Duel.Status.READY, Duel.Status.IN_PROGRESS, Duel.Status.FINISHED):
            duel.status = status
            with self.assertRaises(ValidationError):
                duel.save()

    def test_answer_once_per_player_question_and_server_time(self):
        duel = self.accept(self.create())
        question = self.question(duel)
        state = duel.participants.get(player=self.creator)
        answer = DuelAnswer.objects.create(question=question, participant=state, answer='2', received_at=self.now - timedelta(days=1))
        self.assertGreater(answer.received_at, self.now)
        with self.assertRaises(ValidationError):
            DuelAnswer.objects.create(question=question, participant=state, answer='1')
        self.assert_db_rejects(lambda: DuelAnswer.objects.bulk_create([
            DuelAnswer(question=question, participant=state, answer='1'),
        ]))
        with patch('apps.duckyclash.models.timezone.now', return_value=question.closes_at):
            with self.assertRaises(ValidationError):
                DuelAnswer.objects.create(
                    question=question, participant=duel.participants.get(player=self.opponent), answer='2',
                )
        answer.answer = '1'
        with self.assertRaises(ValidationError):
            answer.save()

    def test_question_and_answer_cross_duel_rejected(self):
        duel = self.accept(self.create())
        question = self.question(duel)
        another = self.accept(self.create())
        with self.assertRaises(ValidationError):
            DuelAnswer.objects.create(
                question=question, participant=another.participants.get(player=self.creator), answer='2',
            )
        self.assert_db_rejects(lambda: DuelQuestion.objects.filter(pk=question.pk).update(closes_at=question.opens_at))

    def test_items_require_inventory_and_consume_reference_is_unique(self):
        duel = self.accept(self.create())
        question = self.question(duel)
        state = duel.participants.get(player=self.creator)
        item = ArenaItem.objects.create(code='clash-test-item', name='Objeto de prueba')
        with self.assertRaises(ValidationError):
            DuelItemUse.objects.create(question=question, participant=state, item=item, consumption_reference='consume-1')
        entry = UserArenaInventory.objects.create(profile=self.creator.user_profile_arenas, item=item)
        DuelItemUse.objects.create(
            question=question, participant=state, item=item,
            consumption_reference='consume-1', effect_snapshot={'kind': 'shield'},
        )
        with self.assertRaises(ValidationError):
            DuelItemUse.objects.create(
                question=question, participant=state, item=item,
                consumption_reference='consume-1', effect_snapshot={'kind': 'shield'},
            )
        entry.refresh_from_db()
        self.assertEqual(entry.quantity, 1)  # Reference storage is not a consumption gateway.

    def test_economic_operation_constraints_and_single_settlement(self):
        duel = self.accept(self.create())
        operation = EconomicOperation.objects.create(
            duel=duel, player=self.creator, kind='reserve', amount=20,
        )
        with self.assertRaises(ValidationError):
            EconomicOperation.objects.create(duel=duel, player=self.outsider, kind='reserve', amount=20)
        self.assert_db_rejects(lambda: EconomicOperation.objects.filter(pk=operation.pk).update(amount=0))
        self.assert_db_rejects(lambda: EconomicOperation.objects.filter(pk=operation.pk).update(status='confirmed'))
        with self.assertRaises(ValidationError):
            EconomicOperation.objects.create(duel=duel, player=self.creator, kind='reserve', amount=20)
        settlement = EconomicOperation.objects.create(duel=duel, kind='settle', amount=40)
        self.assert_db_rejects(lambda: EconomicOperation.objects.bulk_create([
            EconomicOperation(duel=duel, kind='settle', amount=40),
        ]))
        settlement.status = 'confirmed'
        with self.assertRaises(ValidationError):
            settlement.save()

    def test_one_resolution_per_duel_and_valid_winner(self):
        duel = self.accept(self.create())
        with self.assertRaises(ValidationError):
            DuelResolution.objects.create(duel=duel, winner=self.creator, reason='No iniciado')
        # Simulate the future authoritative service to exercise database integrity.
        Duel.objects.filter(pk=duel.pk).update(status=Duel.Status.SETTLEMENT_PENDING)
        duel.refresh_from_db()
        with self.assertRaises(ValidationError):
            DuelResolution.objects.create(duel=duel, winner=self.outsider, reason='Inválido')
        result = DuelResolution.objects.create(duel=duel, winner=self.creator, reason='Prueba')
        self.assert_db_rejects(lambda: DuelResolution.objects.bulk_create([
            DuelResolution(duel=duel, winner=self.creator, reason='Duplicado'),
        ]))
        self.assert_db_rejects(lambda: DuelResolution.objects.filter(pk=result.pk).update(is_draw=True))

    def test_events_require_participants_and_unique_sequence(self):
        duel = self.accept(self.create())
        event = DuelEvent.objects.create(duel=duel, sequence=1, actor=self.creator, kind='duel.created')
        with self.assertRaises(ValidationError):
            DuelEvent.objects.create(duel=duel, sequence=2, actor=self.outsider, kind='invalid')
        self.assert_db_rejects(lambda: DuelEvent.objects.bulk_create([
            DuelEvent(duel=duel, sequence=1, kind='duplicate'),
        ]))
        event.payload = {'changed': True}
        with self.assertRaises(ValidationError):
            event.save()

    def test_authenticated_views_preserve_private_ring_and_question_secrets(self):
        challenge = self.create()
        duel = self.accept(challenge)
        self.question(duel)
        ring_url = reverse('duckyclash:ring', args=[duel.pk])
        self.assertEqual(self.client.get(ring_url).status_code, 302)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(ring_url).status_code, 403)
        self.assertEqual(self.client.get(reverse('duckyclash:challenge_detail', args=[challenge.pk])).status_code, 403)
        self.client.force_login(self.creator)
        self.assertContains(self.client.get(reverse('duckyclash:home')), 'combate validados por Django')
        self.assertContains(self.client.get(reverse('duckyclash:challenge_detail', args=[challenge.pk])), '20 DuckyCoins')
        response = self.client.get(ring_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'correct_answers')
        self.assertNotContains(response, '¿Cuánto es')
        self.assertEqual(self.client.post(ring_url, {'status': 'finished'}).status_code, 405)

    def test_catalog_admin_editable_operations_read_only(self):
        self.creator.is_staff = self.creator.is_superuser = True
        self.creator.save()
        self.client.force_login(self.creator)
        self.assertEqual(self.client.get(reverse('admin:duckyclash_landscape_add')).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:duckyclash_duel_add')).status_code, 403)
        self.assertIsInstance(admin.site._registry[Duel], admin.ModelAdmin)

    def test_inactive_or_incomplete_players_cannot_create_or_accept(self):
        self.opponent.is_active = False
        self.opponent.save()
        with self.assertRaises(PermissionDenied):
            self.create()
        challenge = self.create(public=True)
        with self.assertRaises(PermissionDenied):
            self.accept(challenge)
        self.opponent.is_active = True
        self.opponent.save()
        profile = self.opponent.user_profile_arenas
        profile.onboardingCompleted = False
        profile.save()
        with self.assertRaises(PermissionDenied):
            self.accept(challenge)

    def test_offer_agreement_and_operation_unique_keys_at_database_level(self):
        challenge = self.create()
        offer = challenge.clash_offers.get()
        self.assert_db_rejects(lambda: ChallengeOffer.objects.bulk_create([
            ChallengeOffer(
                challenge=challenge, version=1, proposed_by=self.creator, category=self.category,
                landscape=self.landscape, scheduled_at=offer.scheduled_at,
                expires_at=offer.expires_at, stake=20,
            ),
        ]))
        duel = self.accept(challenge)
        self.assert_db_rejects(lambda: Duel.objects.bulk_create([Duel(agreement=duel.agreement)]))
        operation = EconomicOperation.objects.create(duel=duel, player=self.creator, kind='reserve', amount=20)
        self.assert_db_rejects(lambda: EconomicOperation.objects.bulk_create([
            EconomicOperation(
                duel=duel, player=self.opponent, kind='reserve', amount=20,
                operation_key=operation.operation_key,
            ),
        ]))


class ClashMigrationTests(TransactionTestCase):
    def test_additive_migration_roundtrip_preserves_users_profiles_and_legacy_challenges(self):
        user = get_user_model().objects.create_user(username='clash_migration_player')
        profile = UserProfileArenas.objects.create(user=user, nickname='Persistent', duckCoins=123)
        challenge = BattleChallenge.objects.create(
            creator=user, proposed_by=user, is_public=True, stake=0,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([('duckyclash', None)])
            self.assertNotIn('duckyclash_duel', connection.introspection.table_names())
            self.assertTrue(UserProfileArenas.objects.filter(pk=profile.pk, duckCoins=123).exists())
            self.assertTrue(BattleChallenge.objects.filter(pk=challenge.pk, stake=0).exists())
            executor = MigrationExecutor(connection)
            executor.migrate(leaves)
            self.assertIn('duckyclash_duel', connection.introspection.table_names())
            self.assertTrue(UserProfileArenas.objects.filter(pk=profile.pk, user=user).exists())
        finally:
            MigrationExecutor(connection).migrate(leaves)
