"""Management command to seed gamification and rewards catalog."""
from django.core.management.base import BaseCommand

from apps.gamification.models import Insignia, Mision
from apps.rewards.models import Recompensa


class Command(BaseCommand):
    help = "Carga insignias, misiones y recompensas iniciales."

    def handle(self, *args, **options):
        badges = [
            {
                "nombre": "Racha de 3 días",
                "descripcion": "Mantén una racha de estudio de 3 días.",
                "tipo": "streak",
                "icono": "bi-fire",
                "requisito_valor": 3,
                "visible": True,
                "puntos_recompensa": 25,
            },
            {
                "nombre": "Racha de 7 días",
                "descripcion": "Mantén una racha de estudio de 7 días.",
                "tipo": "streak",
                "icono": "bi-fire",
                "requisito_valor": 7,
                "visible": True,
                "puntos_recompensa": 75,
            },
            {
                "nombre": "100 puntos",
                "descripcion": "Acumula 100 puntos de experiencia.",
                "tipo": "puntos",
                "icono": "bi-star-fill",
                "requisito_valor": 100,
                "visible": True,
                "puntos_recompensa": 20,
            },
            {
                "nombre": "500 puntos",
                "descripcion": "Acumula 500 puntos de experiencia.",
                "tipo": "puntos",
                "icono": "bi-gem",
                "requisito_valor": 500,
                "visible": True,
                "puntos_recompensa": 100,
            },
            {
                "nombre": "Primer tema completado",
                "descripcion": "Completa tu primer tema.",
                "tipo": "tema",
                "icono": "bi-book-half",
                "requisito_valor": 1,
                "visible": True,
                "puntos_recompensa": 15,
            },
            {
                "nombre": "Primer curso terminado",
                "descripcion": "Finaliza tu primer curso.",
                "tipo": "curso",
                "icono": "bi-trophy-fill",
                "requisito_valor": 1,
                "visible": True,
                "puntos_recompensa": 100,
            },
        ]

        missions = [
            {
                "nombre": "Completa 1 tema hoy",
                "descripcion": "Termina al menos un tema durante el día.",
                "periodicidad": "diaria",
                "requisito_tipo": "tema",
                "requisito_cantidad": 1,
                "puntos_recompensa": 20,
                "activa": True,
            },
            {
                "nombre": "Responde 1 quiz",
                "descripcion": "Completa un quiz para validar tu progreso.",
                "periodicidad": "diaria",
                "requisito_tipo": "quiz",
                "requisito_cantidad": 1,
                "puntos_recompensa": 25,
                "activa": True,
            },
            {
                "nombre": "Completa 5 temas",
                "descripcion": "Avanza en tu aprendizaje completando 5 temas.",
                "periodicidad": "semanal",
                "requisito_tipo": "tema",
                "requisito_cantidad": 5,
                "puntos_recompensa": 80,
                "activa": True,
            },
            {
                "nombre": "Aprueba 3 quizzes",
                "descripcion": "Aprueba tres quizzes en la semana.",
                "periodicidad": "semanal",
                "requisito_tipo": "quiz",
                "requisito_cantidad": 3,
                "puntos_recompensa": 120,
                "activa": True,
            },
        ]

        rewards = [
            {
                "nombre": "Descuento 10% en mentoría",
                "descripcion": "Cupón de descuento para sesión 1:1.",
                "puntos_requeridos": 250,
                "stock": 30,
                "icono": "bi-ticket-perforated-fill",
                "activa": True,
            },
            {
                "nombre": "Plantilla premium",
                "descripcion": "Acceso a plantilla premium de estudio.",
                "puntos_requeridos": 180,
                "stock": -1,
                "icono": "bi-file-earmark-richtext-fill",
                "activa": True,
            },
            {
                "nombre": "Clase magistral grabada",
                "descripcion": "Desbloquea una masterclass exclusiva.",
                "puntos_requeridos": 400,
                "stock": 50,
                "icono": "bi-camera-video-fill",
                "activa": True,
            },
        ]

        for payload in badges:
            badge, created = Insignia.objects.update_or_create(
                nombre=payload["nombre"],
                defaults=payload,
            )
            self.stdout.write(
                self.style.SUCCESS(f"{'Creada' if created else 'Actualizada'} insignia: {badge.nombre}")
            )

        for payload in missions:
            mission, created = Mision.objects.update_or_create(
                nombre=payload["nombre"],
                defaults=payload,
            )
            self.stdout.write(
                self.style.SUCCESS(f"{'Creada' if created else 'Actualizada'} misión: {mission.nombre}")
            )

        for payload in rewards:
            reward, created = Recompensa.objects.update_or_create(
                nombre=payload["nombre"],
                defaults=payload,
            )
            self.stdout.write(
                self.style.SUCCESS(f"{'Creada' if created else 'Actualizada'} recompensa: {reward.nombre}")
            )

        self.stdout.write(self.style.SUCCESS("Seed inicial completado correctamente."))
