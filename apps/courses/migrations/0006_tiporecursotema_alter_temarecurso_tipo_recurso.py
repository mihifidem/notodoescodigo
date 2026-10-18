from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def crear_tipos_y_migrar_recursos(apps, schema_editor):
    TipoRecursoTema = apps.get_model('courses', 'TipoRecursoTema')
    TemaRecurso = apps.get_model('courses', 'TemaRecurso')

    tipos = {
        'audio': TipoRecursoTema.objects.create(codigo='audio', nombre='Audio', activo=True),
        'video': TipoRecursoTema.objects.create(codigo='video', nombre='Video', activo=True),
        'pdf': TipoRecursoTema.objects.create(codigo='pdf', nombre='PDF', activo=True),
        'imagen': TipoRecursoTema.objects.create(codigo='imagen', nombre='Imagen', activo=True),
    }

    mapping = {
        'mp3': tipos['audio'].id,
        'mp4': tipos['video'].id,
        'pdf': tipos['pdf'].id,
    }
    for recurso in TemaRecurso.objects.all():
        recurso.tipo_recurso_id = mapping.get(recurso.tipo, tipos['pdf'].id)
        recurso.save(update_fields=['tipo_recurso'])


def revertir_tipos_y_recursos(apps, schema_editor):
    TipoRecursoTema = apps.get_model('courses', 'TipoRecursoTema')
    TemaRecurso = apps.get_model('courses', 'TemaRecurso')

    mapping = {
        'audio': 'mp3',
        'video': 'mp4',
        'pdf': 'pdf',
        'imagen': 'pdf',
    }
    for recurso in TemaRecurso.objects.select_related('tipo_recurso').all():
        codigo = recurso.tipo_recurso.codigo if recurso.tipo_recurso_id else 'pdf'
        recurso.tipo = mapping.get(codigo, 'pdf')
        recurso.save(update_fields=['tipo'])

    TipoRecursoTema.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0005_temarecurso_temarecursovisualizacion'),
    ]

    operations = [
        migrations.CreateModel(
            name='TipoRecursoTema',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo', models.CharField(max_length=20, unique=True)),
                ('nombre', models.CharField(max_length=50, unique=True)),
                ('activo', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Tipo de recurso',
                'verbose_name_plural': 'Tipos de recurso',
                'ordering': ['nombre'],
            },
        ),
        migrations.AddField(
            model_name='temarecurso',
            name='tipo_recurso',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='recursos',
                to='courses.tiporecursotema',
            ),
        ),
        migrations.RunPython(crear_tipos_y_migrar_recursos, revertir_tipos_y_recursos),
        migrations.AlterField(
            model_name='temarecurso',
            name='tipo_recurso',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='recursos',
                to='courses.tiporecursotema',
            ),
        ),
        migrations.RemoveField(
            model_name='temarecurso',
            name='tipo',
        ),
    ]