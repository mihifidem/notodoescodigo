from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nexus', '0002_testplantilla_sesiontest'),
    ]

    operations = [
        migrations.AddField(
            model_name='sesiontest',
            name='guardado_en_cv',
            field=models.BooleanField(
                default=False,
                help_text='True si el alumno eligió volcar los resultados en su curriculum.',
                verbose_name='Guardado en perfil CV',
            ),
        ),
    ]
