import re
import unicodedata
from datetime import datetime

from django.core.exceptions import ValidationError
from django.core.validators import validate_email, URLValidator

from .cv_importer import SECTION_PATTERNS


LIMITATIONS = [
    'Revisión orientativa con reglas textuales; no certifica compatibilidad con todos los ATS ni contratación.',
    'No se evalúan diseño visual, columnas, tipografías ni el orden visual del PDF.',
    'Una sección no identificada puede existir con otro nombre o no haberse extraído.',
    'Coincidencias textuales no acreditan dominio; no encontrar evidencia no demuestra falta de competencia.',
    'No hay análisis semántico IA, puntuación de empleabilidad ni probabilidad de contratación.',
]
SENSITIVE = re.compile(
    r'\b(edad|age|sexo|sex|g[eé]nero|gender|raza|race|religi[oó]n|religion|'
    r'estado civil|marital|nacionalidad|nationality|discapacidad|disability|fotograf[ií]a|foto)\b', re.I,
)
STOP_WORDS = set(
    'de la el los las un una y o en con para por del al se es que su sus como '
    'requisitos requisito requerimos requiere requiere requeridos obligatorio obligatoria '
    'imprescindible deseable valorable valorar experiencia conocimiento conocimientos '
    'nivel capacidad buscamos necesario requerida required must preferred skills experience '
    'the and or with a an of in to for is be have you our we requirements'.split()
)


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c))


def tokens(value):
    return [token.rstrip('.-') for token in re.findall(r'(?<!\w)[\w][\w+#.-]*', normalized(value))]


