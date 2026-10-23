from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.users.access import can, effective_plan
from .models import (
    AnswerOption, AssessmentSession, ModuleResult, NexusAIOperation, NexusGoalLink,
    NexusModality, NexusVersion, Question, UserAnswer,
)


AI_MESSAGE = (
    'Interpretación y planes IA no disponibles hasta integrar Energía Ducky y reserva '
    'de servicios individuales. Coste desconocido, no cero. No se ha enviado contenido '
    'al proveedor ni reservado energía. El resultado calculado sigue accesible.'
)


class NexusConflict(Exception):
    pass


def _decimal(value, label):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValidationError(f'{label}: número no válido.') from error
    if not number.is_finite():
        raise ValidationError(f'{label}: el número debe ser finito.')
    return number


def editorial_snapshot(version):
    essential = (
        'purpose', 'audience', 'instructions', 'authorship', 'provenance', 'authorization',
        'foundation', 'limitations', 'change_reason',
    )
    if any(not getattr(version, key).strip() for key in essential) or not version.editor_id or not version.reviewed_on:
        raise ValidationError('Faltan finalidad, audiencia, instrucciones, procedencia, autorización, '
                              'fundamento, limitaciones, responsable, fecha o motivo de revisión.')
    if not isinstance(version.definition, dict):
        raise ValidationError('La definición debe ser un objeto JSON.')
    definition = version.definition
    if definition.get('purchased_report_scope', 'basic') not in ('basic', 'standard', 'extended'):
        raise ValidationError('El alcance adquirido debe ser basic, standard o extended.')
    if version.entitlement_code and not version.entitlement_code.startswith('nexus_access_'):
        raise ValidationError('Solo se aceptan derechos de acceso nexus_access_; no unidades de generación IA.')
    from .original_scoring import METHOD
    if definition.get('method') == METHOD:
        from .original_assessments import original_snapshot
        return original_snapshot(version)
    if definition.get('method') != 'weighted_mean_0_100_v1':
        raise ValidationError('Método admitido: weighted_mean_0_100_v1; no se ejecuta código.')
    dimensions = definition.get('dimensions')
    rules = definition.get('questions')
    if not isinstance(dimensions, dict) or not dimensions or not isinstance(rules, dict) or not rules:
        raise ValidationError('Faltan dimensiones o reglas explícitas por pregunta.')
    if len(rules) > 200:
        raise ValidationError('Máximo técnico: 200 preguntas por versión.')
    questions = list(Question.objects.filter(
        module__assessments=version.assessment, active=True, module__active=True,
    ).select_related('module').prefetch_related('options').distinct())
    if not questions or {str(q.pk) for q in questions} != set(rules):
        raise ValidationError('Las reglas deben identificar exactamente las preguntas activas de esta evaluación.')
    snapshot_dimensions = {}
    for code, dimension in dimensions.items():
        if not isinstance(dimension, dict) or any(
            not isinstance(dimension.get(key), str) or not dimension[key].strip()
            for key in ('label', 'brief', 'explanation', 'recommendation', 'steps')
        ):
            raise ValidationError('Cada dimensión necesita etiqueta, explicación breve/ampliada, recomendación y pasos aprobados.')
        minimum = dimension.get('minimum_answers')
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
            raise ValidationError('Define un mínimo explícito de respuestas por dimensión.')
        snapshot_dimensions[code] = dict(dimension)
    frozen_questions = []
    for question in questions:
        rule = rules[str(question.pk)]
        if not isinstance(rule, dict) or rule.get('dimension') not in dimensions:
            raise ValidationError('Cada pregunta necesita una dimensión aprobada.')
        if not isinstance(rule.get('required'), bool) or not isinstance(rule.get('reverse'), bool):
            raise ValidationError('Define required y reverse como booleanos por pregunta.')
        weight = _decimal(rule.get('weight'), 'Ponderación')
        if not 0 < weight <= 100:
            raise ValidationError('Ponderación admitida: mayor que cero y hasta 100.')
        options = list(question.options.all())
        if question.question_type not in (Question.TYPE_LIKERT, Question.TYPE_MULTIPLE) or len(options) < 2:
            raise ValidationError('Solo escala de acuerdo o elección única con al menos dos opciones explícitas.')
        if any(not 0 <= option.value <= 100 for option in options):
            raise ValidationError('Los valores de opciones deben estar entre 0 y 100.')
        frozen_questions.append({
            'id': question.pk, 'text': question.text, 'dimension': rule['dimension'],
            'module_id': question.module_id, 'weight': str(weight),
            'reverse': rule['reverse'], 'required': rule['required'],
            'options': [{'id': option.pk, 'text': option.text, 'value': str(option.value)} for option in options],
        })
    for code, dimension in dimensions.items():
        count = sum(q['dimension'] == code for q in frozen_questions)
        if count < dimension['minimum_answers']:
            raise ValidationError('El mínimo por dimensión supera sus preguntas disponibles.')
    return {
        'schema': 1, 'code': version.code, 'version': version.version, 'mode': version.mode,
        'title': version.title, 'family': version.family.name,
        'method': definition['method'], 'dimensions': snapshot_dimensions, 'questions': frozen_questions,
        'purpose': version.purpose, 'instructions': version.instructions, 'limitations': version.limitations,
        'authorship': version.authorship, 'provenance': version.provenance, 'authorization': version.authorization,
        'foundation': version.foundation, 'validation_evidence': version.validation_evidence,
        'is_fixture': version.is_fixture, 'duration_minutes': version.duration_minutes,
        'reviewed_on': str(version.reviewed_on),
    }


