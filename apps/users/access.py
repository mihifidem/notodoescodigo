from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone

from .models import (
    AccessMembership,
    AccessPolicyConfiguration,
    AccessRoleAssignment,
    Organization,
    OrganizationMembership,
    PurchasedEntitlement,
)
from .billing_models import AdministrativeAccessGrant, MembershipContract


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str


class AccessDenied(PermissionDenied):
    def __init__(self, decision):
        self.decision = decision
        super().__init__(decision.reason)


def _deny(reason):
    return AccessDecision(False, reason)


def effective_plan(actor):
    if not getattr(actor, 'is_authenticated', False):
        return 'visitor'
    now = timezone.now()
    plans = [AccessMembership.Plan.FREE]
    membership = AccessMembership.objects.filter(user=actor).first()
    if membership:
        plans.append(membership.effective_plan)
    plans.extend(
        AdministrativeAccessGrant.objects.filter(
            user=actor,
            status=AdministrativeAccessGrant.Status.ACTIVE,
            starts_at__lte=now,
        ).filter(
            Q(ends_at__isnull=True) | Q(ends_at__gt=now),
        ).values_list('plan', flat=True)
    )
    plans.extend(
        MembershipContract.objects.filter(
            user=actor,
            status__in=[
                MembershipContract.Status.ACTIVE,
                MembershipContract.Status.RENEWAL_PENDING,
                MembershipContract.Status.RENEWAL_FAILED,
                MembershipContract.Status.CANCEL_AT_PERIOD_END,
            ],
            starts_at__lte=now,
        ).filter(
            Q(period_ends_at__isnull=True) | Q(period_ends_at__gt=now),
        ).values_list('plan', flat=True)
    )
    plan_order = {
        AccessMembership.Plan.FREE: 0,
        AccessMembership.Plan.PLUS: 1,
        AccessMembership.Plan.PRO: 2,
    }
    return max(plans, key=lambda plan: plan_order.get(plan, -1))


def has_access_role(actor, role, *, organization=None):
    if not getattr(actor, 'is_authenticated', False) or not actor.is_active:
        return False
    assignments = AccessRoleAssignment.objects.filter(
        user=actor, role=role, is_active=True,
    )
    if organization is None:
        return assignments.filter(organization__isnull=True).exists()
    return (
        assignments.filter(organization=organization).exists()
        and _active_organization_member(actor, organization)
    )


def is_platform_admin(actor):
    if not getattr(actor, 'is_authenticated', False) or not actor.is_active:
        return False
    return (
        actor.is_superuser
        or has_access_role(actor, AccessRoleAssignment.Role.ADMIN)
        or actor.has_perm('lms.manage_platform')
    )


def _active_organization_member(actor, organization):
    return (
        isinstance(organization, Organization)
        and organization.is_active
        and OrganizationMembership.objects.filter(
            user=actor,
            organization=organization,
            status=OrganizationMembership.Status.ACTIVE,
        ).exists()
    )


