"""Local, conservative extraction of common CV fields from searchable PDFs."""
import re
from datetime import date

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from apps.curriculum.models import (
    CurriculumVitae,
    Education,
    PersonalInfo,
    ProfessionalProfile,
    Skill,
    SocialNetwork,
    WorkExperience,
)


MAX_PDF_PAGES = 30
MAX_EXTRACTED_CHARS = 100_000

SECTION_PATTERNS = {
    'summary': re.compile(
        r'^(?:perfil profesional|resumen profesional|sobre m[ií]|acerca de m[ií])\s*:?\s*(.*)$',
        re.IGNORECASE,
    ),
    'experience': re.compile(
        r'^(?:experiencia(?: laboral| profesional)?|trayectoria profesional|'
        r'work experience|professional experience)\s*:?\s*(.*)$',
        re.IGNORECASE,
    ),
    'education': re.compile(
        r'^(?:educaci[oó]n|formaci[oó]n acad[eé]mica|estudios|education)\s*:?\s*(.*)$',
        re.IGNORECASE,
    ),
    'skills': re.compile(
        r'^(?:habilidades|competencias|skills|conocimientos)\s*:?\s*(.*)$',
        re.IGNORECASE,
    ),
    'other': re.compile(
        r'^(?:formaci[oó]n(?: complementaria)?|idiomas|proyectos|certificaciones|cursos|voluntariado|'
        r'logros|intereses|referencias|contacto|datos personales)\s*:?\s*(.*)$',
        re.IGNORECASE,
    ),
}

_DATE_TOKEN = (
    r'(?:(?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{2}|'
    r'(?:ene(?:ro)?|feb(?:rero)?|mar(?:zo)?|abr(?:il)?|may(?:o)?|'
    r'jun(?:io)?|jul(?:io)?|ago(?:sto)?|sep(?:tiembre)?|oct(?:ubre)?|'
    r'nov(?:iembre)?|dic(?:iembre)?)\.?\s+(?:19|20)\d{2}|'
    r'(?:19|20)\d{2})'
)
_DATE_RANGE = re.compile(
    rf'(?P<start>{_DATE_TOKEN})\s*(?:-|–|—|\ba\b|\bhasta\b)\s*'
    rf'(?P<end>{_DATE_TOKEN}|actualidad|presente|actual|current|present)',
    re.IGNORECASE,
)
_MONTH_NUMBERS = {
    'ene': 1, 'enero': 1, 'feb': 2, 'febrero': 2, 'mar': 3, 'marzo': 3,
    'abr': 4, 'abril': 4, 'may': 5, 'mayo': 5, 'jun': 6, 'junio': 6,
    'jul': 7, 'julio': 7, 'ago': 8, 'agosto': 8, 'sep': 9, 'septiembre': 9,
    'oct': 10, 'octubre': 10, 'nov': 11, 'noviembre': 11, 'dic': 12,
    'diciembre': 12,
}


class PdfImportError(ValueError):
    """A user-facing error while reading or extracting a CV PDF."""


def extract_cv_fields(pdf_file):
    """Read a bounded, text-based PDF and return only confidently identified fields."""
    try:
        reader = PdfReader(pdf_file, strict=False)
        if reader.is_encrypted:
            raise PdfImportError('El PDF está protegido con contraseña.')
        if not reader.pages:
            raise PdfImportError('El PDF no contiene páginas.')

        text_parts = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            text_parts.append(page.extract_text() or '')
        text = '\n'.join(text_parts)[:MAX_EXTRACTED_CHARS]
    except PdfImportError:
        raise
    except (PdfReadError, EOFError, ValueError) as exc:
        raise PdfImportError('No se pudo leer el PDF. Comprueba que el archivo no esté dañado.') from exc

    if not text.strip():
        raise PdfImportError(
            'No se encontró texto seleccionable. Los PDF escaneados necesitan OCR y no se pueden importar todavía.'
        )
    return parse_cv_text(text)


