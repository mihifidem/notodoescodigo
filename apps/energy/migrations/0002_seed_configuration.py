from decimal import Decimal

from django.db import migrations

ACTIVITY_PERCENT = {'free': Decimal('10'), 'plus': Decimal('15'), 'pro': Decimal('25')}

RULES = [
    ('course-topic-completed', 'Tema de curso completado', 'courses.topic_completed',
     'Progreso marcado como completado por el usuario en un tema que existe.'),
    ('course-quiz-passed', 'Quiz de curso aprobado', 'courses.quiz_passed',
     'Resultado de quiz con aprobado registrado por el servidor.'),
    ('lms-quiz-passed', 'Evaluación del campus aprobada', 'lms.quiz_passed',
     'Intento corregido en servidor con resultado aprobado.'),
    ('lms-module-completed', 'Módulo del campus completado', 'lms.module_completed',
     'Progreso de módulo completado registrado por el campus.'),
]

TOOLS = [
    ('letters-generate', 'Generar carta de presentación', 'Cartas (07)'),
    ('ats-semantic', 'Análisis semántico ATS', 'ATS (08)'),
    ('ats-rephrase', 'Reformulación ATS', 'ATS (08)'),
    ('nexus-interpretation', 'Interpretación Nexus', 'Nexus (09)'),
    ('nexus-plan', 'Plan Nexus asistido', 'Nexus (09)'),
    ('techdna-help', 'Ayuda Tech DNA', 'Tech DNA (10)'),
]


def seed(apps, schema_editor):
    Policy = apps.get_model('energy', 'EnergyPolicy')
    Rule = apps.get_model('energy', 'EnergyRewardRule')
    Tool = apps.get_model('energy', 'EnergyToolCost')
    # Solo se siembran valores dados por el negocio; capacidad, recuperación, costes y cantidades quedan vacíos.
    for plan, percent in ACTIVITY_PERCENT.items():
        Policy.objects.get_or_create(plan=plan, defaults={'activity_daily_percent': percent, 'enabled': False})
    for code, name, source, requirements in RULES:
        Rule.objects.get_or_create(
            source=source, defaults={'code': code, 'name': name, 'requirements': requirements, 'enabled': False},
        )
    for code, name, module in TOOLS:
        Tool.objects.get_or_create(
            code=code,
            defaults={
                'name': name, 'module': module, 'enabled': False,
                'required_access': 'Acceso que ya exige el módulo (plan o derecho comprado).',
            },
        )


class Migration(migrations.Migration):
    dependencies = [('energy', '0001_initial')]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
