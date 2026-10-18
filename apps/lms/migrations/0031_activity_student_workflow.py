from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('lms', '0030_learningactivity_course_modules')]

    operations = [
        migrations.AddField(
            model_name='learningactivity', name='solution',
            field=models.TextField(blank=True, default='', verbose_name='solución'),
        ),
        migrations.AddField(
            model_name='learningactivity', name='lifecycle_state',
            field=models.CharField(blank=True, default='', editable=False, max_length=12),
        ),
        migrations.AddField(
            model_name='assignment', name='learning_activity',
            field=models.OneToOneField(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='assignment', to='lms.learningactivity', verbose_name='actividad de aprendizaje',
            ),
        ),
    ]
