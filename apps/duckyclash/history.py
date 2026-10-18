"""Read statistics from authoritative facts; never mirror bank balances."""
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Greatest

from apps.ducky_arenas.models import UserProfileArenas
from apps.ducky_arenas.services import update_match_stats
from .models import Duel, DuelResolution, EconomicOperation, ParticipantState, ProfileResultUpdate, HealthChange, DuelAnswer, DuelItemUse, RoundResolution
from .notifications import duel_notice


@transaction.atomic
def publish_result(resolution):
    duel = Duel.objects.select_for_update().select_related('agreement__offer').get(pk=resolution.duel_id)
    _, created = ProfileResultUpdate.objects.get_or_create(resolution=resolution)
    if not created:
        return
    profiles = UserProfileArenas.objects.select_for_update().filter(
        user_id__in=[duel.agreement.creator_id, duel.agreement.opponent_id],
    ).order_by('pk')
    profiles = list(profiles)
    if len(profiles) != 2:
        from django.core.exceptions import ValidationError
        raise ValidationError('Falta un perfil existente para registrar las estadísticas del resultado.')
    for profile in profiles:
        result = 'DRAW' if resolution.is_draw else 'WIN' if profile.user_id == resolution.winner_id else 'LOSS'
        last_played = profile.lastPlayedAt
        streak = (profile.currentWinStreak, profile.bestWinStreak)
        update_match_stats(profile, result)
        if last_played and last_played > resolution.resolved_at:
            profile.lastPlayedAt = last_played
            profile.currentWinStreak, profile.bestWinStreak = streak
            profile.save(update_fields=['lastPlayedAt', 'currentWinStreak', 'bestWinStreak'])
        else:
            profile.lastPlayedAt = resolution.resolved_at
            profile.save(update_fields=['lastPlayedAt'])
    message = 'Empate confirmado por el servidor.' if resolution.is_draw else 'El servidor ha registrado el ganador.'
    duel_notice(duel, 'result', 'Resultado de DuckyClash', f'{message} La liquidación se verifica por separado.')
    from .economy import enqueue_local_reconciliation
    enqueue_local_reconciliation(duel.pk)


def participant_metrics(participant):
    duel = participant.duel
    try:
        resolution = duel.resolution
    except DuelResolution.DoesNotExist:
        resolution = None
    outcome = 'Pendiente' if resolution is None else (
        'Empate' if resolution.is_draw else 'Victoria' if resolution.winner_id == participant.player_id else 'Derrota'
    )
    answers = list(participant.answers.all())
    damage_received = sum(max(0, change.before - change.after)
                          for change in participant.health_changes.all() if change.kind == 'damage')
    damage_caused = sum(max(0, change.before - change.after) for rival in duel.participants.all()
                        if rival.player_id != participant.player_id
                        for change in rival.health_changes.all() if change.kind == 'damage')
    net = None
    if duel.status in (Duel.Status.FINISHED, Duel.Status.CANCELED):
        operations = [op for op in duel.economic_operations.all() if op.status == EconomicOperation.Status.CONFIRMED]
        if duel.bank_mode == Duel.BankMode.PAYOUT and resolution and any(
            op.kind == EconomicOperation.Kind.SETTLE for op in operations
        ):
            paid = sum(op.amount for op in operations if op.kind == EconomicOperation.Kind.SETTLE
                       and resolution.winner_id == participant.player_id)
            spent = sum(op.amount for op in operations if op.kind == EconomicOperation.Kind.RESERVE
                        and op.player_id == participant.player_id)
            net = paid - spent
        elif duel.bank_mode in (Duel.BankMode.DRAW, Duel.BankMode.CANCEL):
            net = 0
    return {
        'duel': duel, 'outcome': outcome, 'answered': len(answers),
        'correct': sum(answer.is_correct is True and hasattr(answer.question, 'damage_resolution')
                       and answer.question.damage_resolution.status == RoundResolution.Status.APPLIED
                       for answer in answers), 'damage_received': damage_received,
        'damage_caused': damage_caused, 'items': participant.item_uses.count(),
        'coins_won': max(0, net) if net is not None else 0,
        'coins_lost': max(0, -net) if net is not None else 0, 'net_coins': net,
    }


def summary(participations, user):
    duels = participations.values('duel_id')
    result = participations.aggregate(
        wins=Count('pk', filter=Q(duel__resolution__winner=user)),
        losses=Count('pk', filter=Q(duel__resolution__is_draw=False) & ~Q(duel__resolution__winner=user)),
        draws=Count('pk', filter=Q(duel__resolution__is_draw=True)),
    )
    result.update(DuelAnswer.objects.filter(participant__in=participations).aggregate(
        answered=Count('pk'), correct=Count('pk', filter=Q(
            is_correct=True, question__damage_resolution__status=RoundResolution.Status.APPLIED,
        )),
    ))
    for key, players in (
        ('damage_received', participations),
        ('damage_caused', ParticipantState.objects.filter(duel_id__in=duels).exclude(player=user)),
    ):
        result[key] = HealthChange.objects.filter(participant__in=players, kind='damage').aggregate(
            total=Sum(Greatest(F('before') - F('after'), 0)),
        )['total'] or 0
    result['items'] = DuelItemUse.objects.filter(participant__in=participations).count()
    operations = EconomicOperation.objects.filter(duel_id__in=duels, status=EconomicOperation.Status.CONFIRMED)
    paid_duels = operations.filter(
        kind=EconomicOperation.Kind.SETTLE, duel__status=Duel.Status.FINISHED, duel__bank_mode=Duel.BankMode.PAYOUT,
    ).values('duel_id')
    won = operations.filter(duel_id__in=paid_duels, duel__resolution__winner=user)
    result['coins_won'] = ((won.filter(kind=EconomicOperation.Kind.SETTLE).aggregate(total=Sum('amount'))['total'] or 0)
                          - (won.filter(kind=EconomicOperation.Kind.RESERVE, player=user).aggregate(total=Sum('amount'))['total'] or 0))
    result['coins_lost'] = operations.filter(
        duel_id__in=paid_duels, kind=EconomicOperation.Kind.RESERVE, player=user,
    ).exclude(duel__resolution__winner=user).aggregate(total=Sum('amount'))['total'] or 0
    result['pending_money'] = participations.exclude(
        Q(duel_id__in=paid_duels) | Q(duel__status__in=[Duel.Status.FINISHED, Duel.Status.CANCELED],
                                    duel__bank_mode__in=[Duel.BankMode.DRAW, Duel.BankMode.CANCEL]),
    ).count()
    return result
