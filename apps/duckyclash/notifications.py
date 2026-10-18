"""Transactional delivery into the existing notification inbox, once per fact."""
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notificacion
from .models import Challenge, Duel, NotificationReceipt


@transaction.atomic
def deliver(recipient_id, key, title, message, url):
    receipt, created = NotificationReceipt.objects.get_or_create(recipient_id=recipient_id, key=key)
    if not created:
        return False
    receipt.notification = Notificacion.objects.create(
        usuario_id=recipient_id, tipo='sistema', titulo=title, mensaje=f'{message} {url}',
    )
    receipt.save(update_fields=['notification'])
    return True


def challenge_notice(recipient, challenge, title, message):
    offer = challenge.clash_offers.order_by('-version').first()
    deliver(
        recipient.pk, f'challenge:{challenge.pk}:{offer.version}:{challenge.status}:{title}',
        title, message, reverse('duckyclash:challenge_detail', args=[challenge.pk]),
    )


def duel_notice(duel, kind, title, message):
    for player_id in (duel.agreement.creator_id, duel.agreement.opponent_id):
        deliver(player_id, f'duel:{duel.pk}:{kind}', title, message,
                reverse(f"duckyclash:{'results' if kind in ('result', 'settlement') else 'waiting'}", args=[duel.pk]))


@transaction.atomic
def expire_challenge(challenge_id):
    challenge = Challenge.objects.select_for_update().get(pk=challenge_id)
    if (challenge.status != Challenge.Status.PENDING or challenge.expires_at > timezone.now()
            or not challenge.clash_offers.exists()):
        return False
    challenge.status = Challenge.Status.EXPIRED
    challenge.revision += 1
    Challenge.objects.filter(pk=challenge.pk).update(
        status=challenge.status, revision=challenge.revision, updated_at=timezone.now(),
    )
    for recipient in (challenge.creator, challenge.opponent):
        if recipient is not None:
            challenge_notice(recipient, challenge, 'Reto caducado', 'El plazo de aceptación ha terminado.')
    return True


@transaction.atomic
def upcoming_notice(duel_id):
    duel = Duel.objects.select_for_update().select_related('agreement__offer').get(pk=duel_id)
    from .engine import verified_reserves
    if duel.status != Duel.Status.SCHEDULED or not verified_reserves(duel):
        return False
    rules = duel.rules_snapshot
    now = timezone.now()
    start = duel.agreement.offer.scheduled_at
    if not start - timedelta(seconds=rules['reminder_seconds']) <= now < start:
        return False
    duel_notice(duel, 'upcoming', 'Tu duelo empieza pronto',
                'Las reservas están verificadas. Revisa la hora y prepara tu entrada al Ring.')
    return True
