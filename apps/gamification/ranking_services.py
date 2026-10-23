import hashlib
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import CharField
from django.db.models.functions import Cast
from django.utils import timezone

from apps.users.access import is_platform_admin

from .models import (
    RankingAuditEvent,
    RankingDefinition,
    RankingEntry,
    RankingParticipation,
    RankingSeason,
    RankingAdjustment,
)


def _period_bounds(season):
    tz = ZoneInfo(season.timezone_name)
    start = datetime.combine(season.starts_on, time.min, tzinfo=tz)
    end = datetime.combine(season.ends_on + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


def _metric_source(definition, season):
    from apps.courses.models import InscripcionCurso, Progreso

    start, end = _period_bounds(season)
    if definition.metric == RankingDefinition.Metric.COMPLETED_COURSES:
        model = InscripcionCurso
        source_name = 'courses.InscripcionCurso'
        records = model.objects.filter(
            completado=True,
            fecha_completado__gte=start,
            fecha_completado__lt=end,
        ).values_list('pk', 'usuario_id').order_by('pk')
    elif definition.metric == RankingDefinition.Metric.COMPLETED_LESSONS:
        model = Progreso
        source_name = 'courses.Progreso'
        records = model.objects.filter(
            completado=True,
            fecha_completado__gte=start,
            fecha_completado__lt=end,
        ).values_list('pk', 'usuario_id').order_by('pk')
    else:
        raise ValidationError('La fuente de esta métrica todavía no está disponible.')

    blocked_ids = RankingAdjustment.objects.filter(
        source_model=source_name,
        status__in=[RankingAdjustment.Status.PENDING, RankingAdjustment.Status.EXCLUDED],
    ).values_list('source_record_id', flat=True)
    records = records.alias(
        source_record_key=Cast('pk', output_field=CharField(max_length=80)),
    ).exclude(source_record_key__in=blocked_ids)
    counts = {}
    signature = hashlib.sha256()
    for record_id, user_id in records.iterator():
        counts[user_id] = counts.get(user_id, 0) + 1
        signature.update(f'{record_id}:{user_id};'.encode('ascii'))
    return counts, signature.hexdigest()


def _score_rows(season):
    definition = season.ranking
    if not definition.rule_versions.filter(pk=season.rule_version_id).exists():
        raise ValidationError('La versión de reglas no pertenece al ranking.')
    counts, source_signature = _metric_source(definition, season)
    participations = RankingParticipation.objects.filter(
        ranking=definition,
        is_active=True,
        user__is_active=True,
    ).select_related('user')
    rows = [
        {
            'user': participation.user,
            'alias': participation.alias,
            'score': counts.get(participation.user_id, 0),
            'source_signature': source_signature,
        }
        for participation in participations
    ]
    rows.sort(key=lambda row: (-row['score'], row['alias'].casefold()))
    position = 0
    previous_score = None
    for index, row in enumerate(rows, start=1):
        if row['score'] != previous_score:
            position = index
            previous_score = row['score']
        row['position'] = position
    return rows, source_signature


def ranking_rows(season):
    if season.status in {RankingSeason.Status.CLOSED, RankingSeason.Status.ARCHIVED}:
        return list(
            season.entries.filter(
                user__ranking_participations__ranking_id=season.ranking_id,
                user__ranking_participations__is_active=True,
            ).select_related('user').order_by('position', 'alias')
        )
    if season.status not in {RankingSeason.Status.ACTIVE, RankingSeason.Status.PREPARED}:
        return []
    return _score_rows(season)[0]


def dashboard_ranking_options(user):
    participations = {
        item.ranking_id: item for item in RankingParticipation.objects.filter(user=user)
    }
    rankings = RankingDefinition.objects.filter(
        status=RankingDefinition.Status.PUBLISHED, scope='public',
    ).prefetch_related('seasons')
    return [
        {
            'ranking': ranking,
            'participation': participations.get(ranking.pk),
            'can_join': any(
                season.status in {RankingSeason.Status.PREPARED, RankingSeason.Status.ACTIVE}
                for season in ranking.seasons.all()
            ),
        }
        for ranking in rankings
    ]


@transaction.atomic
def update_participation(actor, ranking, *, enabled, alias):
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied('Inicia sesión para gestionar tu participación.')
    alias = alias.strip()
    if enabled:
        if ranking.status != RankingDefinition.Status.PUBLISHED:
            raise ValidationError('Este ranking no admite participación.')
        if not ranking.seasons.filter(
            status__in=[RankingSeason.Status.PREPARED, RankingSeason.Status.ACTIVE]
        ).exists():
            raise ValidationError('No hay una temporada abierta para este ranking.')
        if not alias:
            raise ValidationError('Indica el alias que se mostrará en el ranking.')
        if len(alias) > 40:
            raise ValidationError('El alias no puede superar 40 caracteres.')

    if not enabled and not RankingParticipation.objects.filter(
        ranking=ranking,
        user=actor,
    ).exists():
        return None
    participation, created = RankingParticipation.objects.get_or_create(
        ranking=ranking,
        user=actor,
        defaults={'alias': alias or actor.username, 'is_active': enabled},
    )
    previous_alias = participation.alias
    was_active = participation.is_active
    if enabled and alias:
        participation.alias = alias
    participation.is_active = enabled
    if enabled:
        participation.full_clean()
    participation.save(update_fields=['alias', 'is_active', 'updated_at'])
    if created or enabled != was_active:
        action = (
            RankingAuditEvent.Action.PARTICIPATION_ENABLED
            if enabled else RankingAuditEvent.Action.PARTICIPATION_DISABLED
        )
        RankingAuditEvent.objects.create(
            actor=actor,
            ranking=ranking,
            action=action,
            reason='Cambio de participación realizado por la persona propietaria.',
            metadata={'alias': participation.alias},
        )
    elif previous_alias != participation.alias:
        RankingAuditEvent.objects.create(
            actor=actor,
            ranking=ranking,
            action=RankingAuditEvent.Action.ALIAS_CHANGED,
            reason='La persona propietaria actualizó el alias.',
            metadata={'alias': participation.alias},
        )
    return participation


def _require_platform_admin(actor):
    if not is_platform_admin(actor):
        raise PermissionDenied('La gestión de temporadas requiere rol ADMIN.')


@transaction.atomic
def activate_season(actor, season):
    _require_platform_admin(actor)
    season = RankingSeason.objects.select_for_update().select_related(
        'ranking'
    ).get(pk=season.pk)
    if season.status != RankingSeason.Status.PREPARED:
        raise ValidationError('Solo se puede activar una temporada preparada.')
    if season.ranking.status != RankingDefinition.Status.PUBLISHED:
        raise ValidationError('Publica la definición antes de activar su temporada.')
    if season.rule_version is None:
        raise ValidationError('Selecciona una versión de reglas para esta temporada.')
    today = timezone.localdate(timezone=ZoneInfo(season.timezone_name))
    if not season.starts_on <= today <= season.ends_on:
        raise ValidationError('La temporada solo se activa dentro de las fechas publicadas.')
    season.status = RankingSeason.Status.ACTIVE
    season.full_clean()
    season.save(update_fields=['status'])
    RankingAuditEvent.objects.create(
        actor=actor,
        ranking=season.ranking,
        season=season,
        action=RankingAuditEvent.Action.SEASON_ACTIVATED,
        reason='Temporada activada por ADMIN.',
    )
    return season


@transaction.atomic
def close_season(actor, season):
    _require_platform_admin(actor)
    season = RankingSeason.objects.select_for_update().select_related(
        'ranking'
    ).get(pk=season.pk)
    if season.status != RankingSeason.Status.ACTIVE:
        raise ValidationError('Solo se puede cerrar una temporada activa.')
    if season.ends_on >= timezone.localdate(timezone=ZoneInfo(season.timezone_name)):
        raise ValidationError('No se puede cerrar antes de que termine el periodo publicado.')
    rows, source_signature = _score_rows(season)
    RankingEntry.objects.filter(season=season).delete()
    RankingEntry.objects.bulk_create([
        RankingEntry(
            season=season,
            user=row['user'],
            alias=row['alias'],
            score=row['score'],
            position=row['position'],
            source_signature=source_signature,
        )
        for row in rows
    ])
    now = timezone.now()
    season.status = RankingSeason.Status.CLOSED
    season.calculated_at = now
    season.closed_at = now
    season.save(update_fields=['status', 'calculated_at', 'closed_at'])
    RankingAuditEvent.objects.create(
        actor=actor,
        ranking=season.ranking,
        season=season,
        action=RankingAuditEvent.Action.SEASON_CLOSED,
        reason='Clasificación calculada y cerrada desde fuentes verificadas.',
        metadata={'entries': len(rows), 'source_signature': source_signature},
    )
    return season


@transaction.atomic
def recalculate_closed_season(actor, season, *, reason):
    _require_platform_admin(actor)
    if not reason.strip():
        raise ValidationError('El motivo del recálculo es obligatorio.')
    season = RankingSeason.objects.select_for_update().select_related(
        'ranking'
    ).get(pk=season.pk)
    if season.status not in {RankingSeason.Status.CLOSED, RankingSeason.Status.ARCHIVED}:
        raise ValidationError('El recálculo auditado está reservado a temporadas cerradas.')
    rows, source_signature = _score_rows(season)
    previous_signature = season.entries.order_by('-calculated_at').values_list(
        'source_signature', flat=True
    ).first()
    RankingEntry.objects.filter(season=season).delete()
    RankingEntry.objects.bulk_create([
        RankingEntry(
            season=season,
            user=row['user'],
            alias=row['alias'],
            score=row['score'],
            position=row['position'],
            source_signature=source_signature,
        )
        for row in rows
    ])
    season.calculated_at = timezone.now()
    season.save(update_fields=['calculated_at'])
    RankingAuditEvent.objects.create(
        actor=actor,
        ranking=season.ranking,
        season=season,
        action=RankingAuditEvent.Action.SEASON_RECALCULATED,
        reason=reason,
        metadata={
            'entries': len(rows),
            'previous_source_signature': previous_signature,
            'source_signature': source_signature,
        },
    )
    return season