def access(actor, version):
    if not can(actor, 'view_registered_content').allowed:
        return False, 'registration_required'
    if (version.status != NexusVersion.Status.PUBLISHED or not version.family.active or version.is_fixture
            or not NexusModality.objects.filter(code=version.mode, active=True).exists()):
        return False, 'content_pending'
    required = {'basic': 'free', 'essential': 'plus', 'advanced': 'pro', 'premium': 'pro'}[version.mode]
    if version.own_content and can(actor, 'view_plan_content', required_plan=required).allowed:
        return True, f'plan:{effective_plan(actor)}'
    if version.entitlement_code and can(
        actor, 'access_entitlement', entitlement=version.entitlement_code,
    ).allowed:
        return True, f'purchase:{version.entitlement_code}'
    return False, 'plan_or_purchase_required'


def _scope(actor):
    return {'free': 'basic', 'plus': 'standard', 'pro': 'extended'}.get(effective_plan(actor), 'basic')


def _lock(actor):
    if not can(actor, 'view_registered_content').allowed:
        raise PermissionDenied('Se requiere una cuenta activa.')
    get_user_model().objects.select_for_update().get(pk=actor.pk)


def require_owner(actor, session):
    if session.candidate_id != actor.pk:
        raise PermissionDenied('Intento privado de otra cuenta.')
    if not can(actor, 'view_registered_content').allowed:
        raise PermissionDenied('Cuenta inactiva.')


@transaction.atomic
def start(actor, version):
    _lock(actor)
    current = AssessmentSession.objects.filter(
        candidate=actor, editorial_version=version, status=AssessmentSession.STATUS_IN_PROGRESS,
    ).first()
    if current:
        return current
    allowed, basis = access(actor, version)
    if not allowed:
        raise PermissionDenied(basis)
    # The immutable editorial snapshot, not mutable questions, defines this attempt.
    return AssessmentSession.objects.create(
        assessment=version.assessment, candidate=actor, editorial_version=version,
        frozen_definition=version.snapshot, access_basis=basis,
        report_scope=_scope(actor) if not basis.startswith('purchase:') else version.definition.get('purchased_report_scope', 'basic'),
        status=AssessmentSession.STATUS_IN_PROGRESS, started_at=timezone.now(), last_activity=timezone.now(),
    )


def answer_fields(session):
    from django import forms
    fields = {}
    for question in session.frozen_definition['questions']:
        fields[f'q_{question["id"]}'] = forms.ChoiceField(
            label=question['text'], required=False,
            help_text=question.get('original', {}).get('scenario') or '',
            choices=[('', 'Sin responder')] + [(str(o['id']), o['text']) for o in question['options']],
            widget=forms.RadioSelect,
        )
    return fields


def questionnaire_form(session, data=None):
    from django import forms
    fields = answer_fields(session)
    fields['revision'] = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    if session.frozen_definition.get('original_family'):
        fields['timings'] = forms.JSONField(required=False, widget=forms.HiddenInput)
    form_class = type('NexusQuestionnaireForm', (forms.Form,), fields)
    initial = {'revision': session.edit_revision, 'timings': session.answer_timings}
    for answer in session.answers.all():
        initial[f'q_{answer.question_id}'] = str(answer.answer_option_id) if answer.answer_option_id else ''
    return form_class(data, initial=initial)


