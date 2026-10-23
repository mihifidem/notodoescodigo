"""Generador de contenido IA — usa OpenAI para reescribir artículos en español."""
import json
import logging

from django.conf import settings

from apps.prompts.internal import render_internal_prompt

logger = logging.getLogger(__name__)


def generar_contenido(titulo_original: str, contenido_original: str) -> dict | None:
    """
    Envía un artículo a OpenAI y devuelve el contenido procesado.

    Returns:
        dict con claves: titulo, resumen, contenido_publico, contenido_privado, hashtags
        o None si hay error.
    """
    if not getattr(settings, "OPENAI_API_KEY", ""):
        logger.error("[ai_generator] OPENAI_API_KEY no configurada.")
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
    except ImportError:
        logger.error("[ai_generator] Librería openai no instalada.")
        return None

    user_msg = render_internal_prompt(
        'blog.user', titulo_original=titulo_original, contenido_original=contenido_original[:4000],
    )
    system_msg = render_internal_prompt('blog.system')

    logger.info("[ai_generator] Procesando: %s", titulo_original[:60])

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=2000,
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
        logger.info("[ai_generator] Contenido generado para: %s", data.get("titulo", "")[:60])
        return data
    except json.JSONDecodeError as exc:
        logger.error("[ai_generator] JSON inválido de OpenAI: %s", exc)
        return None
    except Exception as exc:
        logger.error("[ai_generator] Error en OpenAI: %s", exc)
        return None


def generar_imagen_dall_e(titulo: str) -> bytes | None:
    """
    Genera una imagen ilustrativa con DALL·E 3 basada en el título del post.

    Returns:
        bytes del PNG generado, o None si hay error.
    """
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return None

    try:
        from openai import OpenAI
        import urllib.request
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        prompt = render_internal_prompt('blog.image', titulo=titulo)
        logger.info("[ai_generator] Generando imagen DALL·E para: %s", titulo[:60])

        response = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024",
        )

        image_record = response.data[0]
        if getattr(image_record, 'b64_json', None):
            import base64
            return base64.b64decode(image_record.b64_json)

        image_url = getattr(image_record, 'url', None)
        if not image_url:
            raise RuntimeError('La respuesta de la IA no devolvió una imagen válida.')

        with urllib.request.urlopen(image_url) as resp:  # noqa: S310
            return resp.read()
    except Exception as exc:
        logger.error("[ai_generator] Error generando imagen: %s", exc)
        return None
