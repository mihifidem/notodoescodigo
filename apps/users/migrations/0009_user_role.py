from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0008_aula_cursos'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[('alumno', 'Alumno'), ('basic', 'Basic'), ('premium', 'Premium')],
                default='basic',
                max_length=20,
            ),
        ),
    ]