def calculate(snapshot, answers, timings=None):
    from .original_scoring import METHOD, calculate_original
    if snapshot['method'] == METHOD:
        return calculate_original(snapshot, answers, timings)
    results = []
    for code, dimension in snapshot['dimensions'].items():
        questions = [q for q in snapshot['questions'] if q['dimension'] == code]
        answered = [q for q in questions if str(q['id']) in answers]
        if any(q['required'] and str(q['id']) not in answers for q in questions):
            raise ValidationError('Faltan respuestas obligatorias. Guarda y revisa las pendientes.')
        if len(answered) < dimension['minimum_answers']:
            raise ValidationError(f'Faltan respuestas para la dimensión {dimension["label"]}.')
        numerator, denominator = Decimal(0), Decimal(0)
        for question in answered:
            value = _decimal(answers[str(question['id'])], 'Respuesta')
            if question['reverse']:
                value = Decimal(100) - value
            weight = Decimal(question['weight'])
            numerator += value * weight
            denominator += weight
        results.append({
            'code': code, 'label': dimension['label'],
            'score': str((numerator / denominator).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)),
            'answered': len(answered), 'total': len(questions),
            'brief': dimension['brief'], 'explanation': dimension['explanation'],
            'recommendation': dimension['recommendation'], 'steps': dimension['steps'],
        })
    return {
        'method': snapshot['method'], 'dimensions': results, 'limitations': snapshot['limitations'],
        'validation_evidence': snapshot['validation_evidence'], 'ai_used': False,
        'notice': 'Evaluación orientativa, no diagnóstico ni certificación. Un valor alto no implica mejor persona o profesional.',
    }


@transaction.atomic
def save_answers(actor, session, cleaned, submit=False):
    _lock(actor)
    session = AssessmentSession.objects.select_for_update().get(pk=session.pk, candidate=actor)
    require_owner(actor, session)
    if session.status == AssessmentSession.STATUS_COMPLETED:
        if submit:
            return session
        raise NexusConflict('El intento ya está entregado; las respuestas no se modifican.')
    if session.edit_revision != cleaned['revision']:
        raise NexusConflict('Otra pestaña guardó respuestas. Copia tus cambios y recarga antes de continuar.')
    snapshot = session.frozen_definition
    if snapshot.get('original_family'):
        timings = cleaned.get('timings') or {}
        valid_keys = {str(q['id']) for q in snapshot['questions']}
        if not isinstance(timings, dict) or set(timings) - valid_keys:
            raise ValidationError('Los tiempos no corresponden a preguntas de este intento.')
        for value in timings.values():
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 <= value <= 86400000:
                raise ValidationError('Tiempo de respuesta no válido.')
        session.answer_timings = {**session.answer_timings, **timings}
    values = {}
    for question in snapshot['questions']:
        selected = cleaned.get(f'q_{question["id"]}', '')
        if not selected:
            session.answers.filter(question_id=question['id']).delete()
            session.answer_timings.pop(str(question['id']), None)
            continue
        option = next((o for o in question['options'] if str(o['id']) == str(selected)), None)
        if option is None:
            raise ValidationError('Respuesta no permitida en esta versión.')
        if not Question.objects.filter(pk=question['id']).exists() or not AnswerOption.objects.filter(
            pk=option['id'], question_id=question['id'],
        ).exists():
            raise ValidationError('Falta una referencia histórica. Contacta al responsable editorial; tus respuestas guardadas se conservan.')
        UserAnswer.objects.update_or_create(
            session=session, question_id=question['id'],
            defaults={'answer_option_id': option['id'], 'score': Decimal(option['value'])},
        )
        values[str(question['id'])] = option['value']
    session.edit_revision += 1
    session.last_activity = timezone.now()
    if submit:
        report = calculate(snapshot, values, session.answer_timings)
        session.calculated_report = report
        session.status = AssessmentSession.STATUS_COMPLETED
        session.completed_at = timezone.now()
        for module_id in {q['module_id'] for q in snapshot['questions']}:
            if snapshot.get('original_family'):
                if report['overall_score'] is not None:
                    ModuleResult.objects.update_or_create(
                        session=session, module_id=module_id,
                        defaults={'score': Decimal(report['overall_score'])},
                    )
                continue
            module_questions = [q for q in snapshot['questions'] if q['module_id'] == module_id and str(q['id']) in values]
            numerator = sum(
                (Decimal(100) - Decimal(values[str(q['id'])]) if q['reverse'] else Decimal(values[str(q['id'])]))
                * Decimal(q['weight']) for q in module_questions
            )
            denominator = sum(Decimal(q['weight']) for q in module_questions)
            if denominator:
                ModuleResult.objects.update_or_create(
                    session=session, module_id=module_id,
                    defaults={'score': (numerator / denominator).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)},
                )
    session.save()
    if submit:
        from apps.analytics.models import RegistroActividad
        RegistroActividad.objects.create(
            usuario=actor, tipo='nexus_completed',
            descripcion=f'Evaluación Nexus completada. Intento {session.pk}. Participación, no acreditación de un rasgo.',
            puntos_ganados=0,
        )
    return session


