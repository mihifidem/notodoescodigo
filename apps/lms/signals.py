from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.courses.models import Progreso
from .models import ModuleContentProgress, ModuleProgress, QuizAttempt, Submission


@receiver(post_save, sender=ModuleProgress)
def complete_course_after_module(sender, instance, raw=False, **kwargs):
    if raw or instance.status != ModuleProgress.Status.COMPLETED:
        return
    if instance.course.status in ('published', 'archived'):
        from .lifecycle import complete_student
        complete_student(instance.course_id, instance.user_id, now=timezone.now())


@receiver(post_save, sender=QuizAttempt)
def complete_course_after_quiz(sender, instance, raw=False, **kwargs):
    if raw or instance.status != QuizAttempt.Status.GRADED or not instance.is_passed:
        return
    if instance.quiz.course.status in ('published', 'archived'):
        from .lifecycle import complete_student
        complete_student(instance.quiz.course_id, instance.user_id, now=timezone.now())


@receiver(post_save, sender=ModuleContentProgress)
@receiver(post_save, sender=QuizAttempt)
@receiver(post_save, sender=Submission)
def award_badges_after_content_progress(sender, instance, raw=False, **kwargs):
    if raw:
        return
    if sender == ModuleContentProgress:
        course = instance.content.module.curso
    elif sender == QuizAttempt:
        course = instance.quiz.course
    else:
        course = instance.assignment.course
    from .badge_awards import award_module_badges
    award_module_badges(course, instance.user)


@receiver(post_save, sender=Progreso)
def award_badges_after_topic_progress(sender, instance, raw=False, **kwargs):
    if not raw and instance.completado:
        from .badge_awards import award_module_badges
        award_module_badges(instance.tema.curso, instance.usuario)
