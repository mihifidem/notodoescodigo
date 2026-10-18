"""Authoritative coordinator independent of HTTP, rendering and transport."""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from .combat import resolve_round
from .ecomotor import EcoMotorUnavailable
from .events import emit
from .knowledge import KnowledgeUnavailable
from .models import CombatSession, Duel, DuelResolution, EconomicOperation, ParticipantState
from .policies import require_participant
from .questions import effective_deadline, prepare_round, public_question, round_deadline, round_closed

logger = logging.getLogger(__name__)
Phase = CombatSession.Phase
PUBLIC_EVENT_FIELDS = {
    **{f'engine.{phase}': {'deadline', 'round'} for phase in Phase.values},
    'engine.waiting': set(),
    'player.joined': set(), 'player.left': set(), 'player.disconnected': set(), 'player.ready': set(),
    'answer.locked': {'round'}, 'item.prepared': {'round', 'code', 'target_id'},
    'item.used': {'round', 'code', 'target_id'}, 'round.resolved': {'round', 'outcome', 'hp'},
    'time.changed': {'round', 'target_id'}, 'health.changed': {'round', 'target_id'},
}


def engine_rules():
    from copy import deepcopy
    from .rules import DEFAULT_ENGINE, validate_engine
    rules = {**deepcopy(DEFAULT_ENGINE), **getattr(settings, 'DUCKYCLASH_ENGINE_RULES', {})}
    validate_engine(rules)
    return rules


def verified_reserves(duel):
    players = {duel.agreement.creator_id, duel.agreement.opponent_id}
    holds = list(duel.economic_operations.filter(kind=EconomicOperation.Kind.RESERVE))
    return (
        duel.bank_mode == Duel.BankMode.RESERVE and len(holds) == 2
        and {hold.player_id for hold in holds} == players
        and all(hold.status == EconomicOperation.Status.CONFIRMED and hold.bank_reference for hold in holds)
    )


def require_engine_access(actor, duel):
    # Refresh identity so a stale socket/session cannot bypass deactivation.
    from django.contrib.auth import get_user_model
    user = get_user_model().objects.get(pk=actor.pk)
    require_participant(user, duel.agreement)
    return user


def _session(duel):
    session = CombatSession.objects.filter(duel=duel).first()
    if session:
        return session
    if duel.status != Duel.Status.SCHEDULED or not verified_reserves(duel):
        raise ValidationError('El Ring requiere las dos reservas bancarias verificadas.')
    session = CombatSession.objects.create(
        duel=duel, rules=duel.rules_snapshot['engine'] if duel.rules_snapshot else engine_rules(),
    )
    emit(duel, 'engine.waiting')
    return session


def _transition(duel, session, phase, *, deadline=None, error=''):
    CombatSession.objects.filter(pk=session.pk).update(phase=phase, deadline=deadline, last_error=error)
    session.phase, session.deadline, session.last_error = phase, deadline, error
    Duel.objects.filter(pk=duel.pk).update(revision=F('revision') + 1)
    emit(duel, f'engine.{phase}', payload={'deadline': deadline.isoformat() if deadline else None})


def require_question_window(duel, question):
    session = CombatSession.objects.filter(duel=duel).first()
    if session and (session.question_id != question.pk or session.phase != Phase.QUESTION_ACTIVE):
        raise ValidationError('Esta fase no admite respuestas.')


def item_window(duel, question, code):
    session = CombatSession.objects.filter(duel=duel).first()
    if not session:
        return False  # Historical internal test/round contracts retain their active-only window.
    now = timezone.now()
    if session.question_id != question.pk:
        raise ValidationError('El objeto no pertenece a la ronda vigente.')
    countdown = session.phase == Phase.COUNTDOWN and now < question.opens_at
    active = session.phase == Phase.QUESTION_ACTIVE and now >= question.opens_at
    if code in ('NUBE', 'ACELERADOR', 'MULTIPLICADOR'):
        if not countdown:
            raise ValidationError('Prepara los ataques durante la cuenta atrás.')
    elif code == 'BOTIQUIN':
        if session.phase not in session.rules['healing_windows'] or not (countdown or active):
            raise ValidationError('El Botiquín no está disponible en esta ventana.')
    elif not active:
        raise ValidationError('Escudo y Pócima requieren una pregunta activa.')
    return countdown


