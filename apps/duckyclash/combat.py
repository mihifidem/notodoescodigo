"""Resolve a closed round from frozen server facts, then atomically apply both HP."""

import logging
from dataclasses import asdict
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from .ecomotor import DamageRequest, EcoMotorUnavailable, FighterResult, FighterStats, get_ecomotor, validate_result
from .characters import character_stats
from .models import (
    CombatSession, Duel, DuelResolution, DuelQuestion, HealthChange, ParticipantState, RoundResolution,
)
from .questions import effective_deadline, require_active, round_closed

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


def damage_request(row):
    data = row.request_payload
    fighters = tuple(FighterResult(
        player_id=item['player_id'], hp=item['hp'], max_hp=item['max_hp'], correct=item['correct'],
        elapsed_ms=item['elapsed_ms'], modifiers=tuple(item['modifiers']),
        stats=FighterStats(**item['stats']),
    ) for item in data['fighters'])
    return DamageRequest(
        key=str(row.operation_key), duel=data['duel'], round=data['round'],
        difficulty=data['difficulty'], fighters=fighters,
    )


@transaction.atomic
def plan_resolution(question_id):
    question = DuelQuestion.objects.select_related('duel__agreement').get(pk=question_id)
    duel = Duel.objects.select_for_update().get(pk=question.duel_id)
    existing = RoundResolution.objects.filter(question=question).first()
    if existing:
        return existing
    require_active(duel)
    session = CombatSession.objects.filter(duel=duel).first()
    if session and (session.question_id != question.pk or session.phase != CombatSession.Phase.ROUND_RESOLVING):
        raise ValidationError('El motor aún no ha cerrado esta ronda.')
    if duel.questions.order_by('-number').first().pk != question.pk:
        raise ValidationError('Solo se puede resolver la ronda vigente.')
    if not round_closed(question):
        raise ValidationError('Deben responder ambos jugadores o terminar el plazo de la ronda.')
    gateway = getattr(settings, 'DUCKYCLASH_ECOMOTOR_GATEWAY', '')
    if not gateway:
        raise EcoMotorUnavailable('Configura EcoMotor antes de capturar la ronda.')
    players = list(duel.participants.order_by('seat'))
    if len(players) != 2:
        raise ValidationError('Faltan participantes.')
    if any(not 0 < player.hp <= min(player.max_hp, duel.initial_hp) for player in players):
        raise ValidationError('La captura requiere dos participantes vivos con HP válidos.')
    from .rules import current_rules
    rules = duel.rules_snapshot or current_rules()
    tie = rules['double_ko']
    limit = rules['max_tiebreaks']
    if tie not in ('draw', 'tiebreak') or type(limit) is not int or not 0 <= limit <= 10:
        raise ValidationError('La política de doble KO no es válida.')
    facts = []
    for player in players:
        row = question.player_options.get(participant=player)
        answer = question.answers.filter(participant=player).first()
        if answer and type(answer.is_correct) is not bool:
            raise ValidationError('La respuesta no tiene corrección servidor verificable.')
        milliseconds = int(((answer.received_at if answer else effective_deadline(question, row)) - question.opens_at).total_seconds() * 1000)
        facts.append(asdict(FighterResult(
            player_id=player.player_id, hp=player.hp, max_hp=min(player.max_hp, duel.initial_hp),
            correct=bool(answer and answer.is_correct), elapsed_ms=max(0, milliseconds),
            modifiers=tuple(question.item_uses.filter(
                effect_snapshot__target_id=player.player_id,
            ).order_by('pk').values_list('item__code', flat=True)),
            stats=character_stats(player.player),
        )))
    return RoundResolution.objects.create(
        question=question, gateway=gateway,
        request_payload={
            'duel': str(duel.session_id), 'round': question.number,
            'difficulty': question.snapshot['difficulty'], 'fighters': facts,
            'double_ko': tie, 'max_tiebreaks': limit,
        },
    )


@transaction.atomic
def claim_calculation(resolution_id):
    row = RoundResolution.objects.select_for_update().get(pk=resolution_id)
    if (row.status == RoundResolution.Status.APPLIED or row.attempts >= MAX_ATTEMPTS
            or row.next_attempt_at > timezone.now()):
        return False
    return RoundResolution.objects.filter(
        pk=row.pk, status=RoundResolution.Status.PENDING, attempts=row.attempts,
        next_attempt_at__lte=timezone.now(),
    ).update(attempts=F('attempts') + 1, next_attempt_at=timezone.now() + timedelta(seconds=30)) == 1


