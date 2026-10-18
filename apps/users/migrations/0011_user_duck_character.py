from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0010_notificacion_lms_notification'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='genero',
            field=models.CharField(
                blank=True,
                choices=[('hombre', 'Hombre'), ('mujer', 'Mujer')],
                default='',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='duck_specialty',
            field=models.CharField(
                blank=True,
                choices=[
                    ('developer', 'Developer'),
                    ('systems', 'Sistemas'),
                    ('cybersecurity', 'Ciberseguridad'),
                    ('gamer', 'Gamer'),
                    ('data_ia', 'Data IA'),
                ],
                default='',
                max_length=20,
                verbose_name='Especialidad Ducky',
            ),
        ),
    ]
