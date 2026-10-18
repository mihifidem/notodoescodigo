from django.db import migrations, models
import django.db.models.deletion


def assign_existing_contents(apps, schema_editor):
    Unit = apps.get_model('lms', 'FormativeUnit')
    Content = apps.get_model('lms', 'ModuleContent')
    Module = apps.get_model('courses', 'Tema')
    alias = schema_editor.connection.alias
    for module_id in Module.objects.using(alias).values_list('pk', flat=True).iterator():
        unit = Unit.objects.using(alias).create(module_id=module_id, title='General', position=0)
        Content.objects.using(alias).filter(module_id=module_id).update(unit_id=unit.pk)


class Migration(migrations.Migration):
    dependencies = [
        ('lms', '0034_badge_image_badge_module'),
    ]

    operations = [
        migrations.CreateModel(
            name='FormativeUnit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200, verbose_name='nombre de la unidad formativa')),
                ('description', models.TextField(blank=True, default='', verbose_name='descripción')),
                ('position', models.PositiveIntegerField(default=0, verbose_name='orden')),
                ('module', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='formative_units', to='courses.tema')),
            ],
            options={
                'verbose_name': 'unidad formativa',
                'verbose_name_plural': 'unidades formativas',
                'ordering': ['position', 'pk'],
            },
        ),
        migrations.AddConstraint(
            model_name='formativeunit',
            constraint=models.UniqueConstraint(fields=('module', 'title'), name='lms_unit_title_per_module'),
        ),
        migrations.AddField(
            model_name='modulecontent',
            name='unit',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='contents', to='lms.formativeunit', verbose_name='unidad formativa'),
        ),
        migrations.RunPython(assign_existing_contents, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='modulecontent',
            name='unit',
            field=models.ForeignKey(blank=True, on_delete=django.db.models.deletion.RESTRICT, related_name='contents', to='lms.formativeunit', verbose_name='unidad formativa'),
        ),
    ]
