from .models import AccessRoleAssignment, Organization, OrganizationMembership, User


def sync_user_role(user, *, previous_role=None, using='default'):
    assignments = AccessRoleAssignment.objects.using(using).filter(user_id=user.pk)
    if previous_role in dict(User.ROLE_CHOICES) and previous_role != user.role and previous_role != 'usuario':
        assignments.filter(role=previous_role).update(is_active=False)

    AccessRoleAssignment.objects.using(using).update_or_create(
        user_id=user.pk, role=AccessRoleAssignment.Role.USUARIO, organization=None,
        defaults={'is_active': True},
    )
    if user.role == 'usuario':
        return

    if user.role in {'alumno', 'profesor'}:
        AccessRoleAssignment.objects.using(using).update_or_create(
            user_id=user.pk, role=user.role, organization=None,
            defaults={'is_active': True},
        )
        return

    kind = Organization.Kind.ACADEMY if user.role == 'academia' else Organization.Kind.COMPANY
    organization_ids = list(OrganizationMembership.objects.using(using).filter(
        user_id=user.pk, status=OrganizationMembership.Status.ACTIVE,
        organization__kind=kind, organization__is_active=True,
    ).values_list('organization_id', flat=True))
    assignments.filter(role=user.role).exclude(
        organization_id__in=organization_ids,
    ).update(is_active=False)
    for organization_id in organization_ids:
        AccessRoleAssignment.objects.using(using).update_or_create(
            user_id=user.pk, role=user.role, organization_id=organization_id,
            defaults={'is_active': True},
        )
