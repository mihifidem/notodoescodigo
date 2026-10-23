import importlib
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.courses.models import Curso
from apps.lms.models import CourseMembership

from .access import can, effective_plan, has_access_role
from .forms import PerfilForm, RegistroForm
from .models import AccessMembership, AccessRoleAssignment, Organization, OrganizationMembership
from .serializers import UserSerializer


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class UserRoleTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user('role-user')

    def test_choices_defaults_and_validation(self):
        self.assertEqual(
            [value for value, label in self.User.ROLE_CHOICES],
            ['usuario', 'profesor', 'alumno', 'empresa', 'academia', 'headhunter'],
        )
        self.assertEqual(self.user.role, 'usuario')
        for role in ('basic', 'premium', 'admin', 'unknown'):
            self.user.role = role
            with self.assertRaises(ValidationError):
                self.user.save()
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, 'usuario')

    def test_changes_sync_roles_preserve_admin_course_and_plan(self):
        AccessRoleAssignment.objects.create(user=self.user, role='admin')
        AccessMembership.objects.filter(user=self.user).update(plan='pro', billing_period='monthly')
        course = Curso.objects.create(
            nombre='Owned role course', descripcion='Course', fecha_inicio=date(2026, 10, 22),
        )
        membership = CourseMembership.objects.create(course=course, user=self.user, role='teacher')
        for role in ('profesor', 'alumno', 'usuario'):
            previous = self.user.role
            self.user.role = role
            self.user.save(update_fields=['role'])
            self.assertTrue(has_access_role(self.user, role))
            if previous != 'usuario':
                self.assertFalse(has_access_role(self.user, previous))
            self.assertTrue(has_access_role(self.user, 'admin'))
            self.assertTrue(has_access_role(self.user, 'usuario'))
            self.assertEqual(effective_plan(self.user), 'pro')
            membership.refresh_from_db()
            self.assertEqual(membership.role, 'teacher')

    def test_unrelated_save_does_not_sync_unsaved_role(self):
        self.user.role = 'profesor'
        self.user.first_name = 'Name'
        self.user.save(update_fields=['first_name'])
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, 'usuario')
        self.assertFalse(has_access_role(self.user, 'profesor'))

    def test_role_and_user_save_are_atomic(self):
        self.user.role = 'profesor'
        with patch('apps.users.role_services.sync_user_role', side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                self.user.save()
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, 'usuario')

    def test_organization_roles_are_scoped_and_follow_membership(self):
        company = Organization.objects.create(name='Role company', kind='company')
        other = Organization.objects.create(name='Other company', kind='company')
        academy = Organization.objects.create(name='Role academy', kind='academy')
        for role, organization, wrong in (
            ('empresa', company, academy),
            ('headhunter', company, academy),
            ('academia', academy, company),
        ):
            self.user.role = role
            self.user.save(update_fields=['role'])
            member, _ = OrganizationMembership.objects.update_or_create(
                user=self.user, organization=organization, defaults={'status': 'active'},
            )
            assignment = AccessRoleAssignment.objects.get(
                user=self.user, role=role, organization=organization,
            )
            assignment.full_clean()
            self.assertTrue(has_access_role(self.user, role, organization=organization))
            self.assertFalse(has_access_role(self.user, role))
            self.assertFalse(has_access_role(self.user, role, organization=other))
            self.assertFalse(has_access_role(self.user, role, organization=wrong))
            member.status = 'suspended'
            member.save()
            self.assertFalse(has_access_role(self.user, role, organization=organization))
            assignment.refresh_from_db()
            self.assertFalse(assignment.is_active)
            member.status = 'active'
            member.save()
            self.assertTrue(has_access_role(self.user, role, organization=organization))
            member.delete()
            self.assertFalse(has_access_role(self.user, role, organization=organization))

    def test_organization_changes_recheck_role_compatibility(self):
        self.user.role = 'empresa'
        self.user.save()
        organization = Organization.objects.create(name='Changing company', kind='company')
        OrganizationMembership.objects.create(user=self.user, organization=organization, status='active')
        self.assertTrue(has_access_role(self.user, 'empresa', organization=organization))
        organization.kind = 'academy'
        organization.save()
        self.assertFalse(has_access_role(self.user, 'empresa', organization=organization))
        organization.kind = 'company'
        organization.save()
        self.assertTrue(has_access_role(self.user, 'empresa', organization=organization))
        organization.is_active = False
        organization.save()
        self.assertFalse(has_access_role(self.user, 'empresa', organization=organization))

    def test_public_forms_and_api_cannot_self_assign(self):
        form = RegistroForm(data={
            'username': 'self-assignment', 'first_name': 'Test', 'last_name': 'User',
            'email': 'roles@example.test', 'password1': 'Secure-test-pass-123',
            'password2': 'Secure-test-pass-123', 'role': 'profesor',
        })
        self.assertTrue(form.is_valid(), form.errors)
        registered = form.save()
        self.assertEqual(registered.role, 'usuario')
        self.assertNotIn('role', PerfilForm(instance=registered).fields)
        serializer = UserSerializer(instance=registered, data={'role': 'profesor'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        registered.refresh_from_db()
        self.assertEqual(registered.role, 'usuario')
        self.assertFalse(has_access_role(registered, 'profesor'))

    def test_authorized_admin_role_change_and_audit(self):
        actor = self.User.objects.create_user('role-manager', is_staff=True)
        actor.user_permissions.add(Permission.objects.get(codename='assign_access_roles'))
        request = RequestFactory().post('/admin/')
        request.user = actor
        model_admin = admin.site._registry[self.User]
        self.assertNotIn('role', model_admin.get_readonly_fields(request, self.user))
        self.user.role = 'profesor'
        model_admin.save_model(request, self.user, SimpleNamespace(), True)
        self.assertTrue(has_access_role(self.user, 'profesor'))
        self.assertTrue(actor.access_audit_events.filter(action='user_role_changed').exists())

    def test_staff_without_assignment_permission_cannot_change_role(self):
        actor = self.User.objects.create_user('unprivileged-staff', is_staff=True)
        request = RequestFactory().post('/admin/')
        request.user = actor
        model_admin = admin.site._registry[self.User]
        self.assertIn('role', model_admin.get_readonly_fields(request, self.user))
        self.user.role = 'profesor'
        with self.assertRaises(PermissionDenied):
            model_admin.save_model(request, self.user, SimpleNamespace(), True)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, 'usuario')

    def test_teacher_needs_creation_permission_but_not_staff(self):
        self.user.role = 'profesor'
        self.user.save()
        self.client.force_login(self.user)
        url = reverse('lms:course_create')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.user.user_permissions.add(Permission.objects.get(
            content_type__app_label='courses', codename='add_curso',
        ))
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(self.user.is_staff)
        response = self.client.post(url, {
            'nombre': 'Created by teacher', 'descripcion': 'Learning activities',
            'fecha_inicio': '2026-10-22',
        })
        self.assertEqual(response.status_code, 302)
        own_course = Curso.objects.get(nombre='Created by teacher')
        self.assertTrue(can(self.user, 'edit_course', own_course).allowed)
        other = Curso.objects.create(
            nombre='Other owner course', descripcion='Private', fecha_inicio=date(2026, 10, 22),
        )
        self.assertFalse(can(self.user, 'edit_course', other).allowed)

    def test_data_migration_preserves_plans_and_admin_is_idempotent(self):
        basic = self.User.objects.create_user('legacy-basic')
        premium = self.User.objects.create_user('legacy-premium')
        student = self.User.objects.create_user('legacy-student')
        self.User.objects.filter(pk=basic.pk).update(role='basic')
        self.User.objects.filter(pk=premium.pk).update(role='premium')
        self.User.objects.filter(pk=student.pk).update(role='alumno')
        AccessMembership.objects.filter(user=premium).update(plan='pro', billing_period='monthly')
        AccessRoleAssignment.objects.create(user=premium, role='admin')
        migration = importlib.import_module('apps.users.migrations.0019_migrate_user_roles')
        for _ in range(2):
            migration.migrate_user_roles(apps, SimpleNamespace(connection=connection))
        basic.refresh_from_db()
        premium.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(basic.role, 'usuario')
        self.assertEqual(premium.role, 'usuario')
        self.assertEqual(student.role, 'alumno')
        self.assertEqual(effective_plan(premium), 'pro')
        self.assertTrue(has_access_role(premium, 'admin'))
        self.assertTrue(has_access_role(student, 'alumno'))
