"""
Nexus DNA Scoring Engine
Motor matemático oficial según especificación NTEC_0001 doc 02.
"""
from decimal import Decimal
from django.utils import timezone


# ─── Cálculo de scores ────────────────────────────────────────────────────────

def calcular_score_modulo(session, module):
    """Promedio de respuestas normalizadas (0-100) de un módulo."""
    from .models import UserAnswer
    respuestas = UserAnswer.objects.filter(
        session=session,
        question__module=module,
        question__active=True,
    )
    if not respuestas.exists():
        return Decimal('0')
    total = sum(r.score for r in respuestas)
    return (total / respuestas.count()).quantize(Decimal('0.01'))


def calcular_nexus_score(module_results):
    """
    Fórmula oficial: Σ (score × weight) / Σ weights
    Devuelve un score normalizado 0-100.
    """
    num = Decimal('0')
    den = Decimal('0')
    for r in module_results:
        num += r.score * r.module.weight
        den += r.module.weight
    if den == 0:
        return Decimal('0')
    return (num / den).quantize(Decimal('0.01'))


def _promedio_ponderado(scores_dict, *pares):
    """Helper: promedio ponderado de módulos por código y peso."""
    total = Decimal('0')
    peso_total = Decimal('0')
    for codigo, peso in pares:
        val = Decimal(str(scores_dict.get(codigo, 0)))
        p = Decimal(str(peso))
        total += val * p
        peso_total += p
    if peso_total == 0:
        return Decimal('0')
    return (total / peso_total).quantize(Decimal('0.01'))


def calcular_master_indices(module_results):
    """
    Calcula los 9 Master Intelligence Indices según doc 02.
    """
    s = {r.module.code: r.score for r in module_results}
    return {
        'motivation_score':  _promedio_ponderado(s, ('IDENTITY_MAP', 0.6), ('DRIVE_SYNC', 0.4)),
        'cognitive_score':   _promedio_ponderado(s, ('COGNITYPE', 0.6), ('LEARN_SYNC', 0.4)),
        'emotional_score':   _promedio_ponderado(s, ('EMOTION_SYNC', 0.6), ('BEHAVIOR_MATRIX', 0.4)),
        'leadership_score':  _promedio_ponderado(s, ('LEADERSHIP_SYNC', 0.4), ('DECISION_CORE', 0.2), ('COMMUNICATION_DNA', 0.2), ('TEAM_SYNC', 0.2)),
        'innovation_score':  _promedio_ponderado(s, ('INNOVATION_MIND', 0.6), ('TECH_MIND', 0.4)),
        'future_score':      _promedio_ponderado(s, ('FUTURE_FIT', 0.4), ('ADAPTABILITY_IQ', 0.3), ('TECH_MIND', 0.3)),
        'technology_score':  _promedio_ponderado(s, ('TECH_MIND', 0.7), ('FUTURE_FIT', 0.3)),
        'career_score':      _promedio_ponderado(s, ('CAREER_NAVIGATOR', 0.7), ('DRIVE_SYNC', 0.3)),
        'executive_score':   _promedio_ponderado(s, ('LEADERSHIP_SYNC', 0.3), ('DECISION_CORE', 0.3), ('INNOVATION_MIND', 0.2), ('TECH_MIND', 0.2)),
    }


# ─── Archetype Engine ─────────────────────────────────────────────────────────

_ARCHETIPOS = [
    ('Strategic Builder',     lambda i: i.get('leadership_score', 0) + i.get('cognitive_score', 0)),
    ('Adaptive Innovator',    lambda i: i.get('innovation_score', 0) + i.get('future_score', 0)),
    ('Collaborative Leader',  lambda i: i.get('leadership_score', 0) + i.get('emotional_score', 0)),
    ('Future Navigator',      lambda i: i.get('future_score', 0) + i.get('technology_score', 0)),
    ('Digital Transformer',   lambda i: i.get('technology_score', 0) + i.get('innovation_score', 0)),
    ('Visionary Creator',     lambda i: i.get('innovation_score', 0) + i.get('motivation_score', 0)),
    ('Operational Executor',  lambda i: i.get('cognitive_score', 0) + i.get('career_score', 0)),
    ('Human-Centered Leader', lambda i: i.get('emotional_score', 0) + i.get('motivation_score', 0)),
    ('Enterprise Architect',  lambda i: i.get('leadership_score', 0) + i.get('executive_score', 0)),
    ('Nexus Mastermind',      lambda i: sum(i.values()) / max(len(i), 1)),
]


