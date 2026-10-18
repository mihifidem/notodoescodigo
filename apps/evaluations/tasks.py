import hashlib
from pathlib import Path

from celery import shared_task
from django.utils import timezone

from .models import EntregaEvaluacion, CorreccionEvaluacion, EventoCorreccion
from .services import grade_submission, compute_weighted_total


TEXT_EXTENSIONS = {'.txt', '.md', '.csv', '.py', '.json'}


def _extract_text_from_file(file_field):
    file_path = Path(file_field.path)
    suffix = file_path.suffix.lower()

    if suffix == '.pdf':
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(file_path))
            text_parts = [(page.extract_text() or '') for page in reader.pages]
            extracted = '\n'.join(part.strip() for part in text_parts if part and part.strip())
            if extracted.strip():
                return extracted
        except Exception:
            # Fall through to best-effort decode below if PDF parsing fails.
            pass

    # Basic extraction for text-like files. PDF/DOCX parsers can be added later.
    if suffix in TEXT_EXTENSIONS:
        return file_path.read_text(encoding='utf-8', errors='ignore')

    # Fallback extraction: binary decode best effort to avoid hard failures.
    return file_path.read_bytes().decode('utf-8', errors='ignore')


def _sha256_file(file_field):
    digest = hashlib.sha256()
    with file_field.open('rb') as fh:
        for chunk in fh.chunks():
            digest.update(chunk)
    return digest.hexdigest()


@shared_task(bind=True, max_retries=1)
def procesar_entrega_evaluacion(self, entrega_id):
    entrega = EntregaEvaluacion.objects.select_related('evaluacion', 'alumno').get(pk=entrega_id)
    if entrega.solicita_revision_exhaustiva:
        entrega.estado = EntregaEvaluacion.ESTADO_REVISION
        entrega.procesada_en = timezone.now()
        entrega.save(update_fields=['estado', 'procesada_en'])
        EventoCorreccion.objects.create(
            entrega=entrega,
            evento='EXHAUSTIVE_REVIEW_REQUESTED',
            payload={'motivo': entrega.motivo_revision_exhaustiva},
        )
        return

    entrega.estado = EntregaEvaluacion.ESTADO_PROCESANDO
    entrega.save(update_fields=['estado'])

    EventoCorreccion.objects.create(entrega=entrega, evento='PROCESSING_STARTED', payload={})

    try:
        texto = _extract_text_from_file(entrega.archivo_respuesta)
        entrega.texto_extraido = texto
        entrega.hash_archivo = _sha256_file(entrega.archivo_respuesta)
        entrega.save(update_fields=['texto_extraido', 'hash_archivo'])

        EventoCorreccion.objects.create(
            entrega=entrega,
            evento='TEXT_EXTRACTED',
            payload={'chars': len(texto)},
        )

        graded_payload = grade_submission(entrega, texto)
        puntuacion_total = compute_weighted_total(entrega, graded_payload)
        confianza = graded_payload.get('confidence', 0)
        requiere_revision = bool(confianza < 0.65 or graded_payload.get('insufficient_evidence', False))

        CorreccionEvaluacion.objects.update_or_create(
            entrega=entrega,
            defaults={
                'puntuacion_total': puntuacion_total,
                'puntuacion_por_criterio': {
                    item.get('criterion_id', 'unknown'): item.get('score', 0)
                    for item in graded_payload.get('criteria', [])
                },
                'feedback_global': graded_payload.get('global_feedback', ''),
                'feedback_detallado': graded_payload.get('criteria', []),
                'tipo_correccion': CorreccionEvaluacion.TIPO_IA,
                'evidencias': [
                    {
                        'criterion_id': item.get('criterion_id'),
                        'evidence': item.get('evidence', []),
                    }
                    for item in graded_payload.get('criteria', [])
                ],
                'plan_mejora': graded_payload.get('study_plan', []),
                'confianza_modelo': confianza,
                'modelo_ia': graded_payload.get('model', ''),
                'prompt_version': graded_payload.get('prompt_version', '1.0'),
                'prompt_sistema_usado': graded_payload.get('_system_prompt_used', ''),
                'requiere_revision_humana': requiere_revision,
            },
        )

        entrega.estado = (
            EntregaEvaluacion.ESTADO_REVISION
            if requiere_revision
            else EntregaEvaluacion.ESTADO_CORREGIDA
        )
        entrega.procesada_en = timezone.now()
        entrega.save(update_fields=['estado', 'procesada_en'])

        EventoCorreccion.objects.create(
            entrega=entrega,
            evento='GRADING_FINISHED',
            payload={
                'estado': entrega.estado,
                'score': float(puntuacion_total),
                'confidence': float(confianza),
            },
        )
    except Exception as exc:
        entrega.estado = EntregaEvaluacion.ESTADO_ERROR
        entrega.procesada_en = timezone.now()
        entrega.save(update_fields=['estado', 'procesada_en'])
        EventoCorreccion.objects.create(
            entrega=entrega,
            evento='GRADING_ERROR',
            payload={'error': str(exc)},
        )
        raise
