"""
Utility functions for badge/mission checking.
Imported by signal handlers to avoid circular imports.
"""
from django.db.models import F
from django.utils import timezone


def crear_notificacion(usuario, tipo, titulo, mensaje):
    from apps.users.models import Notificacion

    Notificacion.objects.create(
        usuario=usuario,
        tipo=tipo,
        titulo=titulo,
        mensaje=mensaje,
    )


def _otorgar_insignia(usuario, insignia):
    from .badge_services import grant_if_eligible

    return grant_if_eligible(usuario, insignia)


def get_insignias_activas_ids(usuario):
    from .models import InsigniaUsuario

    return set(InsigniaUsuario.objects.filter(
        usuario=usuario,
        status=InsigniaUsuario.Status.VALID,
    ).values_list('insignia_id', flat=True))


def get_insignias_visibles_para_usuario(usuario=None):
    from .models import Insignia

    return Insignia.objects.filter(
        visible=True,
        status=Insignia.Status.PUBLISHED,
    ).select_related('curso_objetivo__tipo_curso', 'tema_objetivo__curso')


def verificar_insignias(usuario):
    """Recheck currently published numeric requirements against server-side values."""
    from .badge_services import grant_if_eligible
    from .models import Insignia, InsigniaVersion

    badges = Insignia.objects.filter(
        status=Insignia.Status.PUBLISHED,
        issue_method=Insignia.IssueMethod.AUTOMATIC,
        versions__rule_type__in=[
            InsigniaVersion.RuleType.STREAK_DAYS,
            InsigniaVersion.RuleType.TOTAL_POINTS,
            InsigniaVersion.RuleType.TOPICS_COUNT,
        ],
        versions__effective_at__lte=timezone.now(),
    ).distinct()
    for badge in badges:
        grant_if_eligible(usuario, badge)


def verificar_insignia_tema(usuario, tema):
    """Issue only badges whose published rule matches a verified topic completion."""
    from .badge_services import award_verified_topic

    return award_verified_topic(usuario, tema)


def verificar_insignia_curso(usuario, curso):
    """Issue only badges whose published rule matches a verified course completion."""
    from .badge_services import award_verified_course

    return award_verified_course(usuario, curso)


def verificar_misiones(usuario, evento: str):
    """
    Advance active missions that match the event type.
    evento: 'completar_temas' | 'quiz_aprobado' | 'puntos_ganados'
    """
    from .models import MisionUsuario

    missions = MisionUsuario.objects.filter(
        usuario=usuario,
        completada=False,
        mision__requisito_tipo=evento,
        mision__activa=True,
    )
    for mission in missions:
        completed = mission.avanzar(1)
        if completed:
            if mission.mision.puntos_recompensa >= 0:
                detail = f'obtuviste {mission.mision.puntos_recompensa} puntos'
            else:
                detail = f'se aplicaron {abs(mission.mision.puntos_recompensa)} puntos de penalizacion'
            crear_notificacion(
                usuario,
                'mision',
                '¡Misión completada!',
                f'Completaste la misión «{mission.mision.titulo}» y {detail}.',
            )
            verificar_insignias(usuario)


def asignar_puntos_usuario(usuario, puntos, motivo=None):
    """Asigna puntos al usuario y registra el motivo si se provee."""
    type(usuario).objects.filter(pk=usuario.pk).update(puntos=F('puntos') + puntos)
    usuario.refresh_from_db(fields=['puntos'])
    if motivo:
        from apps.analytics.models import RegistroActividad

        RegistroActividad.objects.create(
            usuario=usuario,
            tipo='puntos_ganados',
            descripcion=f'+{puntos} puntos: {motivo}',
        )