def determinar_arquetipos(indices_float):
    """Retorna [principal, secundario, complementario] ordenados por afinidad."""
    ranking = sorted(_ARCHETIPOS, key=lambda t: float(t[1](indices_float)), reverse=True)
    return [t[0] for t in ranking[:3]]


# ─── Opciones Likert por defecto ──────────────────────────────────────────────

LIKERT_DEFAULT = [
    {'value': 0,   'label': 'Totalmente en desacuerdo', 'icon': '😞'},
    {'value': 25,  'label': 'En desacuerdo',            'icon': '🙁'},
    {'value': 50,  'label': 'Neutral',                  'icon': '😐'},
    {'value': 75,  'label': 'De acuerdo',               'icon': '🙂'},
    {'value': 100, 'label': 'Totalmente de acuerdo',    'icon': '😄'},
]


# ─── Procesamiento de envío ───────────────────────────────────────────────────

def procesar_envio(session, post_data):
    """
    Guarda respuestas, calcula scores de módulo, Nexus Score y
    actualiza/crea el DNAProfile del candidato.
    Retorna la sesión completada.
    """
    from .models import Question, AnswerOption, UserAnswer, ModuleResult, DNAProfile
    if session.editorial_version_id:
        from django.core.exceptions import ValidationError
        raise ValidationError('Una sesión versionada solo admite entrega mediante el servicio editorial.')

    preguntas = (
        Question.objects
        .filter(module__assessments=session.assessment, module__active=True, active=True)
        .select_related('module')
        .prefetch_related('options')
    )

    for q in preguntas:
        raw = post_data.get(f'q_{q.pk}')
        if raw is None:
            continue

        tiene_opciones = q.options.exists()

        if tiene_opciones:
            # El value del radio es el PK de AnswerOption
            try:
                opt = AnswerOption.objects.get(pk=int(raw), question=q)
                UserAnswer.objects.update_or_create(
                    session=session,
                    question=q,
                    defaults={'answer_option': opt, 'score': opt.value},
                )
            except (AnswerOption.DoesNotExist, ValueError, TypeError):
                pass
        else:
            # El value es numérico directo (Likert default o Scale)
            try:
                score = max(Decimal('0'), min(Decimal('100'), Decimal(str(raw))))
                UserAnswer.objects.update_or_create(
                    session=session,
                    question=q,
                    defaults={'score': score},
                )
            except Exception:
                pass

    # Calcular scores por módulo
    resultados_modulo = []
    for modulo in session.assessment.modules.filter(active=True):
        score = calcular_score_modulo(session, modulo)
        resultado, _ = ModuleResult.objects.update_or_create(
            session=session,
            module=modulo,
            defaults={'score': score},
        )
        resultados_modulo.append(resultado)

    # Nexus Score global
    nexus_score = calcular_nexus_score(resultados_modulo)

    # Master Indices
    indices = calcular_master_indices(resultados_modulo)
    indices_float = {k: float(v) for k, v in indices.items()}

    # Arquetipos
    arquetipos = determinar_arquetipos(indices_float)

    # Crear/actualizar DNAProfile
    DNAProfile.objects.update_or_create(
        user=session.candidate,
        defaults={
            'session': session,
            'nexus_score': nexus_score,
            'primary_archetype':   arquetipos[0] if len(arquetipos) > 0 else '',
            'secondary_archetype': arquetipos[1] if len(arquetipos) > 1 else '',
            'tertiary_archetype':  arquetipos[2] if len(arquetipos) > 2 else '',
            **indices,
        },
    )

    # Marcar sesión como completada
    session.status = session.STATUS_COMPLETED
    session.completed_at = timezone.now()
    session.save(update_fields=['status', 'completed_at'])

    return session