def compare(actor, first, second):
    require_owner(actor, first)
    require_owner(actor, second)
    if effective_plan(actor) not in ('plus', 'pro'):
        raise PermissionDenied('Evolución requiere PLUS o PRO.')
    if first.status != AssessmentSession.STATUS_COMPLETED or second.status != AssessmentSession.STATUS_COMPLETED:
        raise ValidationError('Solo se comparan intentos completados.')
    if first.pk == second.pk or first.frozen_definition != second.frozen_definition:
        raise ValidationError('Solo se comparan intentos distintos de la misma versión, dimensiones y escala.')
    if first.completed_at > second.completed_at:
        raise ValidationError('Selecciona primero el intento anterior y después el posterior.')
    old = {d['code']: d for d in first.calculated_report['dimensions']}
    return [{
        'label': d['label'], 'before': old[d['code']]['score'], 'after': d['score'],
        'change': str(Decimal(d['score']) - Decimal(old[d['code']]['score'])),
    } for d in second.calculated_report['dimensions']]


@transaction.atomic
def delete_attempt(actor, session):
    _lock(actor)
    require_owner(actor, session)
    locked = AssessmentSession.objects.select_for_update().get(pk=session.pk, candidate=actor)
    if locked.editorial_version_id is None:
        raise ValidationError('Este intento pertenece al historial anterior.')
    locked.delete()


@transaction.atomic
def create_goal(actor, session, code, target_date=None):
    _lock(actor)
    require_owner(actor, session)
    if effective_plan(actor) != 'pro':
        raise PermissionDenied('Convertir recomendaciones en objetivos requiere PRO.')
    if session.status != AssessmentSession.STATUS_COMPLETED:
        raise ValidationError('El intento todavía no tiene resultado.')
    dimension = next((d for d in session.calculated_report['dimensions'] if d['code'] == code), None)
    if dimension is None:
        raise ValidationError('Recomendación no válida.')
    existing = NexusGoalLink.objects.filter(session=session, dimension=code).first()
    if existing:
        return existing.goal
    from apps.users.dashboard_models import PersonalGoal
    goal = PersonalGoal(
        user=actor, title=f'Nexus: {dimension["label"]}'[:140],
        description=f'{dimension["recommendation"]}\n\nPasos aprobados:\n{dimension["steps"]}\n'
                    f'Fuente: /nexus/intento/{session.pk}/informe/\nOrigen: plan estándar, editable manualmente.',
        target_date=target_date, tracking_type=PersonalGoal.TrackingType.MANUAL,
    )
    goal.full_clean()
    goal.save()
    NexusGoalLink.objects.create(session=session, dimension=code, goal=goal)
    return goal


@transaction.atomic
def request_ai(actor, session, key, task):
    _lock(actor)
    require_owner(actor, session)
    if effective_plan(actor) not in ('plus', 'pro') or task == 'plan' and effective_plan(actor) != 'pro':
        raise PermissionDenied('La prestación requiere PLUS (interpretación) o PRO (plan).')
    if session.status != AssessmentSession.STATUS_COMPLETED:
        raise ValidationError('Completa primero la evaluación.')
    if task not in ('interpretation', 'plan'):
        raise ValidationError('Acción IA no admitida.')
    previous = NexusAIOperation.objects.filter(user=actor, key=key).first()
    if previous:
        if previous.session_id != session.pk or previous.task != task:
            raise NexusConflict('La clave ya pertenece a otra operación.')
        return previous
    return NexusAIOperation.objects.create(user=actor, session=session, key=key, task=task)
