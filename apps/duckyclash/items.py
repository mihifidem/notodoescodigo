import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .knowledge import DistractorsUnavailable, VerifiedDistractor
from .models import (
    Duel, DuelAnswer, DuelItemUse, DuelQuestion, DuelQuestionOptions, HealthChange, ParticipantState,
)
from .policies import require_participant
from .questions import effective_deadline, gateway_for, question_contract, require_active
from .shop import ShopGateway


from .rules import DEFAULT_ITEMS as DEFAULT_RULES, current_rules
ATTACKS = ('NUBE', 'ACELERADOR', 'MULTIPLICADOR')


def item_rules(code, duel=None):
    if not isinstance(code, str) or code not in DEFAULT_RULES:
        raise ValidationError('Objeto no admitido en DuckyClash.')
    snapshot = duel.rules_snapshot if duel and duel.rules_snapshot else current_rules()
    rules = dict(snapshot['items'][code])
    rules.setdefault('max_per_round', 1)
    for field, maximum in (('amount', 1000), ('max_uses', 100), ('cooldown', 3600), ('max_per_round', 100)):
        if type(rules[field]) is not int or not 0 <= rules[field] <= maximum:
            raise ValidationError('La configuración del objeto no es válida.')
    if rules['amount'] < 1:
        raise ValidationError('El efecto del objeto debe ser positivo.')
    if code in ('ESCUDO', 'MULTIPLICADOR') and rules['amount'] > 8:
        raise ValidationError('El objeto supera el límite de opciones.')
    if code == 'POCIMA_TIEMPO' and (
        type(rules['max_extra_seconds']) is not int or not 0 <= rules['max_extra_seconds'] <= 300
    ):
        raise ValidationError('El límite de tiempo extra no es válido.')
    return rules


