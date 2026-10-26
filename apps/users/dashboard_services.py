import logging
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Q
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .access import access_snapshot, can, effective_plan, has_access_role, is_platform_admin
from .billing_models import AdministrativeAccessGrant, MembershipContract
from .dashboard_models import PersonalGoal
from .models import AccessMembership, AccessRoleAssignment

logger = logging.getLogger(__name__)


SOURCE_WITH_DATA = 'available_with_data'
SOURCE_EMPTY = 'available_empty'
SOURCE_NOT_IMPLEMENTED = 'not_implemented'
SOURCE_FAILED = 'temporarily_unavailable'
SOURCE_NOT_AUTHORIZED = 'not_authorized'


def _load_source(name, loader):
    try:
        data, has_data = loader()
    except (DatabaseError, ImportError):
        logger.exception('Dashboard source %s is temporarily unavailable', name)
        return {'status': SOURCE_FAILED, 'data': None, 'updated_at': None}
    return {
        'status': SOURCE_WITH_DATA if has_data else SOURCE_EMPTY,
        'data': data,
        'updated_at': timezone.now(),
    }


def _energy_data(user):
    from apps.energy.services import get_status

    status = get_status(user)
    return status, bool(status.get('ok'))


def _course_data(user):
    from apps.courses.models import InscripcionCurso, Progreso
    from apps.lms.campus import campus_settings, learning_status
    from apps.lms.models import CourseMembership

    enrollments = list(
        InscripcionCurso.objects.filter(usuario=user, completado=False)
        .select_related('curso')
        .order_by('-fecha_inscripcion')[:10]
    )
    courses = []
    completed_lessons = 0
    for enrollment in enrollments:
        total_lessons = enrollment.curso.temas.count()
        completed = Progreso.objects.filter(
            usuario=user,
            tema__curso=enrollment.curso,
            completado=True,
        ).count()
        if campus_settings(enrollment.curso):
            membership = CourseMembership.objects.filter(course=enrollment.curso, user=user).first()
            if membership:
                status = learning_status(membership)
                completed, total_lessons = status['completed'], status['total']
        completed_lessons += completed
        courses.append({
            'course': enrollment.curso,
            'completed': completed,
            'total': total_lessons,
            'percent': int(completed / total_lessons * 100) if total_lessons else None,
            'empty_course': total_lessons == 0,
            'enrollment': enrollment,
        })
    metrics = {
        'completed_lessons': Progreso.objects.filter(usuario=user, completado=True).count(),
        'completed_courses': InscripcionCurso.objects.filter(usuario=user, completado=True).count(),
        'enrollments': InscripcionCurso.objects.filter(usuario=user, completado=False).count(),
        'all_enrollments': InscripcionCurso.objects.filter(usuario=user).count(),
    }
    return {'courses': courses, 'metrics': metrics}, bool(enrollments)


def _activity_data(user, history_limit):
    from apps.analytics.models import RegistroActividad

    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    sixty_days_ago = now - timedelta(days=60)
    local_now = timezone.localtime(now)
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    activities = list(
        RegistroActividad.objects.filter(usuario=user).order_by('-timestamp')[:history_limit]
    )
    queryset = RegistroActividad.objects.filter(usuario=user)
    return {
        'recent': activities,
        'last_30_days': queryset.filter(timestamp__gte=thirty_days_ago).count(),
        'previous_30_days': queryset.filter(
            timestamp__gte=sixty_days_ago,
            timestamp__lt=thirty_days_ago,
        ).count(),
        'this_month': queryset.filter(timestamp__gte=month_start).count(),
        'total': queryset.count(),
    }, bool(activities)


def _achievement_data(user):
    from apps.gamification.models import InsigniaUsuario, LogroUsuario

    badges = list(
        InsigniaUsuario.objects.filter(
            usuario=user,
            status=InsigniaUsuario.Status.VALID,
        )
        .select_related('insignia', 'version')
        .order_by('-fecha_obtenida')[:6]
    )
    achievements = list(
        LogroUsuario.objects.filter(usuario=user)
        .select_related('logro')
        .order_by('-fecha_obtenido')[:6]
    )
    return {'badges': badges, 'achievements': achievements}, bool(badges or achievements)


def _mission_data(user):
    from apps.gamification.models import MisionUsuario

    missions = list(
        MisionUsuario.objects.filter(usuario=user, completada=False)
        .select_related('mision')
        .order_by('-fecha_asignada')[:5]
    )
    return missions, bool(missions)


