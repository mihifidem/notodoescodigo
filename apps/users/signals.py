"""Signal handlers for the users app."""
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import AccessMembership, AccessRoleAssignment, Organization, OrganizationMembership, User


@receiver(post_save, sender=User)
def create_default_access_records(sender, instance, created, **kwargs):
    if not created or kwargs.get('raw'):
        return
    using = kwargs.get('using', 'default')
    AccessMembership.objects.using(using).get_or_create(
        user=instance,
        defaults={'plan': AccessMembership.Plan.FREE},
    )
    AccessRoleAssignment.objects.using(using).get_or_create(
        user=instance,
        role=AccessRoleAssignment.Role.USUARIO,
        organization=None,
        defaults={'assigned_by': None},
    )


@receiver(post_save, sender=OrganizationMembership)
@receiver(post_delete, sender=OrganizationMembership)
def sync_organization_member_role(sender, instance, using, **kwargs):
    if kwargs.get('raw'):
        return
    from .role_services import sync_user_role

    with transaction.atomic(using=using):
        user = User.objects.using(using).select_for_update().filter(pk=instance.user_id).first()
        if user and user.role in {'empresa', 'academia', 'headhunter'}:
            sync_user_role(user, using=using)


@receiver(post_save, sender=Organization)
def sync_organization_roles(sender, instance, using, **kwargs):
    if kwargs.get('raw') or kwargs.get('created'):
        return
    from .role_services import sync_user_role

    with transaction.atomic(using=using):
        users = User.objects.using(using).select_for_update().filter(
            organization_memberships__organization_id=instance.pk,
            role__in=['empresa', 'academia', 'headhunter'],
        ).order_by('pk')
        for user in users:
            sync_user_role(user, using=using)
