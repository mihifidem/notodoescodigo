from django.db import migrations


PROMPTS = [{'key': 'ai_tutor.system',
  'title': 'Tutor educativo',
  'content': 'Eres un tutor educativo inteligente, empático y motivador de la plataforma MihiTraining.\n'
             '\n'
             'Datos del alumno:\n'
             '- Nombre: {nombre}\n'
             '- Nivel: {nivel}\n'
             '- Puntos acumulados: {puntos}\n'
             '- Racha actual: {streak} días\n'
             '\n'
             'Tu misión:\n'
             '1. Responder dudas educativas de forma clara y accesible.\n'
             '2. Recomendar los próximos temas/cursos más adecuados.\n'
             '3. Motivar al alumno con su progreso.\n'
             '4. Sugerir mejoras basadas en los resultados del alumno.\n'
             '\n'
             'Normas:\n'
             '- Responde siempre en español.\n'
             '- Sé conciso pero completo.\n'
             '- Usa ejemplos prácticos cuando sea posible.\n'
             '- No reveles ni menciones detalles técnicos internos de la plataforma.\n',
  'variables': ['nombre', 'nivel', 'puntos', 'streak'],
  'version': '1.0',
  'active': True},
 {'key': 'blog.system',
  'title': 'Blog: redaccion de articulos',
  'content': '\n'
             'Eres un redactor experto en tecnología y educación digital.\n'
             'Tu misión es transformar un artículo técnico en un post de blog profesional en ESPAÑOL.\n'
             '\n'
             'REGLAS:\n'
             '- Responde ÚNICAMENTE con un JSON válido, sin markdown ni texto extra.\n'
             '- El contenido debe ser 100% original, no una traducción literal.\n'
             '- Usa un tono cercano, motivador y didáctico.\n'
             '- contenido_publico: introducción gancho + 2-3 secciones principales (min 300 palabras).\n'
             '- contenido_privado: análisis en profundidad, ejemplos avanzados, consejos pro (min 200 '
             'palabras).\n'
             '- hashtags: entre 3 y 6 etiquetas relevantes sin el símbolo #.\n'
             '\n'
             'FORMATO DE RESPUESTA:\n'
             '{\n'
             '  "titulo": "Título optimizado SEO en español (max 80 caracteres)",\n'
             '  "resumen": "Resumen atractivo de 2-3 frases (max 200 caracteres)",\n'
             '  "contenido_publico": "Contenido principal en Markdown",\n'
             '  "contenido_privado": "Contenido premium en Markdown",\n'
             '  "hashtags": ["tag1", "tag2", "tag3"]\n'
             '}\n',
  'variables': [],
  'version': '1.0',
  'active': True},
 {'key': 'blog.user',
  'title': 'Blog: articulo de origen',
  'content': 'TÍTULO ORIGINAL: {titulo_original}\n\nCONTENIDO ORIGINAL:\n{contenido_original}',
  'variables': ['titulo_original', 'contenido_original'],
  'version': '1.0',
  'active': True},
 {'key': 'blog.image',
  'title': 'Blog: imagen ilustrativa',
  'content': "Ilustración digital moderna y profesional para un artículo de blog titulado: '{titulo}'. "
             'Estilo minimalista, colores vibrantes, sin texto.',
  'variables': ['titulo'],
  'version': '1.0',
  'active': True},
 {'key': 'users.ducky_avatar',
  'title': 'Avatar personalizado Ducky',
  'content': 'Genera un avatar personalizado de "Ducky", la mascota de noNoTodoEsCódigo.com.\n'
             '\n'
             'REFERENCIAS:\n'
             '- Imagen A: fotografía del usuario. Utilízala únicamente para identificar y trasladar rasgos '
             'físicos visibles.\n'
             '- Imagen B: imagen oficial de Ducky. Esta imagen es la referencia PRINCIPAL y OBLIGATORIA para '
             'el estilo visual, diseño, composición, proporciones, colores y personalidad del personaje.\n'
             '\n'
             'OBJETIVO\n'
             'Crear una versión personalizada de Ducky que mantenga exactamente la misma identidad visual '
             'que la mascota original, pero incorporando algunos rasgos físicos reconocibles del usuario.\n'
             '\n'
             'El resultado debe parecer un miembro de una colección oficial de avatares de '
             'noNoTodoEsCódigo.com.\n'
             '\n'
             'Ducky debe seguir siendo claramente un pato ilustrado.\n'
             'NO crear un retrato humano.\n'
             'NO transformar literalmente la cara humana en un pato.\n'
             'NO alterar la identidad visual esencial de Ducky.\n'
             '\n'
             'ESTILO VISUAL\n'
             'Seguir fielmente la Imagen B.\n'
             'Mantener:\n'
             '- ilustración cartoon/vectorial limpia\n'
             '- formas simples y redondeadas\n'
             '- contornos gruesos y oscuros\n'
             '- colores sólidos y vivos\n'
             '- sombreado mínimo y suave\n'
             '- apariencia moderna de mascota tecnológica\n'
             '- composición frontal y simétrica\n'
             '- diseño amigable y reconocible incluso a tamaño pequeño\n'
             '\n'
             'Evitar:\n'
             '- realismo\n'
             '- estilo fotográfico\n'
             '- render 3D\n'
             '- aspecto Pixar\n'
             '- textura realista de plumas\n'
             '- exceso de detalle\n'
             '- anatomía humana\n'
             '- caricatura exagerada\n'
             '\n'
             'DUCKY\n'
             'Conservar siempre:\n'
             '- cuerpo amarillo de pato\n'
             '- cabeza redondeada\n'
             '- pico naranja\n'
             '- ojos grandes y expresivos\n'
             '- expresión simpática\n'
             '- gafas cuando formen parte del diseño base\n'
             '- cuerpo compacto\n'
             '- contorno oscuro característico\n'
             '- portátil delante del personaje\n'
             '- símbolo de programación </> visible y centrado en el portátil\n'
             '\n'
             'El portátil y el símbolo </> forman parte de la identidad visual del avatar y deben mantenerse '
             'en todas las generaciones.\n'
             '\n'
             'PERSONALIZACIÓN DEL USUARIO\n'
             'Analiza únicamente los rasgos físicos claramente visibles en la Imagen A.\n'
             'Traslada de forma simplificada al personaje:\n'
             'CABELLO\n'
             '- color\n'
             '- longitud\n'
             '- volumen\n'
             '- textura\n'
             '- peinado\n'
             '- raya\n'
             '- flequillo\n'
             '- rizos u ondas\n'
             '- entradas o ausencia de cabello cuando sean evidentes\n'
             '\n'
             'El cabello debe interpretarse utilizando formas gráficas simples compatibles con el estilo de '
             'Ducky.\n'
             '\n'
             'OJOS\n'
             '- adaptar el color de ojos cuando sea claramente visible\n'
             '- mantener siempre el diseño grande, simple y expresivo de los ojos de Ducky\n'
             '\n'
             'GAFAS\n'
             'Si el usuario lleva gafas:\n'
             '- reproducir aproximadamente la forma\n'
             '- grosor\n'
             '- color\n'
             '- estilo general\n'
             '\n'
             'Simplificarlas para mantener el estilo cartoon de Ducky.\n'
             '\n'
             'Si el usuario no lleva gafas, no añadir gafas salvo que sean una característica imprescindible '
             'e invariable del diseño oficial de Ducky.\n'
             '\n'
             'CEJAS Y VELLO FACIAL\n'
             'Si son claramente visibles y ayudan a reconocer al usuario:\n'
             '- incorporar cejas\n'
             '- barba\n'
             '- bigote\n'
             '- perilla\n'
             '\n'
             'Representarlos mediante formas muy simples.\n'
             'No crear pelo facial realista.\n'
             '\n'
             'TONALIDAD DE PIEL\n'
             'Utilizar la tonalidad visible de la piel únicamente como referencia secundaria para pequeñas '
             'zonas antropomórficas si el diseño lo permite.\n'
             'No sustituir el color amarillo característico de Ducky.\n'
             'Ducky debe continuar siendo principalmente amarillo.\n'
             '\n'
             'RASGOS FACIALES\n'
             'Se pueden trasladar de forma muy sutil:\n'
             '- forma general de la mirada\n'
             '- expresión\n'
             '- cejas\n'
             '- sonrisa\n'
             '- algunos rasgos visuales distintivos\n'
             '\n'
             'Nunca reproducir una cara humana sobre el pato.\n'
             '\n'
             'REGLA DE PRIORIDAD\n'
             'Cuando exista conflicto entre parecerse al usuario y mantener la identidad de Ducky, priorizar '
             'SIEMPRE la identidad visual de Ducky.\n'
             '\n'
             'Orden de prioridad:\n'
             '1. Identidad visual de Ducky.\n'
             '2. Estilo gráfico de la Imagen B.\n'
             '3. Silueta, proporciones y composición.\n'
             '4. Cabello del usuario.\n'
             '5. Gafas.\n'
             '6. Ojos.\n'
             '7. Cejas o vello facial.\n'
             '8. Otros rasgos físicos secundarios.\n'
             '\n'
             'COMPOSICIÓN\n'
             '- formato cuadrado 1:1\n'
             '- personaje centrado\n'
             '- vista frontal\n'
             '- cabeza completamente visible\n'
             '- parte superior del cuerpo visible\n'
             '- portátil en primer plano\n'
             '- símbolo </> centrado en el portátil\n'
             '- fondo blanco, gris muy claro o fondo simple idéntico al estilo de referencia\n'
             '- suficiente margen alrededor del personaje para permitir un recorte circular\n'
             '- sin elementos innecesarios alrededor\n'
             '\n'
             'CONSISTENCIA ENTRE USUARIOS\n'
             'Todas las imágenes generadas deben parecer variantes del MISMO personaje.\n'
             '\n'
             'Entre usuarios solo deberían cambiar principalmente:\n'
             '- cabello\n'
             '- ojos\n'
             '- gafas\n'
             '- cejas\n'
             '- barba o bigote\n'
             '- pequeños rasgos visuales distintivos\n'
             '\n'
             'No cambiar entre generaciones:\n'
             '- anatomía de Ducky\n'
             '- color amarillo principal\n'
             '- forma del pico\n'
             '- proporciones generales\n'
             '- estilo gráfico\n'
             '- portátil\n'
             '- símbolo </>\n'
             '- grosor de los contornos\n'
             '- composición general\n'
             '- lenguaje visual\n'
             '\n'
             'RESTRICCIONES\n'
             'No inferir ni representar:\n'
             '- etnia\n'
             '- nacionalidad\n'
             '- religión\n'
             '- profesión\n'
             '- personalidad\n'
             '- orientación sexual\n'
             '- ideología\n'
             '- edad exacta\n'
             '- género\n'
             '\n'
             'salvo que exista algún elemento visual estrictamente necesario para reproducir el aspecto '
             'visible del usuario.\n'
             '\n'
             'No exagerar rasgos físicos.\n'
             'No generar caricaturas ofensivas.\n'
             'No añadir accesorios que no aparezcan en la foto.\n'
             'No añadir textos, nombres, logotipos ni marcas.\n'
             '\n'
             'RESULTADO FINAL\n'
             'Un avatar limpio, simpático y reconocible de Ducky personalizado con los rasgos visuales '
             'principales del usuario, manteniendo una identidad gráfica completamente consistente con el '
             'resto de avatares de noNoTodoEsCódigo.com.\n'
             '\n'
             'Debe dar la sensación de:\n'
             '“Este es Ducky, pero personalizado para este usuario”.\n'
             '\n'
             'No:\n'
             '“Esta persona ha sido convertida literalmente en un pato”.\n'
             '\n'
             'Usuario: {nombre_usuario}\n'
             'Nombre para la referencia visual: {nombre_usuario}\n',
  'variables': ['nombre_usuario'],
  'version': '1.0',
  'active': True},
 {'key': 'evaluations.fallback.default',
  'title': 'Correccion: sistema por defecto',
  'content': 'Eres un evaluador academico estricto y justo. Debes corregir solo con base en el enunciado, la '
             'rubrica y el texto del alumno. No inventes informacion. Devuelve exclusivamente JSON valido.',
  'variables': [],
  'version': '1.0',
  'active': True},
 {'key': 'evaluations.fallback.OBJ_TEST',
  'title': 'Correccion: test con penalizacion',
  'content': 'Eres un evaluador academico estricto y justo. Debes corregir solo con base en el enunciado, la '
             'rubrica y el texto del alumno. No inventes informacion. Devuelve exclusivamente JSON valido.\n'
             '\n'
             'REGLA OBLIGATORIA DE CALCULO PARA TEST CON PENALIZACION:\n'
             '  puntuacion_bruta = (aciertos × valor_acierto) - (errores × penalizacion_por_error)\n'
             '  Las preguntas en blanco NO puntuan ni penalizan.\n'
             '  NUNCA omitas la resta de la penalizacion. Si hay 0 errores la resta es 0.\n'
             '  Ejemplo: 10 aciertos × 0.5 = 5.0 ; 10 errores × 0.17 = 1.7 ; puntuacion_bruta = 5.0 - 1.7 = '
             '3.3\n'
             '  Incluye siempre en el JSON el campo "test_stats" con {aciertos, errores, en_blanco, '
             'valor_acierto, penalizacion_por_error, puntuacion_bruta}.\n'
             '  El campo overall_score debe coincidir exactamente con puntuacion_bruta calculada con la '
             'formula.',
  'variables': [],
  'version': '1.0',
  'active': True},
 {'key': 'evaluations.rules',
  'title': 'Correccion: reglas de la respuesta',
  'content': '[\n'
             '  "No inventes contenido no presente en la entrega.",\n'
             '  "Usa los criterios y pesos de la rubrica.",\n'
             '  "Cada criterio debe incluir evidencias literales breves.",\n'
             '  "Devuelve exclusivamente JSON valido."\n'
             ']',
  'variables': [],
  'version': '1.0',
  'active': True},
 {'key': 'evaluations.precomputed_rules',
  'title': 'Correccion: reglas con puntuacion calculada',
  'content': '[\n'
             '  "OBLIGATORIO: El campo overall_score DEBE ser exactamente '
             'precomputed_test_stats.puntuacion_bruta. NO recalcules la nota; solo genera el feedback '
             'narrativo y el plan de mejora.",\n'
             '  "El campo test_stats del JSON de respuesta debe copiar literalmente los valores de '
             'precomputed_test_stats."\n'
             ']',
  'variables': [],
  'version': '1.0',
  'active': True},
 {'key': 'evaluations.response_schema',
  'title': 'Correccion: formato de respuesta',
  'content': '{\n'
             '  "overall_score": "number",\n'
             '  "criteria": [\n'
             '    {\n'
             '      "criterion_id": "string",\n'
             '      "score": "number",\n'
             '      "justification": "string",\n'
             '      "evidence": [\n'
             '        "string"\n'
             '      ],\n'
             '      "improvement": "string"\n'
             '    }\n'
             '  ],\n'
             '  "global_feedback": "string",\n'
             '  "study_plan": [\n'
             '    "string"\n'
             '  ],\n'
             '  "confidence": "number_between_0_and_1",\n'
             '  "insufficient_evidence": "boolean"\n'
             '}',
  'variables': [],
  'version': '1.0',
  'active': True}]


