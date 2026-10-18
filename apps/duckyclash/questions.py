"""Authoritative question services. Never serialize model snapshots to clients."""

import secrets
import uuid
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .knowledge import (
    DistractorsUnavailable, KnowledgeQuestion, KnowledgeUnavailable, VerifiedDistractor,
    get_knowledge_gateway, validate_candidate,
)
from .models import CombatSession, Duel, DuelAnswer, DuelQuestion, DuelQuestionOptions, ParticipantState
from .policies import require_participant


def question_contract(question):
    if not question.provider or not question.source_id or not question.snapshot.get('gateway'):
        raise KnowledgeUnavailable('Esta pregunta antigua no tiene un contrato de validación verificable.')
    data = question.snapshot
    return KnowledgeQuestion(
        provider=question.provider, identity=question.source_id, revision=question.source_version,
        category_id=data['category_id'], subcategory_id=data['subcategory_id'],
        prompt=data['prompt'], difficulty=data['difficulty'],
        options=tuple(data['original_options']), validation=data['validation'],
        local_version_id=question.version_id,
    )


def gateway_for(question):
    if question.snapshot.get('gateway') != getattr(settings, 'DUCKYCLASH_KNOWLEDGE_GATEWAY', ''):
        raise KnowledgeUnavailable('El proveedor de esta ronda ha cambiado; requiere conciliación.')
    return get_knowledge_gateway()


def require_active(duel):
    if duel.status != Duel.Status.IN_PROGRESS or duel.bank_mode != Duel.BankMode.RESERVE:
        raise ValidationError('El duelo no está en combate activo.')


def effective_deadline(question, options):
    return options.deadline or question.closes_at


def round_deadline(question):
    deadlines = [effective_deadline(question, row) for row in question.player_options.all()]
    return max(deadlines) if deadlines else question.closes_at


def round_closed(question):
    players = set(question.duel.participants.values_list('pk', flat=True))
    answered = set(question.answers.values_list('participant_id', flat=True))
    return (len(players) == 2 and players == answered) or timezone.now() >= round_deadline(question)


