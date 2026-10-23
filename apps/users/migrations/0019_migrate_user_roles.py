from django.db import migrations


def migrate_user_roles(apps, schema_editor):
    User = apps.get_model('users', 'User')
    Assignment = apps.get_model('users', 'AccessRoleAssignment')
    using = schema_editor.connection.alias
    User.objects.using(using).filter(role__in=['basic', 'premium']).update(role='usuario')
    for user in User.objects.using(using).all().iterator():
        Assignment.objects.using(using).get_or_create(
            user_id=user.pk, role='usuario', organization_id=None,
        )
        if user.role in {'alumno', 'profesor'}:
            Assignment.objects.using(using).update_or_create(
                user_id=user.pk, role=user.role, organization_id=None,
                defaults={'is_active': True},
            )


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0018_alter_accessroleassignment_role_alter_user_role'),
    ]

    operations = [
        migrations.RunPython(migrate_user_roles, migrations.RunPython.noop),
    ]
