from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nexus', '0007_testplantilla_nivel_acceso'),
    ]

    operations = [
        migrations.AddField(
            model_name='sesiontest',
            name='version_test',
            field=models.CharField(
                blank=True,
                choices=[
                    ('essential', 'Essential'),
                    ('advanced',  'Advanced'),
                    ('premium',   'Premium'),
                ],
                default='',
                help_text='Essential / Advanced / Premium seleccionado al realizar el test.',
                max_length=20,
                verbose_name='Variante del test',
            ),
        ),
    ]
