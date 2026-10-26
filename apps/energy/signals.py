"""Eventos de progreso verificados en servidor que pueden recompensar energía.

Las reglas nacen desactivadas y sin cantidad: hasta que ADMIN las configure no se concede nada.
Las claves de evento son por objeto (tema, quiz, módulo), así repetir un ejercicio no financia
nuevas llamadas IA. Las llamadas IA nunca emiten eventos de actividad.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.courses.models import Progreso, ResultadoQuiz
from apps.lms.models import ModuleProgress, QuizAttempt

from .models import EnergyRewardRule
from .services import emit_activity_on_commit

Source = EnergyRewardRule.Source


@receiver(post_save, sender=Progreso, dispatch_uid='energy_topic_completed')
def topic_completed(sender, instance, raw=False, **kwargs):
    if not raw and instance.completado:
        emit_activity_on_commit(instance.usuario_id, Source.COURSE_TOPIC_COMPLETED, f'topic:{instance.tema_id}')


@receiver(post_save, sender=ResultadoQuiz, dispatch_uid='energy_course_quiz_passed')
def course_quiz_passed(sender, instance, created, raw=False, **kwargs):
    if not raw and created and instance.aprobado:
        emit_activity_on_commit(instance.usuario_id, Source.COURSE_QUIZ_PASSED, f'quiz:{instance.quiz_id}')


@receiver(post_save, sender=QuizAttempt, dispatch_uid='energy_lms_quiz_passed')
def lms_quiz_passed(sender, instance, raw=False, **kwargs):
    if not raw and instance.status == QuizAttempt.Status.GRADED and instance.is_passed:
        emit_activity_on_commit(instance.user_id, Source.LMS_QUIZ_PASSED, f'lms-quiz:{instance.quiz_id}')


@receiver(post_save, sender=ModuleProgress, dispatch_uid='energy_lms_module_completed')
def lms_module_completed(sender, instance, raw=False, **kwargs):
    if not raw and instance.status == ModuleProgress.Status.COMPLETED:
        emit_activity_on_commit(instance.user_id, Source.LMS_MODULE_COMPLETED, f'lms-module:{instance.module_id}')