def _course_teacher_data(user):
    from apps.lms.models import CourseMembership

    memberships = list(
        CourseMembership.objects.filter(
            user=user,
            role__in=[
                CourseMembership.Role.OWNER,
                CourseMembership.Role.TEACHER,
                CourseMembership.Role.ASSISTANT,
            ],
            status=CourseMembership.Status.ACTIVE,
        ).select_related('course').order_by('course__nombre')[:20]
    )
    return memberships, bool(memberships)


def _nexus_data(user):
    from apps.nexus.models import AssessmentSession, DNAProfile, SesionTest

    profile = DNAProfile.objects.filter(user=user).first()
    sessions = list(
        SesionTest.objects.filter(
            alumno=user,
            estado=SesionTest.COMPLETADO,
        ).select_related('test').order_by('-completado_en', '-creado_en')[:6]
    )
    versioned = list(AssessmentSession.objects.filter(
        candidate=user, editorial_version__isnull=False,
    ).order_by('-created_at')[:6])
    return {'profile': profile, 'sessions': sessions, 'versioned': versioned}, bool(profile or sessions or versioned)


def _credential_data(user):
    from apps.certifications.models import Certificado, UserCredential
    from apps.lms.models import CourseCertificate

    declared = UserCredential.objects.filter(
        user=user,
        archived_at__isnull=True,
    ).count()
    internal = Certificado.objects.filter(usuario=user).count()
    lms_internal = CourseCertificate.objects.filter(user=user).count()
    verified = UserCredential.objects.filter(
        user=user,
        archived_at__isnull=True,
        review_status='verified',
    ).count()
    return {
        'declared': declared,
        'internal': internal + lms_internal,
        'verified': verified,
        'total': declared + internal + lms_internal,
    }, bool(declared or internal or lms_internal)


def _goal_metric(user, metric):
    if metric == PersonalGoal.SourceMetric.COMPLETED_LESSONS:
        from apps.courses.models import Progreso

        return Progreso.objects.filter(usuario=user, completado=True).count()
    if metric == PersonalGoal.SourceMetric.COMPLETED_COURSES:
        from apps.courses.models import InscripcionCurso

        return InscripcionCurso.objects.filter(usuario=user, completado=True).count()
    if metric == PersonalGoal.SourceMetric.COMPLETED_NEXUS_TESTS:
        from apps.nexus.models import AssessmentSession, SesionTest

        return (
            SesionTest.objects.filter(alumno=user, estado=SesionTest.COMPLETADO).count()
            + AssessmentSession.objects.filter(
                candidate=user, editorial_version__isnull=False, status=AssessmentSession.STATUS_COMPLETED,
            ).count()
        )
    if metric == PersonalGoal.SourceMetric.ACTIVITY_ENTRIES:
        from apps.analytics.models import RegistroActividad

        return RegistroActividad.objects.filter(usuario=user).count()
    raise ValueError(f'Unsupported personal goal source metric: {metric}')


def goal_progress(user, goal):
    if goal.tracking_type == PersonalGoal.TrackingType.MANUAL:
        return {
            'current': goal.current_value,
            'target': goal.target_value,
            'percent': goal.progress_percent,
            'status': SOURCE_WITH_DATA if goal.current_value is not None else SOURCE_EMPTY,
            'updated_at': goal.updated_at,
        }
    try:
        current = _goal_metric(user, goal.source_metric)
    except (DatabaseError, ImportError):
        logger.exception('Automatic dashboard goal source %s failed', goal.source_metric)
        return {
            'current': None,
            'target': goal.target_value,
            'percent': None,
            'status': SOURCE_FAILED,
            'updated_at': None,
        }
    percent = min(100, int(current / goal.target_value * 100)) if goal.target_value else None
    return {
        'current': current,
        'target': goal.target_value,
        'percent': percent,
        'status': SOURCE_WITH_DATA,
        'updated_at': timezone.now(),
    }


