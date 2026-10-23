"""Bounded PDF text extraction in a separate, timeout-controlled process."""
import json
import logging
import sys
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError


def extract(data, max_pages, max_chars):
    if not data.startswith(b'%PDF-'):
        return {'error': 'El contenido no es un PDF válido.'}
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            return {'error': 'El PDF está protegido. Aporta una copia legible sin contraseña.'}
        root = reader.trailer['/Root']
        if any(key in root for key in ('/OpenAction', '/AA', '/AcroForm')):
            return {'error': 'No se admiten PDF con acciones o formularios activos. Exporta una copia simple.'}
        names = root.get('/Names')
        if names:
            names = names.get_object()
            if '/JavaScript' in names or '/EmbeddedFiles' in names:
                return {'error': 'No se admiten scripts ni archivos adjuntos dentro del PDF.'}
        if not reader.pages:
            return {'error': 'El PDF no contiene páginas.'}
        if len(reader.pages) > max_pages:
            return {'error': f'El PDF supera el límite de {max_pages} páginas.'}
        parts, limitations, total = [], [], 0
        for index, page in enumerate(reader.pages, start=1):
            if page.get('/AA'):
                return {'error': 'No se admiten acciones activas en páginas PDF.'}
            for reference in page.get('/Annots', []):
                annotation = reference.get_object()
                if annotation.get('/Subtype') == '/FileAttachment' or annotation.get('/AA'):
                    return {'error': 'No se admiten adjuntos o acciones activas en anotaciones PDF.'}
                action = annotation.get('/A')
                if action:
                    action = action.get_object()
                    if action.get('/S') not in ('/URI', '/GoTo'):
                        return {'error': 'No se admiten acciones ejecutables o externas en el PDF.'}
                    if action.get('/Next'):
                        return {'error': 'No se admiten cadenas de acciones en el PDF.'}
                    if action.get('/S') == '/URI' and not str(action.get('/URI', '')).lower().startswith(
                        ('https://', 'http://', 'mailto:'),
                    ):
                        return {'error': 'El PDF contiene un enlace con protocolo no admitido.'}
            contents = page.get('/Contents')
            if contents:
                streams = contents.get_object()
                if not isinstance(streams, list):
                    streams = [streams]
                for stream in streams:
                    if len(stream.get_object().get_data()) > 2 * 1024 * 1024:
                        return {'error': 'Una página supera el límite de procesamiento. Exporta un PDF más sencillo.'}
            text = page.extract_text() or ''
            if not text.strip():
                limitations.append(f'Página {index}: no se recuperó texto; puede contener imágenes. No hay OCR integrado.')
            remaining = max(0, max_chars - total)
            if len(text) > remaining:
                parts.append(text[:remaining])
                limitations.append('Extracción parcial: se alcanzó el límite de caracteres. Revisa el contenido.')
                break
            parts.append(text)
            total += len(text) + 1
        if not ''.join(parts).strip():
            limitations.append('No se encontró texto seleccionable. Pega texto o sube una versión legible; no se ejecuta OCR.')
        return {'text': '\n'.join(parts)[:max_chars], 'limitations': limitations}
    except (PdfReadError, PdfStreamError, EOFError, ValueError, TypeError, KeyError, RecursionError):
        return {'error': 'No se pudo leer el PDF: está dañado o utiliza una estructura no compatible.'}


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    raw = sys.stdin.buffer.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        result = {'error': 'El PDF supera el límite de 5 MB.'}
    else:
        result = extract(raw, int(sys.argv[1]), int(sys.argv[2]))
    sys.stdout.write(json.dumps(result, ensure_ascii=True))
