import json
import logging
import re
from decimal import Decimal

from django.conf import settings

from .models import CriterioRubrica, PromptCorreccion


def _resolve_system_prompt(tipo_prueba: str) -> tuple[str, str]:
    """
    Returns (system_prompt_text, version) by looking up the active PromptCorreccion in DB.
    Falls back to: scope-specific hardcoded → DEFAULT hardcoded.
    Returns the text AND the version string for audit purposes.
    """
    # 1. Try active DB prompt for the exact tipo_prueba scope
    for scope in (tipo_prueba, PromptCorreccion.SCOPE_DEFAULT):
        try:
            p = PromptCorreccion.objects.get(scope=scope, activo=True)
            return p.system_prompt, p.version
        except PromptCorreccion.DoesNotExist:
            continue

    # 2. Hard-coded fallback
    if tipo_prueba == 'OBJ_TEST':
        return SYSTEM_PROMPT_OBJ_TEST, PROMPT_VERSION
    return SYSTEM_PROMPT, PROMPT_VERSION

PROMPT_VERSION = '1.0'
GRADING_MODEL = 'gpt-4o-mini'
MAX_SUBMISSION_CHARS = 12000

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    'Eres un evaluador academico estricto y justo. '
    'Debes corregir solo con base en el enunciado, la rubrica y el texto del alumno. '
    'No inventes informacion. Devuelve exclusivamente JSON valido.'
)

# Instrucciones especificas de calculo para tests objetivos con penalizacion.
SYSTEM_PROMPT_OBJ_TEST = (
    'Eres un evaluador academico estricto y justo. '
    'Debes corregir solo con base en el enunciado, la rubrica y el texto del alumno. '
    'No inventes informacion. Devuelve exclusivamente JSON valido.\n\n'
    'REGLA OBLIGATORIA DE CALCULO PARA TEST CON PENALIZACION:\n'
    '  puntuacion_bruta = (aciertos × valor_acierto) - (errores × penalizacion_por_error)\n'
    '  Las preguntas en blanco NO puntuan ni penalizan.\n'
    '  NUNCA omitas la resta de la penalizacion. Si hay 0 errores la resta es 0.\n'
    '  Ejemplo: 10 aciertos × 0.5 = 5.0 ; 10 errores × 0.17 = 1.7 ; '
    'puntuacion_bruta = 5.0 - 1.7 = 3.3\n'
    '  Incluye siempre en el JSON el campo "test_stats" con '
    '{aciertos, errores, en_blanco, valor_acierto, penalizacion_por_error, puntuacion_bruta}.\n'
    '  El campo overall_score debe coincidir exactamente con puntuacion_bruta calculada con la formula.'
)


def _get_client():
    try:
        from openai import OpenAI
        return OpenAI(api_key=settings.OPENAI_API_KEY)
    except ImportError:
        return None


def _build_rubric_payload(evaluacion):
    if not hasattr(evaluacion, 'rubrica'):
        return {
            'version': '1.0',
            'nota_maxima': float(evaluacion.max_puntuacion),
            'umbral_aprobado': float(Decimal('5.0')),
            'criterios': [],
        }

    criterios = []
    for criterio in evaluacion.rubrica.criterios.all().order_by('orden', 'id'):
        criterios.append({
            'codigo': criterio.codigo,
            'nombre': criterio.nombre,
            'descripcion': criterio.descripcion,
            'peso': float(criterio.peso),
            'escala_min': float(criterio.escala_min),
            'escala_max': float(criterio.escala_max),
            'obligatorio': criterio.obligatorio,
        })

    return {
        'version': evaluacion.rubrica.version,
        'nota_maxima': float(evaluacion.rubrica.nota_maxima),
        'umbral_aprobado': float(evaluacion.rubrica.umbral_aprobado),
        'criterios': criterios,
    }


