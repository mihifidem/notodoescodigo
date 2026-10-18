from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('lms', '0025_announcement_requires_acknowledgement_and_more'),
        ('lms', '0003_membership_constraints_and_profiles'),
    ]

    operations = [
        migrations.AddField(
            model_name='discussionpost', name='title',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name='título de la consulta'),
        ),
        migrations.AddField(
            model_name='discussionpost', name='accepted_solution',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='solves_queries', to='lms.discussionpost', verbose_name='solución aceptada',
            ),
        ),
    ]
