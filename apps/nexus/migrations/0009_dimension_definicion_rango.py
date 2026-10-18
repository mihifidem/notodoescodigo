from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('nexus', '0008_sesiontest_version_test'),
    ]

    operations = [
        migrations.CreateModel(
            name='DimensionDefinicion',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('test_tipo', models.CharField(
                    blank=True, choices=[
                        ('', 'Todos los tests (genérico)'),
                        ('techmind', 'TechMind'),
                        ('careernav', 'CareerNavigator'),
                        ('behaviormatrix', 'BehaviorMatrix'),
                    ],
                    default='', help_text='Deja vacío para dimensiones comunes a varios tests.',
                    max_length=30, verbose_name='Test',
                )),
                ('label', models.CharField(
                    help_text='Debe coincidir exactamente con el label del JSON de resultados.',
                    max_length=200, verbose_name='Nombre de la dimensión',
                )),
                ('descripcion', models.TextField(
                    help_text='Explica qué mide esta dimensión y por qué importa.',
                    verbose_name='Descripción general',
                )),
                ('icono', models.CharField(
                    blank=True, default='bi-bar-chart-line', max_length=60,
                    verbose_name='Icono (Bootstrap Icons)',
                )),
                ('orden', models.PositiveSmallIntegerField(default=0, verbose_name='Orden')),
            ],
            options={
                'verbose_name': 'Dimensión',
                'verbose_name_plural': 'Dimensiones',
                'ordering': ['test_tipo', 'orden', 'label'],
                'unique_together': {('test_tipo', 'label')},
            },
        ),
        migrations.CreateModel(
            name='DimensionRango',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('dimension', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='rangos', to='nexus.dimensiondefinicion',
                    verbose_name='Dimensión',
                )),
                ('rango_min', models.PositiveSmallIntegerField(verbose_name='Score mínimo (0–100)')),
                ('rango_max', models.PositiveSmallIntegerField(verbose_name='Score máximo (0–100)')),
                ('titulo', models.CharField(max_length=100, verbose_name='Título del nivel')),
                ('explicacion', models.TextField(verbose_name='Explicación detallada')),
                ('recomendacion', models.TextField(blank=True, verbose_name='Recomendación de mejora')),
                ('color_nivel', models.CharField(
                    choices=[
                        ('#22C55E', 'Verde — Excelente'),
                        ('#84CC16', 'Verde lima — Bueno'),
                        ('#F59E0B', 'Amarillo — Moderado'),
                        ('#F97316', 'Naranja — Bajo'),
                        ('#EF4444', 'Rojo — Muy bajo'),
                    ],
                    default='#64748B', max_length=7, verbose_name='Color del nivel',
                )),
            ],
            options={
                'verbose_name': 'Rango de score',
                'verbose_name_plural': 'Rangos de score',
                'ordering': ['rango_min'],
            },
        ),
    ]