def parse_cv_text(text):
    """Extract recognizable profile data and clearly formatted experience/education entries."""
    lines = [line.strip(' \t•·▪◦*-') for line in text.splitlines()]
    lines = [line for line in lines if line]
    flattened = '\n'.join(lines)
    fields = {}

    email_match = re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', flattened)
    phone_match = re.search(
        r'(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{2,4}(?!\w)',
        flattened,
    )
    if email_match:
        fields['email_profesional1'] = email_match.group(0).strip('.,;')
    if phone_match:
        phone = phone_match.group(0).strip(' .-')
        if 9 <= len(re.sub(r'\D', '', phone)) <= 15:
            fields['telefono1'] = phone

    name = _find_name(lines)
    if name:
        name_parts = name.split()
        fields['nombre'] = name_parts[0]
        fields['apellidos'] = ' '.join(name_parts[1:])

    location_match = re.search(
        r'(?im)^\s*(?:ubicaci[oó]n|ciudad|localidad)\s*[:\-]\s*([^\n]+)',
        flattened,
    )
    if location_match:
        location_parts = [part.strip() for part in location_match.group(1).split(',', 1)]
        fields['ciudad'] = location_parts[0][:100]
        if len(location_parts) > 1:
            fields['pais'] = location_parts[1][:100]
    country_match = re.search(r'(?im)^\s*(?:pa[ií]s|country)\s*[:\-]\s*([^\n]+)', flattened)
    if country_match:
        fields['pais'] = country_match.group(1).strip()[:100]

    personal = {
        key: fields[key]
        for key in ('nombre', 'apellidos', 'telefono1', 'email_profesional1', 'ciudad', 'pais')
        if fields.get(key)
    }
    if personal:
        fields = {'personal': personal}
    else:
        fields = {}

    links = {}
    for field, domain in (
        ('linkedin', 'linkedin\\.com'),
        ('github', 'github\\.com'),
        ('twitter', r'(?:twitter\.com|x\.com)'),
    ):
        match = re.search(
            rf'(?i)(?:https?://)?(?:www\.)?{domain}/[^\s,;]+',
            flattened,
        )
        if match:
            url = match.group(0).rstrip('.,;')
            if not url.lower().startswith(('http://', 'https://')):
                url = 'https://' + url
            links[field] = url
    portfolio_match = re.search(
        r'(?im)^\s*(?:portfolio|web personal|sitio web)\s*[:\-]\s*(https?://\S+)',
        flattened,
    )
    if portfolio_match:
        links['portfolio'] = portfolio_match.group(1).rstrip('.,;')
    if links:
        fields['social'] = links

    profession_match = re.search(
        r'(?im)^\s*(?:profesi[oó]n|especialidad|puesto objetivo)\s*[:\-]\s*([^\n]+)',
        flattened,
    )
    summary = _extract_section(lines, 'summary')
    professional = {}
    if profession_match:
        professional['profesion'] = profession_match.group(1).strip()[:200]
    if summary:
        professional['resumen'] = summary[:5000]
    if professional:
        fields['professional'] = professional

    skills_text = _extract_section(lines, 'skills')
    if skills_text:
        skills = []
        for item in re.split(r'[,;|•·\n]', skills_text):
            value = item.strip(' \t-*')
            if value and len(value) <= 100 and value.casefold() not in {s.casefold() for s in skills}:
                skills.append(value)
            if len(skills) == 40:
                break
        if skills:
            fields['skills'] = skills

    experience = _extract_dated_entries(lines, 'experience')
    if experience:
        fields['experience'] = experience

    education = _extract_dated_entries(lines, 'education')
    if education:
        fields['education'] = education

    return fields


def _parse_date(value):
    value = value.strip().rstrip('.')
    numeric = re.fullmatch(r'(?:(0?[1-9]|1[0-2])[/.-])?((?:19|20)\d{2})', value)
    if numeric:
        return date(int(numeric.group(2)), int(numeric.group(1) or 1), 1)

    named = re.fullmatch(
        r'([a-záéíóú]+)\.?\s+((?:19|20)\d{2})',
        value,
        re.IGNORECASE,
    )
    if named:
        month = _MONTH_NUMBERS.get(named.group(1).casefold())
        if month:
            return date(int(named.group(2)), month, 1)
    return None


def _extract_dated_entries(lines, section_key):
    section = _extract_section_lines(lines, section_key)
    entries = []
    current_entry = None
    for line in section:
        date_match = _DATE_RANGE.search(line)
        if date_match:
            header = (line[:date_match.start()] + line[date_match.end():]).strip(' \t|,;·–—-')
            parts = [
                part.strip(' \t,;·')
                for part in re.split(r'\s*\|\s*|\s+[–—]\s+|\s*;\s*', header)
                if part.strip(' \t,;·')
            ]
            if len(parts) < 2:
                current_entry = None
                continue

            start_date = _parse_date(date_match.group('start'))
            is_current = date_match.group('end').casefold() in {
                'actualidad', 'presente', 'actual', 'current', 'present',
            }
            end_date = None if is_current else _parse_date(date_match.group('end'))
            if not start_date or (not is_current and not end_date):
                current_entry = None
                continue

            if section_key == 'experience':
                current_entry = {
                    'puesto': parts[0][:200],
                    'empresa': parts[1][:200],
                    'ubicacion': parts[2][:200] if len(parts) > 2 else '',
                    'fecha_inicio': start_date.isoformat(),
                    'fecha_fin': end_date.isoformat() if end_date else '',
                    'trabajo_actual': is_current,
                    'descripcion': '',
                }
            else:
                current_entry = {
                    'titulo': parts[0][:200],
                    'centro': parts[1][:200],
                    'ubicacion': parts[2][:200] if len(parts) > 2 else '',
                    'fecha_inicio': start_date.isoformat(),
                    'fecha_fin': end_date.isoformat() if end_date else '',
                    'en_curso': is_current,
                }
            entries.append(current_entry)
            if len(entries) >= 20:
                break
        elif current_entry and section_key == 'experience':
            description = current_entry['descripcion']
            addition = line.strip()
            if addition and len(description) < 3000:
                current_entry['descripcion'] = (
                    f'{description}\n{addition}' if description else addition
                )[:3000]
    return entries


