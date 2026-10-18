from celery import shared_task
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.utils import timezone

from .bank import BankUnavailable
from .economy import reconcile_duel
from .models import Challenge, Duel, DuelQuestion
from .combat import resolve_round
from .engine import DuelEngine

logger = logging.getLogger(__name__)


@shared_task
def resolve_closed_rounds():
    if not getattr(settings, 'DUCKYCLASH_ECOMOTOR_GATEWAY', ''):
        logger.warning('DuckyClash combat resolution disabled: no EcoMotor configured')
        return 0
    questions = DuelQuestion.objects.filter(
        duel__status=Duel.Status.IN_PROGRESS, duel__engine__isnull=True,
    ).exclude(damage_resolution__status='applied').order_by('pk')
    processed = 0
    from .questions import round_deadline
    for question in questions.iterator():
        if round_deadline(question) > timezone.now():
            continue
        try:
            result = resolve_round(question.pk)
        except ValidationError:
            logger.exception('Combat invariant requires review for question %s', question.pk)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                raise
            logger.warning('Concurrent DuckyClash combat resolution for question %s', question.pk)
        else:
            processed += result.status == 'applied'
    return processed


@shared_task
def advance_duel_engines():
    from .notifications import upcoming_notice
    from .history import publish_result
    from .models import DuelResolution
    # SQLite invalidates live cursors when an individual projection rolls back.
    pending_results = list(DuelResolution.objects.filter(
        profileresultupdate__isnull=True,
    ).order_by('resolved_at', 'pk'))
    for resolution in pending_results:
        try:
            publish_result(resolution)
        except ValidationError:
            logger.exception('DuckyClash profile projection requires review for result %s', resolution.pk)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                raise
            logger.warning('Concurrent DuckyClash profile projection for result %s', resolution.pk)
    processed = 0
    duel_ids = list(Duel.objects.filter(
        status__in=[Duel.Status.SCHEDULED, Duel.Status.IN_PROGRESS],
        bank_mode=Duel.BankMode.RESERVE,
    ).values_list('pk', flat=True))
    for duel_id in duel_ids:
        try:
            upcoming_notice(duel_id)
            DuelEngine(duel_id).advance()
        except ValidationError:
            logger.exception('DuelEngine invariant requires review for duel %s', duel_id)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                raise
            logger.warning('Concurrent DuelEngine transition for duel %s', duel_id)
        else:
            processed += 1
    return processed


@shared_task
def expire_open_challenges():
    # Existing Beat entry now covers both public and private Clash offers.
    from .notifications import expire_challenge
    now = timezone.now()
    ids = Challenge.objects.filter(
        status=Challenge.Status.PENDING,
        expires_at__lte=now, clash_offers__isnull=False,
    ).values_list('pk', flat=True).distinct()
    return sum(expire_challenge(pk) for pk in ids.iterator())


@shared_task
def reconcile_bank_operations():
    duels = Duel.objects.exclude(status__in=[Duel.Status.FINISHED, Duel.Status.CANCELED])
    if not getattr(settings, 'DUCKYCLASH_PAYMENT_GATEWAY', ''):
        duels = duels.exclude(payment_gateway='')
    if not duels.exists():
        logger.warning('DuckyClash bank reconciliation disabled: no payment gateway configured')
        return 0
    ids = list(duels.filter(
        status__in=[Duel.Status.PENDING_RESERVATION, Duel.Status.SETTLEMENT_PENDING],
    ).order_by('pk').values_list('pk', flat=True))
    ids += list(duels.filter(
        bank_mode=Duel.BankMode.CANCEL, status__in=[Duel.Status.SCHEDULED, Duel.Status.IN_PROGRESS],
    ).values_list('pk', flat=True))
    processed = 0
    for duel_id in ids:
        try:
            reconcile_duel(duel_id)
        except BankUnavailable:
            logger.exception('DuckyClash bank configuration requires review for duel %s', duel_id)
        except ValidationError:
            logger.exception('DuckyClash bank invariant requires review for duel %s', duel_id)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'deadlock' not in str(exc).lower():
                raise
            logger.warning('Concurrent DuckyClash bank reconciliation for duel %s', duel_id)
        else:
            processed += 1
    return processed