def seed_prompts(apps, schema_editor):
    Prompt = apps.get_model('prompts', 'InternalPrompt')
    Legacy = apps.get_model('evaluations', 'PromptCorreccion')
    alias = schema_editor.connection.alias
    for entry in PROMPTS:
        Prompt.objects.using(alias).get_or_create(
            key=entry['key'], version=entry['version'],
            defaults={field: value for field, value in entry.items() if field not in ('key', 'version')},
        )
    for legacy in Legacy.objects.using(alias).order_by('pk'):
        key = f'evaluations.system.{legacy.scope}'
        origin = f'Importado de PromptCorreccion #{legacy.pk}. '
        if Prompt.objects.using(alias).filter(key=key, description__startswith=origin).exists():
            continue
        version = legacy.version
        if Prompt.objects.using(alias).filter(key=key, version=version).exists():
            version = f'{version}-legacy-{legacy.pk}'
        Prompt.objects.using(alias).get_or_create(
            key=key, version=version,
            defaults={
                'title': f'Correccion: {legacy.scope}',
                'description': origin + legacy.descripcion,
                'content': legacy.system_prompt, 'variables': [], 'active': legacy.activo,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ('prompts', '0005_internalprompt_and_more'),
        ('evaluations', '0006_evaluacion_clave_respuestas'),
    ]
    operations = [migrations.RunPython(seed_prompts, migrations.RunPython.noop)]