def _membership_data(user, plan):
    now = timezone.now()
    contract = MembershipContract.objects.filter(
        user=user,
        status__in=[
            MembershipContract.Status.ACTIVE,
            MembershipContract.Status.RENEWAL_PENDING,
            MembershipContract.Status.RENEWAL_FAILED,
            MembershipContract.Status.CANCEL_AT_PERIOD_END,
        ],
        starts_at__lte=now,
    ).filter(
        Q(period_ends_at__isnull=True) | Q(period_ends_at__gt=now),
        plan=plan,
    ).order_by('-period_ends_at', '-created_at').first()
    if contract:
        return {
            'plan': plan,
            'origin': 'Contrato de membresía',
            'period': contract.get_billing_period_display(),
            'ends_at': contract.period_ends_at,
            'auto_renew': contract.auto_renew,
            'status': contract.get_status_display(),
        }
    grant = AdministrativeAccessGrant.objects.filter(
        user=user,
        status=AdministrativeAccessGrant.Status.ACTIVE,
        starts_at__lte=now,
        plan=plan,
    ).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gt=now),
    ).order_by('-ends_at', '-created_at').first()
    if grant:
        return {
            'plan': plan,
            'origin': 'Concesión administrativa',
            'period': '',
            'ends_at': grant.ends_at,
            'auto_renew': False,
            'status': grant.get_status_display(),
        }
    legacy = AccessMembership.objects.filter(user=user).first()
    return {
        'plan': plan,
        'origin': 'Membresía de acceso' if legacy and legacy.effective_plan == plan else 'Nivel gratuito',
        'period': legacy.get_billing_period_display() if legacy and legacy.billing_period else '',
        'ends_at': legacy.ends_at if legacy and legacy.effective_plan == plan else None,
        'auto_renew': False,
        'status': legacy.get_status_display() if legacy and legacy.effective_plan == plan else 'Vigente',
    }


def _activity_target(activity):
    routes = {
        'tema_completado': 'courses:list',
        'quiz_completado': 'courses:list',
        'recurso_visualizado': 'courses:list',
        'insignia_obtenida': 'gamification:insignias',
        'recompensa_canjeada': 'rewards:list',
        'mision_completada': 'gamification:misiones',
        'cv_seccion': 'curriculum:dashboard',
        'bug_validado': 'bug_reports:list',
    }
    route = routes.get(activity.tipo)
    if not route:
        return None
    try:
        return reverse(route)
    except NoReverseMatch:
        logger.exception('Dashboard activity target route %s could not be resolved', route)
        return None


def visitor_dashboard_context(request):
    next_url = request.get_full_path()
    return {
        'is_demo': True,
        'demo_cards': [
            {'title': 'Cursos', 'value': 'Ejemplo: 2 en curso', 'meta': 'Dato ficticio de demostración'},
            {'title': 'Actividad reciente', 'value': 'Ejemplo: tema completado', 'meta': 'Dato ficticio de demostración'},
            {'title': 'Logros', 'value': 'Ejemplo: insignia de bienvenida', 'meta': 'Dato ficticio de demostración'},
            {'title': 'Nivel', 'value': 'FREE', 'meta': 'El nivel real se asigna al registrarte'},
        ],
        'demo_signup_url': f"{reverse('users:registro')}?{urlencode({'next': next_url})}",
        'demo_login_url': f"{settings.LOGIN_URL}?{urlencode({'next': next_url})}",
    }


