from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.gamification.models import InsigniaUsuario

from .dashboard_models import PersonalGoal
from .dashboard_services import goal_progress


EXCLUDED_ACTIVITY_TYPES = {
    'tema_completado',
    'quiz_completado',
    'insignia_obtenida',
}


def _window(date_from, date_to):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(date_from, time.min), tz) if date_from else None
    end = (
        timezone.make_aware(datetime.combine(date_to + timedelta(days=1), time.min), tz)
        if date_to else None
    )
    return start, end


def _filtered(queryset, field, start, end):
    if start:
        queryset = queryset.filter(**{f'{field}__gte': start})
    if end:
        queryset = queryset.filter(**{f'{field}__lt': end})
    return queryset


def personal_progress(user, *, date_from=None, date_to=None, area='all'):
    from apps.analytics.models import RegistroActividad
    from apps.courses.models import InscripcionCurso, Progreso, ResultadoQuiz
    from apps.nexus.models import SesionTest

    start, end = _window(date_from, date_to)
    areas = {'all', 'formation', 'competencies', 'games', 'employment'}
    if area not in areas:
        raise ValidationError('El área seleccionada no es válida.')

    rows = []
    statuses = {'formation': 'available', 'competencies': 'available'}

    if area in {'all', 'formation'}:
        progress = _filtered(
            Progreso.objects.filter(usuario=user, completado=True).select_related('tema__curso'),
            'fecha_completado',
            start,
            end,
        ).order_by('-fecha_completado', '-pk')
        enrollments = _filtered(
            InscripcionCurso.objects.filter(usuario=user, completado=True).select_related('curso'),
            'fecha_completado',
            start,
            end,
        ).order_by('-fecha_completado', '-pk')
        quizzes = _filtered(
            ResultadoQuiz.objects.filter(usuario=user).select_related('quiz__tema__curso'),
            'fecha',
            start,
            end,
        ).order_by('-fecha', '-pk')
        rows.extend(
            {
                'area': 'formation',
                'title': item.tema.titulo,
                'description': f'Tema completado · {item.tema.curso.nombre}',
                'date': item.fecha_completado,
                'result': 'Completado',
                'url': f'/cursos/{item.tema.curso_id}/',
                'metric': 'completed_lessons',
                'version': None,
            }
            for item in progress
        )
        rows.extend(
            {
                'area': 'formation',
                'title': item.curso.nombre,
                'description': 'Curso completado',
                'date': item.fecha_completado,
                'result': 'Completado',
                'url': f'/cursos/{item.curso_id}/',
                'metric': 'completed_courses',
                'version': None,
            }
            for item in enrollments
        )
        rows.extend(
            {
                'area': 'formation',
                'title': item.quiz.titulo,
                'description': f'Evaluación · {item.quiz.tema.curso.nombre}',
                'date': item.fecha,
                'result': f'{item.porcentaje}% · {"Aprobada" if item.aprobado else "No aprobada"}',
                'url': f'/cursos/{item.quiz.tema.curso_id}/',
                'metric': 'quiz_attempts',
                'version': None,
            }
            for item in quizzes
        )

    if area in {'all', 'competencies'}:
        from apps.nexus.models import AssessmentSession
        versioned = _filtered(
            AssessmentSession.objects.filter(
                candidate=user, editorial_version__isnull=False, status=AssessmentSession.STATUS_COMPLETED,
            ),
            'completed_at', start, end,
        )
        rows.extend({
            'area': 'competencies',
            'title': item.frozen_definition['title'],
            'description': 'Evaluación Nexus versionada · resultado privado, sin ranking de rasgos',
            'date': item.completed_at, 'result': 'Completada',
            'url': f'/nexus/intento/{item.pk}/informe/',
            'metric': 'completed_nexus_tests',
            'version': str(item.frozen_definition['version']),
        } for item in versioned)
        sessions = _filtered(
            SesionTest.objects.filter(
                alumno=user,
                estado=SesionTest.COMPLETADO,
            ).select_related('test'),
            'completado_en',
            start,
            end,
        ).order_by('-completado_en', '-pk')
        rows.extend(
            {
                'area': 'competencies',
                'title': item.test.nombre,
                'description': 'Evaluación Nexus completada · resultado personal privado',
                'date': item.completado_en,
                'result': f'{item.nexus_score} puntos' if item.nexus_score is not None else 'Completada',
                'url': f'/nexus/sesion/{item.pk}/resultados/',
                'metric': 'completed_nexus_tests',
                'version': item.version_test or 'No especificada por el origen',
            }
            for item in sessions
        )

    if area in {'all', 'games'}:
        statuses['games'] = 'not_implemented'
    if area in {'all', 'employment'}:
        statuses['employment'] = 'not_implemented'

    if area == 'all':
        activities = _filtered(
            RegistroActividad.objects.filter(usuario=user)
            .exclude(tipo__in=EXCLUDED_ACTIVITY_TYPES),
            'timestamp',
            start,
            end,
        ).order_by('-timestamp', '-pk')[:200]
        rows.extend(
            {
                'area': 'activity',
                'title': item.get_tipo_display(),
                'description': item.descripcion,
                'date': item.timestamp,
                'result': 'Registrada',
                'url': '',
                'metric': item.tipo,
                'version': None,
            }
            for item in activities
        )

    rows.sort(
        key=lambda row: row['date'] or datetime.min.replace(tzinfo=timezone.get_current_timezone()),
        reverse=True,
    )
    has_area_source = area not in {'games', 'employment'}
    goals = [
        {'goal': goal, 'progress': goal_progress(user, goal)}
        for goal in PersonalGoal.objects.filter(user=user).exclude(
            status=PersonalGoal.Status.ARCHIVED
        ).order_by('target_date', '-updated_at')[:20]
    ]
    badges = list(
        InsigniaUsuario.objects.filter(
            usuario=user,
            status=InsigniaUsuario.Status.VALID,
        ).select_related('insignia', 'version').order_by('-fecha_obtenida')[:10]
    )

    start_of_month = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_activities = RegistroActividad.objects.filter(
        usuario=user,
        timestamp__gte=start_of_month,
    ).count()
    summary = {
        'completed_lessons': _filtered(
            Progreso.objects.filter(usuario=user, completado=True),
            'fecha_completado',
            start,
            end,
        ).count() if area in {'all', 'formation'} else None,
        'completed_courses': _filtered(
            InscripcionCurso.objects.filter(usuario=user, completado=True),
            'fecha_completado',
            start,
            end,
        ).count() if area in {'all', 'formation'} else None,
        'quiz_attempts': _filtered(
            ResultadoQuiz.objects.filter(usuario=user),
            'fecha',
            start,
            end,
        ).count() if area in {'all', 'formation'} else None,
        'completed_nexus_tests': _filtered(
            SesionTest.objects.filter(alumno=user, estado=SesionTest.COMPLETADO),
            'completado_en',
            start,
            end,
        ).count() + versioned.count() if area in {'all', 'competencies'} else None,
        'valid_badges': len(badges),
        'monthly_activities': monthly_activities,
        'has_source_data': bool(rows) if has_area_source else False,
    }
    return {
        'rows': rows,
        'goals': goals,
        'badges': badges,
        'summary': summary,
        'source_statuses': statuses,
        'generated_at': timezone.now(),
    }


