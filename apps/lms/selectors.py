from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.courses.models import InscripcionCurso

from .models import CourseMembership, LearningProgress, ModuleProgress, Quiz
from .policies import TEACHING_ROLES, course_evaluations, legacy_student_enrollments


def memberships_for_dashboard(user):
    return CourseMembership.objects.filter(
        user=user,
        status__in=[
            CourseMembership.Status.ACTIVE,
            CourseMembership.Status.SUSPENDED,
            CourseMembership.Status.COMPLETED,
        ],
    ).select_related('course', 'course__lms_settings', 'section')


def student_courses(user):
    memberships = memberships_for_dashboard(user).filter(
        role__in=[CourseMembership.Role.STUDENT, CourseMembership.Role.OBSERVER],
    )
    legacy_courses = legacy_student_enrollments(user).select_related('curso')
    return memberships, legacy_courses


def open_quizzes_for_student(user, course_ids, *, now=None):
    timestamp = now or timezone.now()
    memberships = CourseMembership.objects.filter(
        user=user, role=CourseMembership.Role.STUDENT, status=CourseMembership.Status.ACTIVE,
    )
    legacy_ids = legacy_student_enrollments(user).values_list('curso_id', flat=True)
    quizzes = Quiz.objects.filter(
        course_id__in=course_ids, status=Quiz.Status.PUBLISHED,
    ).filter(
        Q(course_id__in=memberships.values('course_id')) | Q(course_id__in=legacy_ids),
    ).filter(
        Q(available_from__isnull=True) | Q(available_from__lte=timestamp),
    ).filter(
        Q(available_until__isnull=True) | Q(available_until__gt=timestamp),
    ).filter(
        Q(due_at__isnull=True) | Q(due_at__gt=timestamp),
    ).filter(
        Q(section__isnull=True) | Q(section_id__in=memberships.values('section_id')),
    ).select_related('course').order_by('due_at', 'available_until', 'pk')
    return quizzes


def teacher_memberships(user):
    return CourseMembership.objects.filter(
        user=user,
        role__in=TEACHING_ROLES,
        status=CourseMembership.Status.ACTIVE,
    ).select_related('course', 'section').order_by('course__nombre')


def course_participants(course):
    memberships = CourseMembership.objects.filter(course=course)
    participants = [
        {
            'user': membership.user,
            'role': membership.get_role_display(),
            'role_key': membership.role,
            'status': membership.get_status_display(),
            'section': membership.section,
            'joined_at': membership.joined_at or membership.created_at,
        }
        for membership in memberships.filter(
            status__in=[
                CourseMembership.Status.ACTIVE,
                CourseMembership.Status.SUSPENDED,
                CourseMembership.Status.COMPLETED,
            ],
        ).select_related('user', 'section')
    ]
    participants.extend(
        {
            'user': enrollment.usuario,
            'role': CourseMembership.Role.STUDENT.label,
            'role_key': CourseMembership.Role.STUDENT,
            'status': (
                CourseMembership.Status.COMPLETED.label if enrollment.completado
                else CourseMembership.Status.ACTIVE.label
            ),
            'section': None,
            'joined_at': enrollment.fecha_inscripcion,
        }
        for enrollment in InscripcionCurso.objects.filter(curso=course).exclude(
            usuario_id__in=memberships.values('user_id'),
        ).select_related('usuario')
    )
    return sorted(participants, key=lambda person: (
        (person['user'].get_full_name() or person['user'].username).casefold(),
        person['user'].pk,
    ))


def discoverable_courses(user):
    enrolled_ids = CourseMembership.objects.filter(
        user=user,
    ).values('course_id')
    legacy_ids = InscripcionCurso.objects.filter(
        usuario=user,
    ).values('curso_id')
    return user_courses_queryset().filter(
        activo=True,
    ).exclude(
        Q(pk__in=enrolled_ids) | Q(pk__in=legacy_ids),
    ).order_by('-destacado', '-creado_en', 'nombre')


def user_courses_queryset():
    from apps.courses.models import Curso

    return Curso.objects.all()


def editable_courses(user):
    from apps.courses.models import Curso

    if (
        user.is_superuser
        or user.has_perm('lms.manage_platform')
        or user.has_perm('courses.change_curso')
    ):
        return Curso.objects.all()
    return Curso.objects.filter(
        lms_memberships__user=user,
        lms_memberships__status=CourseMembership.Status.ACTIVE,
        lms_memberships__role__in=TEACHING_ROLES,
    ).distinct()


def upcoming_evaluations(course, *, include_unpublished=False):
    now = timezone.now()
    queryset = course_evaluations(course, include_unpublished=include_unpublished)
    return queryset.filter(
        Q(fecha_cierre__gte=now) | Q(fecha_prueba__gte=now),
    ).order_by('fecha_cierre', 'fecha_prueba', 'titulo')[:8]


def switcher_courses(user):
    """Courses the user belongs to (any capacity), for the header course selector."""
    from apps.courses.models import Curso

    member_ids = CourseMembership.objects.filter(
        user=user,
        status__in=[
            CourseMembership.Status.ACTIVE,
            CourseMembership.Status.SUSPENDED,
            CourseMembership.Status.COMPLETED,
        ],
    ).values('course_id')
    legacy_ids = InscripcionCurso.objects.filter(usuario=user).values('curso_id')
    return Curso.objects.filter(
        Q(pk__in=member_ids) | Q(pk__in=legacy_ids),
    ).only('pk', 'nombre', 'activo').order_by('nombre')


