from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0004_quiz_csv_filename'),
        ('users', '0007_user_aula_fk'),
    ]

    operations = [
        migrations.AddField(
            model_name='aula',
            name='cursos',
            field=models.ManyToManyField(
                blank=True,
                related_name='aulas_disponibles',
                to='courses.curso',
                verbose_name='Cursos disponibles',
            ),
        ),
    ]