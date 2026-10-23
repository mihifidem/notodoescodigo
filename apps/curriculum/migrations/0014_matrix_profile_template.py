from django.db import migrations
from django.utils import timezone


def seed_matrix(apps, schema_editor):
    template = apps.get_model('curriculum', 'ProfileTemplate')
    template.objects.using(schema_editor.connection.alias).get_or_create(
        code='matrix', version=1,
        defaults={
            'name': 'Matrix Premium',
            'description': 'Diseño Matrix con lluvia de caracteres, tipografía monoespaciada y secciones interactivas.',
            'category': 'thematic', 'schema_version': 1, 'renderer': 'matrix',
            'allowed_plans': ['pro'], 'recommended_branch_codes': ['informatica'],
            'compatible_branch_codes': [], 'default_color': '#166534',
            'customization_options': {}, 'status': 'published', 'published_at': timezone.now(),
        },
    )


class Migration(migrations.Migration):
    dependencies = [('curriculum', '0013_ats_verification')]
    operations = [migrations.RunPython(seed_matrix, migrations.RunPython.noop)]
