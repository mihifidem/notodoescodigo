from django.db import migrations
from django.utils.text import slugify


PROMPTS = [
    {
        'titulo': 'Explica un tema como si tuviera 10 anos',
        'slug': 'explica-tema-como-si-tuviera-10-anos',
        'descripcion': 'Version sencilla para ninos, familias o personas que empiezan desde cero.',
        'contenido': (
            'Explica el tema {tema} como si hablaras con una persona de {edad_objetivo} anos. '
            'Usa ejemplos cotidianos, frases cortas y una comparacion facil de recordar. '
            'Termina con 3 preguntas de repaso muy simples.'
        ),
        'variables_json': [
            {'nombre': 'tema', 'descripcion': 'Tema a explicar', 'valor_defecto': 'internet'},
            {'nombre': 'edad_objetivo', 'descripcion': 'Edad o nivel de la persona', 'valor_defecto': '10'},
        ],
        'categoria': 'Educacion',
        'subcategoria': 'Explicacion sencilla',
        'hashtags': ['aprendizaje', 'explicacion', 'familias'],
        'visibilidad': 'publico',
        'destacado': True,
    },
    {
        'titulo': 'Tutor de estudio por pasos',
        'slug': 'tutor-de-estudio-por-pasos',
        'descripcion': 'Crea una sesion guiada para estudiar sin agobio.',
        'contenido': (
            'Actua como tutor personal. Quiero estudiar {asignatura}. '
            'Organiza una sesion de {duracion_minutos} minutos en bloques cortos. '
            'Incluye objetivo, explicacion, ejercicio, descanso breve y mini resumen final. '
            'Adapta el nivel a {nivel_estudio}.'
        ),
        'variables_json': [
            {'nombre': 'asignatura', 'descripcion': 'Materia o tema', 'valor_defecto': 'matematicas'},
            {'nombre': 'duracion_minutos', 'descripcion': 'Tiempo total de estudio', 'valor_defecto': '25'},
            {'nombre': 'nivel_estudio', 'descripcion': 'Nivel del estudiante', 'valor_defecto': 'ESO'},
        ],
        'categoria': 'Educacion',
        'subcategoria': 'Estudio guiado',
        'hashtags': ['estudio', 'productividad', 'estudiantes'],
        'visibilidad': 'semipublico',
        'destacado': True,
    },
    {
        'titulo': 'Plan de contenidos para redes',
        'slug': 'plan-de-contenidos-para-redes',
        'descripcion': 'Genera una parrilla de publicaciones orientada a objetivos de negocio.',
        'contenido': (
            'Eres estratega de contenidos. Diseña un plan de {frecuencia_publicacion} publicaciones para {marca}. '
            'El publico objetivo es {publico_objetivo} y el objetivo principal es {objetivo_negocio}. '
            'Propone ideas para Instagram, LinkedIn y newsletter con tono {tono_marca}. '
            'Devuelve el resultado en tabla con idea, gancho, CTA y formato.'
        ),
        'variables_json': [
            {'nombre': 'frecuencia_publicacion', 'descripcion': 'Numero de publicaciones', 'valor_defecto': '12'},
            {'nombre': 'marca', 'descripcion': 'Marca o proyecto', 'valor_defecto': 'NoNoTodoEsCódigo.com'},
            {'nombre': 'publico_objetivo', 'descripcion': 'Publico al que te diriges', 'valor_defecto': 'profesionales que quieren aprender IA'},
            {'nombre': 'objetivo_negocio', 'descripcion': 'Objetivo comercial', 'valor_defecto': 'captar leads cualificados'},
            {'nombre': 'tono_marca', 'descripcion': 'Tono de comunicacion', 'valor_defecto': 'cercano y experto'},
        ],
        'categoria': 'Marketing',
        'subcategoria': 'Contenido',
        'hashtags': ['marketing', 'rrss', 'copywriting'],
        'visibilidad': 'publico',
        'destacado': True,
    },
    {
        'titulo': 'Escribe un email de venta sin sonar agresivo',
        'slug': 'escribe-email-de-venta-sin-sonar-agresivo',
        'descripcion': 'Convierte una oferta en un email claro, humano y persuasivo.',
        'contenido': (
            'Redacta un email de venta para ofrecer {producto_servicio} a {tipo_cliente}. '
            'Debe sonar {tono}, incluir problema, solucion, prueba o argumento de confianza y CTA final. '
            'Longitud maxima: {max_palabras} palabras. Evita lenguaje agresivo o demasiado comercial.'
        ),
        'variables_json': [
            {'nombre': 'producto_servicio', 'descripcion': 'Producto o servicio a vender', 'valor_defecto': 'una mentoria de IA aplicada'},
            {'nombre': 'tipo_cliente', 'descripcion': 'Cliente ideal', 'valor_defecto': 'autonomos digitales'},
            {'nombre': 'tono', 'descripcion': 'Tono del email', 'valor_defecto': 'humano y profesional'},
            {'nombre': 'max_palabras', 'descripcion': 'Numero maximo de palabras', 'valor_defecto': '180'},
        ],
        'categoria': 'Marketing',
        'subcategoria': 'Email marketing',
        'hashtags': ['email', 'ventas', 'copywriting'],
        'visibilidad': 'semipublico',
        'destacado': False,
    },
    {
        'titulo': 'Refactoriza este codigo Python',
        'slug': 'refactoriza-este-codigo-python',
        'descripcion': 'Pide mejoras de estructura, legibilidad y mantenibilidad.',
        'contenido': (
            'Actua como senior Python developer. Analiza el siguiente codigo:\n\n{codigo}\n\n'
            '1. Explica los problemas detectados.\n'
            '2. Propone una version refactorizada.\n'
            '3. Justifica los cambios.\n'
            '4. Indica riesgos o pruebas recomendadas.\n\n'
            'Prioriza claridad, bajo acoplamiento y nombres legibles.'
        ),
        'variables_json': [
            {'nombre': 'codigo', 'descripcion': 'Codigo Python a revisar', 'valor_defecto': 'def saludo(n):\n print("hola",n)'},
        ],
        'categoria': 'Programacion',
        'subcategoria': 'Python',
        'hashtags': ['python', 'refactor', 'codigo'],
        'visibilidad': 'publico',
        'destacado': True,
    },
    {
        'titulo': 'Generador de tests unitarios',
        'slug': 'generador-de-tests-unitarios',
        'descripcion': 'Obtiene casos de prueba utiles a partir de una funcion o modulo.',
        'contenido': (
            'Escribe tests unitarios para este codigo en {framework_test}:\n\n{codigo}\n\n'
            'Incluye casos felices, edge cases y errores esperados. '
            'Si faltan datos para testear bien, lista primero los supuestos.'
        ),
        'variables_json': [
            {'nombre': 'framework_test', 'descripcion': 'Framework de testing', 'valor_defecto': 'pytest'},
            {'nombre': 'codigo', 'descripcion': 'Codigo a probar', 'valor_defecto': 'def suma(a, b):\n    return a + b'},
        ],
        'categoria': 'Programacion',
        'subcategoria': 'Testing',
        'hashtags': ['testing', 'pytest', 'codigo'],
        'visibilidad': 'semipublico',
        'destacado': False,
    },
    {
        'titulo': 'Resume una reunion y saca tareas',
        'slug': 'resume-una-reunion-y-saca-tareas',
        'descripcion': 'Convierte notas caoticas en decisiones y acciones claras.',
        'contenido': (
            'Resume estas notas de reunion:\n\n{notas_reunion}\n\n'
            'Devuelve: 1) resumen ejecutivo, 2) decisiones tomadas, 3) tareas con responsable y fecha, '
            '4) bloqueos o riesgos. Si faltan datos, marca las incertidumbres.'
        ),
        'variables_json': [
            {'nombre': 'notas_reunion', 'descripcion': 'Notas o transcripcion de la reunion', 'valor_defecto': 'Se reviso el lanzamiento. Ana prepara la landing. Pablo revisa pricing.'},
        ],
        'categoria': 'Productividad',
        'subcategoria': 'Reuniones',
        'hashtags': ['productividad', 'reuniones', 'resumen'],
        'visibilidad': 'publico',
        'destacado': False,
    },
    {
        'titulo': 'Checklist para lanzar un curso online',
        'slug': 'checklist-para-lanzar-un-curso-online',
        'descripcion': 'Checklist completa para no dejar huecos antes de publicar un curso.',
        'contenido': (
            'Crea una checklist accionable para lanzar un curso online sobre {tema_curso}. '
            'Divide en preproduccion, grabacion, plataforma, ventas y post-lanzamiento. '
            'El nivel de detalle debe ser {nivel_detalle} y el formato en tabla con prioridad.'
        ),
        'variables_json': [
            {'nombre': 'tema_curso', 'descripcion': 'Tema del curso', 'valor_defecto': 'IA para emprendedores'},
            {'nombre': 'nivel_detalle', 'descripcion': 'Profundidad de la respuesta', 'valor_defecto': 'alto'},
        ],
        'categoria': 'Negocio digital',
        'subcategoria': 'Cursos online',
        'hashtags': ['lanzamiento', 'cursos', 'negocio-digital'],
        'visibilidad': 'semipublico',
        'destacado': True,
    },
    {
        'titulo': 'Analiza un prompt y mejoralo',
        'slug': 'analiza-un-prompt-y-mejoralo',
        'descripcion': 'Meta prompt para mejorar otros prompts antes de usarlos.',
        'contenido': (
            'Evalua este prompt:\n\n{prompt_original}\n\n'
            'Indica sus puntos debiles, ambiguedades y huecos de contexto. '
            'Despues reescribelo para que sea mas claro, especifico y facil de ejecutar. '
            'Entrega una version tecnica y otra version facil para usuarios no tecnicos.'
        ),
        'variables_json': [
            {'nombre': 'prompt_original', 'descripcion': 'Prompt que quieres mejorar', 'valor_defecto': 'Hazme un plan de marketing'},
        ],
        'categoria': 'IA aplicada',
        'subcategoria': 'Prompt engineering',
        'hashtags': ['prompts', 'ia', 'mejora'],
        'visibilidad': 'privado',
        'destacado': True,
    },
]


