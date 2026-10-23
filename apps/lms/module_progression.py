from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.courses.models import Progreso
from .models import CourseMembership, CourseSettingsLMS, ModuleContent, ModuleContentProgress, QuizAttempt, Submission
from .policies import can_edit_course, is_course_member


def sequential_enabled(course):
    return CourseSettingsLMS.objects.filter(course=course, sequential_modules=True).exists()


def content_complete(content, user):
    criterion = content.criterion
    if criterion in (ModuleContent.Criterion.MANUAL, ModuleContent.Criterion.VIEWED):
        field = 'completed_at' if criterion == ModuleContent.Criterion.MANUAL else 'opened_at'
        return content.progress.filter(user=user, **{f'{field}__isnull': False}).exists()
    if content.activity_id:
        submission = Submission.objects.filter(
            assignment__learning_activity_id=content.activity_id, user=user,
            submitted_at__isnull=False,
        ).order_by('-version', '-pk').first()
        if not submission:
            return False
        if criterion == ModuleContent.Criterion.SUBMITTED:
            return True
        from .activity_workflow import feedback_visible
        return bool(
            (submission.status == Submission.Status.GRADED or
             submission.status == Submission.Status.RETURNED and submission.graded_at is not None)
            and feedback_visible(content.activity, submission)
            and submission.grade * 100 >= Decimal(content.activity.points) * content.passing_percentage
        )
    attempts = QuizAttempt.objects.filter(quiz_id=content.quiz_id, user=user, status=QuizAttempt.Status.GRADED)
    if criterion == ModuleContent.Criterion.PASSED:
        attempts = attempts.filter(is_passed=True)
    return attempts.exists()


def module_complete(module, user):
    from .campus import campus_settings, requirement_status
    if campus_settings(module.curso) and not can_edit_course(user, module.curso):
        membership = CourseMembership.objects.filter(course=module.curso, user=user).first()
        if not membership or not membership.learning_contract:
            return False
        items = [
            item for item in membership.learning_contract['items']
            if item['module_id'] == module.pk and item['required']
        ]
        return all(requirement_status(item, user) for item in items)
    contents = list(module.learning_contents.all())
    if not contents:
        return Progreso.objects.filter(usuario=user, tema=module, completado=True).exists()
    return all(content_complete(content, user) for content in contents if content.required)


def module_lock_reason(module, user, *, now=None):
    from .campus import access_message, material_access
    decision = material_access(user, module.curso)
    if not decision.allowed:
        return access_message(decision.reason)
    if can_edit_course(user, module.curso) or not sequential_enabled(module.curso):
        return ''
    if not is_course_member(user, module.curso):
        return _('Debes estar matriculado en el curso.')
    now = now or timezone.now()
    schedule = getattr(module, 'learning_schedule', None)
    if schedule:
        if schedule.starts_at and now < schedule.starts_at:
            return _('El módulo todavía no ha abierto.')
        if schedule.ends_at and now >= schedule.ends_at:
            return _('El plazo del módulo ha terminado.')
    for previous in module.curso.temas.order_by('orden', 'pk'):
        if previous.pk == module.pk:
            break
        if not module_complete(previous, user):
            return _('Completa el módulo anterior: %(module)s.') % {'module': previous.titulo}
    return ''


def content_lock_reason(content, user, *, now=None):
    from .campus import require_frozen_content
    reason = module_lock_reason(content.module, user, now=now)
    if not reason:
        try:
            require_frozen_content(content, user)
        except PermissionDenied as error:
            return str(error)
    if reason or can_edit_course(user, content.module.curso) or not sequential_enabled(content.module.curso):
        return reason
    previous = content.prerequisite
    seen = {content.pk}
    while previous:
        if previous.pk in seen or previous.module_id != content.module_id:
            return _('La configuración de prerrequisitos no es válida. Contacta con el profesor.')
        seen.add(previous.pk)
        if not content_complete(previous, user):
            return _('Primero completa: %(content)s.') % {'content': previous.title}
        previous = previous.prerequisite
    return ''


def target_content(target):
    return getattr(target, 'learning_content', None)