def course_teachers(course):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(
        course_memberships__course=course,
        course_memberships__status=CourseMembership.Status.ACTIVE,
        course_memberships__role__in=[
            CourseMembership.Role.OWNER,
            CourseMembership.Role.TEACHER,
        ],
    ).distinct().order_by('first_name', 'username')


def progress_by_course(user, course_ids):
    """Return {course_id: (completed_topics, total_topics, percent)}."""
    from django.db.models import Count

    from apps.courses.models import Progreso, Tema

    totals = dict(
        Tema.objects.filter(curso_id__in=course_ids)
        .values('curso_id')
        .annotate(total=Count('pk'))
        .values_list('curso_id', 'total')
    )
    done = dict(
        Progreso.objects.filter(
            usuario=user,
            completado=True,
            tema__curso_id__in=course_ids,
        )
        .values('tema__curso_id')
        .annotate(done=Count('pk'))
        .values_list('tema__curso_id', 'done')
    )
    result = {}
    for course_id in course_ids:
        total = totals.get(course_id, 0)
        completed = min(done.get(course_id, 0), total)
        percent = round(completed * 100 / total) if total else 0
        result[course_id] = (completed, total, percent)
    from .campus import learning_status
    for membership in CourseMembership.objects.filter(
        user=user, course_id__in=course_ids, course__lms_settings__campus_enabled=True,
    ).select_related('user'):
        status = learning_status(membership)
        result[membership.course_id] = (status['completed'], status['total'], status['percent'])
    return result

def course_learning_summary(course):
    """Return a privacy-safe, aggregate view of a course learning status."""
    active_students = CourseMembership.objects.filter(
        course=course,
        role=CourseMembership.Role.STUDENT,
        status=CourseMembership.Status.ACTIVE,
    ).count()
    rows = LearningProgress.objects.filter(course=course)
    stats = rows.aggregate(
        average_completion=Avg('completion_percentage'),
        completed_students=Count('pk', filter=Q(completion_percentage__gte=100)),
        active_trackers=Count('pk'),
    )
    return {
        'active_students': active_students,
        'tracked_students': stats['active_trackers'] or 0,
        'average_completion': round(stats['average_completion'] or 0, 1),
        'completed_students': stats['completed_students'] or 0,
        'completion_rate': round((stats['completed_students'] or 0) * 100 / active_students) if active_students else 0,
        'module_progress': ModuleProgress.objects.filter(course=course).aggregate(
            averages=Avg('percentage'),
            completed_modules=Count('pk', filter=Q(status=ModuleProgress.Status.COMPLETED)),
        ),
    }

def searchable_courses(user, *, platform_admin=False):
    """Courses whose landing page the user may open (mirrors can_view_course)."""
    from apps.courses.models import Curso

    if platform_admin:
        return Curso.objects.all()
    member_ids = CourseMembership.objects.filter(
        user=user,
        status__in=[
            CourseMembership.Status.ACTIVE,
            CourseMembership.Status.SUSPENDED,
            CourseMembership.Status.COMPLETED,
        ],
    ).values('course_id')
    legacy_ids = InscripcionCurso.objects.filter(usuario=user).values('curso_id')
    return Curso.objects.filter(
        Q(activo=True) | Q(pk__in=member_ids) | Q(pk__in=legacy_ids),
    )


def search_campus(user, query, *, platform_admin=False, limit=20):
    from apps.courses.models import Tema

    query = (query or '').strip()
    if len(query) < 2:
        return [], []
    courses = searchable_courses(user, platform_admin=platform_admin)
    course_hits = courses.filter(
        Q(nombre__icontains=query) | Q(descripcion__icontains=query),
    ).order_by('-activo', 'nombre')[:limit]
    topic_hits = Tema.objects.filter(
        curso__in=courses,
        titulo__icontains=query,
    ).select_related('curso').order_by('curso__nombre', 'orden')[:limit]
    return list(course_hits), list(topic_hits)


def upcoming_for_student(user, course_ids):
    now = timezone.now()
    enrollments = legacy_student_enrollments(user).filter(
        curso_id__in=course_ids,
    )
    member_course_ids = CourseMembership.objects.filter(
        user=user,
        course_id__in=course_ids,
        role__in=[CourseMembership.Role.STUDENT, CourseMembership.Role.OBSERVER],
        status__in=[
            CourseMembership.Status.ACTIVE,
            CourseMembership.Status.SUSPENDED,
            CourseMembership.Status.COMPLETED,
        ],
    ).values('course_id')
    from apps.evaluations.models import Evaluacion

    return Evaluacion.objects.filter(
        Q(tema__curso_id__in=member_course_ids)
        | Q(curso_id__in=member_course_ids)
        | Q(cursos__in=member_course_ids)
        | Q(tema__curso__inscripciones__in=enrollments)
        | Q(curso__inscripciones__in=enrollments)
        | Q(cursos__inscripciones__in=enrollments),
        estado=Evaluacion.ESTADO_PUBLICADA,
    ).filter(
        Q(fecha_cierre__gte=now) | Q(fecha_prueba__gte=now),
    ).select_related('curso', 'tema').distinct().order_by(
        'fecha_cierre',
        'fecha_prueba',
        'titulo',
    )[:8]
def group_question_banks(banks):
    import re

    groups = {}
    for bank in banks:
        match = re.fullmatch(r'(.+?)\s+([1-5])\s*/\s*5', bank.title.strip())
        title = match.group(1).strip() if match else bank.title
        group = groups.setdefault(title, {'title': title, 'banks': []})
        group['banks'].append(bank)
    return list(groups.values())

