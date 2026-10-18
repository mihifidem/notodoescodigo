from django.db import migrations, connection


def ensure_curso_horas_duracion(apps, schema_editor):
    """Repair stale SQLite databases that are missing the legacy course duration column."""
    table_name = 'courses_curso'
    with schema_editor.connection.cursor() as cursor:
        tables = connection.introspection.table_names(cursor)
        if table_name not in tables:
            return

        columns = connection.introspection.get_table_description(cursor, table_name)
        if any(column.name == 'horas_duracion' for column in columns):
            return

        cursor.execute(
            'ALTER TABLE courses_curso ADD COLUMN horas_duracion INTEGER DEFAULT 0'
        )


class Migration(migrations.Migration):
    dependencies = [
        ('courses', '0010_curso_horas_duracion'),
    ]

    operations = [
        migrations.RunPython(
            ensure_curso_horas_duracion,
            migrations.RunPython.noop,
        ),
    ]