def _fallback_result(evaluacion, texto_entrega):
    criterios = CriterioRubrica.objects.filter(rubrica__evaluacion=evaluacion).order_by('orden', 'id')
    criterios_payload = []
    for criterio in criterios:
        criterios_payload.append({
            'criterion_id': criterio.codigo,
            'score': float(criterio.escala_min),
            'justification': 'No se pudo evaluar automaticamente por falta de configuracion IA.',
            'evidence': [],
            'improvement': 'Solicita revision docente para obtener una valoracion completa.',
        })

    return {
        'overall_score': 0,
        'criteria': criterios_payload,
        'global_feedback': (
            'La correccion automatica no esta disponible en este momento. '
            'Tu entrega fue registrada para revision manual.'
        ),
        'study_plan': [
            'Revisa el enunciado y la rubrica antes de reenviar.',
            'Solicita feedback docente para validar mejoras.',
        ],
        'confidence': 0.0,
        'insufficient_evidence': len(texto_entrega.strip()) < 50,
        'model': 'fallback',
        'prompt_version': PROMPT_VERSION,
    }


def _safe_json_load(content):
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find('{')
        end = content.rfind('}')
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start:end + 1])
        raise


def _parse_student_answers(text: str) -> dict:
    """
    Extracts {question_number: answer_letter} from the submission text.
    Handles formats like '1. A', '1) B', '1: C', '1 A', '1-A', 'Pregunta 1: D'.
    Returns {} if fewer than 3 answers are found (unreliable parse).
    """
    patterns = [
        r'(?:pregunta\s+)?(\d+)\s*[.):\-]\s*([A-Da-d])\b',
        r'\b(\d+)\s+([A-Da-d])\b',
    ]
    answers = {}
    for pattern in patterns:
        found = re.findall(pattern, text, re.IGNORECASE)
        if found:
            for num_str, letter in found:
                num = int(num_str)
                if 1 <= num <= 200:  # sanity bound
                    answers[str(num)] = letter.upper()
            if len(answers) >= 3:
                break
    return answers


def _compute_obj_test_stats(clave: dict, student_answers: dict, max_puntuacion: float) -> dict | None:
    """
    Deterministically computes test statistics and score.
    Formula: score = (aciertos * valor_acierto) - (errores * penalizacion)
    penalizacion = valor_acierto / 3  (standard Spanish test penalty)
    Returns None if the answer key is empty.
    """
    n = len(clave)
    if n == 0:
        return None

    valor_acierto = max_puntuacion / n
    penalizacion = valor_acierto / 3

    aciertos = errores = en_blanco = 0
    for num_str, correct_letter in clave.items():
        student_letter = student_answers.get(str(num_str))
        if student_letter is None:
            en_blanco += 1
        elif student_letter.upper() == correct_letter.upper():
            aciertos += 1
        else:
            errores += 1

    puntuacion_bruta = round((aciertos * valor_acierto) - (errores * penalizacion), 4)
    puntuacion_bruta = max(0.0, puntuacion_bruta)

    return {
        'aciertos': aciertos,
        'errores': errores,
        'en_blanco': en_blanco,
        'total_preguntas': n,
        'valor_acierto': round(valor_acierto, 4),
        'penalizacion_por_error': round(penalizacion, 4),
        'puntuacion_bruta': puntuacion_bruta,
    }


