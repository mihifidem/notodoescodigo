from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from apps.courses.models import CourseCategory
from apps.duckyclash.models import ChallengeSubcategory


TAXONOMY = {
    'Desarrollo web': ('Frontend', 'Backend'),
    'Ciberseguridad': ('Seguridad web', 'Redes seguras'),
    'Sistemas': ('Linux', 'Administración de sistemas'),
    'Gamer': ('Historia de videojuegos', 'Mecánicas de juego'),
    'Data e IA': ('Datos', 'Inteligencia artificial'),
}


class Command(BaseCommand):
    help = 'Crea categorías/subcategorías iniciales reutilizando los catálogos; no crea preguntas ni soluciones.'

    @transaction.atomic
    def handle(self, *args, **options):
        for name, subcategories in TAXONOMY.items():
            category, _ = CourseCategory.objects.get_or_create(nombre=name, defaults={'slug': slugify(name)})
            for subcategory in subcategories:
                ChallengeSubcategory.objects.get_or_create(category=category, name=subcategory)
        self.stdout.write(self.style.SUCCESS('Categorías disponibles; no se han inventado preguntas.'))