def dashboard_context(user):
    snapshot = access_snapshot(user)
    plan = effective_plan(user)
    is_plus = can(user, 'view_plan_content', required_plan=AccessMembership.Plan.PLUS).allowed
    is_pro = can(user, 'view_plan_content', required_plan=AccessMembership.Plan.PRO).allowed
    is_student = has_access_role(user, AccessRoleAssignment.Role.ALUMNO)
    courses = _load_source('courses', lambda: _course_data(user))
    activities = _load_source(
        'activity',
        lambda: _activity_data(user, 20 if is_plus else 5),
    )
    achievements = _load_source('achievements', lambda: _achievement_data(user))
    missions = _load_source('missions', lambda: _mission_data(user))
    nexus = _load_source('nexus', lambda: _nexus_data(user))
    certifications = _load_source('certifications', lambda: _credential_data(user))

    roles = snapshot.get('roles', [])
    is_teacher = has_access_role(user, AccessRoleAssignment.Role.PROFESOR) or any(
        role['role'] == AccessRoleAssignment.Role.PROFESOR for role in roles
    )
    role_courses = (
        _load_source('teacher_courses', lambda: _course_teacher_data(user))
        if is_teacher else {'status': SOURCE_NOT_AUTHORIZED, 'data': [], 'updated_at': None}
    )

    course_data = courses['data'] or {'courses': [], 'metrics': {}}
    activity_data = activities['data'] or {
        'recent': [], 'last_30_days': None, 'previous_30_days': None, 'this_month': None, 'total': None,
    }
    achievement_data = achievements['data'] or {'badges': [], 'achievements': []}
    nexus_data = nexus['data'] or {'profile': None, 'sessions': []}
    mission_data = missions['data'] or []
    certification_data = certifications['data'] or {}

    summary_cards = [
        {
            'title': 'Cursos en marcha',
            'value': course_data['metrics'].get('enrollments', 'No disponible')
            if courses['status'] != SOURCE_FAILED else 'No disponible',
            'meta': 'Matrículas propias',
            'icon': 'bi-book',
            'tone': 'primary',
            'href': reverse('courses:list'),
        },
        {
            'title': 'Insignias',
            'value': len(achievement_data['badges']) if achievements['status'] != SOURCE_FAILED else 'No disponible',
            'meta': 'Insignias recientes de esta cuenta',
            'icon': 'bi-award',
            'tone': 'success',
            'href': reverse('gamification:insignias'),
        },
        {
            'title': 'Puntos',
            'value': user.puntos_totales,
            'meta': 'Total acumulado',
            'icon': 'bi-star-fill',
            'tone': 'secondary',
            'href': reverse('users:progress'),
        },
        {
            'title': 'Mis certificaciones',
            'value': certification_data.get('total', 'No disponible')
            if certifications['status'] != SOURCE_FAILED else 'No disponible',
            'meta': (
                f"{certification_data.get('verified', 0)} verificadas · "
                f"{certification_data.get('internal', 0)} emisiones internas"
                if certifications['status'] != SOURCE_FAILED
                else 'Fuente temporalmente no disponible'
            ),
            'icon': 'bi-patch-check',
            'tone': 'info',
            'href': reverse('certifications:list'),
        },
    ]

    active_courses = course_data['courses']
    next_mission = mission_data[0] if mission_data else None
    next_course = active_courses[0] if active_courses else None
    if next_mission:
        next_action = {
            'title': next_mission.mision.titulo,
            'description': next_mission.mision.descripcion,
            'href': reverse('gamification:misiones'),
            'badge': f'{next_mission.progreso}/{next_mission.mision.requisito_cantidad}',
        }
    elif next_course:
        next_action = {
            'title': next_course['course'].nombre,
            'description': 'Continúa una formación en la que ya estás inscrito/a.',
            'href': reverse('courses:detail', args=[next_course['course'].pk]),
            'badge': (
                'Progreso no disponible'
                if next_course['percent'] is None
                else f'{next_course["percent"]}% completado'
            ),
        }
    else:
        next_action = {
            'title': 'Explora cursos',
            'description': 'Consulta las formaciones disponibles para tu cuenta.',
            'href': reverse('courses:list'),
            'badge': 'Disponible',
        }

    for activity in activity_data['recent']:
        activity.dashboard_href = _activity_target(activity)

    quick_actions = [
        {'label': 'Editar mi CV', 'href': reverse('curriculum:dashboard'), 'icon': 'bi-file-earmark-person'},
        {'label': 'Explorar cursos', 'href': reverse('courses:list'), 'icon': 'bi-book'},
        {'label': 'Retomar evaluaciones', 'href': reverse('nexus:student_list'), 'icon': 'bi-clipboard2-check'},
        {'label': 'Entrenamiento', 'href': reverse('minigames:hub'), 'icon': 'bi-controller'},
        {'label': 'Mis certificaciones', 'href': reverse('certifications:list'), 'icon': 'bi-patch-check'},
    ]
    if user.is_staff:
        quick_actions.append({'label': 'Administración', 'href': reverse('admin:index'), 'icon': 'bi-gear'})
    if is_platform_admin(user):
        quick_actions.append({
            'label': 'Copias JSON: prompts, blog y enlaces',
            'href': reverse('data_admin:content_backups'), 'icon': 'bi-database-down',
        })

    monthly = [
        {
            'label': 'Actividad registrada este mes',
            'value': activity_data['this_month'],
            'status': activities['status'],
        },
        {
            'label': 'Matrículas propias',
            'value': course_data['metrics'].get('all_enrollments') if courses['status'] != SOURCE_FAILED else None,
            'status': courses['status'],
        },
        {
            'label': 'Temas completados',
            'value': course_data['metrics'].get('completed_lessons') if courses['status'] != SOURCE_FAILED else None,
            'status': courses['status'],
        },
    ]

    period_comparison = {
        'current_label': 'Últimos 30 días',
        'previous_label': '30 días anteriores',
        'current': activity_data['last_30_days'],
        'previous': activity_data['previous_30_days'],
        'status': activities['status'],
    }
    if activities['status'] != SOURCE_FAILED and period_comparison['current'] is not None:
        period_comparison['difference'] = (
            period_comparison['current'] - period_comparison['previous']
        )
    else:
        period_comparison['difference'] = None

    goals = [
        {'goal': goal, 'progress': goal_progress(user, goal)}
        for goal in PersonalGoal.objects.filter(user=user, status=PersonalGoal.Status.ACTIVE).order_by('target_date', '-updated_at')[:20]
    ]

    energy = _load_source('energy', lambda: _energy_data(user))
    source_statuses = {
        'activity': activities['status'],
        'courses': courses['status'],
        'achievements': achievements['status'],
        'missions': missions['status'],
        'nexus': nexus['status'],
        'certifications': certifications['status'],
        'employment': SOURCE_NOT_IMPLEMENTED,
        'ai_quota': energy['status'],
        'ducky_bank': SOURCE_NOT_IMPLEMENTED,
        'teacher_courses': role_courses['status'],
        'academy_management': SOURCE_NOT_IMPLEMENTED if any(
            role['role'] == AccessRoleAssignment.Role.ACADEMIA for role in roles
        ) else SOURCE_NOT_AUTHORIZED,
        'recruiter_processes': SOURCE_NOT_IMPLEMENTED if any(
            role['role'] == AccessRoleAssignment.Role.HEADHUNTER for role in roles
        ) else SOURCE_NOT_AUTHORIZED,
    }

    return {
        'is_demo': False,
        'effective_plan': plan,
        'access': snapshot,
        'membership_summary': _membership_data(user, plan),
        'roles': roles,
        'is_plus': is_plus,
        'is_pro': is_pro,
        'is_student': is_student,
        'is_teacher': is_teacher,
        'ducky_summary_cards': summary_cards,
        'proxima_accion': next_action,
        'cursos_en_marcha': [
            {
                'curso': item['course'],
                'temas_completados': item['completed'],
                'total_temas': item['total'],
                'porcentaje': item['percent'],
                'empty_course': item['empty_course'],
            }
            for item in active_courses[:4]
        ],
        'inscripciones': [item['enrollment'] for item in active_courses],
        'insignias_recientes': achievement_data['badges'],
        'logros_recientes': achievement_data['achievements'],
        'canjes_recientes': [],
        'misiones_activas': mission_data[:3],
        'actividades': activity_data['recent'][:5],
        'lecturas_posts': [],
        'total_posts_vistos': None,
        'total_posts_con_puntos': None,
        'mis_bugs_total': None,
        'mis_bugs_validados': None,
        'ducky_profile': None,
        'dna_profile': nexus_data['profile'],
        'dna_profile_url': reverse('nexus:student_my_dna'),
        'results_url': reverse('nexus:catalog'),
        'quick_actions': quick_actions,
        'source_statuses': source_statuses,
        'energy_source': energy,
        'activity_source': activities,
        'courses_source': courses,
        'achievements_source': achievements,
        'missions_source': missions,
        'nexus_source': nexus,
        'certifications_source': certifications,
        'nexus_sessions': nexus_data['sessions'],
        'nexus_versioned_sessions': nexus_data.get('versioned', []),
        'monthly_metrics': monthly if is_plus else [],
        'period_comparison': period_comparison if is_pro else None,
        'goals': goals,
        'can_manage_goals': is_pro,
        'goal_access_reason': can(
            user,
            'view_plan_content',
            required_plan=AccessMembership.Plan.PRO,
        ).reason,
        'manual_goal_count': PersonalGoal.objects.filter(
            user=user,
            tracking_type=PersonalGoal.TrackingType.MANUAL,
        ).exclude(status=PersonalGoal.Status.ARCHIVED).count() if is_pro else None,
        'automatic_goal_integration_pending': True,
        'teacher_courses': role_courses['data'] or [],
        'has_membership_badge': False,
        'membership_badge': plan.upper() if plan in {'plus', 'pro'} else '',
    }
