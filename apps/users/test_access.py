from datetime import date, timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import Curso, InscripcionCurso
from apps.lms.models import CourseMembership

from .access import access_snapshot, can
from .models import (
    AccessMembership,
    AccessPolicyConfiguration,
    AccessRoleAssignment,
    Organization,
    OrganizationMembership,
    PurchasedEntitlement,
    MembershipContract,
)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class CommonAccessPolicyTests(TestCase):
    def test_sidebar_shows_effective_plan_next_to_experience_level(self):
        user = self.create_user('sidebar-plan')
        self.client.force_login(user)
        url = reverse('users:access_summary')
        response = self.client.get(url)
        self.assertContains(response, 'aria-label="Plan FREE"')
        self.assertContains(response, f'>{user.nivel}</span>')
        contract = MembershipContract.objects.create(
            user=user, plan='plus', status='active', billing_period='monthly',
            contracted_price='6.90', starts_at=timezone.now() - timedelta(minutes=1),
            period_ends_at=timezone.now() + timedelta(days=30),
            idempotency_key='sidebar-plan-test',
        )
        response = self.client.get(url)
        self.assertContains(response, 'aria-label="Plan PLUS"')
        self.assertNotContains(response, 'aria-label="Plan FREE"')
        contract.plan = 'pro'
        contract.save()
        self.assertContains(self.client.get(url), 'aria-label="Plan PRO"')
        contract.period_ends_at = timezone.now() - timedelta(seconds=1)
        contract.save()
        self.assertContains(self.client.get(url), 'aria-label="Plan FREE"')

    def test_access_page_has_tech_layout_and_plan_management_link(self):
        user = self.create_user('access-design')
        self.client.force_login(user)
        response = self.client.get(reverse('users:access_summary'))
        self.assertContains(response, 'css/access-summary.css')
        self.assertContains(response, 'css/billing.css')
        self.assertContains(response, 'billing-hero')
        self.assertContains(response, reverse('users:billing') + '#mejorar-plan')
        for target in ('access-level', 'access-roles', 'access-capabilities', 'access-entitlements'):
            self.assertContains(response, f'id="{target}"')
        self.assertContains(response, 'Requiere otro nivel')

    def create_user(self, username):
        return get_user_model().objects.create_user(username=username, password='Strong-pass-123')

    def create_course(self, name):
        return Curso.objects.create(
            nombre=name,
            descripcion='Curso de prueba',
            fecha_inicio=date.today(),
        )

    def test_anonymous_visitor_has_no_persistent_identity(self):
        response = self.client.get(reverse('access-snapshot'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['identity'], 'visitor')
        self.assertEqual(response.json()['plan'], 'visitor')
        self.assertFalse(response.json()['capabilities']['plus_content']['allowed'])

    def test_registration_creates_free_without_professional_roles(self):
        user = self.create_user('free_access')
        snapshot = access_snapshot(user)

        self.assertEqual(snapshot['plan'], AccessMembership.Plan.FREE)
        self.assertEqual([role['role'] for role in snapshot['roles']], ['usuario'])

    def test_pro_without_professional_role_cannot_edit_course(self):
        user = self.create_user('pro_no_teacher')
        user.access_membership.plan = AccessMembership.Plan.PRO
        user.access_membership.billing_period = AccessMembership.BillingPeriod.MONTHLY
        user.access_membership.save()
        course = self.create_course('Curso no autorizado')

        self.assertFalse(can(user, 'edit_course', course).allowed)
        self.assertEqual(can(user, 'edit_course', course).reason, 'role_required')
        self.assertTrue(can(user, 'view_plan_content', required_plan='plus').allowed)
        self.assertTrue(can(user, 'view_plan_content', required_plan='free').allowed)

    def test_course_delete_requires_platform_staff_not_course_permission(self):
        user = self.create_user('course_delete_permission')
        user.user_permissions.add(Permission.objects.get(codename='delete_curso'))

        self.assertFalse(can(user, 'delete_course').allowed)
        user.is_staff = True
        user.save(update_fields=['is_staff'])
        self.assertTrue(can(user, 'delete_course').allowed)

    def test_specific_content_plan_is_not_weakened_by_general_policy(self):
        user = self.create_user('free_private_content')
        administrator = self.create_user('policy_administrator')
        AccessPolicyConfiguration.objects.create(
            key='access',
            version=1,
            status=AccessPolicyConfiguration.Status.ACTIVE,
            approved_by=administrator,
            configuration={
                'rules': {
                    'view_plan_content': {
                        'minimum_plan': AccessMembership.Plan.FREE,
                    },
                },
            },
        )

        self.assertFalse(
            can(
                user,
                'view_plan_content',
                required_plan=AccessMembership.Plan.PLUS,
            ).allowed
        )
        self.assertTrue(
            can(
                user,
                'view_plan_content',
                required_plan=AccessMembership.Plan.FREE,
            ).allowed
        )

    def test_roles_are_independent_and_professor_is_scoped_to_course_membership(self):
        user = self.create_user('alumno_profesor')
        course_as_teacher = self.create_course('Curso docente')
        course_as_student = self.create_course('Curso alumno')
        AccessRoleAssignment.objects.create(user=user, role=AccessRoleAssignment.Role.ALUMNO)
        AccessRoleAssignment.objects.create(user=user, role=AccessRoleAssignment.Role.PROFESOR)
        CourseMembership.objects.create(
            user=user,
            course=course_as_teacher,
            role=CourseMembership.Role.TEACHER,
            status=CourseMembership.Status.ACTIVE,
        )
        InscripcionCurso.objects.create(usuario=user, curso=course_as_student)

        self.assertTrue(can(user, 'access_role', AccessRoleAssignment.Role.ALUMNO).allowed)
        self.assertTrue(can(user, 'edit_course', course_as_teacher).allowed)
        self.assertFalse(can(user, 'edit_course', course_as_student).allowed)

    def test_dropped_course_membership_revokes_legacy_enrollment_access(self):
        user = self.create_user('dropped_course_member')
        course = self.create_course('Curso baja')
        course.activo = False
        course.save(update_fields=['activo'])
        InscripcionCurso.objects.create(usuario=user, curso=course)
        CourseMembership.objects.create(
            user=user,
            course=course,
            role=CourseMembership.Role.STUDENT,
            status=CourseMembership.Status.DROPPED,
        )

        self.assertFalse(can(user, 'view_course', course).allowed)
        self.assertFalse(
            can(user, 'access_role', AccessRoleAssignment.Role.ALUMNO).allowed
        )

    def test_organization_roles_and_resources_are_isolated(self):
        user = self.create_user('headhunter_a')
        company_a = Organization.objects.create(name='Empresa A', kind=Organization.Kind.COMPANY)
        company_b = Organization.objects.create(name='Empresa B', kind=Organization.Kind.COMPANY)
        OrganizationMembership.objects.create(
            user=user,
            organization=company_a,
            status=OrganizationMembership.Status.ACTIVE,
        )
        AccessRoleAssignment.objects.create(
            user=user,
            role=AccessRoleAssignment.Role.HEADHUNTER,
            organization=company_a,
        )
        candidate_resource = SimpleNamespace(organization=company_a, recruiter_id=user.pk, is_active=True)
        foreign_resource = SimpleNamespace(organization=company_b, recruiter_id=user.pk, is_active=True)

        self.assertTrue(can(user, 'view_candidates', candidate_resource).allowed)
        self.assertFalse(can(user, 'view_candidates', foreign_resource).allowed)
        self.assertFalse(can(user, 'view_organization_resource', company_b).allowed)

    def test_purchased_right_survives_downgrade_and_expires_independently(self):
        user = self.create_user('paid_right_user')
        entitlement = PurchasedEntitlement.objects.create(
            user=user,
            code='report-42',
            source='verified_payment',
            source_reference='payment-42',
            idempotency_key='grant-report-42',
        )
        user.access_membership.plan = AccessMembership.Plan.PRO
        user.access_membership.billing_period = AccessMembership.BillingPeriod.ANNUAL
        user.access_membership.save()
        user.access_membership.plan = AccessMembership.Plan.FREE
        user.access_membership.billing_period = ''
        user.access_membership.save()

        self.assertTrue(can(user, 'access_entitlement', entitlement='report-42').allowed)
        entitlement.expires_at = timezone.now()
        entitlement.save()
        self.assertFalse(can(user, 'access_entitlement', entitlement='report-42').allowed)

    def test_api_and_profile_edits_cannot_change_permissions(self):
        user = self.create_user('readonly_access')
        self.client.force_login(user)

        api_response = self.client.post(
            reverse('access-snapshot'),
            {'plan': 'pro', 'roles': ['admin']},
            content_type='application/json',
        )
        profile_response = self.client.post(
            reverse('users:editar_perfil'),
            {'role': 'premium', 'codigo_acceso_alumno': '010819'},
        )
        user.refresh_from_db()

        self.assertEqual(api_response.status_code, 405)
        self.assertEqual(profile_response.status_code, 302)
        self.assertEqual(user.access_membership.plan, AccessMembership.Plan.FREE)
        self.assertFalse(user.access_role_assignments.filter(role=AccessRoleAssignment.Role.ADMIN).exists())
        self.assertEqual(self.client.get(reverse('users:access_summary')).status_code, 200)