def resolve_round(question_id):
    if connection.in_atomic_block:
        raise RuntimeError('No se debe esperar a EcoMotor dentro de una transacción.')
    row = plan_resolution(question_id)
    if row.status == RoundResolution.Status.APPLIED:
        return row
    if not claim_calculation(row.pk):
        return RoundResolution.objects.get(pk=row.pk)
    request = damage_request(row)
    try:
        if row.gateway != getattr(settings, 'DUCKYCLASH_ECOMOTOR_GATEWAY', ''):
            raise EcoMotorUnavailable('El proveedor de la ronda ha cambiado.')
        result = get_ecomotor(row.gateway).calculate(request)
        validate_result(request, result)
    except (EcoMotorUnavailable, TimeoutError, ConnectionError):
        logger.warning('EcoMotor unavailable for round resolution %s', row.pk)
        RoundResolution.objects.filter(pk=row.pk, status=RoundResolution.Status.PENDING).update(
            last_error='ecomotor_unavailable_or_invalid',
        )
        return RoundResolution.objects.get(pk=row.pk)
    return apply_damage(row.pk, result)


@transaction.atomic
def apply_damage(resolution_id, result):
    row = RoundResolution.objects.select_related('question').get(pk=resolution_id)
    duel = Duel.objects.select_for_update().get(pk=row.question.duel_id)
    row.refresh_from_db()
    if row.status == RoundResolution.Status.APPLIED:
        return row
    require_active(duel)
    request = damage_request(row)
    validate_result(request, result)
    players = list(ParticipantState.objects.select_for_update().filter(duel=duel).order_by('seat'))
    if [player.player_id for player in players] != [fighter.player_id for fighter in request.fighters]:
        raise ValidationError('Los participantes no coinciden con la ronda.')
    for player, fighter in zip(players, request.fighters):
        if player.hp != fighter.hp:
            raise ValidationError('Los HP han cambiado desde la captura de la ronda.')
    after = [max(0, min(fighter.max_hp, fighter.hp - damage)) for fighter, damage in zip(request.fighters, result.damage)]
    for player, hp, damage in zip(players, after, result.damage):
        HealthChange.objects.create(
            participant=player, question=row.question, source_key=f'damage:{row.operation_key}',
            kind='damage', before=player.hp, after=hp, requested=damage,
        )
        ParticipantState.objects.filter(pk=player.pk).update(hp=hp)
    outcome = 'continue'
    winner = None
    draw = False
    if after == [0, 0]:
        previous_ties = RoundResolution.objects.filter(question__duel=duel, outcome='tiebreak').count()
        if row.request_payload['double_ko'] == 'tiebreak' and previous_ties < row.request_payload['max_tiebreaks']:
            outcome = 'tiebreak'
            for player, fighter in zip(players, request.fighters):
                ParticipantState.objects.filter(pk=player.pk).update(hp=fighter.max_hp)
                HealthChange.objects.create(
                    participant=player, question=row.question, source_key=f'tiebreak:{row.operation_key}',
                    kind='tiebreak', before=0, after=fighter.max_hp, requested=fighter.max_hp,
                )
        else:
            draw, outcome = True, 'draw'
    elif 0 in after:
        winner = players[0].player_id if after[0] else players[1].player_id
        outcome = 'victory'
    if outcome in ('victory', 'draw'):
        Duel.objects.filter(pk=duel.pk).update(status=Duel.Status.SETTLEMENT_PENDING, revision=F('revision') + 1)
        duel.refresh_from_db()
        resolution = DuelResolution.objects.create(
            duel=duel, winner_id=winner, is_draw=draw, reason=f'Ronda {row.question.number}: {outcome}',
        )
        from .history import publish_result
        publish_result(resolution)
    else:
        Duel.objects.filter(pk=duel.pk).update(revision=F('revision') + 1)
    RoundResolution.objects.filter(pk=row.pk, status=RoundResolution.Status.PENDING).update(
        status=RoundResolution.Status.APPLIED, response_payload={
            'damage': list(result.damage), 'engine_version': result.engine_version,
        }, outcome=outcome, last_error='', applied_at=timezone.now(),
    )
    from .events import emit
    emit(duel, 'round.resolved', payload={
        'round': row.question.number, 'outcome': outcome,
        'hp': [fighter.max_hp for fighter in request.fighters] if outcome == 'tiebreak' else after,
    })
    session = CombatSession.objects.filter(duel=duel).first()
    if session:
        from .engine import _transition
        terminal = outcome in ('victory', 'draw')
        _transition(
            duel, session, CombatSession.Phase.FINISHED if terminal else CombatSession.Phase.ROUND_RESULT,
            deadline=None if terminal else timezone.now() + timedelta(seconds=session.rules['result_seconds']),
        )
    row.refresh_from_db()
    return row
