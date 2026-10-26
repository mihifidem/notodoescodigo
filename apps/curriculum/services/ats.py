import hashlib
import json
import logging
import subprocess
import sys
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.users.access import can, effective_plan
from apps.curriculum.models import ATSComparison, ATSConfiguration, ATSOperation, ATSReport, ATSSource
from .ats_rules import analyze_text, compare_results
from .profiles import snapshot_profile


logger = logging.getLogger(__name__)
SCOPE_ORDER = {'basic': 0, 'detail': 1, 'pro': 2}
SCOPE_LABELS = {'basic': 'Básico', 'detail': 'Detallado', 'pro': 'Detallado con priorización y comparativas'}
AI_MESSAGE = (
    'Análisis semántico y reformulación no disponibles: sin proveedor IA ni coste energético aprobado en '
    'Energía Ducky. Coste desconocido, no cero. No se ha llamado al proveedor ni reservado energía. '
    'Las reglas convencionales siguen disponibles.'
)
ENERGY_TOOLS = {'semantic': 'ats-semantic', 'rephrase': 'ats-rephrase'}


def ai_availability(actor, task='semantic'):
    from apps.energy.services import module_gate

    return module_gate(actor, ENERGY_TOOLS.get(task, 'ats-semantic'))


class ATSConflict(Exception):
    pass


class ATSRateLimited(Exception):
    pass


def configuration():
    config = ATSConfiguration.objects.first()
    if config is None:
        raise ValidationError('Falta aplicar la migración de Verificar CV.')
    return config


def access_scope(actor, config):
    plan = effective_plan(actor)
    scope = {'free': 'basic', 'plus': 'detail', 'pro': 'pro'}.get(plan, 'basic')
    basis = f'plan:{plan}'
    for field, candidate in (('detail_entitlement', 'detail'), ('comparison_entitlement', 'pro')):
        code = getattr(config, field)
        if code and SCOPE_ORDER[candidate] > SCOPE_ORDER[scope] and can(
            actor, 'access_entitlement', entitlement=code,
        ).allowed:
            scope, basis = candidate, f'purchase:{code}'
    return scope, basis


def available_scopes(actor, config):
    maximum, _ = access_scope(actor, config)
    return [(key, SCOPE_LABELS[key]) for key in SCOPE_ORDER if SCOPE_ORDER[key] <= SCOPE_ORDER[maximum]]


def report_scope(actor, report):
    current, _ = access_scope(actor, configuration())
    return max((current, report.scope), key=SCOPE_ORDER.get)


def _lock(actor):
    if not can(actor, 'view_registered_content').allowed:
        raise PermissionDenied('Se requiere una cuenta activa.')
    get_user_model().objects.select_for_update().get(pk=actor.pk)


def _frequency(actor, config):
    since = timezone.now() - timedelta(minutes=1)
    count = ATSSource.objects.filter(user=actor, created_at__gte=since).count()
    count += ATSReport.objects.filter(user=actor, created_at__gte=since).count()
    count += ATSOperation.objects.filter(user=actor, created_at__gte=since).exclude(
        task='extract', status='complete',
    ).count()
    if count >= config.requests_per_minute:
        raise ATSRateLimited('Demasiadas peticiones de procesamiento. Espera un minuto.')


def _owner(actor, resource):
    if not can(actor, 'view_owned_resource', resource).allowed:
        raise PermissionDenied('Recurso privado de otra cuenta.')


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def extract_pdf(data, config):
    if len(data) > config.max_pdf_bytes:
        raise ValidationError('El PDF supera el tamaño admitido.')
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'apps.curriculum.services.ats_pdf_worker',
             str(config.max_pages), str(config.max_text_chars)],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=settings.BASE_DIR, timeout=config.processing_seconds, check=False,
        )
    except subprocess.TimeoutExpired as error:
        logger.warning('ats_pdf_extraction_timeout')
        raise ValidationError('El PDF agotó el tiempo de procesamiento. Prueba una copia simple o pega el texto.') from error
    if result.returncode != 0:
        logger.warning('ats_pdf_extraction_worker_failure returncode=%s', result.returncode)
        raise ValidationError('No se pudo procesar el PDF. Aporta una copia simple o pega el texto.')
    try:
        extracted = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        logger.error('ats_pdf_extraction_invalid_worker_response')
        raise ValidationError('La extracción no devolvió un resultado válido.') from error
    if 'error' in extracted:
        raise ValidationError(extracted['error'])
    return extracted['text'], extracted['limitations']


