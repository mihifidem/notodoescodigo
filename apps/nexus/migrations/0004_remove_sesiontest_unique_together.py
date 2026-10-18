from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('nexus', '0003_sesiontest_guardado_en_cv'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='sesiontest',
            unique_together=set(),
        ),
    ]
