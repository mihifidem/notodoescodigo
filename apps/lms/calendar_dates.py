from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import CalendarEvent, CourseMembership, LearningActivity, ModuleSchedule, Page, Quiz
from .policies import can_edit_course, can_view_course, course_evaluations
from .selectors import switcher_courses


def calendar_dates(user, *, course_id=None):
    events = []
    seen = set()

    def add(key, title, value, *, course=None, url='', all_day=False, zone=None):
        if value is None or key in seen:
            return
        zone = zone or (course.zona_horaria if course else timezone.get_current_timezone_name())
        if all_day:
            value = timezone.make_aware(datetime.combine(value, time.min), ZoneInfo(zone))
        seen.add(key)
        events.append({
            'key': key, 'title': title, 'moment': value, 'course': course,
            'url': url, 'all_day': all_day, 'time_zone': zone,
        })

    courses = switcher_courses(user).select_related('lms_settings')
    if course_id is not None:
        courses = courses.filter(pk=course_id)
    for course in courses:
        if not can_view_course(user, course):
            continue
        teacher = can_edit_course(user, course)
        course_url = reverse('lms:course_detail', kwargs={'course_id': course.pk})
        settings = getattr(course, 'lms_settings', None)
        for name, label, date in [
            ('start', _('Inicio del curso'), course.fecha_inicio),
            ('end', _('Fin del curso'), course.fecha_fin),
        ]:
            exact = getattr(settings, 'starts_at' if name == 'start' else 'ends_at', None)
            add(f'course:{course.pk}:{name}', f'{label}: {course.nombre}',
                exact or date, course=course, url=course_url, all_day=not bool(exact))
        module_url = reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': 'content'})
        for schedule in ModuleSchedule.objects.filter(module__curso=course).select_related('module'):
            for name, label in [('starts_at', _('Inicio del módulo')), ('ends_at', _('Fin del módulo'))]:
                add(f'module:{schedule.module_id}:{name}', f'{label}: {schedule.module.titulo}',
                    getattr(schedule, name), course=course, url=module_url)
        for quiz in Quiz.objects.filter(course=course):
            if not teacher:
                if quiz.status not in (Quiz.Status.PUBLISHED, Quiz.Status.CLOSED):
                    continue
                if quiz.section_id and not CourseMembership.objects.filter(
                    course=course, user=user, section_id=quiz.section_id,
                ).exists():
                    continue
            url = reverse('lms:quiz_detail', kwargs={'course_id': course.pk, 'quiz_id': quiz.pk})
            for name, label in [
                ('available_from', _('Inicio de la prueba')),
                ('available_until', _('Cierre de la prueba')),
                ('due_at', _('Entrega de la prueba')),
                ('publish_grade_at', _('Publicación de notas')),
                ('answers_released_at', _('Soluciones de la prueba')),
            ]:
                add(f'quiz:{quiz.pk}:{name}', f'{label}: {quiz.title}', getattr(quiz, name), course=course, url=url)
        for activity in LearningActivity.objects.filter(course=course):
            if not teacher and activity.status == LearningActivity.Status.DRAFT:
                continue
            url = reverse('lms:course_activity_detail', kwargs={'course_id': course.pk, 'activity_id': activity.pk})
            for name, label in [
                ('starts_at', _('Inicio de la actividad')),
                ('due_at', _('Entrega de la actividad')),
                ('feedback_at', _('Feedback de la actividad')),
                ('solution_release_at', _('Solución de la actividad')),
            ]:
                add(f'activity:{activity.pk}:{name}', f'{label}: {activity.title}', getattr(activity, name), course=course, url=url)
        for page in Page.objects.filter(course=course):
            if not teacher and page.status != Page.Status.PUBLISHED:
                continue
            url = reverse('lms:course_page_detail', kwargs={'course_id': course.pk, 'page_slug': page.slug})
            for name, label in [('publish_at', _('Publicación de la página')), ('expires_at', _('Cierre de la página'))]:
                add(f'page:{page.pk}:{name}', f'{label}: {page.title}', getattr(page, name), course=course, url=url)
        for evaluation in course_evaluations(course, include_unpublished=teacher).distinct():
            for name, label in [('fecha_prueba', _('Inicio de la evaluación')), ('fecha_cierre', _('Cierre de la evaluación'))]:
                add(f'evaluation:{evaluation.pk}:{name}', f'{label}: {evaluation.titulo}',
                    getattr(evaluation, name), course=course, url=course_url)
    personal_events = CalendarEvent.objects.for_user(user).select_related('course')
    if course_id is not None:
        personal_events = personal_events.filter(course_id=course_id)
    for event in personal_events:
        if event.course and not can_view_course(user, event.course):
            continue
        add(f'event:{event.pk}:start', event.title, event.starts_at, course=event.course, zone=event.time_zone)
        add(f'event:{event.pk}:end', _('Fin: %(title)s') % {'title': event.title}, event.ends_at,
            course=event.course, zone=event.time_zone)
    return sorted(events, key=lambda event: (event['moment'], event['title'], event['key']))


def course_calendar_days(user, course, weeks):
    by_date = {}
    for event in calendar_dates(user, course_id=course.pk):
        day = timezone.localtime(event['moment'], ZoneInfo(event['time_zone'])).date()
        by_date.setdefault(day, []).append(event)
    calendar_url = reverse('lms:global_area', kwargs={'area': 'calendar'})
    return [[{
        'date': day,
        'events': by_date.get(day, []),
        'description': ' · '.join(event['title'] for event in by_date.get(day, [])),
        'url': (
            f"{calendar_url}#calendar-event-{by_date[day][0]['key']}" if day in by_date else ''
        ),
    } for day in week] for week in weeks]
