"""
Utility functions for badge/mission checking.
Imported by signal handlers to avoid circular imports.
"""
from django.db.models import F, Q
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
    from .models import InsigniaUsuario
    _, creada = InsigniaUsuario.objects.get_or_create(usuario=usuario, insignia=insignia)
    if creada:
        crear_notificacion(
            usuario, 'insignia',
            f'🏅 ¡Insignia desbloqueada!',
            f'Has obtenido la insignia «{insignia.nombre}».',
        )
        # Log activity
        from apps.analytics.models import RegistroActividad
        RegistroActividad.objects.create(
            usuario=usuario,
            tipo='insignia_obtenida',
            descripcion=f'Obtuvo la insignia: {insignia.nombre}',
        )


def get_insignias_activas_ids(usuario):
    from .models import Insignia, InsigniaUsuario
    from apps.courses.models import InscripcionCurso, Progreso

    activas_ids = set(
        InsigniaUsuario.objects.filter(usuario=usuario).values_list('insignia_id', flat=True)
    )

    temas_completados_ids = set(
        Progreso.objects.filter(usuario=usuario, completado=True).values_list('tema_id', flat=True)
    )
    if temas_completados_ids:
        activas_ids.update(
            Insignia.objects.filter(tipo='tema', tema_objetivo_id__in=temas_completados_ids)
            .values_list('id', flat=True)
        )

    cursos_completados_ids = set(
        InscripcionCurso.objects.filter(usuario=usuario, completado=True).values_list('curso_id', flat=True)
    )
    if cursos_completados_ids:
        activas_ids.update(
            Insignia.objects.filter(tipo='curso', curso_objetivo_id__in=cursos_completados_ids)
            .values_list('id', flat=True)
        )

    return activas_ids


def get_insignias_visibles_para_usuario(usuario):
    from .models import Insignia
    from apps.courses.models import InscripcionCurso, Curso, TipoCurso

    if usuario.is_staff:
        return Insignia.objects.filter(visible=True).select_related(
            'curso_objetivo__tipo_curso', 'tema_objetivo__curso'
        )

    cursos_inscritos = InscripcionCurso.objects.filter(
        usuario=usuario
    ).values_list('curso_id', flat=True)
    return Insignia.objects.filter(visible=True).filter(
        Q(tipo='curso', curso_objetivo__in=cursos_inscritos) |
        Q(tipo='tema', tema_objetivo__curso__in=cursos_inscritos)
    ).select_related('curso_objetivo', 'tema_objetivo__curso')


def verificar_insignias(usuario):
    """Check all automatic badge conditions for a user."""
    from .models import Insignia, InsigniaUsuario
    ya_tiene = set(
        InsigniaUsuario.objects.filter(usuario=usuario).values_list('insignia_id', flat=True)
    )

    # --- Streak badges ---
    for ins in Insignia.objects.filter(tipo='streak').exclude(id__in=ya_tiene):
        if usuario.streak >= ins.requisito_valor:
            _otorgar_insignia(usuario, ins)

    # --- Points badges ---
    for ins in Insignia.objects.filter(tipo='puntos').exclude(id__in=ya_tiene):
        if usuario.puntos >= ins.requisito_valor:
            _otorgar_insignia(usuario, ins)


def verificar_insignia_tema(usuario, tema):
    """Award topic badges: explicit topic-linked and topic-count based."""
    from .models import Insignia
    from apps.courses.models import Progreso

    # Explicit badges linked to a concrete topic
    for ins in Insignia.objects.filter(tipo='tema', tema_objetivo=tema):
        _otorgar_insignia(usuario, ins)

    # Generic topic-count badges (no explicit topic linked)
    total_completados = Progreso.objects.filter(usuario=usuario, completado=True).count()
    for ins in Insignia.objects.filter(tipo='tema', tema_objetivo__isnull=True):
        if total_completados >= ins.requisito_valor:
            _otorgar_insignia(usuario, ins)


def verificar_insignia_curso(usuario, curso):
    """Award course badges, supporting explicit course link and legacy mapping."""
    from .models import Insignia
    queryset = Insignia.objects.filter(tipo='curso').filter(
        curso_objetivo=curso
    ) | Insignia.objects.filter(
        tipo='curso',
        curso_objetivo__isnull=True,
        requisito_valor=curso.id,
    )
    for ins in queryset.distinct():
        _otorgar_insignia(usuario, ins)


def verificar_misiones(usuario, evento: str):
    """
    Advance active missions that match the event type.
    evento: 'completar_temas' | 'quiz_aprobado' | 'puntos_ganados'
    """
    from .models import MisionUsuario
    misiones_activas = MisionUsuario.objects.filter(
        usuario=usuario,
        completada=False,
        mision__requisito_tipo=evento,
        mision__activa=True,
    )
    for mu in misiones_activas:
        completada = mu.avanzar(1)
        if completada:
            if mu.mision.puntos_recompensa >= 0:
                detalle_puntos = f'obtuviste {mu.mision.puntos_recompensa} puntos'
            else:
                detalle_puntos = f'se aplicaron {abs(mu.mision.puntos_recompensa)} puntos de penalizacion'
            crear_notificacion(
                usuario, 'mision',
                '🎯 ¡Misión completada!',
                f'Completaste la misión «{mu.mision.titulo}» y {detalle_puntos}.',
            )
            # Check if any mission-type badge should be awarded
            verificar_insignias(usuario)


def asignar_puntos_usuario(usuario, puntos, motivo=None):
    """
    Asigna puntos al usuario y registra el motivo si se provee.
    """
    type(usuario).objects.filter(pk=usuario.pk).update(puntos=F('puntos') + puntos)
    usuario.refresh_from_db(fields=['puntos'])
    if motivo:
        from apps.analytics.models import RegistroActividad
        RegistroActividad.objects.create(
            usuario=usuario,
            tipo='puntos_ganados',
            descripcion=f'+{puntos} puntos: {motivo}'
        )