def analyze_text(text, offer, configuration, source_limitations):
    findings = []

    def add(code, category, severity, description, evidence, recommendation):
        findings.append({
            'code': code, 'category': category, 'severity': severity, 'origin': 'rule',
            'description': description, 'evidence': evidence[:500],
            'recommendation': recommendation,
            'limitation': 'Detección textual; confirma el contexto y la extracción antes de aplicar cambios.',
        })

    lines = text.splitlines()
    if 'sections' in configuration.enabled_rules:
        for section, label in (
            ('summary', 'Presentación profesional'), ('experience', 'Experiencia'),
            ('education', 'Formación'), ('skills', 'Competencias'),
        ):
            matched = next((line for line in lines if SECTION_PATTERNS[section].match(line.strip())), '')
            if not matched:
                add(f'section-{section}', 'Estructura', 'info',
                    f'{label}: sección no identificada por su encabezado.', '',
                    'Comprueba la extracción y utiliza un encabezado claro si procede. '
                    'No es obligatorio tener experiencia laboral ni añadir datos inexistentes.')
    if 'contacts' in configuration.enabled_rules:
        candidates = re.findall(r'[^\s<>]+@[^\s<>]+', text)
        if not candidates:
            add('contact-missing', 'Contacto', 'info', 'Email no identificado en los datos autorizados.', '',
                'Si deseas facilitar contacto, comprueba su visibilidad. No es obligatorio incluir datos sensibles.')
        for candidate in candidates[:30]:
            candidate = candidate.rstrip('.,;')
            try:
                validate_email(candidate)
            except ValidationError:
                add('contact-format-' + candidate, 'Contacto', 'warning',
                    'Posible email con formato no válido.', candidate, 'Revisa el email en el documento original.')
    if 'links' in configuration.enabled_rules:
        for link in re.findall(r'https?://[^\s<>]+', text)[:50]:
            link = link.rstrip('.,;)')
            try:
                URLValidator(schemes=['http', 'https'])(link)
            except ValidationError:
                add('link-format-' + link, 'Enlaces', 'warning', 'Enlace con formato no válido.',
                    link, 'Corrige la URL. No se ha abierto ni comprobado su disponibilidad.')
    if 'dates' in configuration.enabled_rules:
        for match in list(re.finditer(
            r'\b(\d{4}-\d{2}-\d{2})\s*(?:a|to|hasta|–|—| - )\s*(\d{4}-\d{2}-\d{2})\b', text,
        ))[:100]:
            start, end = match.groups()
            try:
                start_date = datetime.strptime(start, '%Y-%m-%d').date()
                end_date = datetime.strptime(end, '%Y-%m-%d').date()
            except ValueError:
                add('date-invalid-' + match.group(), 'Fechas', 'warning', 'Fecha ISO no válida.',
                    match.group(), 'Revisa mes y día en la fuente.')
            else:
                if end_date < start_date:
                    add('date-order-' + match.group(), 'Fechas', 'warning', 'El fin es anterior al inicio.',
                        match.group(), 'Comprueba el intervalo en la fuente; no se corrige automáticamente.')
    if '\ufffd' in text or '\x00' in text:
        add('extraction-characters', 'Extracción', 'warning', 'Caracteres de extracción no legibles.',
            '\ufffd', 'Compara con el original y corrige el texto antes de interpretar ausencias.')
    if len(text.split()) < 30:
        add('limited-text', 'Extracción', 'warning', 'Texto breve: cobertura limitada.',
            '', 'Comprueba que has autorizado y recuperado el contenido relevante; no se asume falta de experiencia.')
    requirements = []
    excluded_sensitive = 0
    cv_tokens = set(tokens(text))
    for raw in re.split(r'[\n;]+', offer):
        raw = raw.strip(' \t-*•')
        if not raw:
            continue
        if SENSITIVE.search(raw):
            excluded_sensitive += 1
            continue
        meaningful = list(dict.fromkeys(t for t in tokens(raw) if t not in STOP_WORDS and len(t) > 1))
        mandatory = bool(re.search(r'\b(obligatori[oa]|imprescindible|requerid[oa]|must|required)\b', raw, re.I))
        desired = bool(re.search(r'\b(deseable|valorable|preferred|preferible)\b', raw, re.I))
        kind = 'Obligatorio explícito' if mandatory else 'Deseable explícito' if desired else 'No clasificado'
        found = [token for token in meaningful if token in cv_tokens]
        if not meaningful:
            state = 'not_evaluable'
        elif len(found) == len(meaningful):
            state = 'explicit'
        elif found:
            state = 'review'
        else:
            state = 'not_found'
        evidence = next((line[:400] for line in lines if found and any(t in tokens(line) for t in found)), '')
        requirements.append({
            'text': raw[:1000], 'type': kind, 'state': state, 'origin': 'rule',
            'keywords': meaningful, 'matched_keywords': found, 'evidence': evidence,
            'explanation': {
                'explicit': 'Se encontraron los términos de la línea; no acredita experiencia ni cumplimiento.',
                'review': 'Coincidencia textual parcial; revisa el significado y aporta evidencia real si procede.',
                'not_found': 'No se encontró evidencia textual; no significa que carezcas de la competencia.',
                'not_evaluable': 'La línea no contiene términos evaluables por estas reglas.',
            }[state],
            'recommendation': 'Revisa el requisito completo y enlázalo solo con hechos y experiencia reales.',
        })
        if len(requirements) >= 100:
            break
    limits = list(LIMITATIONS) + list(source_limitations)
    limits.append('Fechas: solo intervalos ISO AAAA-MM-DD; no se verifica cronología completa ni disponibilidad de enlaces.')
    if offer.strip():
        limits.append('Oferta: líneas de texto aportadas, hasta 100; clasificación solo por marcadores explícitos. Sin interpretación semántica.')
        if len([line for line in re.split(r'[\n;]+', offer) if line.strip()]) > 100:
            limits.append('Comparación parcial: la oferta excede 100 líneas evaluables.')
    else:
        limits.append('Sin oferta: no se ha calculado afinidad ni se han inventado requisitos.')
    if excluded_sensitive:
        limits.append(f'Se excluyeron {excluded_sensitive} líneas con posibles atributos sensibles; no se usan para afinidad.')
    counts = {state: sum(item['state'] == state for item in requirements)
              for state in ('explicit', 'review', 'not_found', 'not_evaluable')}
    return {
        'criteria_version': configuration.criteria_version, 'findings': findings,
        'requirements': requirements, 'requirement_counts': counts,
        'limitations': limits, 'ai_used': False,
        'summary': f'{len(findings)} observaciones de reglas. {len(requirements)} líneas de oferta evaluadas.',
        'coverage': (
            'Texto revisado; reglas activas: ' + ', '.join(configuration.enabled_rules)
            + '. Oferta: coincidencias literales por línea. Sin evaluación visual ni semántica.'
        ),
    }


def compare_results(before, after):
    old = {f['code']: f for f in before.result['findings']}
    new = {f['code']: f for f in after.result['findings']}
    old_evidence = {r['text'] for r in before.result['requirements'] if r['state'] == 'explicit'}
    new_evidence = {r['text'] for r in after.result['requirements'] if r['state'] == 'explicit'}
    return {
        'resolved': [old[key] for key in sorted(old.keys() - new.keys())],
        'new': [new[key] for key in sorted(new.keys() - old.keys())],
        'pending': [new[key] for key in sorted(new.keys() & old.keys())],
        'evidence_added': sorted(new_evidence - old_evidence),
        'evidence_removed': sorted(old_evidence - new_evidence),
        'limitations': [
            'Una observación no detectada en otra revisión no implica una mejora demostrada.',
            'Más longitud o palabras clave no demuestran mejor calidad. Compara las evidencias.',
        ] + before.source.limitations + after.source.limitations,
    }
