from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0009_coursecategory_coursetag_remove_curso_horas_duracion_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='curso',
            name='horas_duracion',
            field=models.PositiveIntegerField(blank=True, default=0, null=True, verbose_name='horas de duración'),
        ),
    ]
