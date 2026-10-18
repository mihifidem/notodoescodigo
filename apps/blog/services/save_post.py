"""save_post.py — guarda el post en la base de datos evitando duplicados."""
import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone

from apps.blog.models import CategoriaBlog, HashtagBlog, PostBlog

logger = logging.getLogger(__name__)

_DEFAULT_CATEGORIA = "Tecnología"


def _obtener_o_crear_categoria(nombre: str) -> CategoriaBlog:
    from django.utils.text import slugify
    nombre = nombre.strip() or _DEFAULT_CATEGORIA
    categoria, created = CategoriaBlog.objects.get_or_create(
        nombre__iexact=nombre,
        defaults={"nombre": nombre, "slug": slugify(nombre), "activa": True},
    )
    if created:
        logger.info("[save_post] Categoría creada: %s", nombre)
    return categoria


def _obtener_o_crear_hashtag(nombre: str) -> HashtagBlog:
    from django.utils.text import slugify
    nombre = nombre.strip().lstrip("#").lower()
    if not nombre:
        return None
    hashtag, _ = HashtagBlog.objects.get_or_create(
        nombre__iexact=nombre,
        defaults={"nombre": nombre, "slug": slugify(nombre)},
    )
    return hashtag


def ya_existe_post(titulo: str, slug: str) -> bool:
    """Comprueba si ya existe un post con ese slug o título similar."""
    if PostBlog.objects.filter(slug=slug).exists():
        return True
    if PostBlog.objects.filter(titulo__iexact=titulo).exists():
        return True
    return False


def guardar_post(
    datos: dict,
    categoria_nombre: str = _DEFAULT_CATEGORIA,
    imagen_bytes: bytes = None,
    publicado: bool = False,
) -> PostBlog | None:
    """
    Crea y guarda un PostBlog en la base de datos.

    Args:
        datos:             Dict devuelto por post_builder.construir_datos_post().
        categoria_nombre:  Nombre de la categoría (se crea si no existe).
        imagen_bytes:      Bytes de la imagen (DALL·E o None).
        publicado:         Si el post debe publicarse de inmediato.

    Returns:
        La instancia PostBlog creada, o None si ya existía.
    """
    titulo = datos["titulo"]
    slug = datos["slug"]

    if ya_existe_post(titulo, slug):
        logger.warning("[save_post] Post duplicado ignorado: %s", titulo[:60])
        return None

    categoria = _obtener_o_crear_categoria(categoria_nombre)

    post = PostBlog(
        titulo=titulo,
        slug=slug,
        resumen=datos.get("resumen", ""),
        contenido_publico=datos.get("contenido_publico", ""),
        contenido_privado=datos.get("contenido_privado", ""),
        categoria=categoria,
        visibilidad=PostBlog.VISIBILIDAD_PUBLICA,
        publicado=publicado,
        destacado=False,
        publicado_en=timezone.now(),
        puntos_lectura=datos.get("puntos_lectura", 10),
        segundos_lectura_requeridos=datos.get("segundos_lectura"),
    )

    # Imagen generada por IA
    if imagen_bytes:
        nombre_imagen = f"{slug}.png"
        post.imagen.save(nombre_imagen, ContentFile(imagen_bytes), save=False)

    post.save()

    # Asignar hashtags
    for nombre_tag in datos.get("hashtags", []):
        tag = _obtener_o_crear_hashtag(nombre_tag)
        if tag:
            post.hashtags.add(tag)

    logger.info("[save_post] Post guardado: %s (id=%d)", titulo[:60], post.pk)
    return post
