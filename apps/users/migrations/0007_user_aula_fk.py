# Generated manually to convert User.aula text into FK to Aula.
from django.db import migrations, models
import django.db.models.deletion


def migrar_aula_texto_a_fk(apps, schema_editor):
    User = apps.get_model('users', 'User')
    Aula = apps.get_model('users', 'Aula')

    usuarios = User.objects.exclude(aula='').exclude(aula__isnull=True)
    for usuario in usuarios.iterator():
        nombre_aula = (usuario.aula or '').strip()
        if not nombre_aula:
            continue
        aula_obj, _ = Aula.objects.get_or_create(
            nombre=nombre_aula,
            defaults={
                'direccion': 'Sin direccion',
                'horario': 'Sin horario',
            },
        )
        usuario.aula_fk = aula_obj
        usuario.save(update_fields=['aula_fk'])


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0006_aula'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='aula_fk',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='usuarios',
                to='users.aula',
                verbose_name='Aula',
            ),
        ),
        migrations.RunPython(migrar_aula_texto_a_fk, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='user',
            name='aula',
        ),
        migrations.RenameField(
            model_name='user',
            old_name='aula_fk',
            new_name='aula',
        ),
    ]
