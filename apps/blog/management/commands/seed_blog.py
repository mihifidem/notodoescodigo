from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.blog.models import CategoriaBlog, HashtagBlog, PostBlog, SubcategoriaBlog


class Command(BaseCommand):
    help = 'Crea datos iniciales del blog (categorias, subcategorias, hashtags y posts de ejemplo).'

    def handle(self, *args, **options):
        data = [
            {
                'categoria': 'Programacion',
                'subcategorias': ['Python', 'Django', 'Buenas practicas'],
                'hashtags': ['backend', 'python', 'productividad'],
            },
            {
                'categoria': 'Carrera y Empleo',
                'subcategorias': ['Portfolio', 'Entrevistas', 'Soft skills'],
                'hashtags': ['empleabilidad', 'cv', 'entrevista'],
            },
            {
                'categoria': 'IA y Automatizacion',
                'subcategorias': ['Prompts', 'Flujos de trabajo', 'Herramientas'],
                'hashtags': ['ia', 'automatizacion', 'aprendizaje'],
            },
        ]

        categorias = {}
        for item in data:
            categoria, _ = CategoriaBlog.objects.get_or_create(
                slug=slugify(item['categoria']),
                defaults={
                    'nombre': item['categoria'],
                    'descripcion': f'Categoria {item["categoria"]} para contenido del blog.',
                    'activa': True,
                },
            )
            categorias[item['categoria']] = categoria

            for sub in item['subcategorias']:
                SubcategoriaBlog.objects.get_or_create(
                    categoria=categoria,
                    slug=slugify(sub),
                    defaults={
                        'nombre': sub,
                        'descripcion': f'Subcategoria {sub}.',
                        'activa': True,
                    },
                )

            for tag_name in item['hashtags']:
                HashtagBlog.objects.get_or_create(
                    slug=slugify(tag_name),
                    defaults={'nombre': tag_name},
                )

        posts_data = [
            {
                'slug': 'guia-estudio-python-90-dias',
                'titulo': 'Guia de estudio Python en 90 dias',
                'resumen': 'Plan de accion para aprender Python paso a paso.',
                'contenido_publico': (
                    'Semana 1-2: sintaxis, variables y condicionales. '
                    'Semana 3-4: funciones y estructuras de datos. '
                    'Semana 5-8: proyectos mini para consolidar.'
                ),
                'contenido_privado': '',
                'categoria': 'Programacion',
                'subcategoria_slug': 'python',
                'visibilidad': PostBlog.VISIBILIDAD_PUBLICA,
                'destacado': True,
                'puntos_lectura': 12,
                'segundos_lectura_requeridos': 45,
                'hashtags': ['backend', 'python', 'aprendizaje'],
            },
            {
                'slug': 'checklist-django-para-proyectos-reales',
                'titulo': 'Checklist Django para proyectos reales',
                'resumen': 'Buenas practicas para desplegar con menos errores.',
                'contenido_publico': 'Configura settings por entorno, logs, tests y manejo de errores.',
                'contenido_privado': 'Plantilla premium de checklist completa para pre-produccion y produccion.',
                'categoria': 'Programacion',
                'subcategoria_slug': 'django',
                'visibilidad': PostBlog.VISIBILIDAD_SEMIPUBLICA,
                'destacado': False,
                'puntos_lectura': 14,
                'segundos_lectura_requeridos': 50,
                'hashtags': ['backend', 'python', 'productividad'],
            },
            {
                'slug': 'arquitectura-clean-en-proyectos-python',
                'titulo': 'Arquitectura clean en proyectos Python',
                'resumen': 'Separa dominio, aplicacion e infraestructura de forma clara.',
                'contenido_publico': 'Como estructurar carpetas y capas para escalar mantenibilidad.',
                'contenido_privado': 'Ejemplo completo de arquitectura con casos de uso y repositorios.',
                'categoria': 'Programacion',
                'subcategoria_slug': 'buenas-practicas',
                'visibilidad': PostBlog.VISIBILIDAD_PRIVADA,
                'destacado': True,
                'puntos_lectura': 20,
                'segundos_lectura_requeridos': 70,
                'hashtags': ['python', 'backend', 'productividad'],
            },
            {
                'slug': 'roadmap-primer-empleo-tech',
                'titulo': 'Roadmap para tu primer empleo tech',
                'resumen': 'Como prepararte para entrevistas y destacar tu perfil.',
                'contenido_publico': (
                    'Define tu stack base, mejora tu GitHub y practica entrevistas tecnicas. '
                    'Construye un portfolio claro y medible.'
                ),
                'contenido_privado': (
                    'Plantilla premium: plan semanal de 8 semanas, checklists de CV y '
                    'guion de respuestas para entrevistas de RRHH y tecnicas.'
                ),
                'categoria': 'Carrera y Empleo',
                'subcategoria_slug': 'entrevistas',
                'visibilidad': PostBlog.VISIBILIDAD_SEMIPUBLICA,
                'destacado': False,
                'puntos_lectura': 15,
                'segundos_lectura_requeridos': 60,
                'hashtags': ['empleabilidad', 'cv', 'entrevista'],
            },
            {
                'slug': 'como-armar-un-portfolio-que-convierte',
                'titulo': 'Como armar un portfolio que convierte',
                'resumen': 'Estructura, casos y metricas para destacar ante reclutadores.',
                'contenido_publico': 'Incluye problema, solucion, stack y resultados cuantificables.',
                'contenido_privado': '',
                'categoria': 'Carrera y Empleo',
                'subcategoria_slug': 'portfolio',
                'visibilidad': PostBlog.VISIBILIDAD_PUBLICA,
                'destacado': True,
                'puntos_lectura': 10,
                'segundos_lectura_requeridos': 40,
                'hashtags': ['empleabilidad', 'cv', 'productividad'],
            },
            {
                'slug': 'negociacion-salarial-para-perfiles-junior',
                'titulo': 'Negociacion salarial para perfiles junior',
                'resumen': 'Tecnicas para defender tu propuesta de valor.',
                'contenido_publico': 'Como investigar rangos y presentar logros en entrevista final.',
                'contenido_privado': 'Guiones premium para responder objeciones y negociar beneficios.',
                'categoria': 'Carrera y Empleo',
                'subcategoria_slug': 'soft-skills',
                'visibilidad': PostBlog.VISIBILIDAD_PRIVADA,
                'destacado': False,
                'puntos_lectura': 18,
                'segundos_lectura_requeridos': 65,
                'hashtags': ['entrevista', 'empleabilidad', 'productividad'],
            },
            {
                'slug': 'automatizacion-con-ia-para-freelancers',
                'titulo': 'Automatizacion con IA para freelancers',
                'resumen': 'Flujos avanzados para ahorrar tiempo y escalar entregas.',
                'contenido_publico': 'Resumen ejecutivo del enfoque de automatizacion.',
                'contenido_privado': (
                    'Playbooks premium: flujo completo de captacion, propuesta, produccion '
                    'y QA con IA, mas prompts listos para copiar y adaptar.'
                ),
                'categoria': 'IA y Automatizacion',
                'subcategoria_slug': 'flujos-de-trabajo',
                'visibilidad': PostBlog.VISIBILIDAD_PRIVADA,
                'destacado': True,
                'puntos_lectura': 25,
                'segundos_lectura_requeridos': 75,
                'hashtags': ['ia', 'automatizacion', 'productividad'],
            },
            {
                'slug': 'prompts-efectivos-para-estudiar-mas-rapido',
                'titulo': 'Prompts efectivos para estudiar mas rapido',
                'resumen': 'Plantillas de prompts para resumir, practicar y evaluar.',
                'contenido_publico': 'Estructura base para prompts claros: contexto, objetivo, formato.',
                'contenido_privado': '',
                'categoria': 'IA y Automatizacion',
                'subcategoria_slug': 'prompts',
                'visibilidad': PostBlog.VISIBILIDAD_PUBLICA,
                'destacado': False,
                'puntos_lectura': 11,
                'segundos_lectura_requeridos': 42,
                'hashtags': ['ia', 'aprendizaje', 'productividad'],
            },
            {
                'slug': 'herramientas-ia-para-documentar-proyectos',
                'titulo': 'Herramientas IA para documentar proyectos',
                'resumen': 'Acelera documentacion tecnica con asistencia de IA.',
                'contenido_publico': 'Flujo para generar README, changelog y FAQ en minutos.',
                'contenido_privado': 'Kit premium con prompts por tipo de repositorio y checklist QA.',
                'categoria': 'IA y Automatizacion',
                'subcategoria_slug': 'herramientas',
                'visibilidad': PostBlog.VISIBILIDAD_SEMIPUBLICA,
                'destacado': False,
                'puntos_lectura': 16,
                'segundos_lectura_requeridos': 58,
                'hashtags': ['ia', 'automatizacion', 'backend'],
            },
        ]

        for item in posts_data:
            post, _ = PostBlog.objects.get_or_create(
                slug=item['slug'],
                defaults={
                    'titulo': item['titulo'],
                    'resumen': item['resumen'],
                    'contenido_publico': item['contenido_publico'],
                    'contenido_privado': item['contenido_privado'],
                    'categoria': categorias[item['categoria']],
                    'subcategoria': SubcategoriaBlog.objects.get(
                        categoria=categorias[item['categoria']],
                        slug=item['subcategoria_slug'],
                    ),
                    'visibilidad': item['visibilidad'],
                    'publicado': True,
                    'destacado': item['destacado'],
                    'puntos_lectura': item['puntos_lectura'],
                    'segundos_lectura_requeridos': item['segundos_lectura_requeridos'],
                },
            )
            post.hashtags.set(HashtagBlog.objects.filter(slug__in=item['hashtags']))

        self.stdout.write(self.style.SUCCESS('Datos iniciales del blog creados/actualizados correctamente.'))
