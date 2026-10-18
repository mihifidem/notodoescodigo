from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nexus', '0005_recurso_test'),
    ]

    operations = [
        migrations.AddField(
            model_name='testplantilla',
            name='es_demo',
            field=models.BooleanField(
                default=False,
                verbose_name='Demo gratuito (sin login)',
                help_text='Si está activado, cualquier usuario puede realizar este test sin registrarse.',
            ),
        ),
    ]