def apply_item(*, actor, question_id, code, action_id):
    try:
        action_id = uuid.UUID(str(action_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError('La acción necesita un identificador UUID estable.') from exc
    question = DuelQuestion.objects.select_related('duel__agreement').get(pk=question_id)
    require_participant(actor, question.duel.agreement)
    player = ParticipantState.objects.get(duel=question.duel, player=actor)
    existing = DuelItemUse.objects.filter(participant=player, action_id=action_id).first()
    if existing:
        if existing.question_id != question.pk or existing.item.code != code:
            raise ValidationError('Este identificador ya se utilizó para otra acción.')
        return existing
    rules = item_rules(code, question.duel)
    require_active(question.duel)
    from .engine import item_window
    item_window(question.duel, question, code)
    ShopGateway().stock(actor.pk, code)
    # Providers may be remote: validate options outside the write transaction,
    # then check the exact options snapshot again under the duel lock.
    target = ParticipantState.objects.get(duel=question.duel, seat=3 - player.seat) if code in ATTACKS else player
    row = DuelQuestionOptions.objects.get(question=question, participant=target)
    before_options = list(row.options)
    extras = []
    removals = []
    if code == 'MULTIPLICADOR':
        gateway = gateway_for(question)
        contract = question_contract(question)
        extras = gateway.distractors(contract, rules['amount'])
        if (not isinstance(extras, list) or len(extras) != rules['amount']
                or any(not isinstance(extra, VerifiedDistractor) or not isinstance(extra.text, str)
                       or not extra.text.strip() or len(extra.text) > 2000
                       or not isinstance(extra.evidence, str) or not extra.evidence.strip() for extra in extras)):
            raise DistractorsUnavailable('No hay distractores certificados suficientes.')
        if any(gateway.validate(contract, extra.text) is not False for extra in extras):
            raise DistractorsUnavailable('Un distractor no ha sido verificado como incorrecto.')
    elif code == 'ESCUDO':
        if row.hidden_until and row.hidden_until > timezone.now():
            raise ValidationError('El Escudo requiere opciones visibles.')
        gateway = gateway_for(question)
        contract = question_contract(question)
        checks = [(option, gateway.validate(contract, option['text'])) for option in before_options]
        if any(type(correct) is not bool for _, correct in checks) or sum(correct for _, correct in checks) != 1:
            raise ValidationError('No se puede verificar la respuesta correcta de estas opciones.')
        false_options = [option for option, correct in checks if not correct]
        secrets.SystemRandom().shuffle(false_options)
        removals = [option['id'] for option in false_options[:rules['amount']]]
        if not removals:
            raise ValidationError('No quedan opciones falsas que eliminar.')
    return commit_item(
        actor=actor, question_id=question_id, code=code, action_id=action_id, rules=rules,
        before_options=before_options, extras=extras, removals=removals,
    )


@transaction.atomic
def commit_item(*, actor, question_id, code, action_id, rules, before_options, extras, removals):
    question = DuelQuestion.objects.select_related('duel__agreement').get(pk=question_id)
    duel = Duel.objects.select_for_update().get(pk=question.duel_id)
    require_participant(actor, question.duel.agreement)
    player = ParticipantState.objects.select_for_update().get(duel=duel, player=actor)
    existing = DuelItemUse.objects.filter(participant=player, action_id=action_id).first()
    if existing:
        if existing.question_id != question_id or existing.item.code != code:
            raise ValidationError('Este identificador ya se utilizó para otra acción.')
        return existing
    require_active(duel)
    from .engine import item_window
    countdown = item_window(duel, question, code)
    if player.hp == 0 or duel.questions.order_by('-number').first().pk != question.pk:
        raise ValidationError('El jugador o la ronda no admite objetos.')
    uses = DuelItemUse.objects.filter(participant=player, item__code=code)
    latest = uses.order_by('-created_at').first()
    now = timezone.now()
    if uses.count() >= rules['max_uses'] or uses.filter(question=question).count() >= rules['max_per_round'] or (
        latest and latest.created_at + timedelta(seconds=rules['cooldown']) > now
    ):
        raise ValidationError('Has alcanzado el máximo de usos o el tiempo de espera.')
    target = ParticipantState.objects.select_for_update().get(duel=duel, seat=3 - player.seat) if code in ATTACKS else player
    own_row = DuelQuestionOptions.objects.get(question=question, participant=player)
    row = DuelQuestionOptions.objects.select_for_update().get(question=question, participant=target)
    if code == 'ESCUDO' and row.hidden_until and row.hidden_until > now:
        raise ValidationError('El Escudo requiere opciones visibles.')
    if (target.hp == 0 or (not countdown and not question.opens_at <= now < effective_deadline(question, own_row))
            or (not countdown and not question.opens_at <= now < effective_deadline(question, row))
            or DuelAnswer.objects.filter(question=question, participant_id__in=[player.pk, target.pk]).exists()
            or hasattr(question, 'damage_resolution')):
        raise ValidationError('La pregunta, el jugador o su respuesta ya no admite efectos.')
    if row.options != before_options:
        raise ValidationError('Las opciones han cambiado. Recarga antes de activar el objeto.')
    snapshot = {'code': code, 'rules': rules, 'target_id': target.player_id, 'prepared': countdown}
    effect_start = question.opens_at if countdown else now
    deadline = effective_deadline(question, row)
    if code == 'NUBE':
        row.hidden_until = min(deadline, max(effect_start, row.hidden_until or effect_start) + timedelta(seconds=rules['amount']))
        snapshot['hidden_until'] = row.hidden_until.isoformat()
    elif code == 'ACELERADOR':
        row.deadline = max(effect_start, deadline - timedelta(seconds=rules['amount']))
        snapshot['deadline'] = row.deadline.isoformat()
    elif code == 'POCIMA_TIEMPO':
        row.deadline = min(
            deadline + timedelta(seconds=rules['amount']),
            question.closes_at + timedelta(seconds=rules['max_extra_seconds']),
        )
        if row.deadline <= deadline:
            raise ValidationError('Ya has alcanzado el límite de tiempo extra.')
        snapshot['deadline'] = row.deadline.isoformat()
    elif code == 'MULTIPLICADOR':
        texts = [option['text'].strip().casefold() for option in row.options] + [
            extra.text.strip().casefold() for extra in extras
        ]
        if len(texts) > 12 or len(texts) != len(set(texts)):
            raise DistractorsUnavailable('Distractores duplicados o límite de opciones superado.')
        row.options += [{'id': uuid.uuid4().hex, 'text': extra.text} for extra in extras]
        row.evidence += [extra.evidence for extra in extras]
        snapshot['added'] = len(extras)
    elif code == 'ESCUDO':
        row.options = [option for option in row.options if option['id'] not in removals]
        snapshot['removed'] = len(removals)
    elif code == 'BOTIQUIN':
        before = player.hp
        after = min(player.max_hp, duel.initial_hp, before + rules['amount'])
        if after <= before:
            raise ValidationError('Ya tienes la salud máxima.')
        ParticipantState.objects.filter(pk=player.pk, hp=before).update(hp=after)
        HealthChange.objects.create(
            participant=player, question=question, source_key=f'item:{action_id}', kind='healing',
            before=before, after=after, requested=rules['amount'],
        )
        snapshot.update(before=before, after=after)
    item_id = ShopGateway().stock(actor.pk, code).item_id
    receipt = DuelItemUse(
        question=question, participant=player, item_id=item_id, action_id=action_id,
        consumption_reference=f'clash:{duel.session_id}:{player.pk}:{action_id}', effect_snapshot=snapshot,
    )
    receipt.save()
    ShopGateway().consume(actor.pk, code)
    row.save()
    from .events import emit
    emit(duel, 'item.prepared' if countdown else 'item.used', actor=actor, payload={
        'round': question.number, 'code': code, 'target_id': target.player_id,
    })
    if code in ('ACELERADOR', 'POCIMA_TIEMPO'):
        emit(duel, 'time.changed', actor=actor, payload={'round': question.number, 'target_id': target.player_id})
    elif code == 'BOTIQUIN':
        emit(duel, 'health.changed', actor=actor, payload={'round': question.number, 'target_id': player.player_id})
    Duel.objects.filter(pk=duel.pk).update(revision=duel.revision + 1)
    return receipt
