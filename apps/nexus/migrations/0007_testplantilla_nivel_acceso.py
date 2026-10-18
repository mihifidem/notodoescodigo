from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nexus', '0006_testplantilla_es_demo'),
    ]

    operations = [
        migrations.AddField(
            model_name='testplantilla',
            name='nivel_acceso',
            field=models.CharField(
                choices=[
                    ('essential', 'Essential (todos los usuarios)'),
                    ('advanced',  'Advanced (requiere login)'),
                    ('premium',   'Premium (requiere membresía)'),
                ],
                default='advanced',
                max_length=20,
                verbose_name='Nivel de acceso',
                help_text='Quién puede realizar este test.',
            ),
        ),
    ]