def can(actor, action, resource=None, *, required_plan=None, entitlement=None, organization=None):
    """Resolve access at request time; callers must pass the concrete resource."""
    if not getattr(actor, 'is_authenticated', False):
        return _deny('registration_required')
    if not actor.is_active:
        return _deny('account_inactive')

    if action == 'manage_access':
        return AccessDecision(is_platform_admin(actor), 'allowed' if is_platform_admin(actor) else 'role_required')

    if action in {'access_plan', 'view_plan_content'}:
        has_specific_plan = required_plan is not None
        required_plan = required_plan or AccessMembership.Plan.FREE
        configured_rule = AccessPolicyConfiguration.objects.filter(
            key='access', status=AccessPolicyConfiguration.Status.ACTIVE,
        ).values_list('configuration', flat=True).first()
        if configured_rule is not None:
            rule = configured_rule.get('rules', {}).get(action, {})
            if isinstance(rule, dict):
                if rule.get('enabled') is False:
                    return _deny('not_available')
                configured_plan = rule.get('minimum_plan')
                if not has_specific_plan and configured_plan in AccessMembership.Plan.values:
                    required_plan = configured_plan
        plan_order = {
            AccessMembership.Plan.FREE: 0,
            AccessMembership.Plan.PLUS: 1,
            AccessMembership.Plan.PRO: 2,
        }
        current_plan = effective_plan(actor)
        allowed = plan_order.get(current_plan, -1) >= plan_order.get(required_plan, 99)
        if not allowed and current_plan == AccessMembership.Plan.FREE:
            membership = AccessMembership.objects.filter(user=actor).first()
            if membership and membership.plan != AccessMembership.Plan.FREE and (
                membership.status != AccessMembership.Status.ACTIVE
                or membership.starts_at > timezone.now()
                or (membership.ends_at and membership.ends_at <= timezone.now())
            ):
                return _deny('subscription_inactive')
        return AccessDecision(allowed, 'allowed' if allowed else 'plan_required')

    if action == 'view_registered_content':
        return AccessDecision(True, 'allowed')

    if action == 'view_owned_resource':
        owner_id = getattr(resource, 'owner_id', None) or getattr(resource, 'user_id', None)
        allowed = owner_id == actor.pk
        return AccessDecision(allowed, 'allowed' if allowed else 'ownership_required')

    if action == 'view_organization_resource':
        organization = organization or getattr(resource, 'organization', None)
        if organization is None and isinstance(resource, Organization):
            organization = resource
        if organization is None:
            organization_id = getattr(resource, 'organization_id', None)
            organization = Organization.objects.filter(pk=organization_id).first()
        allowed = _active_organization_member(actor, organization)
        return AccessDecision(allowed, 'allowed' if allowed else 'organization_membership_required')

    if action == 'edit_course':
        if resource is None:
            return _deny('resource_required')
        if (
            is_platform_admin(actor)
            or actor.has_perm('lms.manage_platform')
            or actor.has_perm('courses.change_curso')
        ):
            return AccessDecision(True, 'allowed')
        from apps.lms.models import CourseMembership

        allowed = CourseMembership.objects.filter(
            user=actor,
            course=resource,
            role__in=(
                CourseMembership.Role.OWNER,
                CourseMembership.Role.TEACHER,
                CourseMembership.Role.ASSISTANT,
            ),
            status=CourseMembership.Status.ACTIVE,
        ).exists()
        return AccessDecision(allowed, 'allowed' if allowed else 'role_required')

    if action == 'publish_course':
        if resource is None:
            return _deny('resource_required')
        if is_platform_admin(actor) or actor.has_perm('courses.change_curso'):
            return AccessDecision(True, 'allowed')
        from apps.lms.models import CourseMembership

        if CourseMembership.objects.filter(
            user=actor,
            course=resource,
            role=CourseMembership.Role.OWNER,
            status=CourseMembership.Status.ACTIVE,
        ).exists():
            return AccessDecision(True, 'allowed')
        allowed = (
            actor.has_perm('lms.publish_course')
            and CourseMembership.objects.filter(
                user=actor,
                course=resource,
                role__in=(
                    CourseMembership.Role.TEACHER,
                    CourseMembership.Role.ASSISTANT,
                ),
                status=CourseMembership.Status.ACTIVE,
            ).exists()
        )
        return AccessDecision(allowed, 'allowed' if allowed else 'role_required')

    if action == 'delete_course':
        allowed = (
            is_platform_admin(actor)
            or actor.is_staff
        )
        return AccessDecision(allowed, 'allowed' if allowed else 'role_required')

    if action == 'view_candidates':
        if resource is None:
            return _deny('resource_required')
        recruiter_id = getattr(resource, 'recruiter_id', None)
        if recruiter_id != actor.pk:
            return _deny('ownership_required')
        organization = organization or getattr(resource, 'organization', None)
        if organization is not None:
            if not has_access_role(actor, AccessRoleAssignment.Role.HEADHUNTER, organization=organization):
                return _deny('role_required')
            if not _active_organization_member(actor, organization):
                return _deny('organization_membership_required')
        elif not has_access_role(actor, AccessRoleAssignment.Role.HEADHUNTER):
            return _deny('role_required')
        if getattr(resource, 'is_active', True) is False:
            return _deny('resource_unavailable')
        return AccessDecision(True, 'allowed')

    if action == 'access_entitlement':
        if not entitlement:
            return _deny('purchase_required')
        allowed = PurchasedEntitlement.objects.filter(
            user=actor,
            code=entitlement,
            revoked_at__isnull=True,
            starts_at__lte=timezone.now(),
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        ).exists()
        return AccessDecision(allowed, 'allowed' if allowed else 'purchase_required')

    if action == 'view_course':
        if resource is None:
            return _deny('resource_required')
        from apps.lms.campus import campus_settings, material_access
        if campus_settings(resource):
            return material_access(actor, resource)
        if (
            is_platform_admin(actor)
            or actor.has_perm('lms.manage_platform')
            or actor.has_perm('courses.view_curso')
        ):
            return AccessDecision(True, 'allowed')
        if getattr(resource, 'activo', False):
            return AccessDecision(True, 'allowed')
        from apps.lms.models import CourseMembership
        from apps.courses.models import InscripcionCurso

        membership_status = CourseMembership.objects.filter(
            user=actor,
            course=resource,
        ).values_list('status', flat=True).first()
        if membership_status is not None:
            is_member = membership_status in (
                CourseMembership.Status.ACTIVE,
                CourseMembership.Status.SUSPENDED,
                CourseMembership.Status.COMPLETED,
            )
        else:
            is_member = InscripcionCurso.objects.filter(usuario=actor, curso=resource).exists()
        is_member = is_member or can(actor, 'edit_course', resource).allowed
        return AccessDecision(is_member, 'allowed' if is_member else 'resource_unavailable')

    if action == 'access_role':
        role = resource
        if role == AccessRoleAssignment.Role.ALUMNO:
            from apps.lms.models import CourseMembership
            from apps.courses.models import InscripcionCurso

            has_active_student_membership = CourseMembership.objects.filter(
                user=actor,
                role=CourseMembership.Role.STUDENT,
                status=CourseMembership.Status.ACTIVE,
            ).exists()
            has_legacy_enrollment = InscripcionCurso.objects.filter(
                usuario=actor,
            ).exclude(
                curso_id__in=CourseMembership.objects.filter(user=actor).values('course_id'),
            ).exists()
            if has_active_student_membership or has_legacy_enrollment:
                return AccessDecision(True, 'allowed')
        allowed = has_access_role(actor, role, organization=organization)
        return AccessDecision(allowed, 'allowed' if allowed else 'role_required')

    return _deny('not_available')


