"""
Signal handlers for the courses app.

Wired up in apps.courses.apps.CoursesConfig.ready().
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.rewards.models import Seccion
from .models import Progreso, ResultadoQuiz, InscripcionCurso


@receiver(post_save, sender=Progreso)
def on_progreso_guardado(sender, instance, created, **kwargs):
    """When a topic is completed: award points, log activity, check badges/missions."""
    if not instance.completado:
        return

    usuario = instance.usuario
    tema = instance.tema

    # Add points
    usuario.agregar_puntos(tema.puntos_otorgados)
    Seccion.registrar_puntos_usuario(usuario=usuario, tema=tema)

    # Log activity
    from apps.analytics.models import RegistroActividad
    RegistroActividad.objects.get_or_create(
        usuario=usuario,
        tipo='tema_completado',
        defaults={
            'descripcion': f'Completó el tema: {tema.titulo}',
            'puntos_ganados': tema.puntos_otorgados,
        },
    )

    # Gamification checks
    from apps.gamification.utils import verificar_insignia_tema, verificar_misiones
    verificar_insignia_tema(usuario, tema)
    verificar_misiones(usuario, 'completar_temas')

    # Check if the full course is complete
    curso = tema.curso
    if curso.lms_memberships.exists() or curso.lms_modules.exists() or curso.lms_quizzes.exists():
        from apps.lms.lifecycle import complete_student
        if curso.status in ('published', 'archived'):
            complete_student(curso.pk, usuario.pk, now=timezone.now())
        return
    total = curso.temas.count()
    completados = Progreso.objects.filter(
        usuario=usuario, tema__curso=curso, completado=True
    ).count()

    if total > 0 and completados >= total:
        InscripcionCurso.objects.filter(usuario=usuario, curso=curso).update(
            completado=True, fecha_completado=timezone.now()
        )
        # Issue certificate
        from apps.certifications.models import Certificado
        from apps.certifications.utils import generar_y_guardar_certificado
        cert, nuevo = Certificado.objects.get_or_create(usuario=usuario, curso=curso)
        if nuevo:
            generar_y_guardar_certificado(cert)

        from apps.gamification.utils import crear_notificacion
        crear_notificacion(
            usuario, 'certificado',
            '¡Curso completado!',
            f'Has completado «{curso.nombre}» y tu certificado está listo.',
        )
        # Insignia curso
        from apps.gamification.utils import verificar_insignia_curso
        verificar_insignia_curso(usuario, curso)


@receiver(post_save, sender=ResultadoQuiz)
def on_quiz_completado(sender, instance, created, **kwargs):
    """When a quiz is submitted and passed: award bonus points."""
    if not created or not instance.aprobado:
        return

    usuario = instance.usuario
    usuario.agregar_puntos(instance.quiz.puntos_bonus)
    Seccion.registrar_puntos_usuario(
        usuario=usuario,
        nombre_seccion=f'Quiz: {instance.quiz.titulo}',
        puntos=instance.quiz.puntos_bonus,
        notas=f'Puntos otorgados por aprobar el quiz "{instance.quiz.titulo}".',
    )

    from apps.analytics.models import RegistroActividad
    RegistroActividad.objects.create(
        usuario=usuario,
        tipo='quiz_completado',
        descripcion=f'Aprobó el quiz: {instance.quiz.titulo}',
        puntos_ganados=instance.quiz.puntos_bonus,
    )

    from apps.gamification.badge_services import award_verified_quiz
    from apps.gamification.utils import verificar_misiones
    verificar_misiones(usuario, 'quiz_aprobado')
    award_verified_quiz(usuario, instance)
