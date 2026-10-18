from django.db import migrations, models
from django.conf import settings
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0004_quiz_csv_filename'),
    ]

    operations = [
        migrations.CreateModel(
            name='TemaRecurso',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('titulo', models.CharField(max_length=200)),
                ('archivo', models.FileField(upload_to='recursos_tema/')),
                ('tipo', models.CharField(choices=[('pdf', 'PDF'), ('mp3', 'MP3'), ('mp4', 'MP4')], max_length=10)),
                ('orden', models.PositiveIntegerField(default=0)),
                ('activo', models.BooleanField(default=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('tema', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recursos', to='courses.tema')),
            ],
            options={
                'verbose_name': 'Recurso de tema',
                'verbose_name_plural': 'Recursos de tema',
                'ordering': ['orden', 'titulo'],
            },
        ),
        migrations.CreateModel(
            name='TemaRecursoVisualizacion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('puntos_otorgados', models.PositiveIntegerField(default=3)),
                ('vista_en', models.DateTimeField(auto_now_add=True)),
                ('recurso', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='visualizaciones', to='courses.temarecurso')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='visualizaciones_recursos_tema', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Visualización de recurso',
                'verbose_name_plural': 'Visualizaciones de recursos',
                'ordering': ['-vista_en'],
                'unique_together': {('usuario', 'recurso')},
            },
        ),
    ]