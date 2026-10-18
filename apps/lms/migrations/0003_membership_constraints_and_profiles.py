from itertools import islice

from django.conf import settings
from django.db import migrations, models


def backfill_profiles(apps, schema_editor):
    """Bounded historical backfill; reruns preserve all existing preferences."""
    database = schema_editor.connection.alias
    app_label, model_name = settings.AUTH_USER_MODEL.split('.')
    User = apps.get_model(app_label, model_name)
    Profile = apps.get_model('lms', 'UserProfileLMS')
    missing_ids = User.objects.using(database).exclude(
        pk__in=Profile.objects.using(database).values('user_id'),
    ).values_list('pk', flat=True).iterator(chunk_size=500)
    while True:
        batch = list(islice(missing_ids, 500))
        if not batch:
            break
        # get_or_create handles only uniqueness races, not unrelated DB errors.
        for user_id in batch:
            Profile.objects.using(database).get_or_create(user_id=user_id)


class Migration(migrations.Migration):
    dependencies = [
        ('lms', '0002_integrate_courses_app'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='coursemembership',
            constraint=models.CheckConstraint(
                check=models.Q(role__in=['owner', 'teacher', 'assistant', 'student', 'observer']),
                name='lms_member_valid_role',
            ),
        ),
        migrations.AddConstraint(
            model_name='coursemembership',
            constraint=models.CheckConstraint(
                check=models.Q(status__in=['invited', 'active', 'suspended', 'completed', 'dropped']),
                name='lms_member_valid_status',
            ),
        ),
        migrations.RunPython(backfill_profiles, migrations.RunPython.noop),
    ]