def _find_name(lines):
    ignored = (
        '@', 'http', 'www.', 'tel', 'móvil', 'telefono', 'linkedin', 'github',
        'contacto', 'datos personales', 'curriculum', 'currículum', 'experiencia',
        'formación', 'educación', 'habilidades', 'perfil profesional',
    )
    for line in lines[:10]:
        if any(term in line.casefold() for term in ignored) or re.search(r'\d', line):
            continue
        if re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ'’ -]{3,100}", line) and 2 <= len(line.split()) <= 5:
            return line
    return ''


def _extract_section(lines, section_key):
    return '\n'.join(_extract_section_lines(lines, section_key)).strip()


def _extract_section_lines(lines, section_key):
    pattern = SECTION_PATTERNS[section_key]
    stop_patterns = tuple(
        candidate
        for key, candidate in SECTION_PATTERNS.items()
        if key != section_key
    )
    stop_patterns += (SECTION_PATTERNS['other'],)

    collecting = False
    content = []
    for line in lines:
        match = pattern.match(line)
        if match:
            collecting = True
            if match.group(1):
                content.extend(part.strip() for part in match.group(1).splitlines() if part.strip())
            continue
        if collecting and any(candidate.match(line) for candidate in stop_patterns):
            break
        if collecting:
            content.append(line)
    return content


def apply_cv_fields(cv, fields):
    """Fill blank CV fields only; existing user-entered values are never overwritten."""
    changed = {
        'personal': 0,
        'professional': 0,
        'social': 0,
        'skills': 0,
        'experience': 0,
        'education': 0,
    }

    personal_fields = fields.get('personal', {})
    if personal_fields:
        personal, _ = PersonalInfo.objects.get_or_create(cv=cv)
        for key, value in personal_fields.items():
            if value and not getattr(personal, key):
                setattr(personal, key, value)
                changed['personal'] += 1
        if changed['personal']:
            personal.save()

    professional_fields = fields.get('professional', {})
    existing_profile = getattr(cv, 'professional_profile', None)
    if professional_fields and (
        existing_profile
        or (professional_fields.get('profesion') and professional_fields.get('resumen'))
    ):
        profile, created = ProfessionalProfile.objects.get_or_create(
            cv=cv,
            defaults={
                'profesion': professional_fields.get('profesion', ''),
                'resumen': professional_fields.get('resumen', ''),
            },
        )
        if created:
            changed['professional'] = sum(bool(value) for value in professional_fields.values())
        else:
            for key, value in professional_fields.items():
                if value and not getattr(profile, key):
                    setattr(profile, key, value)
                    changed['professional'] += 1
            if changed['professional']:
                profile.save()

    social_fields = fields.get('social', {})
    if social_fields:
        social, _ = SocialNetwork.objects.get_or_create(cv=cv)
        for key, value in social_fields.items():
            if value and not getattr(social, key):
                setattr(social, key, value)
                changed['social'] += 1
        if changed['social']:
            social.save()

    for skill_name in fields.get('skills', []):
        if not Skill.objects.filter(cv=cv, nombre__iexact=skill_name).exists():
            Skill.objects.create(cv=cv, nombre=skill_name, tipo='hard', categoria='h_otro')
            changed['skills'] += 1

    for entry in fields.get('experience', []):
        if WorkExperience.objects.filter(
            cv=cv,
            puesto__iexact=entry.get('puesto', ''),
            empresa__iexact=entry.get('empresa', ''),
            fecha_inicio=entry.get('fecha_inicio'),
        ).exists():
            continue
        WorkExperience.objects.create(
            cv=cv,
            puesto=entry['puesto'],
            empresa=entry['empresa'],
            ubicacion=entry.get('ubicacion', ''),
            fecha_inicio=date.fromisoformat(entry['fecha_inicio']),
            fecha_fin=date.fromisoformat(entry['fecha_fin']) if entry.get('fecha_fin') else None,
            trabajo_actual=entry.get('trabajo_actual', False),
            descripcion=entry.get('descripcion', ''),
        )
        changed['experience'] += 1

    for entry in fields.get('education', []):
        if Education.objects.filter(
            cv=cv,
            titulo__iexact=entry.get('titulo', ''),
            centro__iexact=entry.get('centro', ''),
            fecha_inicio=entry.get('fecha_inicio'),
        ).exists():
            continue
        Education.objects.create(
            cv=cv,
            titulo=entry['titulo'],
            centro=entry['centro'],
            ubicacion=entry.get('ubicacion', ''),
            fecha_inicio=date.fromisoformat(entry['fecha_inicio']),
            fecha_fin=date.fromisoformat(entry['fecha_fin']) if entry.get('fecha_fin') else None,
            en_curso=entry.get('en_curso', False),
        )
        changed['education'] += 1

    return changed