class DuelEngine:
    def __init__(self, duel_id):
        self.duel_id = duel_id

    @transaction.atomic
    def enter(self, actor):
        duel = Duel.objects.select_for_update().select_related('agreement__offer').get(pk=self.duel_id)
        actor = require_engine_access(actor, duel)
        if duel.rules_snapshot.get('ring_access', 'scheduled') == 'disabled':
            raise ValidationError('El acceso al Ring está deshabilitado para este duelo.')
        if timezone.now() < duel.agreement.offer.scheduled_at:
            raise ValidationError('El Ring se desbloquea en la fecha y hora pactadas.')
        session = _session(duel)
        if session.phase == Phase.FINISHED:
            return session
        if (session.phase in (Phase.WAITING_PLAYERS, Phase.READY)
                and timezone.now() >= duel.agreement.offer.scheduled_at + timedelta(seconds=session.rules['waiting_seconds'])):
            raise ValidationError('El plazo de preparación ha terminado.')
        player = ParticipantState.objects.get(duel=duel, player=actor)
        now = timezone.now()
        connected = player.online and player.last_seen_at and (
            player.last_seen_at + timedelta(seconds=session.rules['presence_seconds']) > now
        )
        if connected and player.last_seen_at + timedelta(seconds=2) > now:
            return session
        ParticipantState.objects.filter(pk=player.pk).update(last_seen_at=now, online=True)
        if not connected:
            emit(duel, 'player.joined', actor=actor)
        return session

    @transaction.atomic
    def ready(self, actor):
        session = self.enter(actor)
        duel = Duel.objects.select_for_update().select_related('agreement__offer').get(pk=self.duel_id)
        player = ParticipantState.objects.get(duel=duel, player=actor)
        if player.ready_at:
            return session
        if session.phase != Phase.WAITING_PLAYERS or not verified_reserves(duel):
            raise ValidationError('No se puede cambiar la preparación en esta fase.')
        ParticipantState.objects.filter(pk=player.pk, ready_at__isnull=True).update(ready_at=timezone.now())
        emit(duel, 'player.ready', actor=actor)
        players = list(duel.participants.all())
        if len(players) == 2 and all(p.ready_at for p in players):
            _transition(duel, session, Phase.READY)
        return session

    @transaction.atomic
    def _step(self):
        duel = Duel.objects.select_for_update().select_related('agreement__offer').get(pk=self.duel_id)
        session = CombatSession.objects.filter(duel=duel).first()
        if session is None:
            if (duel.status != Duel.Status.SCHEDULED or not verified_reserves(duel)
                    or timezone.now() < duel.agreement.offer.scheduled_at):
                return None
            session = _session(duel)
        if session.phase == Phase.FINISHED:
            return None
        if duel.bank_mode != Duel.BankMode.RESERVE or duel.status in (
            Duel.Status.FINISHED, Duel.Status.CANCELED, Duel.Status.SETTLEMENT_PENDING,
        ):
            _transition(duel, session, Phase.FINISHED)
            return None
        now = timezone.now()
        players = list(duel.participants.select_related('player').order_by('seat'))
        rules = duel.rules_snapshot
        if (rules.get('abandonment') == 'refund'
                and session.phase not in (Phase.WAITING_PLAYERS, Phase.READY)
                and any(p.last_seen_at and p.last_seen_at + timedelta(
                    seconds=rules['abandonment_seconds'],
                ) <= now for p in players)):
            Duel.objects.filter(pk=duel.pk).update(
                bank_mode=Duel.BankMode.CANCEL, bank_reason='Ausencia continuada: devolución configurada.',
                status=Duel.Status.SETTLEMENT_PENDING, revision=F('revision') + 1,
            )
            _transition(duel, session, Phase.FINISHED, error='abandonment_refund_pending')
            from .economy import enqueue_local_reconciliation
            enqueue_local_reconciliation(duel.pk)
            return None
        for player in players:
            if player.online and (not player.last_seen_at or player.last_seen_at + timedelta(
                seconds=session.rules['presence_seconds'],
            ) <= now):
                ParticipantState.objects.filter(pk=player.pk).update(online=False)
                emit(duel, 'player.disconnected', actor=player.player)
        if session.phase in (Phase.WAITING_PLAYERS, Phase.READY):
            if now >= duel.agreement.offer.scheduled_at + timedelta(seconds=session.rules['waiting_seconds']):
                Duel.objects.filter(pk=duel.pk).update(
                    bank_mode=Duel.BankMode.CANCEL, bank_reason='Ausencia o preparación incompleta.',
                    revision=F('revision') + 1,
                )
                _transition(duel, session, Phase.FINISHED, error='no_show_refund_pending')
                return None
            if session.phase == Phase.READY and len(players) == 2 and all(
                p.ready_at and p.last_seen_at and p.last_seen_at + timedelta(seconds=session.rules['presence_seconds']) > now
                for p in players
            ):
                return ('prepare', 1)
        elif session.phase == Phase.COUNTDOWN:
            if now >= session.question.opens_at:
                _transition(duel, session, Phase.QUESTION_ACTIVE, deadline=round_deadline(session.question))
        elif session.phase == Phase.QUESTION_ACTIVE:
            if round_closed(session.question):
                _transition(duel, session, Phase.ROUND_RESOLVING)
                return ('resolve', session.question_id)
        elif session.phase == Phase.ROUND_RESOLVING:
            return ('resolve', session.question_id)
        elif session.phase == Phase.ROUND_RESULT and now >= session.deadline:
            if session.question.number >= session.rules['max_rounds']:
                Duel.objects.filter(pk=duel.pk).update(status=Duel.Status.SETTLEMENT_PENDING)
                duel.refresh_from_db()
                resolution = DuelResolution.objects.create(duel=duel, is_draw=True, reason='Límite de rondas del servidor.')
                from .history import publish_result
                publish_result(resolution)
                _transition(duel, session, Phase.FINISHED)
            else:
                return ('prepare', session.question.number + 1)
        return None

    def advance(self):
        if connection.in_atomic_block:
            raise RuntimeError('El motor no espera a proveedores dentro de una transacción.')
        action = self._step()
        if action is None:
            return
        kind, value = action
        try:
            if kind == 'prepare':
                prepare_round(duel_id=self.duel_id, number=value)
            else:
                result = resolve_round(value)
                if result.status != result.Status.APPLIED:
                    raise EcoMotorUnavailable('La resolución permanece pendiente.')
        except (KnowledgeUnavailable, EcoMotorUnavailable):
            code = 'knowledge_unavailable' if kind == 'prepare' else 'ecomotor_pending'
            logger.warning('DuelEngine %s requires retry: %s', self.duel_id, code)
            CombatSession.objects.filter(duel_id=self.duel_id).update(last_error=code)

    def snapshot(self, actor, *, after=0):
        if type(after) is not int or after < 0:
            raise ValidationError('Cursor de eventos no válido.')
        # Consistent snapshot + ordered event watermark under the same lock used by writers.
        with transaction.atomic():
            duel = Duel.objects.select_for_update().select_related('agreement__offer').get(pk=self.duel_id)
            require_engine_access(actor, duel)
            session = CombatSession.objects.filter(duel=duel).first()
            now = timezone.now()
            payload = None
            if session and session.question_id and session.phase == Phase.QUESTION_ACTIVE:
                question = session.question
                question.duel = duel
                own_options = question.player_options.get(participant__player=actor)
                if question.opens_at <= now < effective_deadline(question, own_options):
                    payload = public_question(actor=actor, question=question)
            events = list(duel.events.filter(sequence__gt=after).order_by('sequence')[:101])
            phase = session.phase if session else Phase.WAITING_PLAYERS
            deadline = session.deadline if session else duel.agreement.offer.scheduled_at
            if session and session.phase == Phase.QUESTION_ACTIVE:
                deadline = round_deadline(session.question)
            latest = duel.events.order_by('-sequence').first()
            from apps.ducky_arenas.models import UserArenaInventory
            from .items import DEFAULT_RULES, item_rules
            from .models import DuelItemUse
            own = duel.participants.get(player=actor)
            inventory = {
                entry.item.code: entry for entry in UserArenaInventory.objects.select_related('item').filter(
                    profile__user=actor, isEquipped=False, item__isActive=True,
                    item__itemType='COMPLEMENT', item__code__in=DEFAULT_RULES,
                )
            }
            items = []
            uses = list(DuelItemUse.objects.filter(participant=own).select_related('item').order_by('-created_at'))
            own_options = session.question.player_options.get(participant=own) if session and session.question_id else None
            has_answer = session.question.answers.exists() if session and session.question_id else False
            for code in DEFAULT_RULES:
                entry = inventory.get(code)
                enabled = False
                if entry and entry.quantity > 0 and session and session.question_id and own.hp > 0:
                    try:
                        item_window(duel, session.question, code)
                    except ValidationError:
                        pass  # An unavailable activation window is normal presentation state.
                    else:
                        rules = item_rules(code, duel)
                        code_uses = [use for use in uses if use.item.code == code]
                        last_use = code_uses[0] if code_uses else None
                        enabled = (
                            duel.status == Duel.Status.IN_PROGRESS
                            and not has_answer
                            and len(code_uses) < rules['max_uses']
                            and sum(use.question_id == session.question_id for use in code_uses) < rules['max_per_round']
                            and (not last_use or last_use.created_at + timedelta(seconds=rules['cooldown']) <= now)
                            and (session.phase == Phase.COUNTDOWN or now < effective_deadline(session.question, own_options))
                            and not (code == 'ESCUDO' and own_options.hidden_until and own_options.hidden_until > now)
                            and not (code == 'BOTIQUIN' and own.hp >= own.max_hp)
                        )
                items.append({'code': code, 'quantity': entry.quantity if entry else 0, 'enabled': enabled})
            resolution = DuelResolution.objects.filter(duel=duel).first()
            return {
                'status': session.get_phase_display() if session else 'Esperando la fecha y reservas',
                'phase': phase, 'server_time': now.isoformat(),
                'deadline': deadline.isoformat() if deadline else None,
                'unlocked': (now >= duel.agreement.offer.scheduled_at and verified_reserves(duel)
                             and duel.rules_snapshot.get('ring_access', 'scheduled') == 'scheduled'),
                'items': items,
                'result': {
                    'winner_id': resolution.winner_id, 'is_draw': resolution.is_draw,
                    'reason': resolution.reason,
                } if resolution else None,
                'economic_status': duel.status,
                'error': session.last_error if session else '',
                'question_id': session.question_id if session else None,
                'round': session.question.number if session and session.question_id else 0,
                'question': payload,
                'answered': bool(session and session.question_id and session.question.answers.filter(
                    participant__player=actor,
                ).exists()),
                'participants': [
                    {'player_id': p.player_id, 'hp': p.hp, 'max_hp': p.max_hp, 'ready': bool(p.ready_at),
                     'online': bool(p.online and p.last_seen_at and p.last_seen_at + timedelta(
                         seconds=session.rules['presence_seconds'] if session else 20,
                     ) > now)}
                    for p in duel.participants.order_by('seat')
                ],
                'events': [
                    {'sequence': event.sequence, 'kind': event.kind, 'actor_id': event.actor_id,
                     'payload': {key: value for key, value in event.payload.items()
                                 if key in PUBLIC_EVENT_FIELDS[event.kind]},
                     'at': event.created_at.isoformat()}
                    for event in events[:100] if event.kind in PUBLIC_EVENT_FIELDS
                ],
                'cursor': events[min(len(events), 100) - 1].sequence if events else after,
                'has_more': len(events) > 100, 'sequence': latest.sequence if latest else 0,
            }
