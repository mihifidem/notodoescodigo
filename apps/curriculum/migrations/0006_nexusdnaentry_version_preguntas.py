from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('curriculum', '0005_nexusdnaentry_dimensiones'),
    ]

    operations = [
        migrations.AddField(
            model_name='nexusdnaentry',
            name='version_test',
            field=models.CharField(blank=True, default='', help_text='essential / advanced / premium', max_length=20),
        ),
        migrations.AddField(
            model_name='nexusdnaentry',
            name='total_preguntas',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='nexusdnaentry',
            name='num_likert',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='nexusdnaentry',
            name='num_abcd',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
