from django.db import migrations


FAMILIES = (
    ('personasphere', 'PersonaSphere'),
    ('identitymap', 'IdentityMap'),
    ('decissioncore', 'DecissionCore'),
    ('conglicttsync', 'ConglicttSync'),
    ('careernavigator', 'CareerNavigator'),
    ('behaviormatrix', 'BehaviorMatrix'),
    ('techmind', 'Techmind'),
)
MODES = ('basic', 'essential', 'advanced', 'premium')


def seed_catalog(apps, schema_editor):
    Family = apps.get_model('nexus', 'NexusFamily')
    Modality = apps.get_model('nexus', 'NexusModality')
    Assessment = apps.get_model('nexus', 'Assessment')
    Version = apps.get_model('nexus', 'NexusVersion')
    alias = schema_editor.connection.alias
    for order, code in enumerate(MODES):
        Modality.objects.using(alias).get_or_create(code=code, defaults={'name': code.title(), 'order': order})
    for order, (code, name) in enumerate(FAMILIES):
        family, _ = Family.objects.using(alias).get_or_create(
            code=code, defaults={'name': name, 'order': order},
        )
        for mode in MODES:
            if Version.objects.using(alias).filter(code=f'{code}-{mode}', version=1).exists():
                continue
            assessment = Assessment.objects.using(alias).create(title=f'{name} {mode.title()} · borrador editorial')
            Version.objects.using(alias).get_or_create(
                code=f'{code}-{mode}', version=1,
                defaults={
                    'family': family, 'assessment': assessment, 'mode': mode,
                    'title': f'{name} {mode.title()}', 'status': 'draft',
                    'change_reason': 'Catálogo inicial sin contenido aprobado. No publicable.',
                },
            )


class Migration(migrations.Migration):
    dependencies = [('nexus', '0019_modalities_and_session_uniqueness')]
    operations = [migrations.RunPython(seed_catalog, migrations.RunPython.noop)]
