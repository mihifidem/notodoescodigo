from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.users.access import effective_plan
from apps.users.models import AdministrativeAccessGrant, BillingEvent, User


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class AdministrativeAccessGrantAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username='grant-superuser', email='grant@example.test', password='test-only-password',
        )
        cls.member = User.objects.create_user(username='grant-member')
        cls.staff = User.objects.create_user(username='grant-staff', is_staff=True)
        cls.manager = User.objects.create_user(username='grant-manager', is_staff=True)
        cls.manager.user_permissions.add(Permission.objects.get(codename='manage_access_grants'))

    def test_superuser_and_grant_manager_can_open_add_and_change(self):
        grant = AdministrativeAccessGrant.objects.create(
            user=self.member, plan='pro', reason='Test', granted_by=self.admin,
        )
        for actor in (self.admin, self.manager):
            self.client.force_login(actor)
            for url in (
                reverse('admin:users_administrativeaccessgrant_add'),
                reverse('admin:users_administrativeaccessgrant_change', args=[grant.pk]),
            ):
                with self.subTest(actor=actor.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_staff_without_grant_permission_cannot_open_form(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('admin:users_administrativeaccessgrant_add'))
        self.assertEqual(response.status_code, 403)

    def test_superuser_can_save_grant_and_activate_effective_plan(self):
        self.client.force_login(self.admin)
        now = timezone.localtime()
        response = self.client.post(reverse('admin:users_administrativeaccessgrant_add'), {
            'user': self.member.pk, 'plan': 'pro', 'status': 'active',
            'reason': 'Administrative test access',
            'starts_at_0': now.strftime('%Y-%m-%d'),
            'starts_at_1': now.strftime('%H:%M:%S'),
            'ends_at_0': '', 'ends_at_1': '',
            'revoked_at_0': '', 'revoked_at_1': '', 'revoked_by': '',
            '_save': 'Save',
        })
        self.assertEqual(response.status_code, 302)
        grant = AdministrativeAccessGrant.objects.get(user=self.member)
        self.assertEqual(grant.granted_by_id, self.admin.pk)
        self.assertEqual(effective_plan(self.member), 'pro')
        self.assertTrue(BillingEvent.objects.filter(
            actor=self.admin, user=self.member, action='admin_created',
            object_type='users.administrativeaccessgrant', object_id=str(grant.pk),
        ).exists())
