"""
Management command to populate initial gamification data.

Usage:
    python manage.py setup_gamification
    python manage.py setup_gamification --rewards   # also create sample rewards
"""

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Creates initial badges, missions (and optionally rewards) for the platform.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--rewards',
            action='store_true',
            help='Also create sample marketplace rewards.',
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            self._create_badges()
            self._create_missions()
            if options['rewards']:
                self._create_rewards()
        self.stdout.write(self.style.SUCCESS('✓ Gamification data loaded successfully.'))

    # ------------------------------------------------------------------ badges
    def _create_badges(self):
        from apps.gamification.models import Insignia

        badges = [
            # Streak badges
            dict(nombre='Racha de 3 días',   descripcion='Accede 3 días seguidos.',     tipo='streak',  requisito_valor=3,    emoji='🔥', visible=True),
            dict(nombre='Racha de 7 días',   descripcion='Accede 7 días seguidos.',     tipo='streak',  requisito_valor=7,    emoji='⚡', visible=True),
            dict(nombre='Racha de 30 días',  descripcion='Accede 30 días seguidos.',    tipo='streak',  requisito_valor=30,   emoji='🌟', visible=True),
            # Points badges
            dict(nombre='Primeros 100 puntos',  descripcion='Acumula 100 puntos.',       tipo='puntos',  requisito_valor=100,  emoji='💯', visible=True),
            dict(nombre='500 puntos',            descripcion='Acumula 500 puntos.',       tipo='puntos',  requisito_valor=500,  emoji='🏅', visible=True),
            dict(nombre='1000 puntos',           descripcion='Acumula 1 000 puntos.',     tipo='puntos',  requisito_valor=1000, emoji='🥇', visible=True),
            dict(nombre='5000 puntos',           descripcion='Acumula 5 000 puntos.',     tipo='puntos',  requisito_valor=5000, emoji='👑', visible=True),
            # Topic / course badges
            dict(nombre='Primer tema',          descripcion='Completa tu primer tema.',   tipo='tema',    requisito_valor=1,    emoji='📖', visible=True),
            dict(nombre='10 temas',             descripcion='Completa 10 temas.',         tipo='tema',    requisito_valor=10,   emoji='📚', visible=True),
            dict(nombre='Primer curso',         descripcion='Completa tu primer curso.',  tipo='curso',   requisito_valor=1,    emoji='🎓', visible=True),
            dict(nombre='5 cursos',             descripcion='Completa 5 cursos.',         tipo='curso',   requisito_valor=5,    emoji='🏆', visible=True),
            # Mission badge
            dict(nombre='Misionero',            descripcion='Completa una misión diaria.',tipo='mision',  requisito_valor=1,    emoji='🎯', visible=True),
            # Special hidden badge
            dict(nombre='Explorador',           descripcion='Visita todas las secciones.',tipo='especial',requisito_valor=1,    emoji='🧭', visible=False),
        ]

        created = 0
        for data in badges:
            _, is_new = Insignia.objects.get_or_create(
                nombre=data['nombre'],
                defaults=data,
            )
            if is_new:
                created += 1

        self.stdout.write(f'  Badges: {created} created, {len(badges) - created} already existed.')

    # ---------------------------------------------------------------- missions
    def _create_missions(self):
        from apps.gamification.models import Mision

        missions = [
            # Daily missions
            dict(nombre='Acceso diario',         descripcion='Inicia sesión hoy.',                      tipo='diaria',  requisito_tipo='login',      requisito_cantidad=1,  puntos_recompensa=10),
            dict(nombre='Completa un tema',       descripcion='Completa al menos 1 tema hoy.',           tipo='diaria',  requisito_tipo='tema',        requisito_cantidad=1,  puntos_recompensa=20),
            dict(nombre='Haz un quiz',            descripcion='Realiza un quiz hoy.',                    tipo='diaria',  requisito_tipo='quiz',        requisito_cantidad=1,  puntos_recompensa=25),
            # Weekly missions
            dict(nombre='Semana de estudio',      descripcion='Completa 5 temas esta semana.',           tipo='semanal', requisito_tipo='tema',        requisito_cantidad=5,  puntos_recompensa=80),
            dict(nombre='Racha semanal',          descripcion='Accede 5 días distintos esta semana.',    tipo='semanal', requisito_tipo='login',       requisito_cantidad=5,  puntos_recompensa=60),
            dict(nombre='Quiz master',            descripcion='Completa 3 quizzes esta semana.',         tipo='semanal', requisito_tipo='quiz',        requisito_cantidad=3,  puntos_recompensa=75),
            dict(nombre='Completa un curso',      descripcion='Finaliza un curso esta semana.',          tipo='semanal', requisito_tipo='curso',       requisito_cantidad=1,  puntos_recompensa=150),
        ]

        created = 0
        for data in missions:
            _, is_new = Mision.objects.get_or_create(
                nombre=data['nombre'],
                defaults=data,
            )
            if is_new:
                created += 1

        self.stdout.write(f'  Missions: {created} created, {len(missions) - created} already existed.')

    # ----------------------------------------------------------------- rewards
    def _create_rewards(self):
        from apps.rewards.models import Recompensa

        rewards = [
            dict(nombre='Avatar Premium',          descripcion='Desbloquea avatares exclusivos.',     costo_puntos=200,  stock=-1, emoji='🎨'),
            dict(nombre='Certificado Express',      descripcion='Genera un certificado adicional.',   costo_puntos=500,  stock=-1, emoji='📜'),
            dict(nombre='Mes Premium gratis',       descripcion='1 mes de acceso premium.',           costo_puntos=2000, stock=50, emoji='⭐'),
            dict(nombre='Mentoring 1h',             descripcion='Sesión de 1h con un mentor.',        costo_puntos=3000, stock=10, emoji='👨‍🏫'),
            dict(nombre='Pack de stickers',         descripcion='Pack exclusive de stickers digitales.', costo_puntos=100, stock=-1,emoji='🎁'),
        ]

        created = 0
        for data in rewards:
            _, is_new = Recompensa.objects.get_or_create(
                nombre=data['nombre'],
                defaults=data,
            )
            if is_new:
                created += 1

        self.stdout.write(f'  Rewards: {created} created, {len(rewards) - created} already existed.')