def _profile_text(snapshot, selected_sections):
    headings = {
        'name': 'Nombre', 'profession': 'Titular profesional', 'summary': 'Perfil profesional',
        'experiences': 'Experiencia laboral', 'education': 'Formación académica',
        'training': 'Formación complementaria', 'skills': 'Competencias', 'languages': 'Idiomas',
        'projects': 'Proyectos', 'achievements': 'Logros', 'volunteering': 'Voluntariado',
        'email': 'Email', 'phone': 'Teléfono', 'location': 'Ubicación',
    }
    chunks = []
    for key in selected_sections:
        value = snapshot.get(key)
        if not value or key not in headings:
            continue
        chunks.append(headings[key])
        if isinstance(value, list):
            for item in value:
                chunks.append(' · '.join(str(v) for v in item.values() if v))
        else:
            chunks.append(str(value))
    return '\n'.join(chunks)


def create_source(actor, data):
    if not data.get('pdf'):
        return _create_source(actor, data)
    with transaction.atomic():
        _lock(actor)
        config = configuration()
        if not config.enabled:
            raise ValidationError('Nuevos análisis temporalmente desactivados.')
        _frequency(actor, config)
        attempt = ATSOperation.objects.create(
            user=actor, task='extract', status='running', error_code='', idempotency_key=uuid.uuid4(),
        )
    try:
        source = _create_source(actor, data, check_frequency=False)
    except ValidationError:
        ATSOperation.objects.filter(pk=attempt.pk).update(status='failed', error_code='pdf_extraction_failed')
        raise
    ATSOperation.objects.filter(pk=attempt.pk).update(status='complete')
    return source


@transaction.atomic
def _create_source(actor, data, check_frequency=True):
    _lock(actor)
    config = configuration()
    if not config.enabled:
        raise ValidationError('Nuevos análisis temporalmente desactivados. Tus informes siguen accesibles.')
    if check_frequency:
        _frequency(actor, config)
    profile = data.get('profile')
    revision = data.get('revision')
    original_pdf = None
    limits = []
    if profile:
        _owner(actor, profile)
        if profile.status == 'archived':
            raise ValidationError('El perfil está archivado.')
        if revision and revision.profile_id != profile.pk:
            raise ValidationError('La revisión no pertenece a este perfil.')
        snapshot = revision.snapshot if revision else snapshot_profile(profile)
        text = _profile_text(snapshot, data.get('sections', []))
        method, kind = 'profile_snapshot', 'profile'
        number = revision.revision if revision else profile.revision
        label = f'{profile.nombre} — revisión {number}'
        limits.append('Solo se analizan los campos autorizados del perfil; datos excluidos no se consideran ausentes del CV completo.')
    elif data.get('pdf'):
        pdf = data['pdf']
        pdf.seek(0)
        original_pdf = pdf.read(config.max_pdf_bytes + 1)
        text, limits = extract_pdf(original_pdf, config)
        method, kind, number, label = 'pypdf-text', 'pdf', None, 'PDF privado aportado'
    else:
        text = data['pasted_text']
        method, kind, number, label = 'pasted-text', 'text', None, 'Texto aportado'
    if len(text) > config.max_text_chars:
        raise ValidationError('El texto supera el límite configurado. Reduce los datos autorizados.')
    digest = hashlib.sha256(original_pdf).hexdigest() if original_pdf else _hash(text)
    return ATSSource.objects.create(
        user=actor, title=data['title'], profile=profile, profile_revision=number,
        source_kind=kind, source_label=label, original_pdf=original_pdf, original_hash=digest,
        extracted_text=text, extraction_method=method, limitations=limits, configuration=config,
    )