def grade_submission(entrega, texto_entrega):
    evaluacion = entrega.evaluacion
    rubric_payload = _build_rubric_payload(evaluacion)
    submission_text = (texto_entrega or '').strip()
    was_truncated = len(submission_text) > MAX_SUBMISSION_CHARS
    if was_truncated:
        submission_text = submission_text[:MAX_SUBMISSION_CHARS]

    if not settings.OPENAI_API_KEY:
        return _fallback_result(evaluacion, texto_entrega)

    client = _get_client()
    if client is None:
        return _fallback_result(evaluacion, texto_entrega)

    system_prompt_text, resolved_version = _resolve_system_prompt(evaluacion.tipo_prueba)

    # --- OBJ_TEST: deterministic scoring when answer key is available ---
    precomputed_stats = None
    if evaluacion.tipo_prueba == 'OBJ_TEST' and evaluacion.clave_respuestas:
        student_answers = _parse_student_answers(submission_text)
        min_expected = max(3, len(evaluacion.clave_respuestas) // 2)
        if len(student_answers) >= min_expected:
            precomputed_stats = _compute_obj_test_stats(
                evaluacion.clave_respuestas,
                student_answers,
                float(evaluacion.max_puntuacion),
            )
            logger.info(
                'OBJ_TEST deterministic scoring for entrega_id=%s: %s',
                entrega.pk, precomputed_stats,
            )
        else:
            logger.warning(
                'OBJ_TEST: could not parse enough answers for entrega_id=%s '
                '(found %d, expected >=%d). Falling back to full AI grading.',
                entrega.pk, len(student_answers), min_expected,
            )

    user_prompt = {
        'evaluacion': {
            'titulo': evaluacion.titulo,
            'tipo_examen': (
                evaluacion.get_tipo_prueba_display()
                if hasattr(evaluacion, 'get_tipo_prueba_display')
                else (evaluacion.tipo_examen.nombre if evaluacion.tipo_examen_id else '')
            ),
            'alcance_tipo': evaluacion.alcance_tipo,
            'enunciado': evaluacion.enunciado,
            'instrucciones': evaluacion.instrucciones,
            'criterios_a_valorar': evaluacion.criterios_a_valorar,
            'max_puntuacion': float(evaluacion.max_puntuacion),
        },
        'rubrica': rubric_payload,
        'entrega_alumno': submission_text,
        'entrega_alumno_meta': {
            'original_chars': len(texto_entrega or ''),
            'used_chars': len(submission_text),
            'truncated': was_truncated,
        },
        'response_schema': {
            'overall_score': 'number',
            'criteria': [
                {
                    'criterion_id': 'string',
                    'score': 'number',
                    'justification': 'string',
                    'evidence': ['string'],
                    'improvement': 'string',
                }
            ],
            'global_feedback': 'string',
            'study_plan': ['string'],
            'confidence': 'number_between_0_and_1',
            'insufficient_evidence': 'boolean',
        },
        'rules': [
            'No inventes contenido no presente en la entrega.',
            'Usa los criterios y pesos de la rubrica.',
            'Cada criterio debe incluir evidencias literales breves.',
            'Devuelve exclusivamente JSON valido.',
        ],
    }

    # Inject deterministic stats so the AI MUST use the precomputed score.
    if precomputed_stats is not None:
        user_prompt['precomputed_test_stats'] = precomputed_stats
        user_prompt['rules'] += [
            'OBLIGATORIO: El campo overall_score DEBE ser exactamente precomputed_test_stats.puntuacion_bruta. '
            'NO recalcules la nota; solo genera el feedback narrativo y el plan de mejora.',
            'El campo test_stats del JSON de respuesta debe copiar literalmente los valores de precomputed_test_stats.',
        ]

    try:
        response = client.chat.completions.create(
            model=GRADING_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt_text},
                {'role': 'user', 'content': json.dumps(user_prompt, ensure_ascii=False)},
            ],
            response_format={'type': 'json_object'},
            temperature=0.1,
            max_tokens=1600,
        )
        content = response.choices[0].message.content
        data = _safe_json_load(content)
    except Exception as exc:
        logger.exception('OpenAI grading failed for entrega_id=%s: %s', entrega.pk, exc)
        return _fallback_result(evaluacion, texto_entrega)

    data['model'] = GRADING_MODEL
    data['prompt_version'] = resolved_version
    data['_system_prompt_used'] = system_prompt_text

    # Always override overall_score with the Python-computed value (never trust AI arithmetic).
    if precomputed_stats is not None:
        data['overall_score'] = precomputed_stats['puntuacion_bruta']
        data['test_stats'] = precomputed_stats

    return data


def normalize_score(raw_score, escala_min, escala_max):
    if escala_max <= escala_min:
        return Decimal('0')
    score = Decimal(str(raw_score))
    if score < escala_min:
        score = escala_min
    if score > escala_max:
        score = escala_max
    return (score - escala_min) / (escala_max - escala_min)


def compute_weighted_total(entrega, graded_payload):
    criterios = list(entrega.evaluacion.rubrica.criterios.all().order_by('orden', 'id')) if hasattr(entrega.evaluacion, 'rubrica') else []
    criteria_scores = {item.get('criterion_id'): item for item in graded_payload.get('criteria', [])}

    if not criterios:
        return Decimal(str(graded_payload.get('overall_score', 0)))

    note_max = entrega.evaluacion.rubrica.nota_maxima
    total = Decimal('0')

    for criterio in criterios:
        item = criteria_scores.get(criterio.codigo)
        if not item:
            continue
        normalized = normalize_score(item.get('score', 0), criterio.escala_min, criterio.escala_max)
        total += normalized * (criterio.peso / Decimal('100')) * note_max

    return total.quantize(Decimal('0.01'))