def prepare_round(*, duel_id, number, duration_seconds=30):
    """Trusted combat service only: no player endpoint can choose/start a round."""
    if type(number) is not int or number < 1 or type(duration_seconds) is not int or not 1 <= duration_seconds <= 300:
        raise ValidationError('Ronda o duración no válida.')
    duel = Duel.objects.select_related('agreement__offer').get(pk=duel_id)
    session = CombatSession.objects.filter(duel=duel).first()
    if not (session and session.phase == CombatSession.Phase.READY and duel.status == Duel.Status.SCHEDULED):
        require_active(duel)
    existing = DuelQuestion.objects.filter(duel=duel, number=number).first()
    if existing:
        return existing
    gateway = get_knowledge_gateway()
    offer = duel.agreement.offer
    used = set(duel.questions.values_list('provider', 'source_id'))
    history = Counter(DuelQuestion.objects.filter(
        Q(duel__agreement__creator_id__in=[duel.agreement.creator_id, duel.agreement.opponent_id])
        | Q(duel__agreement__opponent_id__in=[duel.agreement.creator_id, duel.agreement.opponent_id]),
    ).values_list('provider', 'source_id'))
    supplied = gateway.candidates(category_id=offer.category_id, subcategory_id=offer.subcategory_id)
    if not isinstance(supplied, list) or any(not isinstance(item, KnowledgeQuestion) for item in supplied):
        raise KnowledgeUnavailable('El catálogo del proveedor no es válido.')
    candidates = [
        item for item in supplied
        if item.category_id == offer.category_id
        and (offer.subcategory_id is None or item.subcategory_id == offer.subcategory_id)
        and (item.provider, item.identity) not in used
    ]
    if not candidates:
        raise KnowledgeUnavailable('No hay preguntas nuevas autorizadas para esta categoría y subcategoría.')
    secrets.SystemRandom().shuffle(candidates)
    candidates.sort(key=lambda item: history[(item.provider, item.identity)])
    item = candidates[0]
    validate_candidate(item, gateway)
    with transaction.atomic():
        duel = Duel.objects.select_for_update().get(pk=duel_id)
        existing = duel.questions.filter(number=number).first()
        if existing:
            return existing
        session = CombatSession.objects.filter(duel=duel).first()
        if session:
            from .engine import verified_reserves
            if (session.phase not in (CombatSession.Phase.READY, CombatSession.Phase.ROUND_RESULT)
                    or not verified_reserves(duel)
                    or (session.deadline and session.deadline > timezone.now())):
                raise ValidationError('La fase del motor no permite preparar esta ronda.')
            if session.phase == CombatSession.Phase.READY:
                if duel.status != Duel.Status.SCHEDULED:
                    raise ValidationError('El duelo no está programado.')
                scheduled = duel.agreement.offer.scheduled_at
                ready_players = list(duel.participants.all())
                now = timezone.now()
                if (now < scheduled or now >= scheduled + timedelta(seconds=session.rules['waiting_seconds'])
                        or len(ready_players) != 2 or not all(
                            p.ready_at and p.last_seen_at and p.last_seen_at + timedelta(
                                seconds=session.rules['presence_seconds'],
                            ) > now for p in ready_players
                        )):
                    raise ValidationError('Los dos jugadores deben estar listos y presentes dentro del plazo.')
            else:
                require_active(duel)
            duration_seconds = session.rules['question_seconds']
        else:
            require_active(duel)
            if duel.rules_snapshot:
                duration_seconds = duel.rules_snapshot['engine']['question_seconds']
        now = timezone.now()
        previous = duel.questions.order_by('-number').first()
        if number != (previous.number + 1 if previous else 1) or (previous and not round_closed(previous)):
            raise ValidationError('Las rondas deben ser consecutivas y no pueden solaparse.')
        if previous and (
            not hasattr(previous, 'damage_resolution') or previous.damage_resolution.status != 'applied'
        ):
            raise ValidationError('La ronda anterior debe resolverse antes de abrir otra.')
        if duel.questions.filter(provider=item.provider, source_id=item.identity).exists():
            raise ValidationError('Esta pregunta ya se ha presentado en el duelo.')
        participants = list(duel.participants.all())
        if {participant.player_id for participant in participants} != {
            duel.agreement.creator_id, duel.agreement.opponent_id,
        }:
            raise ValidationError('La ronda requiere los dos participantes del acuerdo.')
        if any(not 0 < participant.hp <= min(participant.max_hp, duel.initial_hp) for participant in participants):
            raise ValidationError('La ronda requiere dos participantes vivos con HP válidos.')
        options = [{'id': uuid.uuid4().hex, 'text': text} for text in item.options]
        opens_at = now + timedelta(seconds=3) if session else now
        question = DuelQuestion.objects.create(
            duel=duel, number=number, version_id=item.local_version_id,
            provider=item.provider, source_id=item.identity, source_version=item.revision,
            opens_at=opens_at, closes_at=opens_at + timedelta(seconds=duration_seconds),
            snapshot={
                'prompt': item.prompt, 'difficulty': item.difficulty, 'category_id': item.category_id,
                'subcategory_id': item.subcategory_id, 'original_options': list(item.options),
                'validation': item.validation,
                'gateway': settings.DUCKYCLASH_KNOWLEDGE_GATEWAY,
            },
        )
        for participant in participants:
            DuelQuestionOptions.objects.create(question=question, participant=participant, options=options)
        if session:
            from .events import emit
            CombatSession.objects.filter(pk=session.pk).update(
                phase=CombatSession.Phase.COUNTDOWN, question=question, deadline=opens_at, last_error='',
            )
            Duel.objects.filter(pk=duel.pk).update(
                status=Duel.Status.IN_PROGRESS, started_at=duel.started_at or now, revision=duel.revision + 1,
            )
            emit(duel, 'engine.countdown', payload={'round': number, 'deadline': opens_at.isoformat()})
        return question


def public_question(*, actor, question):
    require_participant(actor, question.duel.agreement)
    require_active(question.duel)
    from .engine import require_question_window
    require_question_window(question.duel, question)
    now = timezone.now()
    row = DuelQuestionOptions.objects.get(question=question, participant__player=actor)
    if not question.opens_at <= now < effective_deadline(question, row):
        raise ValidationError('La pregunta no está abierta.')
    if not question.provider or not question.source_id or not question.snapshot.get('gateway'):
        raise ValidationError('Esta pregunta no tiene una presentación pública verificada.')
    hidden = row.hidden_until is not None and row.hidden_until > now
    options = [] if hidden else row.options
    # Strict whitelist shared by HTML/JSON and any future WebSocket or Pygame transport.
    payload = {
        'id': question.pk, 'round': question.number, 'prompt': question.snapshot['prompt'],
        'difficulty': question.snapshot['difficulty'],
        'opens_at': question.opens_at.isoformat(), 'closes_at': effective_deadline(question, row).isoformat(),
        'options': [{'id': option['id'], 'text': option['text']} for option in options],
    }
    if hidden:
        payload['hidden_until'] = row.hidden_until.isoformat()
    return payload