@transaction.atomic
def run_analysis(actor, source, data):
    _lock(actor)
    _owner(actor, source)
    fingerprint = _hash(json.dumps({
        'source': str(source.pk), 'text': data['reviewed_text'], 'offer': data['offer_text'],
        'url': data['offer_url'], 'scope': data['scope'], 'config': source.configuration_id,
    }, sort_keys=True))
    previous = ATSReport.objects.filter(user=actor, idempotency_key=data['idempotency_key']).first()
    if previous:
        if previous.request_hash != fingerprint:
            raise ATSConflict('La clave de solicitud ya corresponde a otro contenido.')
        return previous
    config = configuration()
    if not config.enabled:
        raise ValidationError('Nuevos análisis temporalmente desactivados.')
    if config.pk != source.configuration_id:
        raise ATSConflict('La configuración cambió. Crea una fuente nueva para revisar los criterios actuales.')
    _frequency(actor, config)
    maximum, basis = access_scope(actor, config)
    if data['scope'] not in SCOPE_ORDER or SCOPE_ORDER[data['scope']] > SCOPE_ORDER[maximum]:
        raise PermissionDenied('El alcance solicitado requiere plan o derecho vigente.')
    text = data['reviewed_text']
    if not text.strip() or len(text) > config.max_text_chars:
        raise ValidationError('Texto vacío o demasiado extenso.')
    result = analyze_text(text, data['offer_text'], config, source.limitations)
    return ATSReport.objects.create(
        user=actor, source=source, configuration=config, reviewed_text=text, content_hash=_hash(text),
        offer_text=data['offer_text'], offer_url=data['offer_url'], scope=data['scope'], access_basis=basis,
        result=result, idempotency_key=data['idempotency_key'], request_hash=fingerprint,
        status='partial' if any('Comparación parcial' in item for item in result['limitations']) or any(
            'Página ' in item or 'parcial' in item or 'No se encontró texto' in item for item in source.limitations
        ) else 'complete',
    )


@transaction.atomic
def compare(actor, before, after, same_cv):
    _lock(actor)
    _owner(actor, before)
    _owner(actor, after)
    if access_scope(actor, configuration())[0] != 'pro':
        raise PermissionDenied('Comparar requiere PRO o un derecho individual vigente configurado.')
    if before.pk == after.pk:
        raise ValidationError('Elige dos informes diferentes.')
    if before.source.source_kind == 'profile' or after.source.source_kind == 'profile':
        if not before.source.profile_id or before.source.profile_id != after.source.profile_id:
            raise ValidationError('Solo se comparan revisiones del mismo perfil.')
    elif not same_cv:
        raise ValidationError('Confirma que las fuentes son versiones del mismo CV.')
    if before.offer_text != after.offer_text or before.offer_url != after.offer_url:
        raise ValidationError('La comparación requiere exactamente la misma oferta y procedencia.')
    if (
        before.configuration.criteria_version != after.configuration.criteria_version
        or before.configuration.enabled_rules != after.configuration.enabled_rules
    ):
        raise ValidationError('Los criterios o reglas no son compatibles. Ejecuta ambos con la misma configuración.')
    comparison, _ = ATSComparison.objects.get_or_create(
        user=actor, before=before, after=after, defaults={'result': compare_results(before, after)},
    )
    return comparison


@transaction.atomic
def request_ai(actor, report, key, task):
    _lock(actor)
    _owner(actor, report)
    if task not in ('semantic', 'rephrase'):
        raise ValidationError('Acción IA no admitida.')
    if task == 'rephrase' and access_scope(actor, configuration())[0] != 'pro':
        raise PermissionDenied('Reformulación requiere PRO o un derecho vigente, además de Energía Ducky.')
    previous = ATSOperation.objects.filter(user=actor, idempotency_key=key).first()
    if previous:
        if previous.report_id != report.pk or previous.task != task:
            raise ATSConflict('La clave pertenece a otra operación.')
        return previous
    _frequency(actor, configuration())
    gate = ai_availability(actor, task)
    return ATSOperation.objects.create(
        user=actor, report=report, idempotency_key=key, task=task, error_code=gate['reason'][:50],
    )


@transaction.atomic
def remove_source(actor, source):
    _lock(actor)
    _owner(actor, source)
    if ATSOperation.objects.filter(report__source=source).exclude(status='blocked').exists():
        raise ValidationError('Hay operaciones pendientes de conciliación. Archiva el informe.')
    source.delete()


@transaction.atomic
def delete_report(actor, report):
    _lock(actor)
    _owner(actor, report)
    if ATSOperation.objects.filter(report=report).exclude(status='blocked').exists():
        raise ValidationError('Hay operaciones pendientes de conciliación. Archiva el informe.')
    source = report.source
    report.delete()
    if not source.reports.exists():
        source.delete()