def compare_personal_progress(user, *, current_start, current_end):
    from apps.courses.models import InscripcionCurso, Progreso, ResultadoQuiz

    span = current_end - current_start
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - span
    current_start_at, current_end_at = _window(current_start, current_end)
    previous_start_at, previous_end_at = _window(previous_start, previous_end)

    def metric(queryset, field, start, end):
        return _filtered(queryset, field, start, end).count()

    metrics = {
        'completed_lessons': (
            Progreso.objects.filter(usuario=user, completado=True),
            'fecha_completado',
        ),
        'completed_courses': (
            InscripcionCurso.objects.filter(usuario=user, completado=True),
            'fecha_completado',
        ),
        'quiz_attempts': (ResultadoQuiz.objects.filter(usuario=user), 'fecha'),
    }
    comparison = {}
    for key, (queryset, field) in metrics.items():
        previous = metric(queryset, field, previous_start_at, previous_end_at)
        current = metric(queryset, field, current_start_at, current_end_at)
        comparison[key] = {
            'previous': previous,
            'current': current,
            'difference': current - previous,
            'percent_change': (
                (current - previous) / previous * 100
                if previous else None
            ),
        }
    return {
        'previous_start': previous_start,
        'previous_end': previous_end,
        'current_start': current_start,
        'current_end': current_end,
        'metrics': comparison,
    }