def submit_answer(*, actor, question_id, option_id):
    question = DuelQuestion.objects.select_related('duel__agreement').get(pk=question_id)
    require_participant(actor, question.duel.agreement)
    if isinstance(option_id, str) and CombatSession.objects.filter(duel_id=question.duel_id).exists():
        previous = DuelAnswer.objects.filter(question=question, participant__player=actor).first()
        if previous:
            if previous.answer.get('option_id') != option_id:
                raise ValidationError('No puedes cambiar tu respuesta.')
            return previous
    require_active(question.duel)
    from .engine import require_question_window
    require_question_window(question.duel, question)
    if not isinstance(option_id, str):
        raise ValidationError('Selecciona una opción válida.')
    participant = ParticipantState.objects.get(duel=question.duel, player=actor)
    row = DuelQuestionOptions.objects.get(question=question, participant=participant)
    options = row.options
    selected = next((option['text'] for option in options if option['id'] == option_id), None)
    if selected is None:
        raise ValidationError('La opción no pertenece a esta pregunta y jugador.')
    if DuelAnswer.objects.filter(question=question, participant=participant).exists():
        previous = DuelAnswer.objects.get(question=question, participant=participant)
        if CombatSession.objects.filter(duel=question.duel).exists() and previous.answer.get('option_id') == option_id:
            return previous
        raise ValidationError('Ya has respondido a esta pregunta.')
    now = timezone.now()
    if (participant.hp == 0 or not question.opens_at <= now < effective_deadline(question, row)
            or (row.hidden_until and row.hidden_until > now)):
        raise ValidationError('La pregunta ha cerrado.')
    correct = gateway_for(question).validate(question_contract(question), selected)
    if type(correct) is not bool:
        raise KnowledgeUnavailable('No se ha podido verificar la respuesta.')
    with transaction.atomic():
        duel = Duel.objects.select_for_update().get(pk=question.duel_id)
        previous = DuelAnswer.objects.filter(question=question, participant=participant).first()
        if previous:
            if CombatSession.objects.filter(duel=duel).exists() and previous.answer.get('option_id') == option_id:
                return previous
            raise ValidationError('Ya has respondido a esta pregunta.')
        require_active(duel)
        require_question_window(duel, question)
        row = DuelQuestionOptions.objects.get(question=question, participant=participant)
        now = timezone.now()
        if (not question.opens_at <= now < effective_deadline(question, row)
                or (row.hidden_until and row.hidden_until > now)
                or not any(option['id'] == option_id for option in row.options)
                or hasattr(question, 'damage_resolution')):
            raise ValidationError('La pregunta ha cerrado durante la validación.')
        if DuelAnswer.objects.filter(question=question, participant=participant).exists():
            raise ValidationError('Ya has respondido a esta pregunta.')
        answer = DuelAnswer.objects.create(
            question=question, participant=participant, answer={'option_id': option_id}, is_correct=correct,
        )
        from .events import emit
        emit(duel, 'answer.locked', actor=actor, payload={'round': question.number})
        return answer


def add_verified_distractors(*, question_id, target_player_id, count):
    """Trusted item-effect hook only. Shop must authorize/consume before invoking.

    No public endpoint: this hook does not claim that inventory has been consumed.
    """
    if type(count) is not int or not 1 <= count <= 8:
        raise ValidationError('Número de distractores no válido.')
    question = DuelQuestion.objects.select_related('duel').get(pk=question_id)
    require_active(question.duel)
    contract = question_contract(question)
    gateway = gateway_for(question)
    extras = gateway.distractors(contract, count)
    if (not isinstance(extras, list) or len(extras) != count
            or any(not isinstance(extra, VerifiedDistractor) or not isinstance(extra.text, str)
                   or not extra.text.strip() or len(extra.text) > 2000
                   or not isinstance(extra.evidence, str) or not extra.evidence.strip() for extra in extras)):
        raise DistractorsUnavailable('El proveedor no certificó los distractores solicitados.')
    if any(gateway.validate(contract, extra.text) is not False for extra in extras):
        raise DistractorsUnavailable('No se puede verificar que todos los distractores sean incorrectos.')
    with transaction.atomic():
        duel = Duel.objects.select_for_update().get(pk=question.duel_id)
        require_active(duel)
        row = DuelQuestionOptions.objects.select_for_update().get(
            question=question, participant__player_id=target_player_id,
        )
        if not question.opens_at <= timezone.now() < effective_deadline(question, row) or DuelAnswer.objects.filter(
            question=question, participant=row.participant,
        ).exists():
            raise ValidationError('No se pueden cambiar las opciones de una pregunta cerrada o respondida.')
        normalized = [option['text'].strip().casefold() for option in row.options] + [
            extra.text.strip().casefold() for extra in extras
        ]
        if len(set(normalized)) != len(normalized) or len(normalized) > 12:
            raise DistractorsUnavailable('Distractores repetidos o límite de opciones superado.')
        row.options += [{'id': uuid.uuid4().hex, 'text': extra.text} for extra in extras]
        row.evidence += [extra.evidence for extra in extras]
        row.save()
