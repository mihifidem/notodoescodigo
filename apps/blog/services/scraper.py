"""Scraper de RSS — obtiene artículos desde Medium u otros feeds."""
import logging
from dataclasses import dataclass, field
from typing import List

import feedparser
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MEDIUM_RSS_URL = "https://medium.com/feed/tag/{keyword}"


@dataclass
class ArticuloRaw:
    titulo: str
    contenido: str
    link: str
    fuente: str = "medium"


def _limpiar_html(html: str) -> str:
    """Extrae texto plano de un fragmento HTML."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n", strip=True)


def obtener_articulos_medium(keyword: str = "python", max_items: int = 5) -> List[ArticuloRaw]:
    """
    Lee el feed RSS de Medium para una keyword y devuelve una lista de ArticuloRaw.

    Args:
        keyword:   Tag de Medium a consultar (p.ej. "python", "django", "ai").
        max_items: Número máximo de artículos a retornar.

    Returns:
        Lista de ArticuloRaw ordenada por fecha descendente.
    """
    url = MEDIUM_RSS_URL.format(keyword=keyword)
    logger.info("[scraper] Leyendo feed: %s", url)

    try:
        feed = feedparser.parse(url)
    except Exception as exc:
        logger.error("[scraper] Error al parsear feed %s: %s", url, exc)
        return []

    if feed.bozo and feed.bozo_exception:
        logger.warning("[scraper] Feed malformado: %s", feed.bozo_exception)

    articulos: List[ArticuloRaw] = []
    for entry in feed.entries[:max_items]:
        titulo = entry.get("title", "").strip()
        link = entry.get("link", "").strip()

        # El contenido puede venir en content[0].value o summary
        raw_html = ""
        if entry.get("content"):
            raw_html = entry["content"][0].get("value", "")
        elif entry.get("summary"):
            raw_html = entry["summary"]

        contenido = _limpiar_html(raw_html)

        if not titulo or not link:
            continue

        articulos.append(ArticuloRaw(titulo=titulo, contenido=contenido, link=link))
        logger.info("[scraper] Artículo obtenido: %s", titulo[:60])

    logger.info("[scraper] Total artículos obtenidos: %d", len(articulos))
    return articulos