def target_lock_reason(target, user, *, now=None):
    from .campus import campus_settings, material_access, access_message, require_frozen_content
    course = getattr(target, 'course', None)
    if course and campus_settings(course):
        decision = material_access(user, course)
        if not decision.allowed:
            return access_message(decision.reason)
        if not can_edit_course(user, course):
            content = target_content(target)
            if not content:
                return _('El material no está vinculado a una versión educativa.')
            try:
                require_frozen_content(content, user)
            except PermissionDenied as error:
                return str(error)
    content = target_content(target)
    if content:
        return content_lock_reason(content, user, now=now)
    module = getattr(target, 'module', None)
    if module and hasattr(module, 'curso_id'):
        return module_lock_reason(module, user, now=now)
    return ''


def require_target_access(target, user, *, now=None):
    reason = target_lock_reason(target, user, now=now)
    if reason:
        raise PermissionDenied(reason)


@transaction.atomic
def record_open(target, user):
    content = target_content(target)
    if not content or can_edit_course(user, content.module.curso):
        return
    require_target_access(target, user)
    progress, _created = ModuleContentProgress.objects.select_for_update().get_or_create(content=content, user=user)
    if not progress.opened_at:
        progress.opened_at = timezone.now()
        progress.save(update_fields=['opened_at'])
        from .campus import campus_settings
        if campus_settings(content.module.curso):
            from .models import LearningEvent
            LearningEvent.objects.create(
                user=user, course=content.module.curso,
                object_type='resource' if content.file_id else 'activity' if content.activity_id else 'quiz' if content.quiz_id else 'page',
                object_id=target.pk, event_type=LearningEvent.EventType.VIEW,
                context={'source': 'campus'},
            )
    from .campus import campus_settings
    if campus_settings(content.module.curso):
        CourseMembership.objects.filter(
            user=user, course=content.module.curso,
        ).update(last_content=content, last_accessed_at=timezone.now())


def content_url(content):
    from django.urls import reverse
    course_id = content.module.curso_id
    if content.page_id:
        return reverse('lms:course_page_detail', kwargs={'course_id': course_id, 'page_slug': content.page.slug})
    route, name, pk = (
        ('course_file_download', 'file_id', content.file_id) if content.file_id else
        ('quiz_detail', 'quiz_id', content.quiz_id) if content.quiz_id else
        ('course_activity_detail', 'activity_id', content.activity_id)
    )
    url = reverse(f'lms:{route}', kwargs={'course_id': course_id, name: pk})
    if content.file_id and content.file.allow_preview:
        return url + '?inline=1'
    return url


def module_rows(course, user):
    rows = []
    teacher = can_edit_course(user, course)
    for module in course.temas.order_by('orden', 'pk').select_related('learning_schedule'):
        contents = module.learning_contents.select_related(
            'unit', 'page', 'file', 'quiz', 'activity', 'prerequisite',
        )
        items = []
        for content in contents:
            target = content.target
            visible = teacher or (
                content.page_id and target.status == 'published' and target.is_currently_published()
                or content.file_id and target.is_active and target.user_can_access(user)
                or content.quiz_id and target.status not in ('draft', 'scheduled')
                and (not target.section_id or CourseMembership.objects.filter(course=course, user=user, section_id=target.section_id).exists())
                or content.activity_id and target.status != 'draft'
            )
            if not visible:
                continue
            reason = content_lock_reason(content, user)
            if content.file_id and not reason and not (target.allow_preview or target.allow_download):
                reason = _('Este fichero no permite vista previa ni descarga.')
            items.append({
                'content': content, 'url': content_url(content),
                'reason': reason, 'complete': content_complete(content, user),
                'published': (
                    target.is_active if content.file_id else target.status not in ('draft', 'scheduled')
                ),
            })
        units = [
            {'unit': unit, 'items': [item for item in items if item['content'].unit_id == unit.pk]}
            for unit in module.formative_units.all()
        ]
        if not teacher:
            units = [row for row in units if row['items']]
        rows.append({
            'module': module, 'schedule': getattr(module, 'learning_schedule', None),
            'reason': module_lock_reason(module, user), 'complete': module_complete(module, user),
            'items': items, 'units': units,
        })
    return rows