def require_access(actor, action, resource=None, **kwargs):
    decision = can(actor, action, resource, **kwargs)
    if not decision.allowed:
        raise AccessDenied(decision)
    return decision


def access_snapshot(actor):
    if not getattr(actor, 'is_authenticated', False):
        return {
            'identity': 'visitor',
            'plan': 'visitor',
            'roles': [],
            'organizations': [],
            'entitlements': [],
            'capabilities': _capability_snapshot(actor),
        }
    if not actor.is_active:
        return {
            'identity': 'inactive',
            'plan': 'free',
            'membership': {'status': 'inactive', 'billing_period': '', 'starts_at': None, 'ends_at': None},
            'roles': [],
            'organizations': [],
            'entitlements': [],
            'capabilities': _capability_snapshot(actor),
        }

    membership = AccessMembership.objects.filter(user=actor).first()
    role_assignments = AccessRoleAssignment.objects.filter(
        user=actor, is_active=True,
    ).filter(
        Q(organization__isnull=True)
        | Q(
            organization__is_active=True,
            organization__memberships__user=actor,
            organization__memberships__status=OrganizationMembership.Status.ACTIVE,
        )
    ).select_related('organization').distinct()
    memberships = OrganizationMembership.objects.filter(
        user=actor, status=OrganizationMembership.Status.ACTIVE, organization__is_active=True,
    ).select_related('organization')
    entitlements = PurchasedEntitlement.objects.filter(
        user=actor, revoked_at__isnull=True, starts_at__lte=timezone.now(),
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
    ).values('code', 'starts_at', 'expires_at')

    now = timezone.now()
    current_contract = MembershipContract.objects.filter(
        user=actor,
        status__in=[
            MembershipContract.Status.ACTIVE,
            MembershipContract.Status.RENEWAL_PENDING,
            MembershipContract.Status.RENEWAL_FAILED,
            MembershipContract.Status.CANCEL_AT_PERIOD_END,
        ],
        starts_at__lte=now,
    ).filter(
        Q(period_ends_at__isnull=True) | Q(period_ends_at__gt=now),
    ).order_by('-period_ends_at', '-created_at').first()
    current_grant = AdministrativeAccessGrant.objects.filter(
        user=actor,
        status=AdministrativeAccessGrant.Status.ACTIVE,
        starts_at__lte=now,
    ).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gt=now),
    ).order_by('-ends_at', '-created_at').first()
    membership_snapshot = {
        'status': membership.status if membership else AccessMembership.Status.ACTIVE,
        'billing_period': membership.billing_period if membership else '',
        'starts_at': membership.starts_at if membership else None,
        'ends_at': membership.ends_at if membership else None,
    }
    if current_contract and current_contract.plan == effective_plan(actor):
        membership_snapshot = {
            'status': current_contract.status,
            'billing_period': current_contract.billing_period,
            'starts_at': current_contract.starts_at,
            'ends_at': current_contract.period_ends_at,
        }
    elif current_grant and current_grant.plan == effective_plan(actor):
        membership_snapshot = {
            'status': current_grant.status,
            'billing_period': '',
            'starts_at': current_grant.starts_at,
            'ends_at': current_grant.ends_at,
        }

    return {
        'identity': 'registered',
        'plan': effective_plan(actor),
        'membership': membership_snapshot,
        'roles': [
            {'role': assignment.role, 'organization': assignment.organization.name if assignment.organization_id else None}
            for assignment in role_assignments
        ],
        'organizations': [membership.organization.name for membership in memberships],
        'entitlements': list(entitlements),
        'capabilities': _capability_snapshot(actor),
    }


def _capability_snapshot(actor):
    return {
        'registered_content': {
            'allowed': can(actor, 'view_registered_content').allowed,
            'reason': can(actor, 'view_registered_content').reason,
        },
        'plus_content': {
            'allowed': can(actor, 'view_plan_content', required_plan=AccessMembership.Plan.PLUS).allowed,
            'reason': can(actor, 'view_plan_content', required_plan=AccessMembership.Plan.PLUS).reason,
        },
        'pro_content': {
            'allowed': can(actor, 'view_plan_content', required_plan=AccessMembership.Plan.PRO).allowed,
            'reason': can(actor, 'view_plan_content', required_plan=AccessMembership.Plan.PRO).reason,
        },
    }
