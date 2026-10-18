from datetime import timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notificacion
from apps.courses.models import CourseCategory
from .models import Challenge, ChallengeOffer, Duel, EconomicOperation
from .services import accept_offer, create_challenge, delete_challenge, edit_challenge
from .selectors import open_challenges
from . import test_private_challenges as private_tests


@override_settings(
    DUCKYCLASH_BANK_GATEWAY='apps.duckyclash.test_private_challenges.FakeBankGateway',
    DUCKYCLASH_PAYMENT_GATEWAY='',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class ChallengeManagementTests(TestCase):
    setUp = private_tests.PrivateChallengeTests.setUp
    create = private_tests.PrivateChallengeTests.create
    post_data = private_tests.PrivateChallengeTests.post_data
    counter = private_tests.PrivateChallengeTests.counter

    def params(self, challenge, **updates):
        result = dict(
            actor=self.creator, challenge_id=challenge.pk,
            version=challenge.clash_offers.first().version, revision=challenge.revision,
            category=self.category, subcategory=self.subcategory, landscape=self.landscape,
            scheduled_at=self.schedule + timedelta(hours=2), expires_at=self.expiry,
            time_zone='Europe/Madrid', stake=30,
        )
        result.update(updates)
        return result

    def edit_data(self, challenge, **updates):
        expiry = timezone.localtime(self.expiry, ZoneInfo('Europe/Madrid'))
        result = self.post_data(
            version=challenge.clash_offers.first().version, revision=challenge.revision,
            expiry_date=expiry.date().isoformat(), expiry_time=expiry.strftime('%H:%M'), stake=30,
        )
        result.update(updates)
        return result

    def test_owner_edits_private_offer_without_overwriting_history_or_reserving(self):
        challenge = self.create()
        url = reverse('duckyclash:challenge_edit', args=[challenge.pk])
        self.assertContains(self.client.get(url), 'Guardar cambios')
        response = self.client.post(url, self.edit_data(challenge))
        self.assertRedirects(response, reverse('duckyclash:challenge_detail', args=[challenge.pk]))
        challenge.refresh_from_db()
        self.assertEqual(challenge.revision, 1)
        self.assertEqual(challenge.stake, 30)
        self.assertEqual(challenge.clash_offers.get(version=1).stake, 20)
        self.assertEqual(challenge.clash_offers.get(version=2).stake, 30)
        self.assertEqual(challenge.proposed_by_id, self.creator.pk)
        self.assertTrue(Notificacion.objects.filter(usuario=self.opponent, titulo='Reto actualizado').exists())
        self.assertFalse(Duel.objects.exists())
        self.assertFalse(EconomicOperation.objects.exists())
        with self.assertRaises(ValidationError):
            accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        duel = accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=2, revision=1)
        self.assertEqual(duel.agreement.offer.stake, 30)

    def test_public_edited_offer_filters_and_acceptance_use_latest_version(self):
        challenge = create_challenge(
            actor=self.creator, opponent=None, is_public=True, category=self.category,
            subcategory=self.subcategory, landscape=self.landscape, scheduled_at=self.schedule,
            expires_at=self.expiry, time_zone='Europe/Madrid', stake=20,
        )
        new_category = CourseCategory.objects.create(nombre='Nueva categoría')
        new_schedule = self.schedule + timedelta(days=1)
        edit_challenge(**self.params(
            challenge, category=new_category, subcategory=None, scheduled_at=new_schedule,
        ))
        challenge.refresh_from_db()
        self.assertIn(challenge, open_challenges())
        response = self.client.get(reverse('duckyclash:open_list'), {'max_stake': 25})
        self.assertNotContains(response, f'Reto #{challenge.pk}')
        response = self.client.get(reverse('duckyclash:open_list'), {'max_stake': 35})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['cards'][0]['offer'].version, 2)
        for filters in (
            {'category': self.category.pk},
            {'date': timezone.localdate(self.schedule).isoformat()},
        ):
            response = self.client.get(reverse('duckyclash:open_list'), filters)
            self.assertEqual(list(response.context['cards']), [])
        response = self.client.get(reverse('duckyclash:open_list'), {
            'category': new_category.pk, 'date': timezone.localdate(new_schedule).isoformat(),
        })
        self.assertEqual(len(response.context['cards']), 1)
        duel = accept_offer(actor=self.outsider, challenge_id=challenge.pk, version=2, revision=1)
        self.assertEqual(duel.agreement.offer.stake, 30)

    def test_only_creator_has_links_and_mutation_access(self):
        challenge = self.create()
        detail = reverse('duckyclash:challenge_detail', args=[challenge.pk])
        self.assertContains(self.client.get(detail), 'Borrar reto')
        for actor in (self.opponent, self.outsider):
            self.client.force_login(actor)
            if actor == self.opponent:
                self.assertNotContains(self.client.get(detail), 'Borrar reto')
            for action in ('edit', 'delete'):
                url = reverse(f'duckyclash:challenge_{action}', args=[challenge.pk])
                self.assertEqual(self.client.post(url, self.edit_data(challenge)).status_code, 404)
            with self.assertRaises(PermissionDenied):
                edit_challenge(**self.params(challenge, actor=actor))
            with self.assertRaises(PermissionDenied):
                delete_challenge(actor=actor, challenge_id=challenge.pk, version=1, revision=0)
        self.assertEqual(challenge.clash_offers.count(), 1)

    def test_delete_is_post_csrf_protected_and_removes_pending_offers_only(self):
        challenge = self.create()
        other = self.create()
        url = reverse('duckyclash:challenge_delete', args=[challenge.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client().post(url, {'version': 1, 'revision': 0}).status_code, 302)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.creator)
        self.assertEqual(csrf.post(url, {'version': 1, 'revision': 0}).status_code, 403)
        response = self.client.post(url, {'version': 1, 'revision': 0})
        self.assertRedirects(response, reverse('duckyclash:home'))
        self.assertFalse(Challenge.objects.filter(pk=challenge.pk).exists())
        self.assertFalse(ChallengeOffer.objects.filter(challenge_id=challenge.pk).exists())
        self.assertTrue(Challenge.objects.filter(pk=other.pk).exists())
        self.assertFalse(EconomicOperation.objects.exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.opponent, titulo='Reto eliminado').exists())
        self.assertEqual(self.client.post(url, {'version': 1, 'revision': 0}).status_code, 404)

    def test_confirmed_and_expired_challenges_cannot_be_changed_or_deleted(self):
        for accepted in (True, False):
            challenge = self.create()
            if accepted:
                accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
                challenge.refresh_from_db()
            else:
                Challenge.objects.filter(pk=challenge.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
            for action in ('edit', 'delete'):
                response = self.client.post(
                    reverse(f'duckyclash:challenge_{action}', args=[challenge.pk]),
                    self.edit_data(challenge),
                )
                self.assertEqual(response.status_code, 409)
            self.assertTrue(Challenge.objects.filter(pk=challenge.pk).exists())
            self.assertEqual(challenge.clash_offers.count(), 1)
            self.assertNotContains(self.client.get(
                reverse('duckyclash:challenge_detail', args=[challenge.pk]),
            ), 'Borrar reto')

    def test_stale_and_invalid_edits_are_rejected_without_changes(self):
        challenge = self.create()
        edit_challenge(**self.params(challenge))
        url = reverse('duckyclash:challenge_edit', args=[challenge.pk])
        stale = self.edit_data(challenge, version=1, revision=0)
        self.assertEqual(self.client.post(url, stale).status_code, 409)
        self.assertEqual(self.client.post(
            reverse('duckyclash:challenge_delete', args=[challenge.pk]), stale,
        ).status_code, 409)
        challenge.refresh_from_db()
        for changes in ({'stake': 0}, {'revision': ''}, {'date': '2000-01-01'}):
            self.assertEqual(self.client.post(url, self.edit_data(challenge, **changes)).status_code, 400)
        self.assertEqual(challenge.clash_offers.count(), 2)
        private_tests.FakeBankGateway.balances[self.creator.pk] = 1
        self.assertEqual(self.client.post(url, self.edit_data(challenge)).status_code, 409)
        self.assertEqual(challenge.clash_offers.count(), 2)

    def test_counteroffer_is_superseded_by_owner_edit_not_silently_accepted(self):
        challenge = self.create()
        self.counter(challenge, self.opponent)
        edit_challenge(**self.params(challenge))
        challenge.refresh_from_db()
        self.assertEqual(challenge.clash_offers.first().version, 3)
        self.assertEqual(challenge.proposed_by_id, self.creator.pk)
        with self.assertRaises(PermissionDenied):
            accept_offer(actor=self.creator, challenge_id=challenge.pk, version=3, revision=2)

    def test_acceptance_during_balance_io_wins_and_blocks_owner_edit(self):
        challenge = self.create()
        def accept_during_balance(*args):
            with patch('apps.duckyclash.services.require_balance'):
                accept_offer(actor=self.opponent, challenge_id=challenge.pk, version=1, revision=0)
        with patch('apps.duckyclash.services.require_balance', side_effect=accept_during_balance):
            with self.assertRaises(ValidationError):
                edit_challenge(**self.params(challenge))
        self.assertEqual(challenge.clash_offers.count(), 1)
        self.assertEqual(Duel.objects.get().agreement.offer.version, 1)

    def test_deletion_during_balance_io_rejects_stale_acceptance(self):
        challenge = self.create()
        deleted_id = challenge.pk
        with patch('apps.duckyclash.services.require_balance', side_effect=lambda *args: (
            delete_challenge(actor=self.creator, challenge_id=deleted_id, version=1, revision=0)
            if Challenge.objects.filter(pk=deleted_id).exists() else None
        )):
            with self.assertRaises(ValidationError):
                accept_offer(actor=self.opponent, challenge_id=deleted_id, version=1, revision=0)
        self.assertFalse(Duel.objects.exists())

    def make_staff_without_player_profile(self):
        self.outsider.is_staff = True
        self.outsider.save(update_fields=['is_staff'])
        self.outsider.user_profile_arenas.delete()
        self.client.force_login(self.outsider)
        return self.outsider

    def test_staff_without_player_profile_can_delete_private_challenge_and_is_audited(self):
        from django.contrib.admin.models import DELETION, LogEntry
        challenge = self.create()
        staff = self.make_staff_without_player_profile()
        detail = reverse('duckyclash:challenge_detail', args=[challenge.pk])
        response = self.client.get(detail)
        self.assertContains(response, 'Borrar reto')
        self.assertNotContains(response, 'Editar reto')
        self.assertEqual(self.client.get(
            reverse('duckyclash:challenge_edit', args=[challenge.pk]),
        ).status_code, 404)
        admin_url = reverse('admin:duckyclash_challenge_changelist')
        self.assertContains(self.client.get(admin_url), detail)
        delete_url = reverse('duckyclash:challenge_delete', args=[challenge.pk])
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(staff)
        self.assertEqual(csrf.post(delete_url, {'version': 1, 'revision': 0}).status_code, 403)
        self.assertEqual(self.client.get(delete_url).status_code, 405)
        response = self.client.post(delete_url, {'version': 1, 'revision': 0})
        self.assertRedirects(response, admin_url)
        self.assertFalse(Challenge.objects.filter(pk=challenge.pk).exists())
        log = LogEntry.objects.get(user=staff, action_flag=DELETION, object_id=str(challenge.pk))
        self.assertIn(f'creador={self.creator.pk}', log.change_message)
        for recipient in (self.creator, self.opponent):
            self.assertTrue(Notificacion.objects.filter(usuario=recipient, titulo='Reto eliminado').exists())
        self.assertFalse(EconomicOperation.objects.exists())

    def test_staff_can_delete_public_pending_challenge(self):
        challenge = create_challenge(
            actor=self.creator, opponent=None, is_public=True, category=self.category,
            subcategory=self.subcategory, landscape=self.landscape, scheduled_at=self.schedule,
            expires_at=self.expiry, time_zone='Europe/Madrid', stake=20,
        )
        self.make_staff_without_player_profile()
        self.assertContains(self.client.get(
            reverse('duckyclash:challenge_detail', args=[challenge.pk]),
        ), 'Borrar reto')
        delete_challenge(actor=self.outsider, challenge_id=challenge.pk, version=1, revision=0)
        self.assertFalse(Challenge.objects.filter(pk=challenge.pk).exists())
        self.assertTrue(Notificacion.objects.filter(usuario=self.creator, titulo='Reto eliminado').exists())

    def test_staff_cannot_delete_accepted_or_expired_challenges_or_stale_revision(self):
        accepted = self.create()
        accept_offer(actor=self.opponent, challenge_id=accepted.pk, version=1, revision=0)
        expired = self.create()
        pending = self.create()
        Challenge.objects.filter(pk=expired.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.make_staff_without_player_profile()
        for challenge, revision in ((accepted, 0), (expired, 0), (pending, 99)):
            response = self.client.post(
                reverse('duckyclash:challenge_delete', args=[challenge.pk]),
                {'version': 1, 'revision': revision},
            )
            self.assertEqual(response.status_code, 409)
            self.assertTrue(Challenge.objects.filter(pk=challenge.pk).exists())
        self.assertEqual(Duel.objects.count(), 1)

    def test_staff_service_permission_requires_active_staff_not_only_staff_flag(self):
        challenge = self.create()
        self.outsider.is_staff = True
        self.outsider.is_active = False
        self.outsider.save(update_fields=['is_staff', 'is_active'])
        with self.assertRaises(PermissionDenied):
            delete_challenge(actor=self.outsider, challenge_id=challenge.pk, version=1, revision=0)
        self.assertTrue(Challenge.objects.filter(pk=challenge.pk).exists())
