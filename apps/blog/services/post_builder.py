"""post_builder.py — prepara los datos del post antes de guardarlo."""
import logging
import re
from io import BytesIO

from django.utils.text import slugify

logger = logging.getLogger(__name__)

# Velocidad de lectura media: 180 palabras/minuto
_WPM = 180
# Puntos base por lectura
_PUNTOS_BASE = 10
# Bonus por cada 100 palabras extra (sobre las primeras 300)
_PUNTOS_POR_100_PALABRAS = 2


def _contar_palabras(texto: str) -> int:
    if not texto:
        return 0
    return len(texto.split())


def _calcular_segundos_lectura(contenido_publico: str, contenido_privado: str = "") -> int:
    total = _contar_palabras(contenido_publico) + _contar_palabras(contenido_privado)
    segundos = int((total / _WPM) * 60)
    return max(segundos, 15)


def _calcular_puntos_lectura(contenido_publico: str, contenido_privado: str = "") -> int:
    total_palabras = _contar_palabras(contenido_publico) + _contar_palabras(contenido_privado)
    bonus = max(0, (total_palabras - 300) // 100) * _PUNTOS_POR_100_PALABRAS
    return _PUNTOS_BASE + bonus


def _generar_slug_unico(titulo: str) -> str:
    """Genera un slug a partir del título, garantizando unicidad en DB."""
    from apps.blog.models import PostBlog
    base = slugify(titulo)
    if not base:
        base = "post"
    slug = base
    n = 1
    while PostBlog.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _generar_meta_description(resumen: str, titulo: str) -> str:
    """Genera una meta description SEO-friendly (max 160 caracteres)."""
    texto = resumen or titulo
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto[:157] + "..." if len(texto) > 160 else texto


def construir_datos_post(ai_data: dict, fuente_url: str = "") -> dict:
    """
    Transforma la respuesta de ai_generator en un dict listo para save_post.

    Args:
        ai_data:    Dict devuelto por generar_contenido().
        fuente_url: URL original del artículo (se añade al final del contenido privado).

    Returns:
        Dict con todos los campos necesarios para crear el PostBlog.
    """
    titulo = ai_data.get("titulo", "Post generado automáticamente").strip()
    resumen = ai_data.get("resumen", "").strip()
    contenido_publico = ai_data.get("contenido_publico", "").strip()
    contenido_privado = ai_data.get("contenido_privado", "").strip()
    hashtags = ai_data.get("hashtags", [])

    # Añadir referencia a la fuente al final del contenido privado
    if fuente_url:
        contenido_privado += f"\n\n---\n*Fuente original: {fuente_url}*"

    slug = _generar_slug_unico(titulo)
    segundos = _calcular_segundos_lectura(contenido_publico, contenido_privado)
    puntos = _calcular_puntos_lectura(contenido_publico, contenido_privado)
    meta_description = _generar_meta_description(resumen, titulo)

    # SEO keywords desde hashtags
    seo_keywords = ", ".join(hashtags)

    logger.info(
        "[post_builder] Slug: %s | %d palabras | %ds lectura | %d pts",
        slug,
        _contar_palabras(contenido_publico) + _contar_palabras(contenido_privado),
        segundos,
        puntos,
    )

    return {
        "titulo": titulo,
        "slug": slug,
        "resumen": resumen,
        "contenido_publico": contenido_publico,
        "contenido_privado": contenido_privado,
        "hashtags": hashtags,
        "segundos_lectura": segundos,
        "puntos_lectura": puntos,
        "meta_description": meta_description,
        "seo_keywords": seo_keywords,
    }