def _get_or_create_categoria(CategoriaPrompt, nombre):
    return CategoriaPrompt.objects.get_or_create(
        slug=slugify(nombre),
        defaults={
            'nombre': nombre,
            'descripcion': f'Categoria de ejemplos para {nombre.lower()}.',
            'activa': True,
        },
    )[0]


def _get_or_create_subcategoria(SubcategoriaPrompt, categoria, nombre):
    return SubcategoriaPrompt.objects.get_or_create(
        categoria=categoria,
        slug=slugify(nombre),
        defaults={
            'nombre': nombre,
            'descripcion': f'Subcategoria {nombre.lower()} de la biblioteca de prompts.',
            'activa': True,
        },
    )[0]


def _get_or_create_hashtag(HashtagPrompt, nombre):
    limpio = nombre.lstrip('#')
    return HashtagPrompt.objects.get_or_create(
        slug=slugify(limpio),
        defaults={'nombre': limpio},
    )[0]


def seed_prompt_examples(apps, schema_editor):
    CategoriaPrompt = apps.get_model('prompts', 'CategoriaPrompt')
    SubcategoriaPrompt = apps.get_model('prompts', 'SubcategoriaPrompt')
    HashtagPrompt = apps.get_model('prompts', 'HashtagPrompt')
    Prompt = apps.get_model('prompts', 'Prompt')

    for item in PROMPTS:
        categoria = _get_or_create_categoria(CategoriaPrompt, item['categoria'])
        subcategoria = _get_or_create_subcategoria(SubcategoriaPrompt, categoria, item['subcategoria'])

        prompt, _ = Prompt.objects.update_or_create(
            slug=item['slug'],
            defaults={
                'titulo': item['titulo'],
                'descripcion': item['descripcion'],
                'contenido': item['contenido'],
                'variables_json': item['variables_json'],
                'categoria': categoria,
                'subcategoria': subcategoria,
                'visibilidad': item['visibilidad'],
                'publicado': True,
                'destacado': item['destacado'],
            },
        )
        tags = [_get_or_create_hashtag(HashtagPrompt, nombre) for nombre in item['hashtags']]
        prompt.hashtags.set(tags)


def unseed_prompt_examples(apps, schema_editor):
    Prompt = apps.get_model('prompts', 'Prompt')
    Prompt.objects.filter(slug__in=[item['slug'] for item in PROMPTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('prompts', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_prompt_examples, unseed_prompt_examples),
    ]