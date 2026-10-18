"""
Management command: python manage.py seed_conflictsync

Creates the ConflictSync TestPlantilla and all 8 DimensionDefinicion entries
with their DimensionRango score ranges, mirroring the integration of
BehaviorMatrix and TechMind.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = 'Seed ConflictSync test, dimensions and score ranges'

    def handle(self, *args, **options):
        from apps.nexus.models import (
            TestPlantilla, DimensionDefinicion, DimensionRango,
        )

        User = get_user_model()
        admin = User.objects.filter(is_superuser=True).first()
        if not admin:
            self.stderr.write('No superuser found. Create one first.')
            return

        # ── TestPlantilla ──────────────────────────────────────────────────────
        test, created = TestPlantilla.objects.get_or_create(
            nombre='ConflictSync',
            defaults={
                'descripcion': (
                    'ConflictSync evalúa tu inteligencia de conflicto en 8 dimensiones: '
                    'Conflict Awareness, Emotional Regulation, Assertive Communication, '
                    'Negotiation Style, Conflict Resolution, Empathy Under Tension, '
                    'Fairness Orientation y Post-Conflict Recovery.'
                ),
                'archivo_html': 'nexus/tests/conflictSync.html',
                'nivel_acceso': 'advanced',
                'activo': True,
                'creado_por': admin,
            },
        )
        verb = 'Creado' if created else 'Ya existe'
        self.stdout.write(f'{verb}: TestPlantilla "{test.nombre}" (pk={test.pk})')

        # ── DimensionDefinicion + DimensionRango ───────────────────────────────
        DIMS = [
            {
                'label': 'Conflict Awareness',
                'descripcion': (
                    'Capacidad para detectar y reconocer señales tempranas de conflicto, '
                    'diferenciar sus tipos y entenderlo como un proceso potencialmente útil.'
                ),
                'icono': 'bi-eye',
                'orden': 1,
                'rangos': [
                    (0,  39,  'En desarrollo',  '#EF4444', 'Dificultad para percibir conflictos latentes.',
                     'Practica la observación activa de dinámicas de equipo.'),
                    (40, 59,  'Moderado',        '#F59E0B', 'Detectas algunos conflictos pero no siempre a tiempo.',
                     'Trabaja la escucha activa y la lectura del clima emocional.'),
                    (60, 79,  'Alto',            '#84CC16', 'Buena capacidad de detección y análisis de conflictos.',
                     'Refuerza la distinción entre conflictos de tarea y relacionales.'),
                    (80, 100, 'Experto',         '#22C55E', 'Excelente inteligencia situacional ante el conflicto.',
                     ''),
                ],
            },
            {
                'label': 'Emotional Regulation',
                'descripcion': (
                    'Capacidad para gestionar las propias emociones durante situaciones de tensión, '
                    'manteniendo la calma y la capacidad de respuesta racional.'
                ),
                'icono': 'bi-heart-pulse',
                'orden': 2,
                'rangos': [
                    (0,  39,  'En desarrollo',  '#EF4444', 'Las emociones interfieren significativamente en el conflicto.',
                     'Aprende técnicas de regulación: respiración, pausa estratégica, reencuadre.'),
                    (40, 59,  'Moderado',        '#F59E0B', 'Gestión emocional variable según la intensidad del conflicto.',
                     'Identifica tus detonantes emocionales y crea protocolos de respuesta.'),
                    (60, 79,  'Alto',            '#84CC16', 'Buena gestión emocional en la mayoría de conflictos.',
                     'Desarrolla mayor resiliencia en conflictos de alta carga emocional.'),
                    (80, 100, 'Experto',         '#22C55E', 'Excelente regulación emocional incluso bajo alta presión.',
                     ''),
                ],
            },
            {
                'label': 'Assertive Communication',
                'descripcion': (
                    'Habilidad para expresar necesidades, límites y opiniones con claridad y respeto, '
                    'sin agresividad ni sumisión excesiva.'
                ),
                'icono': 'bi-chat-square-text',
                'orden': 3,
                'rangos': [
                    (0,  39,  'En desarrollo',  '#EF4444', 'Dificultad para expresar posiciones en situaciones de tensión.',
                     'Practica el modelo DESC (Describir, Expresar, Especificar, Consecuencias).'),
                    (40, 59,  'Moderado',        '#F59E0B', 'Comunicación asertiva inconsistente bajo presión.',
                     'Trabaja el autoconocimiento de tus patrones comunicativos reactivos.'),
                    (60, 79,  'Alto',            '#84CC16', 'Buena comunicación asertiva en la mayoría de contextos.',
                     'Amplía el repertorio de herramientas para conversaciones difíciles.'),
                    (80, 100, 'Experto',         '#22C55E', 'Comunicación asertiva sólida y consistente.',
                     ''),
                ],
            },
            {
                'label': 'Negotiation Style',
                'descripcion': (
                    'Estilo y efectividad en procesos de negociación, incluyendo la capacidad de '
                    'separar personas de problemas y usar intereses subyacentes.'
                ),
                'icono': 'bi-arrows-angle-expand',
                'orden': 4,
                'rangos': [
                    (0,  39,  'En desarrollo',  '#EF4444', 'Dificultad para construir acuerdos equitativos.',
                     'Estudia negociación basada en intereses (método Harvard).'),
                    (40, 59,  'Moderado',        '#F59E0B', 'Negociación efectiva en contextos simples.',
                     'Practica la identificación de intereses vs. posiciones.'),
                    (60, 79,  'Alto',            '#84CC16', 'Buen estilo negociador con capacidad de generar valor.',
                     'Desarrolla habilidades de negociación en contextos de alta complejidad.'),
                    (80, 100, 'Experto',         '#22C55E', 'Negociador hábil capaz de crear acuerdos de alto valor.',
                     ''),
                ],
            },
            {
                'label': 'Conflict Resolution',
                'descripcion': (
                    'Capacidad para resolver conflictos de forma definitiva, facilitando acuerdos '
                    'que aborden las causas raíz y prevengan la recurrencia.'
                ),
                'icono': 'bi-check2-circle',
                'orden': 5,
                'rangos': [
                    (0,  39,  'En desarrollo',  '#EF4444', 'Los conflictos tienden a quedar sin resolver o a reaparecer.',
                     'Aprende frameworks de resolución: mediación, facilitación, consenso.'),
                    (40, 59,  'Moderado',        '#F59E0B', 'Resolución efectiva en conflictos simples.',
                     'Trabaja el cierre explícito y el seguimiento post-resolución.'),
                    (60, 79,  'Alto',            '#84CC16', 'Buena capacidad de resolución y cierre de conflictos.',
                     'Desarrolla habilidades de mediación formal.'),
                    (80, 100, 'Experto',         '#22C55E', 'Excelente capacidad resolutiva, incluso en conflictos complejos.',
                     ''),
                ],
            },
            {
                'label': 'Empathy Under Tension',
                'descripcion': (
                    'Capacidad para mantener la perspectiva del otro y la escucha activa incluso '
                    'en situaciones de alta tensión emocional.'
                ),
                'icono': 'bi-people',
                'orden': 6,
                'rangos': [
                    (0,  39,  'En desarrollo',  '#EF4444', 'La empatía se deteriora significativamente bajo presión.',
                     'Practica la validación emocional y la escucha sin juicio.'),
                    (40, 59,  'Moderado',        '#F59E0B', 'Empatía variable en función de la intensidad del conflicto.',
                     'Trabaja la escucha activa y la reformulación empática.'),
                    (60, 79,  'Alto',            '#84CC16', 'Buena capacidad empática incluso en momentos de tensión.',
                     'Desarrolla la empatía como palanca de desescalada.'),
                    (80, 100, 'Experto',         '#22C55E', 'Empatía sólida y consistente bajo alta presión.',
                     ''),
                ],
            },
            {
                'label': 'Fairness Orientation',
                'descripcion': (
                    'Tendencia a buscar soluciones equitativas, señalar injusticias y actuar '
                    'con imparcialidad en dinámicas de conflicto.'
                ),
                'icono': 'bi-balance',
                'orden': 7,
                'rangos': [
                    (0,  39,  'En desarrollo',  '#EF4444', 'Dificultad para actuar con imparcialidad en conflictos.',
                     'Trabaja la conciencia de sesgos y la perspectiva sistémica.'),
                    (40, 59,  'Moderado',        '#F59E0B', 'Orientación a la equidad presente pero no consistente.',
                     'Practica el análisis de situaciones desde múltiples ángulos.'),
                    (60, 79,  'Alto',            '#84CC16', 'Buena orientación a la justicia y la equidad.',
                     'Desarrolla herramientas para señalar injusticias de forma constructiva.'),
                    (80, 100, 'Experto',         '#22C55E', 'Referente de imparcialidad y equidad en su entorno.',
                     ''),
                ],
            },
            {
                'label': 'Post-Conflict Recovery',
                'descripcion': (
                    'Capacidad para restaurar relaciones y extraer aprendizajes tras un conflicto, '
                    'evitando que el pasado contamine futuras interacciones.'
                ),
                'icono': 'bi-arrow-repeat',
                'orden': 8,
                'rangos': [
                    (0,  39,  'En desarrollo',  '#EF4444', 'Los conflictos pasados afectan negativamente las relaciones futuras.',
                     'Practica el cierre emocional y el perdón estratégico.'),
                    (40, 59,  'Moderado',        '#F59E0B', 'Recuperación post-conflicto variable.',
                     'Desarrolla rituales de cierre y protocolos de restauración relacional.'),
                    (60, 79,  'Alto',            '#84CC16', 'Buena capacidad de recuperación y restauración relacional.',
                     'Trabaja la extracción sistemática de aprendizajes tras cada conflicto.'),
                    (80, 100, 'Experto',         '#22C55E', 'Excelente capacidad de recuperación y aprendizaje post-conflicto.',
                     ''),
                ],
            },
        ]

        for dim_data in DIMS:
            dim, dcreated = DimensionDefinicion.objects.get_or_create(
                test_tipo='conflictsync',
                label=dim_data['label'],
                defaults={
                    'descripcion': dim_data['descripcion'],
                    'icono': dim_data['icono'],
                    'orden': dim_data['orden'],
                },
            )
            dverb = 'Creada' if dcreated else 'Ya existe'
            self.stdout.write(f'  {dverb}: DimensionDefinicion "{dim.label}"')

            if dcreated:
                for rmin, rmax, titulo, color, explicacion, recomendacion in dim_data['rangos']:
                    DimensionRango.objects.create(
                        dimension=dim,
                        rango_min=rmin,
                        rango_max=rmax,
                        titulo=titulo,
                        color_nivel=color,
                        explicacion=explicacion,
                        recomendacion=recomendacion,
                    )
                self.stdout.write(f'    > {len(dim_data["rangos"])} rangos creados')

        self.stdout.write(self.style.SUCCESS('\nConflictSync seed completado.'))
