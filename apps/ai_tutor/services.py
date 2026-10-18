"""AI service layer — wraps the OpenAI API."""
import httpx
from django.conf import settings


SYSTEM_PROMPT_TEMPLATE = """Eres un tutor educativo inteligente, empático y motivador de la plataforma MihiTraining.

Datos del alumno:
- Nombre: {nombre}
- Nivel: {nivel}
- Puntos acumulados: {puntos}
- Racha actual: {streak} días

Tu misión:
1. Responder dudas educativas de forma clara y accesible.
2. Recomendar los próximos temas/cursos más adecuados.
3. Motivar al alumno con su progreso.
4. Sugerir mejoras basadas en los resultados del alumno.

Normas:
- Responde siempre en español.
- Sé conciso pero completo.
- Usa ejemplos prácticos cuando sea posible.
- No reveles ni menciones detalles técnicos internos de la plataforma.
"""


def _get_client():
    try:
        from openai import OpenAI
    except ImportError:
        return None
    try:
        import truststore
        truststore.inject_into_ssl()
        return OpenAI(api_key=settings.OPENAI_API_KEY)
    except ImportError:
        return OpenAI(
            api_key=settings.OPENAI_API_KEY,
            http_client=httpx.Client(verify=False),
        )


def obtener_respuesta_ia(usuario, pregunta: str, historial=None) -> str:
    """
    Send a question to the AI tutor and return the response text.

    historial: list of MensajeIA objects (ordered by timestamp).
    Falls back to a placeholder message if the OpenAI key is not configured.
    """
    if not settings.OPENAI_API_KEY:
        return (
            'El tutor IA no está configurado en este momento. '
            'Configura OPENAI_API_KEY en tu archivo .env para activarlo.'
        )

    client = _get_client()
    if client is None:
        return 'La librería openai no está instalada. Ejecuta: pip install openai'

    system_msg = SYSTEM_PROMPT_TEMPLATE.format(
        nombre=usuario.get_full_name() or usuario.username,
        nivel=usuario.get_nivel_display(),
        puntos=usuario.puntos,
        streak=usuario.streak,
    )

    messages = [{'role': 'system', 'content': system_msg}]

    if historial:
        for msg in historial:
            messages.append({'role': msg.rol, 'content': msg.contenido})

    messages.append({'role': 'user', 'content': pregunta})

    try:
        response = client.chat.completions.create(
            model='gpt-4o',
            messages=messages,
            max_tokens=800,
            temperature=0.7,
        )
        return response.choices[0].message.content
    except Exception as exc:  # Broad catch to surface a friendly error
        return f'Lo siento, ocurrió un error al contactar al tutor: {exc}'


def recomendar_contenido(usuario):
    """Return the next 5 topics the user has not completed yet."""
    from apps.courses.models import Progreso, Tema, InscripcionCurso
    completados = Progreso.objects.filter(
        usuario=usuario, completado=True
    ).values_list('tema_id', flat=True)

    inscritos = InscripcionCurso.objects.filter(
        usuario=usuario
    ).values_list('curso_id', flat=True)

    return (
        Tema.objects.filter(curso__id__in=inscritos)
        .exclude(id__in=completados)
        .select_related('curso')
        .order_by('curso', 'orden')[:5]
    )
