"""Presentation helpers for NoTodoEsCódigo Campus.

Authorization decisions live in ``policies``; this module only turns the
result of those decisions into navigation/state structures for templates.
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

COURSE_AREAS = (
    ('home', _('Página de inicio'), 'bi-house'),
    ('announcements', _('Anuncios'), 'bi-megaphone'),
    ('content', _('Contenido'), 'bi-journals'),
    ('discussion', _('Foro de discusión'), 'bi-chat-left-text'),
    ('grades', _('Notas'), 'bi-clipboard-data'),
    ('people', _('Personas'), 'bi-people'),
    ('pages', _('Páginas'), 'bi-file-earmark-text'),
    ('syllabus', _('Programa de la asignatura'), 'bi-list-check'),
    ('quizzes', _('Pruebas'), 'bi-ui-checks'),
    ('item-bank', _('Base del conocimiento'), 'bi-collection'),
    ('activities', _('Actividades'), 'bi-calendar-event'),
    ('rubrics', _('Rúbricas'), 'bi-grid-3x3'),
    ('collaborations', _('Colaboraciones'), 'bi-people-fill'),
    ('files', _('Archivos'), 'bi-folder2-open'),
    ('competencies', _('Competencias'), 'bi-award'),
    ('settings', _('Configuraciones'), 'bi-gear'),
)
STUDENT_AREA_KEYS = frozenset({
    'home',
    'announcements',
    'content',
    'discussion',
    'grades',
    'people',
    'pages',
    'syllabus',
    'quizzes',
    'activities',
    'files',
    'competencies',
})
AREA_KEYS = frozenset(key for key, _label, _icon in COURSE_AREAS)

THEMES = ('system', 'light', 'dark')
DENSITIES = ('comfortable', 'compact')
DEFAULT_PREFERENCES = {'theme': 'system', 'density': 'comfortable'}


def ui_preferences(profile):
    prefs = profile.accessibility_preferences if profile else {}
    if not isinstance(prefs, dict):
        prefs = {}
    theme = prefs.get('theme')
    density = prefs.get('density')
    return {
        'theme': theme if theme in THEMES else DEFAULT_PREFERENCES['theme'],
        'density': density if density in DENSITIES else DEFAULT_PREFERENCES['density'],
    }


def course_nav(course, *, teacher_view, active_key):
    """Course navigation. Students never receive teacher-only entries."""
    items = []
    for key, label, icon in COURSE_AREAS:
        if not teacher_view and key not in STUDENT_AREA_KEYS:
            continue
        if key == 'home':
            url = reverse('lms:course_detail', kwargs={'course_id': course.pk})
        elif key == 'pages':
            url = reverse('lms:course_pages', kwargs={'course_id': course.pk})
        else:
            url = reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': key})
        items.append({
            'key': key,
            'label': label,
            'icon': icon,
            'url': url,
            'active': key == active_key,
            'teacher_only': key not in STUDENT_AREA_KEYS,
        })
    return items


def pending_announcement_ids(course, user, *, announcements=None):
    from django.db.models import Q

    from .models import Announcement, Notification, NotificationEvent
    visible_ids = [
        announcement.pk
        for announcement in (
            announcements if announcements is not None else Announcement.visible_for_course(course, user)
        )
    ]
    if not visible_ids:
        return set()
    pending_ids = Notification.objects.filter(
        recipient=user,
        course=course,
        status=Notification.Status.UNREAD,
        event__event_type=NotificationEvent.Type.ANNOUNCEMENT,
    ).filter(
        Q(payload__announcement_id__in=visible_ids)
        | Q(payload__announcement_id__in=[str(pk) for pk in visible_ids])
    ).values_list('payload__announcement_id', flat=True)
    return {int(pk) for pk in pending_ids}


def pending_announcements_count(course, user):
    return len(pending_announcement_ids(course, user))


def due_state(moment, *, now=None):
    """Classify a deadline for colour + text (never colour alone)."""
    if moment is None:
        return 'none'
    now = now or timezone.now()
    if moment < now:
        return 'overdue'
    if moment - now <= timedelta(days=2):
        return 'soon'
    return 'scheduled'


def student_open_quiz_cards(user, course_ids):
    from .selectors import open_quizzes_for_student

    now = timezone.now()
    return [{
        'title': quiz.title,
        'moment': quiz.due_at or quiz.available_until or quiz.available_from or now,
        'is_deadline': bool(quiz.due_at or quiz.available_until),
        'state': 'soon', 'published': True,
        'url': reverse('lms:quiz_detail', kwargs={'course_id': quiz.course_id, 'quiz_id': quiz.pk}),
    } for quiz in open_quizzes_for_student(user, course_ids, now=now)]


def decorate_evaluations(evaluations, *, teacher_view, user):
    """Attach the deadline, its state and a real link to each evaluation."""
    can_admin = (
        teacher_view
        and user.is_staff
        and user.has_perm('evaluations.change_evaluacion')
    )
    now = timezone.now()
    items = []
    for evaluation in evaluations:
        moment = evaluation.fecha_cierre or evaluation.fecha_prueba
        if can_admin:
            url = reverse('admin:evaluations_evaluacion_change', args=[evaluation.pk])
        elif not teacher_view and evaluation.estado == evaluation.ESTADO_PUBLICADA:
            url = reverse('evaluations:detail', args=[evaluation.pk])
        else:
            url = ''
        items.append({
            'object': evaluation,
            'title': evaluation.titulo,
            'moment': moment,
            'is_deadline': bool(evaluation.fecha_cierre),
            'state': due_state(moment, now=now),
            'published': evaluation.estado == evaluation.ESTADO_PUBLICADA,
            'url': url,
        })
    return items


def topic_state_for_progress(topic, *, progress=None, current_topic=None):
    """Return a semantic state label for a learner timeline item."""
    if progress and getattr(progress, 'completado', False):
        return 'completed'
    if current_topic and topic == current_topic:
        return 'current'
    return 'upcoming'


def build_course_timeline(course, topics, progress, *, current_topic=None):
    """Build the ordered timeline used by the course home and area views."""
    del course
    entries = []
    for index, topic in enumerate(topics, start=1):
        record = progress.get(topic.pk) if progress else None
        state = topic_state_for_progress(topic, progress=record, current_topic=current_topic)
        entries.append({
            'index': index,
            'topic': topic,
            'record': record,
            'state': state,
            'is_current': state == 'current',
            'is_complete': state == 'completed',
            'is_upcoming': state == 'upcoming',
        })
    return entries


def teacher_tools(user, course):
    """Real edit entry points for staff-capable teachers (Django admin routes)."""
    tools = {'add_topic_url': '', 'topics_admin_url': '', 'can_edit_topics': False}
    if not user.is_staff:
        return tools
    if user.has_perm('courses.add_tema'):
        tools['add_topic_url'] = f"{reverse('admin:courses_tema_add')}?curso={course.pk}"
    if user.has_perm('courses.change_tema'):
        tools['topics_admin_url'] = (
            f"{reverse('admin:courses_tema_changelist')}?curso__id__exact={course.pk}"
        )
        tools['can_edit_topics'] = True
    return tools


def student_steps(*, enrolled, completed, total, next_due):
    """Three-step guide shown to students on the course home."""
    if not enrolled:
        state_1, state_2, state_3 = 'current', 'upcoming', 'upcoming'
    elif total and completed >= total:
        state_1, state_2, state_3 = 'done', 'done', 'current' if next_due else 'done'
    else:
        state_1, state_2, state_3 = 'done', 'current', 'upcoming'
    return [
        {'label': _('Únete al aula'), 'state': state_1},
        {
            'label': _('Avanza por los temas'),
            'detail': _('%(done)s de %(total)s completados') % {'done': completed, 'total': total},
            'state': state_2,
        },
        {'label': _('Prepara tus entregas'), 'state': state_3},
    ]


def component_fixtures():
    """Deterministic sample data for the component catalogue and its tests."""
    now = timezone.now()
    return {
        'courses': [
            {'title': 'Python desde cero', 'status': 'published', 'role': _('Alumno'), 'percent': 64, 'meta': _('7 de 11 temas')},
            {'title': 'Django profesional', 'status': 'draft', 'role': _('Propietario'), 'percent': 0, 'meta': _('Sin temas todavía')},
        ],
        'due': [
            {'title': 'Práctica 2 · APIs REST', 'moment': now + timedelta(hours=20), 'is_deadline': True, 'state': 'soon', 'published': True, 'url': ''},
            {'title': 'Cuestionario de modelos', 'moment': now + timedelta(days=6), 'is_deadline': False, 'state': 'scheduled', 'published': True, 'url': ''},
            {'title': 'Entrega 1 · Variables', 'moment': now - timedelta(days=1), 'is_deadline': True, 'state': 'overdue', 'published': True, 'url': ''},
        ],
        'timeline': [
            {'title': 'Instalar el entorno', 'state': 'done'},
            {'title': 'Tipos y estructuras de datos', 'state': 'current'},
            {'title': 'Funciones y módulos', 'state': 'upcoming'},
        ],
        'steps': student_steps(enrolled=True, completed=3, total=8, next_due=True),
    }
