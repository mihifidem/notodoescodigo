from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('curriculum', '0004_nexus_dna_entry'),
    ]

    operations = [
        migrations.AddField(
            model_name='nexusdnaentry',
            name='dimensiones',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Lista de dimensiones propias del test: [{label, score, color}]',
            ),
        ),
    ]
