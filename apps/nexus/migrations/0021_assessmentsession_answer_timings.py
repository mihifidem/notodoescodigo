from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('nexus', '0020_seed_editorial_catalog')]
    operations = [
        migrations.AddField(
            model_name='assessmentsession', name='answer_timings',
            field=models.JSONField(default=dict, blank=True, editable=False),
        ),
    ]
