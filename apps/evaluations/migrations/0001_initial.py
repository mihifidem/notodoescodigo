# Generated manually for evaluations app
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('courses', '__first__'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TipoExamen',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=100, unique=True)),
                ('codigo', models.CharField(max_length=30, unique=True)),
                ('descripcion', models.TextField(blank=True)),
                ('activo', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Tipo de examen',
                'verbose_name_plural': 'Tipos de examen',
                'ordering': ['nombre'],
            },
        ),
        migrations.CreateModel(
            name='Evaluacion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('titulo', models.CharField(max_length=200)),
                ('alcance_tipo', models.CharField(choices=[('TEMA', 'Tema'), ('MODULO', 'Modulo')], max_length=10)),
                ('modulo_ref', models.CharField(blank=True, help_text='Referencia externa del modulo cuando la evaluacion no es por tema.', max_length=100)),
                ('enunciado', models.TextField()),
                ('instrucciones', models.TextField(blank=True)),
                ('criterios_a_valorar', models.TextField(blank=True)),
                ('max_puntuacion', models.DecimalField(decimal_places=2, default=Decimal('10.00'), max_digits=6)),
                ('fecha_apertura', models.DateTimeField(blank=True, null=True)),
                ('fecha_cierre', models.DateTimeField(blank=True, null=True)),
                ('estado', models.CharField(choices=[('BORRADOR', 'Borrador'), ('PUBLICADA', 'Publicada'), ('CERRADA', 'Cerrada')], default='BORRADOR', max_length=15)),
                ('creada_en', models.DateTimeField(auto_now_add=True)),
                ('actualizada_en', models.DateTimeField(auto_now=True)),
                ('tema', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='evaluaciones', to='courses.tema')),
                ('tipo_examen', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='evaluaciones', to='evaluations.tipoexamen')),
            ],
            options={
                'verbose_name': 'Evaluacion',
                'verbose_name_plural': 'Evaluaciones',
                'ordering': ['-creada_en'],
            },
        ),
        migrations.CreateModel(
            name='EntregaEvaluacion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('archivo_respuesta', models.FileField(upload_to='evaluaciones/entregas/%Y/%m/')),
                ('texto_extraido', models.TextField(blank=True)),
                ('estado', models.CharField(choices=[('SUBIDA', 'Subida'), ('PROCESANDO', 'Procesando'), ('CORREGIDA', 'Corregida'), ('REVISION_DOCENTE', 'Revision docente'), ('ERROR', 'Error')], default='SUBIDA', max_length=20)),
                ('hash_archivo', models.CharField(blank=True, max_length=64)),
                ('intento_numero', models.PositiveIntegerField(default=1)),
                ('fecha_entrega', models.DateTimeField(auto_now_add=True)),
                ('procesada_en', models.DateTimeField(blank=True, null=True)),
                ('alumno', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entregas_evaluacion', to=settings.AUTH_USER_MODEL)),
                ('evaluacion', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entregas', to='evaluations.evaluacion')),
            ],
            options={
                'verbose_name': 'Entrega de evaluacion',
                'verbose_name_plural': 'Entregas de evaluacion',
                'ordering': ['-fecha_entrega'],
                'unique_together': {('evaluacion', 'alumno', 'intento_numero')},
            },
        ),
        migrations.CreateModel(
            name='RubricaEvaluacion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.CharField(default='1.0', max_length=30)),
                ('nota_maxima', models.DecimalField(decimal_places=2, default=Decimal('10.00'), max_digits=6)),
                ('umbral_aprobado', models.DecimalField(decimal_places=2, default=Decimal('5.00'), max_digits=6)),
                ('evaluacion', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='rubrica', to='evaluations.evaluacion')),
            ],
            options={
                'verbose_name': 'Rubrica de evaluacion',
                'verbose_name_plural': 'Rubricas de evaluacion',
            },
        ),
        migrations.CreateModel(
            name='EventoCorreccion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('evento', models.CharField(max_length=80)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('entrega', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='eventos', to='evaluations.entregaevaluacion')),
            ],
            options={
                'verbose_name': 'Evento de correccion',
                'verbose_name_plural': 'Eventos de correccion',
                'ordering': ['-creado_en'],
            },
        ),
        migrations.CreateModel(
            name='CriterioRubrica',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo', models.CharField(max_length=50)),
                ('nombre', models.CharField(max_length=150)),
                ('descripcion', models.TextField(blank=True)),
                ('peso', models.DecimalField(decimal_places=2, help_text='Peso en porcentaje (0-100).', max_digits=5)),
                ('escala_min', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=6)),
                ('escala_max', models.DecimalField(decimal_places=2, default=Decimal('10.00'), max_digits=6)),
                ('obligatorio', models.BooleanField(default=True)),
                ('orden', models.PositiveIntegerField(default=0)),
                ('rubrica', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='criterios', to='evaluations.rubricaevaluacion')),
            ],
            options={
                'verbose_name': 'Criterio de rubrica',
                'verbose_name_plural': 'Criterios de rubrica',
                'ordering': ['orden', 'id'],
                'unique_together': {('rubrica', 'codigo')},
            },
        ),
        migrations.CreateModel(
            name='CorreccionEvaluacion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('puntuacion_total', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=6)),
                ('puntuacion_por_criterio', models.JSONField(blank=True, default=dict)),
                ('feedback_global', models.TextField(blank=True)),
                ('feedback_detallado', models.JSONField(blank=True, default=list)),
                ('evidencias', models.JSONField(blank=True, default=list)),
                ('plan_mejora', models.JSONField(blank=True, default=list)),
                ('confianza_modelo', models.DecimalField(decimal_places=3, default=Decimal('0.000'), max_digits=4)),
                ('modelo_ia', models.CharField(blank=True, max_length=80)),
                ('prompt_version', models.CharField(default='1.0', max_length=30)),
                ('requiere_revision_humana', models.BooleanField(default=False)),
                ('creada_en', models.DateTimeField(auto_now_add=True)),
                ('entrega', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='correccion', to='evaluations.entregaevaluacion')),
            ],
            options={
                'verbose_name': 'Correccion de evaluacion',
                'verbose_name_plural': 'Correcciones de evaluacion',
            },
        ),
    ]
