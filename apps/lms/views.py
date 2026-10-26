from calendar import Calendar
import csv
from datetime import timedelta
from pathlib import Path

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import BigIntegerField, Count, Max, Q, Sum
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
from django.db.models.deletion import ProtectedError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext, gettext_lazy as _, ngettext
from django.views import View
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView

from apps.courses.models import Curso, Progreso, Tema

from .consumers import broadcast_course_update
from .forms import (
    AnnouncementCommentForm,
    AnnouncementForm,
    CourseForm,
    CourseSettingsForm,
    CourseCategoryForm,
    DiscussionTopicForm,
    FileAssetForm,
    LearningActivityForm,
    ModuleForm,
    PageForm,
    PeopleNotificationForm,
)
from .models import (
    Announcement,
    AnnouncementAcknowledgement,
    AnnouncementComment,
    AuditEvent,
    CalendarEvent,
    CourseMembership,
    CourseModule,
    CourseSettingsLMS,
    CourseSyllabus,
    DiscussionTopic,
    FileAsset,
    FileAssetLink,
    LearningActivity,
    LearningEvent,
    LearningProgress,
    Notification,
    NotificationEvent,
    NotificationOutbox,
    Page,
    PageRevision,
    Quiz,
    QuestionBank,
    Section,
    Submission,
)
from .policies import (
    ACTIVE_READ_STATUSES,
    can_edit_course,
    can_enroll,
    can_publish_course,
    can_view_course,
    is_course_member,
    is_platform_admin,
)
from .selectors import (
    course_participants,
    course_learning_summary,
    course_teachers,
    discoverable_courses,
    editable_courses,
    memberships_for_dashboard,
    progress_by_course,
    student_courses,
    teacher_memberships,
    upcoming_evaluations,
    upcoming_for_student,
)
from .services import (
    EnrollmentUnavailable,
    create_course,
    enroll_student,
    get_or_create_lms_profile,
    publish_course,
    notify_announcement_published,
    queue_notification_event,
    send_people_notification,
    update_course,
)
from .teacher_overview import teacher_overview
from .ui import (
    AREA_KEYS,
    COURSE_AREAS,
    STUDENT_AREA_KEYS,
    build_course_timeline,
    course_nav,
    decorate_evaluations,
    student_open_quiz_cards,
    pending_announcements_count,
    pending_announcement_ids,
    student_steps,
    teacher_tools,
    ui_preferences,
)


class LmsContextMixin:
    def get_base_context(self):
        return {
            'platform_admin': is_platform_admin(self.request.user),
            'can_create_course': self.request.user.has_perm('courses.add_curso'),
            **ui_preferences(
                getattr(self.request.user, 'lms_profile', None)
            ),
        }


class FormFieldValidationView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, form_type, *args, **kwargs):
        form_classes = {
            'course': CourseForm, 'page': PageForm,
            'announcement': AnnouncementForm, 'module': ModuleForm, 'discussion': DiscussionTopicForm,
        }
        if form_type not in form_classes:
            return JsonResponse({'error': _('Formulario desconocido.')}, status=400)
        course_id = request.POST.get('_course_id')
        object_id = request.POST.get('_object_id')
        if any(value and not value.isdecimal() for value in (course_id, object_id)):
            return JsonResponse({'error': _('Identificador no válido.')}, status=400)
        course = get_object_or_404(Curso, pk=course_id) if course_id else None
        if course:
            if not can_edit_course(request.user, course):
                raise PermissionDenied
        elif form_type != 'course' or not request.user.has_perm('courses.add_curso'):
            raise PermissionDenied
        options = {}
        if form_type == 'course':
            options['instance'] = course
        else:
            if form_type not in ('module', 'discussion'):
                options['course'] = course
            if object_id:
                if form_type == 'module':
                    options['instance'] = get_object_or_404(Tema, pk=object_id, curso=course)
                else:
                    model = {'page': Page, 'announcement': Announcement, 'discussion': DiscussionTopic}[form_type]
                    options['instance'] = get_object_or_404(model, pk=object_id, course=course)
        form = form_classes[form_type](request.POST, request.FILES, **options)
        field_name = request.POST.get('_field')
        if field_name not in form.fields:
            return JsonResponse({'error': _('Campo desconocido.')}, status=400)
        form.is_valid()
        return JsonResponse({
            'errors': list(form.errors.get(field_name, [])),
            'non_field_errors': list(form.non_field_errors()),
        })


class DashboardView(LoginRequiredMixin, LmsContextMixin, TemplateView):
    template_name = 'lms/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_base_context())
        profile = get_or_create_lms_profile(self.request.user)
        memberships = memberships_for_dashboard(self.request.user)
        student_memberships, legacy_courses = student_courses(self.request.user)
        context.update({
            'profile': profile,
            'memberships': memberships,
            'membership_count': memberships.count(),
            'course_count': memberships.count() + legacy_courses.count(),
            'student_memberships': student_memberships,
            'legacy_courses': legacy_courses,
            'teacher_memberships': teacher_memberships(self.request.user),
            'discoverable_courses': discoverable_courses(self.request.user)[:8],
            'show_onboarding': (
                not memberships.exists()
                and not legacy_courses.exists()
                and not profile.onboarding_completed
                and not profile.onboarding_dismissed
            ),
        })
        return context


class StudentDashboardView(LoginRequiredMixin, LmsContextMixin, TemplateView):
    template_name = 'lms/student_dashboard.html'

    def get(self, request, *args, **kwargs):
        if CourseMembership.objects.filter(user=request.user, course__lms_settings__campus_enabled=True).exists():
            return redirect('lms:campus')
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_base_context())
        memberships, legacy_courses = student_courses(self.request.user)
        allowed_statuses = {
            CourseMembership.Status.ACTIVE,
            CourseMembership.Status.SUSPENDED,
            CourseMembership.Status.COMPLETED,
        }
        memberships = memberships.filter(status__in=allowed_statuses).select_related(
            'course',
            'course__lms_settings',
            'section',
        ).order_by(
            'status',
            'course__nombre',
        )
        course_ids = list(memberships.values_list('course_id', flat=True))
        course_ids.extend(legacy_courses.values_list('curso_id', flat=True))

        profile = get_or_create_lms_profile(self.request.user)
        next_course = None
        if profile.last_course_id and (
            memberships.filter(course_id=profile.last_course_id).exists()
            or legacy_courses.filter(curso_id=profile.last_course_id).exists()
        ):
            next_course = profile.last_course
        if next_course is None and memberships.exists():
            next_course = memberships.first().course
        if next_course is None and legacy_courses.exists():
            next_course = legacy_courses.first().curso

        total_topics = next_course.temas.count() if next_course else 0
        completed_topics = (
            Progreso.objects.filter(
                usuario=self.request.user,
                tema__curso=next_course,
                completado=True,
            ).count()
            if next_course
            else 0
        )

        due = upcoming_for_student(self.request.user, course_ids)
        due_by_course = {}
        for evaluation in due:
            course_id = evaluation.curso_id or (evaluation.tema.curso_id if evaluation.tema_id else None)
            if course_id is None:
                continue
            current = due_by_course.get(course_id)
            if current is None or (evaluation.fecha_cierre or evaluation.fecha_prueba) < (current.fecha_cierre or current.fecha_prueba):
                due_by_course[course_id] = evaluation

        course_progress = progress_by_course(self.request.user, course_ids)
        campus_cards = []
        role_palette = {
            CourseMembership.Role.STUDENT: '#2563eb',
            CourseMembership.Role.OBSERVER: '#7c3aed',
        }
        status_order = {
            CourseMembership.Status.ACTIVE: 0,
            CourseMembership.Status.SUSPENDED: 1,
            CourseMembership.Status.COMPLETED: 2,
        }
        for membership in memberships:
            course = membership.course
            completed, total, percent = course_progress.get(course.pk, (0, 0, 0))
            next_due = due_by_course.get(course.pk)
            latest_announcement = Announcement.objects.filter(
                course=course,
                status=Announcement.Status.PUBLISHED,
                publish_at__lte=timezone.now(),
            ).order_by('-publish_at', '-created_at').first()
            has_unread_course_alert = Notification.objects.filter(
                recipient=self.request.user,
                course=course,
                status=Notification.Status.UNREAD,
            ).exists()
            campus_cards.append({
                'course': course,
                'membership': membership,
                'status_label': membership.get_status_display(),
                'status_key': membership.status,
                'role_label': membership.get_role_display(),
                'role_accent': role_palette.get(membership.role, '#2563eb'),
                'progress_percent': percent,
                'progress_completed': completed,
                'progress_total': total,
                'next_due': next_due,
                'latest_announcement': latest_announcement,
                'has_unread_course_alert': has_unread_course_alert,
                'teachers': course_teachers(course)[:3],
            })
        campus_cards.sort(key=lambda item: (status_order.get(item['membership'].status, 99), item['course'].nombre.lower()))

        agenda_events = CalendarEvent.objects.for_user(
            self.request.user,
            start=timezone.now(),
            end=timezone.now() + timedelta(days=7),
        ).select_related('course')[:5]
        unread_notifications = Notification.objects.filter(
            recipient=self.request.user,
            status=Notification.Status.UNREAD,
        ).select_related('course').order_by('-created_at')[:5]

        context.update({
            'memberships': memberships,
            'legacy_courses': legacy_courses,
            'campus_cards': campus_cards,
            'upcoming_evaluations': decorate_evaluations(
                due,
                teacher_view=False,
                user=self.request.user,
            ) + student_open_quiz_cards(self.request.user, course_ids),
            'agenda_events': agenda_events,
            'unread_notifications': unread_notifications,
            'next_course': next_course,
            'progress_percentage': (
                round(completed_topics * 100 / total_topics)
                if total_topics
                else 0
            ),
            'progress_topics_completed': completed_topics,
            'progress_topics_total': total_topics,
            'campus_stats': {
                'active': memberships.filter(status=CourseMembership.Status.ACTIVE).count(),
                'suspended': memberships.filter(status=CourseMembership.Status.SUSPENDED).count(),
                'completed': memberships.filter(status=CourseMembership.Status.COMPLETED).count(),
                'unread_notifications': unread_notifications.count(),
            },
        })
        return context


class TeacherDashboardView(LoginRequiredMixin, LmsContextMixin, TemplateView):
    template_name = 'lms/teacher_dashboard.html'

    def get(self, request, *args, **kwargs):
        if teacher_memberships(request.user).filter(course__lms_settings__campus_enabled=True).exists():
            return redirect('lms:campus_teaching')
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_base_context())
        memberships = teacher_memberships(self.request.user).select_related('course', 'course__lms_settings')
        selected = None
        requested_id = self.request.GET.get('course')
        if requested_id:
            selected = memberships.filter(course_id=requested_id).first()
            if selected is None:
                raise PermissionDenied(_('No tienes acceso docente a ese curso.'))
        if selected is None:
            selected = memberships.first()
        course = selected.course if selected else None
        due = (
            upcoming_evaluations(course, include_unpublished=True)
            if course
            else []
        )
        now = timezone.now()
        active_student_count = 0
        pending_submissions = 0
        open_quizzes = 0
        progress_percent = 0
        analytics_summary = {}
        alerts = []
        quick_actions = []
        recent_activity = []
        if course:
            active_student_count = CourseMembership.objects.filter(
                course=course,
                role=CourseMembership.Role.STUDENT,
                status=CourseMembership.Status.ACTIVE,
            ).count()
            analytics_summary = course_learning_summary(course)
            pending_submissions = Submission.objects.filter(
                assignment__course=course,
            ).exclude(
                status__in=(Submission.Status.GRADED, Submission.Status.RETURNED),
            ).count()
            open_quizzes = Quiz.objects.filter(
                course=course,
                status=Quiz.Status.PUBLISHED,
            ).filter(
                Q(available_from__lte=now) | Q(available_from__isnull=True),
            ).filter(
                Q(available_until__gte=now) | Q(available_until__isnull=True),
            ).count()
            total_topics = course.temas.count()
            student_ids = list(
                CourseMembership.objects.filter(
                    course=course,
                    role=CourseMembership.Role.STUDENT,
                    status=CourseMembership.Status.ACTIVE,
                ).values_list('user_id', flat=True)
            )
            completed_topic_rows = 0
            if student_ids:
                completed_topic_rows = Progreso.objects.filter(
                    usuario_id__in=student_ids,
                    tema__curso=course,
                    completado=True,
                ).values('usuario_id', 'tema_id').distinct().count()
            total_possible_progress = max(total_topics * max(len(student_ids), 1), 1)
            progress_percent = round((completed_topic_rows / total_possible_progress) * 100)
            recent_activity = list(
                AuditEvent.objects.filter(course=course)
                .select_related('actor')
                .order_by('-created_at')[:5]
            )
            alerts = []
            if not course.activo:
                alerts.append({
                    'title': _('Curso en borrador'),
                    'body': _('Publica la aula para que los alumnos puedan matricularse y ver el contenido.'),
                    'url': reverse('lms:course_edit', kwargs={'course_id': course.pk}),
                })
            if pending_submissions:
                alerts.append({
                    'title': _('Entregas pendientes'),
                    'body': _('%(count)s entregas siguen sin calificar.') % {'count': pending_submissions},
                    'url': reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': 'grades'}),
                })
            if open_quizzes:
                alerts.append({
                    'title': _('Pruebas abiertas'),
                    'body': _('%(count)s pruebas siguen disponibles para los alumnos.') % {'count': open_quizzes},
                    'url': reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': 'quizzes'}),
                })
            if not alerts:
                alerts.append({
                    'title': _('Todo en orden'),
                    'body': _('No hay alertas activas en este momento.'),
                    'url': reverse('lms:course_detail', kwargs={'course_id': course.pk}),
                })
            quick_actions = [
                {'label': _('Crear anuncio'), 'icon': 'bi-megaphone', 'url': reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': 'announcements'})},
                {'label': _('Crear página'), 'icon': 'bi-file-earmark-richtext', 'url': reverse('lms:course_page_create', kwargs={'course_id': course.pk})},
                {'label': _('Añadir módulo'), 'icon': 'bi-collection', 'url': reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': 'content'})},
                {'label': _('Crear actividad'), 'icon': 'bi-clipboard-check', 'url': reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': 'activities'})},
                {'label': _('Crear prueba'), 'icon': 'bi-ui-checks', 'url': reverse('lms:course_area', kwargs={'course_id': course.pk, 'area': 'quizzes'})},
            ]
        context.update({
            'overview': teacher_overview(self.request.user, memberships, self.request.GET, now=now),
            'teacher_memberships': memberships,
            'selected_membership': selected,
            'selected_course': course,
            'can_publish': can_publish_course(self.request.user, course) if course else False,
            'topics': course.temas.all() if course else [],
            'upcoming_evaluations': decorate_evaluations(
                due,
                teacher_view=True,
                user=self.request.user,
            ),
            'teacher_tools': teacher_tools(self.request.user, course) if course else {},
            'kpis': {
                'students': active_student_count,
                'pending_submissions': pending_submissions,
                'open_quizzes': open_quizzes,
                'class_progress': progress_percent,
                'completion_rate': analytics_summary.get('average_completion', 0),
                'tracked_students': analytics_summary.get('tracked_students', 0),
            },
            'alerts': alerts,
            'quick_actions': quick_actions,
            'recent_activity': recent_activity,
            'upcoming_tasks': decorate_evaluations(
                due[:5],
                teacher_view=True,
                user=self.request.user,
            ),
        })
        return context


class FirstCourseWizardView(LoginRequiredMixin, LmsContextMixin, View):
    template_name = 'lms/first_course_wizard.html'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, {
            'wizard_title': _('Crear mi primer curso'),
            'preset_templates': [
                {'value': 'basics', 'title': 'Fundamentos', 'description': 'Lecciones introductorias para iniciar una ruta de aprendizaje.'},
                {'value': 'project', 'title': 'Proyecto', 'description': 'Estructura orientada a entrega y evaluación práctica.'},
                {'value': 'bootcamp', 'title': 'Bootcamp', 'description': 'Recursos, tareas y pruebas para seguimiento acelerado.'},
            ],
            **self.get_base_context(),
        })

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm('courses.add_curso'):
            raise PermissionDenied(_('No tienes permiso para crear cursos.'))

        nombre = (request.POST.get('nombre') or '').strip() or _('Mi primer curso')
        descripcion = (request.POST.get('descripcion') or '').strip() or _('Curso creado con el asistente inicial del Campus.')
        template = request.POST.get('template', 'basics')

        today = timezone.localdate()
        course = Curso(
            nombre=nombre,
            descripcion=descripcion,
            resumen=descripcion[:280],
            fecha_inicio=today,
            fecha_fin=today + timedelta(days=30),
            status='draft',
            visibilidad='public',
            nivel='beginner',
            idioma='es',
            activo=True,
            creado_por=request.user,
            actualizado_por=request.user,
        )
        course = create_course(actor=request.user, course=course)
        settings, _created = CourseSettingsLMS.objects.get_or_create(course=course)
        settings.enrollment_open = True
        settings.archived = False
        settings.save(update_fields=['enrollment_open', 'archived'])
        section = Section.objects.create(
            course=course,
            title='Sección inicial',
            position=1,
            description='Estructura creada por el asistente de primer curso.',
            allow_self_enrollment=True,
            is_active=True,
        )
        module = CourseModule.objects.create(
            course=course,
            title='Módulo 1',
            slug='modulo-1',
            summary='Ruta inicial del curso.',
            position=1,
            status=CourseModule.Status.PUBLISHED,
        )
        page_titles = [
            'Inicio',
            'Objetivos',
            'Contenido',
            'Actividad 1',
            'Recursos',
            'Prueba inicial',
            'Cierre de semana',
            'Foro',
            'Bibliografía',
        ]
        initial_topic = course.temas.order_by('orden').first()
        if initial_topic is None:
            initial_topic = Tema.objects.create(
                curso=course,
                titulo='Módulo inicial',
                contenido='<p>Temario inicial generado por el asistente.</p>',
                orden=1,
                puntos_otorgados=10,
            )
        page_payload = []
        for index, title in enumerate(page_titles, start=1):
            page_payload.append(
                Page(
                    course=course,
                    module=initial_topic if index <= 3 else None,
                    title=title,
                    slug='',
                    status=Page.Status.PUBLISHED,
                    visibility=Page.Visibility.COURSE,
                    author=request.user,
                    position=index,
                    is_homepage=(index == 1),
                    content=f'<h2>{title}</h2><p>Esta página se ha generado automáticamente con el asistente de primer curso.</p>',
                    publish_at=timezone.now(),
                )
            )
        for page in page_payload:
            page.save()
        if template == 'project':
            module.title = 'Proyecto inicial'
            module.slug = 'proyecto-inicial'
            module.save(update_fields=['title', 'slug', 'updated_at'])
        elif template == 'bootcamp':
            module.title = 'Bootcamp de inicio'
            module.slug = 'bootcamp-de-inicio'
            module.save(update_fields=['title', 'slug', 'updated_at'])
        CourseMembership.objects.filter(course=course, user=request.user).update(section=section)
        messages.success(request, _('Tu primer curso ya está creado con una estructura de 9 páginas y un módulo inicial.'))
        return redirect('lms:teacher_dashboard')


class AdminDashboardView(LoginRequiredMixin, LmsContextMixin, TemplateView):
    template_name = 'lms/admin_dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        if not is_platform_admin(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_base_context())
        teacher_ids = CourseMembership.objects.filter(
            role__in=(CourseMembership.Role.OWNER, CourseMembership.Role.TEACHER, CourseMembership.Role.ASSISTANT),
            status=CourseMembership.Status.ACTIVE,
        ).values_list('user_id', flat=True)
        course_qs = Curso.objects.select_related('categoria', 'creado_por')
        moderation_queue = list(
            course_qs.filter(Q(activo=False) | Q(status='draft') | Q(status='archived')).order_by('-actualizado_en')[:8]
        )
        recent_courses = list(course_qs.order_by('-creado_en')[:8])
        pending_notifications = list(
            NotificationOutbox.objects.select_related('recipient', 'event').filter(
                status=NotificationOutbox.Status.PENDING,
            ).order_by('scheduled_for')[:6]
        )
        recent_audit = list(
            AuditEvent.objects.select_related('actor', 'course').order_by('-created_at')[:8]
        )
        storage_total = FileAsset.objects.filter(is_active=True).aggregate(total_size=Sum('size'))['total_size'] or 0
        context.update({
            'course_count': course_qs.count(),
            'active_course_count': course_qs.filter(activo=True).count(),
            'draft_course_count': course_qs.filter(status='draft').count(),
            'archived_course_count': course_qs.filter(status='archived').count(),
            'membership_count': CourseMembership.objects.count(),
            'student_count': CourseMembership.objects.filter(role=CourseMembership.Role.STUDENT).count(),
            'teacher_count': CourseMembership.objects.filter(role__in=(CourseMembership.Role.OWNER, CourseMembership.Role.TEACHER, CourseMembership.Role.ASSISTANT)).values('user_id').distinct().count(),
            'active_membership_count': CourseMembership.objects.filter(status=CourseMembership.Status.ACTIVE).count(),
            'user_count': get_user_model().objects.count(),
            'published_course_count': course_qs.filter(status='published', activo=True).count(),
            'moderation_queue': moderation_queue,
            'recent_courses': recent_courses,
            'pending_notifications': pending_notifications,
            'recent_audit': recent_audit,
            'storage_total_mb': round(storage_total / (1024 * 1024), 2) if storage_total else 0,
            'can_use_django_admin': self.request.user.is_staff,
            'admin_nav': [
                {'name': 'Resumen', 'url': reverse('lms:admin_dashboard')},
                {'name': 'Cursos', 'url': reverse('lms:admin_courses')},
                {'name': 'Usuarios', 'url': reverse('lms:admin_users')},
                {'name': 'Informes', 'url': reverse('lms:admin_reports')},
                {'name': 'Auditoría', 'url': reverse('lms:admin_audit')},
            ],
        })
        return context


class AdminCourseCatalogView(LoginRequiredMixin, LmsContextMixin, TemplateView):
    template_name = 'lms/admin_courses.html'

    def dispatch(self, request, *args, **kwargs):
        if not is_platform_admin(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_base_context())
        query = self.request.GET.get('q', '').strip()
        status = self.request.GET.get('status', '')
        qs = Curso.objects.select_related('categoria', 'creado_por').prefetch_related('lms_sections')
        if query:
            qs = qs.filter(Q(nombre__icontains=query) | Q(codigo__icontains=query) | Q(resumen__icontains=query))
        if status:
            qs = qs.filter(status=status)
        rows = []
        for course in qs.order_by('-actualizado_en')[:50]:
            teacher_count = CourseMembership.objects.filter(course=course, role__in=(CourseMembership.Role.OWNER, CourseMembership.Role.TEACHER, CourseMembership.Role.ASSISTANT), status=CourseMembership.Status.ACTIVE).count()
            active_students = CourseMembership.objects.filter(course=course, role=CourseMembership.Role.STUDENT, status=CourseMembership.Status.ACTIVE).count()
            rows.append({
                'course': course,
                'teacher_count': teacher_count,
                'student_count': active_students,
                'status_label': course.get_status_display(),
                'visible': course.activo,
            })
        context.update({
            'courses': rows,
            'query': query,
            'status_filter': status,
            'admin_nav': [
                {'name': 'Resumen', 'url': reverse('lms:admin_dashboard')},
                {'name': 'Cursos', 'url': reverse('lms:admin_courses')},
                {'name': 'Usuarios', 'url': reverse('lms:admin_users')},
                {'name': 'Informes', 'url': reverse('lms:admin_reports')},
                {'name': 'Auditoría', 'url': reverse('lms:admin_audit')},
            ],
        })
        return context


class AdminUsersView(LoginRequiredMixin, LmsContextMixin, TemplateView):
    template_name = 'lms/admin_users.html'

    def dispatch(self, request, *args, **kwargs):
        if not is_platform_admin(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_base_context())
        query = self.request.GET.get('q', '').strip()
        user_qs = get_user_model().objects.select_related('lms_profile').prefetch_related('lms_notification_preferences')
        if query:
            user_qs = user_qs.filter(Q(username__icontains=query) | Q(email__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query))
        rows = []
        for user in user_qs.order_by('-last_login', 'username')[:60]:
            profile = getattr(user, 'lms_profile', None)
            membership_count = CourseMembership.objects.filter(user=user).count()
            teacher_count = CourseMembership.objects.filter(
                user=user,
                role__in=(CourseMembership.Role.OWNER, CourseMembership.Role.TEACHER, CourseMembership.Role.ASSISTANT),
            ).count()
            rows.append({
                'user': user,
                'profile': profile,
                'membership_count': membership_count,
                'teacher_count': teacher_count,
            })
        context.update({
            'users': rows,
            'query': query,
            'admin_nav': [
                {'name': 'Resumen', 'url': reverse('lms:admin_dashboard')},
                {'name': 'Cursos', 'url': reverse('lms:admin_courses')},
                {'name': 'Usuarios', 'url': reverse('lms:admin_users')},
                {'name': 'Informes', 'url': reverse('lms:admin_reports')},
                {'name': 'Auditoría', 'url': reverse('lms:admin_audit')},
            ],
        })
        return context


class AdminReportsView(LoginRequiredMixin, LmsContextMixin, TemplateView):
    template_name = 'lms/admin_reports.html'

    def dispatch(self, request, *args, **kwargs):
        if not is_platform_admin(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_base_context())
        course_qs = Curso.objects
        active_courses = course_qs.filter(activo=True)
        rows = {
            'total_courses': course_qs.count(),
            'published_courses': active_courses.filter(status='published').count(),
            'draft_courses': course_qs.filter(status='draft').count(),
            'archived_courses': course_qs.filter(status='archived').count(),
            'active_memberships': CourseMembership.objects.filter(status=CourseMembership.Status.ACTIVE).count(),
            'notifications_sent': NotificationOutbox.objects.filter(status=NotificationOutbox.Status.SENT).count(),
            'notifications_pending': NotificationOutbox.objects.filter(status=NotificationOutbox.Status.PENDING).count(),
        }
        storage_total = FileAsset.objects.filter(is_active=True).aggregate(total_size=Sum('size'))['total_size'] or 0
        context.update({
            'metrics': rows,
            'storage_total_mb': round(storage_total / (1024 * 1024), 2) if storage_total else 0,
            'admin_nav': [
                {'name': 'Resumen', 'url': reverse('lms:admin_dashboard')},
                {'name': 'Cursos', 'url': reverse('lms:admin_courses')},
                {'name': 'Usuarios', 'url': reverse('lms:admin_users')},
                {'name': 'Informes', 'url': reverse('lms:admin_reports')},
                {'name': 'Auditoría', 'url': reverse('lms:admin_audit')},
            ],
        })
        return context


class AdminAuditView(LoginRequiredMixin, LmsContextMixin, TemplateView):
    template_name = 'lms/admin_audit.html'

    def dispatch(self, request, *args, **kwargs):
        if not is_platform_admin(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_base_context())
        events = AuditEvent.objects.select_related('actor', 'course').order_by('-created_at')[:40]
        context.update({
            'events': events,
            'admin_nav': [
                {'name': 'Resumen', 'url': reverse('lms:admin_dashboard')},
                {'name': 'Cursos', 'url': reverse('lms:admin_courses')},
                {'name': 'Usuarios', 'url': reverse('lms:admin_users')},
                {'name': 'Informes', 'url': reverse('lms:admin_reports')},
                {'name': 'Auditoría', 'url': reverse('lms:admin_audit')},
            ],
        })
        return context


class AdminCourseToggleView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id):
        if not is_platform_admin(request.user):
            raise PermissionDenied
        course = get_object_or_404(Curso, pk=course_id)
        action = request.POST.get('action', 'publish')
        settings = getattr(course, 'lms_settings', None)
        if action == 'publish':
            course.status = 'published'
            course.activo = True
            if settings is not None:
                settings.enrollment_open = True
                settings.archived = False
                settings.save(update_fields=['enrollment_open', 'archived'])
        elif action == 'archive':
            course.status = 'archived'
            course.activo = False
            if settings is not None:
                settings.archived = True
                settings.enrollment_open = False
                settings.save(update_fields=['enrollment_open', 'archived'])
        elif action == 'hide':
            course.activo = False
            course.status = 'draft'
        else:
            course.activo = True
            course.status = 'draft'
        note = (request.POST.get('reason') or '').strip() or _('Actualización administrativa del portal LMS.')
        course.save(update_fields=['status', 'activo', 'actualizado_en'])
        AuditEvent.objects.create(
            actor=request.user,
            course=course,
            action=AuditEvent.Action.COURSE_UPDATED,
            metadata={'administrative_action': action, 'reason': note},
        )
        messages.success(request, _('El curso ha sido actualizado con la acción administrativa seleccionada.'))
        return redirect('lms:admin_courses')


class AdminUserSuspendView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, user_id):
        if not is_platform_admin(request.user):
            raise PermissionDenied
        user = get_object_or_404(get_user_model(), pk=user_id)
        if user == request.user:
            messages.error(request, _('No puedes desactivar tu propia cuenta desde el panel administrativo.'))
            return redirect('lms:admin_users')
        user.is_active = not user.is_active
        user.save(update_fields=['is_active'])
        AuditEvent.objects.create(
            actor=request.user,
            target_user=user,
            action=AuditEvent.Action.MEMBER_STATUS_CHANGED,
            metadata={'is_active': user.is_active, 'reason': request.POST.get('reason', _('Cambio administrativo de acceso'))},
        )
        messages.success(request, _('El acceso del usuario ha sido actualizado.'))
        return redirect('lms:admin_users')


class GlobalAreaView(LoginRequiredMixin, LmsContextMixin, TemplateView):
    template_name = 'lms/global_area.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_base_context())
        area = self.kwargs['area']
        if area not in ('calendar', 'inbox'):
            raise PermissionDenied
        context['area'] = area
        context['area_title'] = _('Calendario') if area == 'calendar' else _('Bandeja')
        context['upcoming_evaluations'] = []
        if area == 'calendar':
            from .calendar_dates import calendar_dates
            context['upcoming_evaluations'] = calendar_dates(self.request.user)
        return context


class CourseContextMixin(LoginRequiredMixin, LmsContextMixin):
    def get_course(self):
        course = get_object_or_404(Curso, pk=self.kwargs['course_id'])
        if not can_view_course(self.request.user, course):
            raise PermissionDenied
        return course

    def get_course_context(self, course):
        from .calendar_dates import course_calendar_days
        today = timezone.localdate()
        calendar_weeks = Calendar().monthdatescalendar(today.year, today.month)
        membership = CourseMembership.objects.filter(
            user=self.request.user,
            course=course,
            status__in=ACTIVE_READ_STATUSES,
        ).first()
        teacher_view = can_edit_course(self.request.user, course)
        topics = list(course.temas.all())
        progress = {
            item.tema_id: item
            for item in Progreso.objects.filter(
                usuario=self.request.user,
                tema__curso=course,
            )
        }
        next_topic = next(
            (
                topic for topic in topics
                if not progress.get(topic.pk) or not progress[topic.pk].completado
            ),
            None,
        )
        settings = CourseSettingsLMS.objects.filter(course=course).first()
        due = upcoming_evaluations(course, include_unpublished=teacher_view)
        completed_count = sum(
            1 for item in progress.values() if item.completado
        )
        homepage = Page.get_homepage_for_course(course)
        syllabus, _created = CourseSyllabus.objects.get_or_create(course=course)
        syllabus.sync_from_course()
        timeline = build_course_timeline(
            course,
            topics,
            progress,
            current_topic=next_topic,
        )
        unread_announcements = 0
        if not teacher_view:
            unread_announcements = pending_announcements_count(course, self.request.user)
        return {
            **self.get_base_context(),
            'course': course,
            'course_settings': settings,
            'course_archived': bool(settings and settings.archived),
            'enrollment_open': (
                settings.enrollment_open and not settings.archived
                if settings
                else True
            ),
            'membership': membership,
            'teacher_view': teacher_view,
            'course_areas': COURSE_AREAS,
            'student_area_keys': STUDENT_AREA_KEYS,
            'area_keys': AREA_KEYS,
            'announcements_pending_count': unread_announcements,
            'topics': topics,
            'topics_with_progress': [
                (topic, progress.get(topic.pk))
                for topic in topics
            ],
            'course_timeline': timeline,
            'course_teachers': course_teachers(course),
            'progress_by_topic': progress,
            'current_topic': next_topic,
            'completed_topics': completed_count,
            'course_steps': student_steps(
                enrolled=is_course_member(self.request.user, course),
                completed=completed_count,
                total=len(topics),
                next_due=bool(due),
            ),
            'upcoming_evaluations': decorate_evaluations(
                due,
                teacher_view=teacher_view,
                user=self.request.user,
            ) + (student_open_quiz_cards(self.request.user, [course.pk]) if not teacher_view else []),
            'can_edit': can_edit_course(self.request.user, course),
            'can_publish': can_publish_course(self.request.user, course),
            'can_enroll': can_enroll(self.request.user, course),
            'is_course_member': is_course_member(self.request.user, course),
            'home_page': homepage,
            'course_syllabus': syllabus,
            'course_nav': course_nav(
                course,
                teacher_view=teacher_view,
                active_key='home',
            ),
            'teacher_tools': teacher_tools(self.request.user, course) if teacher_view else {},
            'calendar_today': today,
            'calendar_weeks': calendar_weeks,
            'calendar_days': course_calendar_days(self.request.user, course, calendar_weeks),
        }


class CourseDetailView(CourseContextMixin, DetailView):
    model = Curso
    pk_url_kwarg = 'course_id'
    context_object_name = 'course'
    template_name = 'lms/course_detail.html'

    def get(self, request, *args, **kwargs):
        course = get_object_or_404(Curso, pk=kwargs['course_id'])
        from .campus import campus_settings
        if campus_settings(course) and not can_edit_course(request.user, course):
            return redirect('lms:campus_course', course_id=course.pk)
        return super().get(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.get_course()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        course_context = self.get_course_context(self.object)
        context.update(course_context)
        context['recent_announcements'] = [
            announcement for announcement in Announcement.visible_for_course(
                self.object, self.request.user, now=timezone.now(),
            )
            if announcement.is_currently_published(now=timezone.now())
        ][:3]
        profile = get_or_create_lms_profile(self.request.user)
        if profile.last_course_id != self.object.pk:
            profile.last_course = self.object
            profile.save(update_fields=['last_course', 'updated_at'])
        return context


class CourseAreaView(CourseContextMixin, DetailView):
    model = Curso
    pk_url_kwarg = 'course_id'
    context_object_name = 'course'
    template_name = 'lms/course_area.html'

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.kwargs['area'] == 'competencies':
            from .forms import ModuleBadgeForm
            from .models import Badge
            from .badge_awards import award_module_badges_for_students
            if not can_edit_course(request.user, self.object):
                raise PermissionDenied
            badge = None
            if request.GET.get('edit'):
                badge = get_object_or_404(Badge, course=self.object, module__isnull=False, pk=request.GET['edit'])
            action = request.POST.get('action')
            if action == 'deactivate':
                badge = get_object_or_404(Badge, course=self.object, module__isnull=False, pk=request.POST.get('badge_id'))
                badge.is_active = False
                badge.save(update_fields=['is_active', 'updated_at'])
                AuditEvent.objects.create(actor=request.user, course=self.object, action=AuditEvent.Action.COURSE_UPDATED)
                messages.success(request, _('La insignia se ha desactivado. Las ya obtenidas se conservan.'))
                return redirect('lms:course_area', course_id=self.object.pk, area='competencies')
            if action != 'badge':
                raise PermissionDenied(_('Acción de insignia no válida.'))
            form = ModuleBadgeForm(request.POST, request.FILES, course=self.object, instance=badge)
            if form.is_valid():
                with transaction.atomic():
                    form.save()
                    AuditEvent.objects.create(actor=request.user, course=self.object, action=AuditEvent.Action.COURSE_UPDATED)
                    award_module_badges_for_students(self.object)
                messages.success(request, _('La insignia se ha guardado. Se concede al completar el tema.'))
                return redirect('lms:course_area', course_id=self.object.pk, area='competencies')
            return self.render_to_response(self.get_context_data(badge_form=form))
        if self.kwargs['area'] == 'settings':
            form = CourseSettingsForm(request.POST, request.FILES, instance=self.object)
            if form.is_valid():
                try:
                    changes = dict(form.cleaned_data)
                    sequential = changes.pop('sequential_modules')
                    with transaction.atomic():
                        update_course(actor=request.user, course=self.object, changes=changes)
                        CourseSettingsLMS.objects.update_or_create(
                            course=self.object, defaults={'sequential_modules': sequential},
                        )
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    messages.success(request, _('Los detalles del curso se han actualizado.'))
                    return redirect('lms:course_area', course_id=self.object.pk, area='settings')
            return self.render_to_response(self.get_context_data(settings_form=form))
        if self.kwargs['area'] == 'grades':
            from .forms import QuizFeedbackForm
            from .quiz_results import send_quiz_feedback
            if not can_edit_course(request.user, self.object):
                raise PermissionDenied
            form = QuizFeedbackForm(request.POST)
            if form.is_valid():
                try:
                    send_quiz_feedback(course=self.object, actor=request.user, **form.cleaned_data)
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    messages.success(request, _('El feedback se ha guardado y enviado al alumno.'))
                    return redirect('lms:course_area', course_id=self.object.pk, area='grades')
            return self.render_to_response(self.get_context_data(quiz_feedback_form=form))
        if self.kwargs['area'] != 'people' or not course_teachers(self.object).filter(pk=request.user.pk).exists():
            raise PermissionDenied
        participants = course_participants(self.object)
        form = PeopleNotificationForm(request.POST, participants=participants)
        if form.is_valid():
            recipient_ids = (
                [person['user'].pk for person in participants if person['role_key'] == CourseMembership.Role.STUDENT]
                if form.cleaned_data['send_all'] else [int(pk) for pk in form.cleaned_data['recipients']]
            )
            try:
                send_people_notification(
                    course=self.object, actor=request.user, recipient_ids=recipient_ids,
                    title=form.cleaned_data['title'], message=form.cleaned_data['message'],
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, _('La notificación se ha enviado y guardado en este curso.'))
                return redirect('lms:course_area', course_id=self.object.pk, area='people')
        return self.render_to_response(self.get_context_data(people_notification_form=form))

    def get_object(self, queryset=None):
        course = self.get_course()
        area_key = self.kwargs['area']
        if area_key not in AREA_KEYS or area_key == 'home':
            raise PermissionDenied
        teacher_view = can_edit_course(self.request.user, course)
        if not teacher_view and area_key not in STUDENT_AREA_KEYS:
            raise PermissionDenied
        from .campus import campus_settings
        if campus_settings(course) and teacher_view and area_key in ('people', 'grades', 'analytics', 'reports'):
            assignment = CourseMembership.objects.filter(
                course=course, user=self.request.user, status=CourseMembership.Status.ACTIVE,
            ).first()
            if assignment and assignment.section_id:
                raise PermissionDenied(_('Consulta solo tus alumnos asignados en Mis grupos docentes.'))
        if not teacher_view and not is_course_member(self.request.user, course):
            raise PermissionDenied
        self.area = next(item for item in COURSE_AREAS if item[0] == area_key)
        return course

    def get(self, request, *args, **kwargs):
        course = self.get_course()
        from .campus import campus_settings
        if campus_settings(course) and not can_edit_course(request.user, course) and kwargs['area'] in (
            'content', 'activities', 'quizzes', 'files', 'syllabus', 'pages',
        ):
            return redirect('lms:campus_course', course_id=course.pk)
        if self.kwargs['area'] == 'pages':
            return redirect('lms:course_pages', course_id=course.pk)
        response = super().get(request, *args, **kwargs)
        if self.kwargs['area'] == 'grades' and request.GET.get('export') == 'csv':
            book = response.context_data['gradebook']
            export = HttpResponse(content_type='text/csv; charset=utf-8')
            export['Content-Disposition'] = f'attachment; filename="notas-curso-{self.object.pk}.csv"'
            export['Cache-Control'] = 'no-store'
            export.write('\ufeff')
            writer = csv.writer(export)

            def safe_cell(value):
                text = str(value)
                return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text

            writer.writerow([gettext('Estudiante')] + [
                safe_cell(column['title']) for column in book['columns']
            ])
            for row in book['rows']:
                writer.writerow([safe_cell(row['name'])] + [
                    cell['grade'] if cell['grade'] is not None else safe_cell(cell['status'])
                    for cell in row['cells']
                ])
            return export
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_course_context(self.object))
        area_key, title, _icon = self.area
        context['area_key'] = area_key
        context['area_title'] = title
        context['area_message'] = gettext(
            'Esta vista todavía no está habilitada para este curso.'
        )
        if area_key == 'settings':
            context['area_message'] = gettext('Detalles y opciones de la asignatura.')
            form = context.get('settings_form')
            if form is None:
                form = CourseSettingsForm(instance=self.object)
            context['settings_form'] = form
            detail_names = [
                'imagen', 'nombre', 'codigo', 'slug', 'categoria', 'status',
                'zona_horaria', 'fecha_inicio', 'fecha_fin', 'fecha_publicacion',
                'idioma', 'nivel', 'visibilidad', 'resumen', 'descripcion',
            ]
            context['settings_detail_fields'] = [form[name] for name in detail_names]
            context['settings_option_fields'] = [
                form[name] for name in form.fields if name not in detail_names
            ]
            context['settings_tab'] = (
                'sections' if self.request.GET.get('tab') == 'sections' else 'details'
            )
            context['settings_sections'] = self.object.lms_sections.order_by('position', 'pk')
            participants = course_participants(self.object)
            context['settings_student_count'] = sum(
                person['role_key'] == CourseMembership.Role.STUDENT for person in participants
            )
            context['settings_teacher_count'] = sum(
                person['role_key'] in (CourseMembership.Role.TEACHER, CourseMembership.Role.OWNER)
                for person in participants
            )
        elif area_key == 'competencies':
            from .badge_awards import award_module_badges, award_module_badges_for_students
            from .forms import ModuleBadgeForm
            from .models import Badge, UserBadge
            course = self.object
            if context['teacher_view']:
                award_module_badges_for_students(course)
            else:
                award_module_badges(course, self.request.user)
            badges = Badge.objects.filter(course=course, module__isnull=False).select_related('module')
            if not context['teacher_view']:
                badges = badges.filter(Q(is_active=True) | Q(awards__user=self.request.user, awards__status=UserBadge.Status.AWARDED)).distinct()
            context['module_badges'] = badges
            context['badge_awards'] = UserBadge.objects.filter(
                course=course, badge__module__isnull=False, status=UserBadge.Status.AWARDED,
            ).select_related('user', 'badge', 'badge__module')
            context['earned_badge_ids'] = set(context['badge_awards'].filter(user=self.request.user).values_list('badge_id', flat=True))
            if context['teacher_view']:
                editing = None
                if self.request.GET.get('edit'):
                    editing = get_object_or_404(Badge, course=course, module__isnull=False, pk=self.request.GET['edit'])
                context['editing_badge'] = editing
                context['badge_form'] = context.get('badge_form')
                if context['badge_form'] is None:
                    context['badge_form'] = ModuleBadgeForm(course=course, instance=editing)
        elif area_key == 'content':
            context['area_message'] = gettext('Contenido y temas de aprendizaje del curso.')
            from .module_progression import module_rows, sequential_enabled
            context['learning_module_rows'] = module_rows(self.object, self.request.user)
            context['sequential_modules'] = sequential_enabled(self.object)
        elif area_key == 'syllabus':
            syllabus, _created = CourseSyllabus.objects.get_or_create(course=self.object)
            syllabus.sync_from_course()
            context['course_syllabus'] = syllabus
            context['syllabus_units'] = syllabus.get_units()
            context['area_message'] = gettext('Programa y secuencia docente de la asignatura.')
        elif area_key == 'announcements':
            context['announcements'] = Announcement.visible_for_course(
                self.object,
                self.request.user,
                now=timezone.now(),
            )
            context['acknowledged_announcement_ids'] = list(
                AnnouncementAcknowledgement.objects.filter(
                    announcement__course=self.object,
                    user=self.request.user,
                    acknowledged_at__isnull=False,
                ).values_list('announcement_id', flat=True)
            )
            context['pending_announcement_ids'] = (
                pending_announcement_ids(
                    self.object, self.request.user, announcements=context['announcements'],
                ) if not context['teacher_view'] else set()
            )
            if context['teacher_view']:
                from .notification_state import announcement_reading_state
                reading = announcement_reading_state(self.object, self.request.user)
                for announcement in context['announcements']:
                    announcement.reading_state = reading['announcements'].get(str(announcement.pk))
            context['area_message'] = gettext('Avisos, novedades y recordatorios del curso.')
        elif area_key == 'discussion':
            topics = DiscussionTopic.objects.filter(course=self.object, is_deleted=False).select_related('author', 'course', 'section')
            if not context['teacher_view']:
                topics = topics.exclude(status__in=[DiscussionTopic.Status.DRAFT, DiscussionTopic.Status.ARCHIVED])
            context['discussion_topics'] = [
                topic for topic in topics if topic.is_visible_to_user(self.request.user)
            ]
            for topic in context['discussion_topics']:
                topic.can_start_query = (
                    topic.can_participate(self.request.user) and topic.status == topic.Status.OPEN
                    and not topic.is_locked and topic.allow_replies
                )
            context['area_message'] = gettext('Foros de discusión y conversaciones del aula.')
        elif area_key == 'item-bank':
            context['area_message'] = gettext('Bancos de preguntas reutilizables para las pruebas del curso.')
            context['question_banks'] = QuestionBank.objects.filter(course=self.object).annotate(
                question_count=Count('questions'),
                review_count=Count('questions', filter=Q(questions__current_version__metadata__has_key='csv_review')),
            )
            from .selectors import group_question_banks
            context['question_bank_groups'] = group_question_banks(context['question_banks'])
        elif area_key == 'activities':
            from .activity_workflow import activity_state, feedback_visible
            context['area_message'] = gettext('Actividades de aprendizaje, instrucciones y fechas de entrega.')
            activities = LearningActivity.objects.filter(course=self.object).select_related('module')
            if not context['teacher_view']:
                if not is_course_member(self.request.user, self.object):
                    raise PermissionDenied
                activities = activities.exclude(status=LearningActivity.Status.DRAFT)
                from .campus import campus_settings
                if campus_settings(self.object):
                    from .activity_workflow import can_read_activity
                    activities = [activity for activity in activities if can_read_activity(self.request.user, activity)]
            context['learning_activities'] = activities
            for activity in activities:
                activity.display_state = {
                    'created': gettext('Creada'), 'open': gettext('Abierta'), 'closed': gettext('Cerrada'),
                }[activity_state(activity)]
                activity.solution_visible = bool(activity.solution and activity.can_show_solution())
                submission = Submission.objects.filter(
                    assignment__learning_activity=activity, user=self.request.user,
                ).order_by('-version').first()
                activity.student_submission = submission
                activity.evaluation_visible = feedback_visible(activity, submission)
        elif area_key == 'grades':
            from .quiz_results import course_gradebook, quiz_result_groups
            context['area_message'] = gettext('Libro de calificaciones y feedback privado del curso.')
            context['course_quiz_result_groups'] = quiz_result_groups(
                self.object, self.request.user, teacher_view=context['teacher_view'],
                feedback_form=kwargs.get('quiz_feedback_form'),
            )
            context['grade_student_query'] = self.request.GET.get('student', '').strip()[:200]
            context['grade_activity_query'] = self.request.GET.get('activity', '').strip()[:200]
            context['gradebook'] = course_gradebook(
                self.object, context['course_quiz_result_groups'],
                teacher_view=context['teacher_view'],
                user=self.request.user,
                student_query=context['grade_student_query'],
                activity_query=context['grade_activity_query'],
            )
            export_query = self.request.GET.copy()
            export_query['export'] = 'csv'
            context['grade_export_query'] = export_query.urlencode()
        elif area_key == 'quizzes':
            context['area_message'] = gettext('Prácticas con entrega y corrección manual, y pruebas objetivas tipo test.')
            practical_activities = LearningActivity.objects.filter(course=self.object, is_evaluable=True)
            if not context['teacher_view']:
                practical_activities = practical_activities.exclude(status=LearningActivity.Status.DRAFT)
                from .campus import campus_settings
                if campus_settings(self.object):
                    from .activity_workflow import can_read_activity
                    practical_activities = [
                        activity for activity in practical_activities if can_read_activity(self.request.user, activity)
                    ]
            context['practical_activities'] = practical_activities
            quizzes = Quiz.objects.filter(course=self.object).annotate(
                question_count=Count('questions', distinct=True),
                attempt_count=Count('attempts', distinct=True),
            )
            if not context['teacher_view']:
                section_ids = CourseMembership.objects.filter(
                    course=self.object, user=self.request.user, role=CourseMembership.Role.STUDENT,
                    status__in=(CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED),
                ).values_list('section_id', flat=True)
                quizzes = quizzes.filter(status__in=(Quiz.Status.PUBLISHED, Quiz.Status.CLOSED)).filter(Q(section__isnull=True) | Q(section_id__in=section_ids))
            context['lms_quizzes'] = quizzes
            from .quiz_status import quiz_is_closed, quiz_solutions_visible, teacher_quiz_status
            from .quiz_attempts import attempt_deadline
            for quiz in quizzes:
                quiz.effective_end = attempt_deadline(quiz)
                quiz.has_attempt_history = bool(quiz.attempt_count) if context['teacher_view'] else quiz.attempts.filter(user=self.request.user).exists()
                quiz.solutions_visible = quiz_solutions_visible(
                    quiz, student=None if context['teacher_view'] else self.request.user,
                )
                quiz.phase_labels = teacher_quiz_status(
                    quiz, student=None if context['teacher_view'] else self.request.user,
                )
                quiz.can_reopen = context['teacher_view'] and self.object.status == 'published' and quiz_is_closed(quiz)
        elif area_key == 'people':
            context['area_message'] = gettext('Personas inscritas en este curso y sus roles en el aula.')
            participants = course_participants(self.object)
            context['course_participants'] = participants if context['teacher_view'] else [
                {'name': person['user'].get_full_name() or person['user'].username, 'role': person['role']}
                for person in participants
            ]
            context['can_send_people_notification'] = course_teachers(self.object).filter(pk=self.request.user.pk).exists()
            if context['can_send_people_notification'] and 'people_notification_form' not in context:
                context['people_notification_form'] = PeopleNotificationForm(participants=participants)
            if context['can_send_people_notification']:
                notification_form = context['people_notification_form']
                selected_ids = set(notification_form['recipients'].value() or [])
                select_all = notification_form['send_all'].value()
                for person in participants:
                    person['notification_selected'] = bool(select_all) or str(person['user'].pk) in selected_ids
            history = NotificationEvent.objects.filter(course=self.object, metadata__source='people').select_related('actor')
            if not context['can_send_people_notification']:
                history = history.filter(notifications__recipient=self.request.user).distinct()
            context['people_notification_history'] = history
            search = self.request.GET.get('q', '').strip()[:200]
            role = self.request.GET.get('role', '')
            if role not in CourseMembership.Role.values:
                role = ''
            context['people_query'] = search
            context['people_role'] = role
            context['people_roles'] = CourseMembership.Role.choices
            context['people_filtered'] = bool(search or role)
            context['people_tab'] = 'groups' if context['teacher_view'] and self.request.GET.get('tab') == 'groups' else 'all'
            if context['teacher_view']:
                stats = {
                    row['user_id']: row for row in LearningEvent.objects.filter(
                        course=self.object,
                    ).values('user_id').annotate(
                        last_activity=Max('created_at'),
                        total_seconds=Sum(Cast(KeyTextTransform('duration_seconds', 'context'), BigIntegerField())),
                    )
                }
                progress = dict(LearningProgress.objects.filter(course=self.object).values_list('user_id', 'last_activity_at'))
                for person in participants:
                    user_id = person['user'].pk
                    dates = [date for date in (stats.get(user_id, {}).get('last_activity'), progress.get(user_id)) if date]
                    person['last_activity'] = max(dates) if dates else None
                    seconds = stats.get(user_id, {}).get('total_seconds')
                    person['activity_duration'] = f'{seconds // 3600:02}:{seconds % 3600 // 60:02}:{seconds % 60:02}' if seconds is not None else None
                context['people_groups'] = [{
                    'section': section,
                    'members': [person for person in participants if person['section'] and person['section'].pk == section.pk],
                } for section in Section.objects.filter(course=self.object).order_by('position', 'title', 'pk')]
                context['people_ungrouped'] = [person for person in participants if person['section'] is None]
            filtered = [
                person for person in participants
                if (not role or person['role_key'] == role)
                and search.casefold() in (
                    f"{person['user'].get_full_name()} {person['user'].username} {person['user'].email}"
                    if context['teacher_view'] else (person['user'].get_full_name() or person['user'].username)
                ).casefold()
            ]
            context['course_participants'] = filtered if context['teacher_view'] else [
                {'name': person['user'].get_full_name() or person['user'].username, 'role': person['role']}
                for person in filtered
            ]
        elif area_key == 'files':
            files = FileAsset.objects.filter(course=self.object, is_active=True).select_related('owner')
            if not context['teacher_view']:
                files = files.filter(
                    visibility__in=[
                        FileAsset.Visibility.PUBLIC,
                        FileAsset.Visibility.COURSE,
                        FileAsset.Visibility.STUDENTS,
                    ]
                )
            search = self.request.GET.get('q', '').strip()[:200]
            folder = self.request.GET.get('folder', '').strip()[:120]
            context['folders'] = sorted(set(files.exclude(folder='').values_list('folder', flat=True)))
            context['file_storage_size'] = files.aggregate(total=Sum('size'))['total'] or 0
            if search:
                files = files.filter(Q(title__icontains=search) | Q(file_name__icontains=search))
            if folder:
                files = files.filter(folder=folder)
            sort = self.request.GET.get('sort', 'name')
            ordering = {'name': 'title', 'created': 'created_at', 'modified': '-updated_at', 'size': '-size'}
            if sort not in ordering:
                sort = 'name'
            context['file_query'] = search
            context['file_folder'] = folder
            context['file_sort'] = sort
            context['files'] = files.order_by(ordering[sort], 'pk')
            context['area_message'] = gettext('Recursos, materiales y documentos del curso.')
        context['course_evaluations'] = (
            decorate_evaluations(
                upcoming_evaluations(
                    self.object,
                    include_unpublished=context['teacher_view'],
                ),
                teacher_view=context['teacher_view'],
                user=self.request.user,
            )
            if area_key in {'activities', 'quizzes'}
            else []
        )
        context['course_nav'] = course_nav(
            self.object,
            teacher_view=context['teacher_view'],
            active_key=area_key,
        )
        return context


class CourseEditorUploadView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, *args, **kwargs):
        course = get_object_or_404(Curso, pk=course_id)
        if not can_edit_course(request.user, course):
            raise PermissionDenied(_('No tienes permisos para editar este curso.'))

        upload_type = (request.POST.get('upload_type') or 'file').lower()
        uploaded = request.FILES.get('file') or request.FILES.get('upload')
        if not uploaded:
            return JsonResponse({'ok': False, 'error': _('No se recibió ningún archivo.')}, status=400)

        filename = getattr(uploaded, 'name', '') or 'archivo'
        extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        content_type = (getattr(uploaded, 'content_type', '') or '').lower()
        is_image = upload_type == 'image' or content_type.startswith('image/') or extension in {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}

        if not is_image and upload_type == 'image':
            return JsonResponse({'ok': False, 'error': _('Solo puedes subir imágenes válidas.')}, status=400)

        asset = FileAsset.objects.create(
            course=course,
            owner=request.user,
            title=(Path(filename).stem or 'Archivo del editor')[:200],
            slug=(slugify(Path(filename).stem or 'archivo-del-editor') or 'archivo-del-editor')[:220],
            folder='editor',
            file=uploaded,
            file_name=filename,
            mime_type=uploaded.content_type or 'application/octet-stream',
            size=getattr(uploaded, 'size', 0) or 0,
            visibility=FileAsset.Visibility.COURSE,
            access_policy=FileAsset.AccessPolicy.COURSE,
            allow_download=not is_image,
            allow_preview=True,
            is_active=True,
        )

        if is_image:
            url = reverse('lms:course_file_download', kwargs={'course_id': course.pk, 'file_id': asset.pk}) + '?inline=1'
        else:
            url = reverse('lms:course_file_download', kwargs={'course_id': course.pk, 'file_id': asset.pk})

        return JsonResponse({
            'ok': True,
            'kind': 'image' if is_image else 'file',
            'url': url,
            'file_name': asset.file_name,
            'mime_type': asset.mime_type,
        })


class CoursePageMixin(LmsContextMixin):
    def get_course(self):
        course = get_object_or_404(Curso, pk=self.kwargs['course_id'])
        if not can_view_course(self.request.user, course):
            raise PermissionDenied
        return course

    def get_teacher_view(self, course):
        return can_edit_course(self.request.user, course)


class CourseActivityMixin(CoursePageMixin):
    def get_activity(self, course, activity_id):
        return get_object_or_404(
            LearningActivity.objects.select_related('module'),
            pk=activity_id,
            course=course,
        )

    def render_form(self, course, form, *, activity=None):
        return render(self.request, 'lms/activity_form.html', {
            'course': course,
            'activity': activity,
            'form': form,
            'practical_form': self.request.GET.get('from') == 'quizzes',
            'teacher_view': True,
            **self.get_base_context(),
        })


class CourseActivityCreateView(LoginRequiredMixin, CourseActivityMixin, View):
    http_method_names = ['get', 'post']

    def get(self, request, course_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        return self.render_form(course, LearningActivityForm(
            course=course, initial={'points': 10, 'is_evaluable': True} if request.GET.get('from') == 'quizzes' else None,
        ))

    def post(self, request, course_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        form = LearningActivityForm(request.POST, course=course)
        if not form.is_valid():
            return self.render_form(course, form)
        activity = form.save(commit=False)
        activity.course = course
        activity.save()
        from .lifecycle import process_activity
        process_activity(activity.pk, now=timezone.now())
        messages.success(request, _('La actividad se ha creado.'))
        return redirect('lms:course_area', course_id=course.pk, area='quizzes' if request.GET.get('from') == 'quizzes' else 'activities')


class CourseActivityUpdateView(LoginRequiredMixin, CourseActivityMixin, View):
    http_method_names = ['get', 'post']

    def get(self, request, course_id, activity_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        activity = self.get_activity(course, activity_id)
        return self.render_form(course, LearningActivityForm(
            instance=activity,
            course=course,
        ), activity=activity)

    def post(self, request, course_id, activity_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        activity = self.get_activity(course, activity_id)
        form = LearningActivityForm(request.POST, instance=activity, course=course)
        if not form.is_valid():
            return self.render_form(course, form, activity=activity)
        form.save()
        messages.success(request, _('La actividad se ha actualizado.'))
        from .lifecycle import process_activity
        process_activity(activity.pk, now=timezone.now())
        return redirect('lms:course_area', course_id=course.pk, area='quizzes' if request.GET.get('from') == 'quizzes' else 'activities')


class CourseActivityDeleteView(LoginRequiredMixin, CourseActivityMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, activity_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        activity = self.get_activity(course, activity_id)
        activity.delete()
        messages.success(request, _('La actividad se ha eliminado.'))
        return redirect('lms:course_area', course_id=course.pk, area='activities')


class CourseFileMixin(CoursePageMixin):
    def get_file_asset(self, course, file_id):
        return get_object_or_404(FileAsset, pk=file_id, course=course, is_active=True)

    def render_form(self, course, form, *, file_asset=None):
        return render(self.request, 'lms/file_form.html', {
            'course': course,
            'file_asset': file_asset,
            'form': form,
            'teacher_view': True,
            **self.get_base_context(),
        })


class CourseFileCreateView(LoginRequiredMixin, CourseFileMixin, View):
    http_method_names = ['get', 'post']

    def get(self, request, course_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        return self.render_form(course, FileAssetForm(
            initial={'folder': request.GET.get('folder', 'general').strip()[:120] or 'general'},
        ))

    def post(self, request, course_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        form = FileAssetForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_form(course, form)
        asset = form.save(commit=False)
        asset.course = course
        asset.owner = request.user
        asset.save()
        messages.success(request, _('El archivo se ha subido.'))
        return redirect('lms:course_area', course_id=course.pk, area='files')


class CourseFileUpdateView(LoginRequiredMixin, CourseFileMixin, View):
    http_method_names = ['get', 'post']

    def get(self, request, course_id, file_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        asset = self.get_file_asset(course, file_id)
        return self.render_form(course, FileAssetForm(instance=asset), file_asset=asset)

    def post(self, request, course_id, file_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        asset = self.get_file_asset(course, file_id)
        form = FileAssetForm(request.POST, request.FILES, instance=asset)
        if not form.is_valid():
            return self.render_form(course, form, file_asset=asset)
        form.save()
        messages.success(request, _('El archivo se ha actualizado.'))
        return redirect('lms:course_area', course_id=course.pk, area='files')


class CourseFileDeleteView(LoginRequiredMixin, CourseFileMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, file_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        asset = self.get_file_asset(course, file_id)
        asset.is_active = False
        asset.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, _('El archivo se ha retirado del curso.'))
        return redirect('lms:course_area', course_id=course.pk, area='files')


class CourseFileDownloadView(LoginRequiredMixin, View):
    http_method_names = ['get']

    def get(self, request, course_id, file_id, *args, **kwargs):
        course = get_object_or_404(Curso, pk=course_id)
        if not can_view_course(request.user, course):
            raise PermissionDenied

        asset = get_object_or_404(FileAsset, pk=file_id, course=course, is_active=True)
        token = request.GET.get('token', '').strip()
        is_authorized = asset.user_can_access(request.user)

        if token:
            link = get_object_or_404(FileAssetLink, asset=asset, token=token)
            if not link.is_valid():
                raise PermissionDenied
            is_authorized = True

        if not is_authorized:
            raise PermissionDenied
        from .module_progression import require_target_access
        require_target_access(asset, request.user)

        inline_mode = request.GET.get('inline', '').lower() in {'1', 'true', 'yes'}
        if inline_mode and not asset.allow_preview:
            raise PermissionDenied
        if not inline_mode and not asset.allow_download:
            raise PermissionDenied

        storage_file = asset.file
        if not storage_file or not getattr(storage_file, 'name', None):
            raise Http404

        response = FileResponse(
            storage_file.open('rb'),
            as_attachment=not inline_mode,
            filename=asset.file_name or asset.title,
        )
        response['Content-Type'] = asset.mime_type or 'application/octet-stream'
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        from .module_progression import record_open
        record_open(asset, request.user)
        if inline_mode:
            response['Content-Disposition'] = f'inline; filename="{asset.file_name or asset.title}"'
        return response


class CourseCategoryCreateView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm('courses.add_curso'):
            raise PermissionDenied(_('No tienes permiso para crear categorías desde el aula.'))
        form = CourseCategoryForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'errors': form.errors.get_json_data()}, status=400)
        category = form.save()
        return JsonResponse({'id': category.pk, 'nombre': category.nombre}, status=201)


class CourseCreateView(LoginRequiredMixin, View):
    template_name = 'lms/course_form.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not request.user.has_perm('courses.add_curso'):
            raise PermissionDenied(_('No tienes permiso para crear cursos.'))
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self.render_form(CourseForm())

    def post(self, request, *args, **kwargs):
        form = CourseForm(request.POST)
        if not form.is_valid():
            return self.render_form(form)
        course = create_course(actor=request.user, course=form.save(commit=False))
        messages.success(request, _('El curso se ha creado como borrador.'))
        return redirect('lms:course_detail', course_id=course.pk)

    def render_form(self, form):
        return render(self.request, self.template_name, {
            'form': form,
            'form_title': _('Crear un curso'),
            'category_form': CourseCategoryForm(auto_id='category_%s'),
            'can_create_category': self.request.user.has_perm('courses.add_curso'),
            **LmsContextMixin.get_base_context(self),
        })


class CourseDeleteView(LoginRequiredMixin, LmsContextMixin, View):
    http_method_names = ['get', 'post']

    def handle(self, request):
        from .policies import can_delete_course
        from .services import delete_course

        if not can_delete_course(request.user):
            raise PermissionDenied(_('Solo el personal administrador puede eliminar cursos.'))
        course = get_object_or_404(Curso, pk=self.kwargs['course_id'])
        error = ''
        if request.method == 'POST':
            if request.POST.get('confirm') != 'delete' or request.POST.get('course_name') != course.nombre:
                error = _('Escribe el nombre exacto del curso y confirma su eliminación.')
            else:
                try:
                    delete_course(course_id=course.pk, actor=request.user)
                except ProtectedError:
                    error = _('No se puede eliminar: hay preguntas de este curso utilizadas en otros cursos. No se ha borrado ningún dato.')
                else:
                    messages.success(request, _('Curso eliminado definitivamente.'))
                    return redirect('lms:dashboard')
        return render(request, 'lms/course_delete.html', {
            **self.get_base_context(), 'course': course, 'deletion_error': error,
        }, status=400 if error else 200)

    def get(self, request, *args, **kwargs):
        return self.handle(request)

    def post(self, request, *args, **kwargs):
        return self.handle(request)


class CourseUpdateView(LoginRequiredMixin, UpdateView):
    model = Curso
    form_class = CourseForm
    pk_url_kwarg = 'course_id'
    context_object_name = 'course'
    template_name = 'lms/course_form.html'

    def get_queryset(self):
        return editable_courses(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        course = get_object_or_404(Curso, pk=kwargs['course_id'])
        if not can_edit_course(request.user, course):
            raise PermissionDenied(_('No tienes permiso para editar este curso.'))
        if request.method == 'POST' and request.POST.get('action') == 'preview':
            return redirect('lms:course_preview', course_id=course.pk)
        if request.method == 'POST' and request.POST.get('action') == 'reorder_modules':
            module_id = request.POST.get('module_id')
            direction = request.POST.get('direction')
            if module_id and direction in {'up', 'down'}:
                modules = list(CourseModule.objects.filter(course=course).order_by('position', 'pk'))
                current_index = next((index for index, module in enumerate(modules) if str(module.pk) == str(module_id)), None)
                if current_index is not None:
                    target_index = current_index - 1 if direction == 'up' else current_index + 1
                    if 0 <= target_index < len(modules):
                        modules[current_index], modules[target_index] = modules[target_index], modules[current_index]
                        for position, module in enumerate(modules, start=1):
                            module.position = position
                        CourseModule.objects.bulk_update(modules, ['position'])
                        messages.success(request, _('El orden del aula se ha actualizado.'))
                    else:
                        messages.info(request, _('El módulo ya está en el extremo del curso.'))
            return redirect('lms:course_edit', course_id=course.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        course = self.object
        context['form_title'] = _('Editar curso')
        context['course_modules'] = list(
            CourseModule.objects.filter(course=course).order_by('position', 'pk')
        )
        context['course_activities'] = list(
            LearningActivity.objects.filter(course=course).order_by('starts_at', 'due_at', 'pk')
        )
        context['recent_audit_events'] = list(
            AuditEvent.objects.filter(course=course).order_by('-created_at')[:5]
        )
        context['can_publish'] = can_publish_course(self.request.user, course)
        context['editor_mode'] = True
        context.update(LmsContextMixin.get_base_context(self))
        return context

    def form_valid(self, form):
        update_course(
            actor=self.request.user,
            course=form.instance,
            changes=form.cleaned_data,
        )
        messages.success(self.request, _('Los cambios del curso se han guardado.'))
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse('lms:course_detail', kwargs={'course_id': self.object.pk})


class CoursePreviewView(CourseDetailView):
    template_name = 'lms/course_detail.html'

    def dispatch(self, request, *args, **kwargs):
        course = get_object_or_404(Curso, pk=kwargs['course_id'])
        if not can_edit_course(request.user, course):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        preview_context = self.get_course_context(self.object)
        preview_context.update({
            'preview_mode': True,
            'teacher_view': False,
            'course_nav': course_nav(self.object, teacher_view=False, active_key='home'),
            'can_edit': False,
            'can_publish': False,
            'can_enroll': False,
            'is_course_member': False,
            'membership': None,
            'teacher_tools': {},
            'preview_banner': _('Vista previa del alumno'),
        })
        context.update(preview_context)
        return context


class CoursePublishView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id):
        course = get_object_or_404(Curso, pk=course_id)
        if not can_publish_course(request.user, course):
            raise PermissionDenied
        publish_course(actor=request.user, course=course)
        messages.success(request, _('El curso ya está publicado.'))
        return redirect('lms:course_detail', course_id=course.pk)


class CourseEnrollView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id):
        course = get_object_or_404(Curso, pk=course_id)
        try:
            enroll_student(actor=request.user, course=course)
        except EnrollmentUnavailable as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _('Ya formas parte del curso.'))
        return redirect('lms:course_detail', course_id=course.pk)


class LegacyCourseDetailView(LoginRequiredMixin, View):
    def get(self, request, course_slug):
        settings = get_object_or_404(
            CourseSettingsLMS.objects.select_related('course'),
            legacy_slug=course_slug,
        )
        return redirect('lms:course_detail', course_id=settings.course_id)


class CourseForumCreateView(LoginRequiredMixin, CoursePageMixin, View):
    template_name = 'lms/forum_form.html'
    http_method_names = ['get', 'post']

    def get(self, request, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        return render(request, self.template_name, {
            'course': course, 'form': DiscussionTopicForm(), 'teacher_view': True,
            **self.get_base_context(),
        })

    def post(self, request, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        form = DiscussionTopicForm(request.POST)
        if form.is_valid():
            topic = form.save(commit=False)
            topic.course = course
            topic.author = request.user
            topic.save()
            messages.success(request, _('El foro se ha creado.'))
            return redirect('lms:course_area', course_id=course.pk, area='discussion')
        return render(request, self.template_name, {
            'course': course, 'form': form, 'teacher_view': True,
            **self.get_base_context(),
        })


class CourseAnnouncementCountView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['get']

    def get(self, request, course_id, *args, **kwargs):
        course = self.get_course()
        if self.get_teacher_view(course):
            from .notification_state import announcement_reading_state
            count = announcement_reading_state(course, request.user)['pending_students']
            label = ngettext(
                '%(count)s alumno con anuncios pendientes', '%(count)s alumnos con anuncios pendientes', count,
            ) % {'count': count}
        else:
            count = pending_announcements_count(course, request.user)
            label = ngettext('%(count)s anuncio pendiente', '%(count)s anuncios pendientes', count) % {'count': count}
        return JsonResponse({'count': count, 'label': label})


class AnnouncementReadView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, announcement_id, *args, **kwargs):
        course = self.get_course()
        if not is_course_member(request.user, course):
            raise PermissionDenied
        announcement = get_object_or_404(Announcement, pk=announcement_id, course=course)
        if not announcement.is_visible_to_user(request.user):
            raise PermissionDenied
        from apps.users.notifications import mark_lms_notifications_read
        mark_lms_notifications_read(Notification.objects.filter(
            recipient=request.user, course=course, status=Notification.Status.UNREAD,
            event__event_type=NotificationEvent.Type.ANNOUNCEMENT,
        ).filter(
            Q(payload__announcement_id=announcement.pk)
            | Q(payload__announcement_id=str(announcement.pk))
        ))
        count = pending_announcements_count(course, request.user)
        label = ngettext('%(count)s anuncio pendiente', '%(count)s anuncios pendientes', count) % {'count': count}
        return JsonResponse({'count': count, 'label': label})


class AnnouncementCreateView(LoginRequiredMixin, CoursePageMixin, View):
    template_name = 'lms/announcement_form.html'

    def get(self, request, course_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        return render(request, self.template_name, {
            'course': course,
            'form': AnnouncementForm(course=course),
            'teacher_view': True,
            'is_edit': False,
            **self.get_base_context(),
        })

    def post(self, request, course_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        form = AnnouncementForm(request.POST, course=course)
        if not form.is_valid():
            return render(request, self.template_name, {
                'course': course,
                'form': form,
                'teacher_view': True,
                'is_edit': False,
                **self.get_base_context(),
            })
        announcement = form.save(commit=False)
        announcement.course = course
        announcement.author = request.user
        announcement.save()
        if announcement.status == Announcement.Status.PUBLISHED:
            notify_announcement_published(announcement, request.user)
        broadcast_course_update(course, 'created', item_type='announcement', message='announcement-created')
        messages.success(request, _('El anuncio se ha guardado.'))
        return redirect('lms:course_area', course_id=course.pk, area='announcements')


class AnnouncementUpdateView(LoginRequiredMixin, CoursePageMixin, View):
    template_name = 'lms/announcement_form.html'

    def get(self, request, course_id, announcement_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        announcement = get_object_or_404(Announcement, pk=announcement_id, course=course)
        return render(request, self.template_name, {
            'course': course,
            'announcement': announcement,
            'form': AnnouncementForm(instance=announcement, course=course),
            'teacher_view': True,
            'is_edit': True,
            **self.get_base_context(),
        })

    def post(self, request, course_id, announcement_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        announcement = get_object_or_404(Announcement, pk=announcement_id, course=course)
        previous_status = announcement.status
        form = AnnouncementForm(request.POST, instance=announcement, course=course)
        if not form.is_valid():
            return render(request, self.template_name, {
                'course': course,
                'announcement': announcement,
                'form': form,
                'teacher_view': True,
                'is_edit': True,
                **self.get_base_context(),
            })
        form.save()
        if previous_status != Announcement.Status.PUBLISHED and announcement.status == Announcement.Status.PUBLISHED:
            notify_announcement_published(announcement, request.user)
        broadcast_course_update(course, 'updated', item_type='announcement', message='announcement-updated')
        messages.success(request, _('El anuncio se ha actualizado.'))
        return redirect('lms:course_area', course_id=course.pk, area='announcements')


class AnnouncementDeleteView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, announcement_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        announcement = get_object_or_404(Announcement, pk=announcement_id, course=course)
        announcement.delete()
        broadcast_course_update(course, 'deleted', item_type='announcement', message='announcement-deleted')
        messages.success(request, _('El anuncio se ha eliminado.'))
        return redirect('lms:course_area', course_id=course.pk, area='announcements')


class AnnouncementCommentCreateView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, announcement_id, *args, **kwargs):
        course = self.get_course()
        if not is_course_member(request.user, course):
            raise PermissionDenied

        announcement = get_object_or_404(Announcement, pk=announcement_id, course=course)
        if not announcement.allow_comments:
            messages.error(request, _('Este anuncio no admite comentarios.'))
            return redirect('lms:course_area', course_id=course.pk, area='announcements')

        form = AnnouncementCommentForm(request.POST)
        if not form.is_valid():
            for field, errors in form.errors.items():
                for message in errors:
                    messages.error(request, f'{form.fields[field].label}: {message}')
            return redirect('lms:course_area', course_id=course.pk, area='announcements')

        comment = AnnouncementComment.objects.create(
            announcement=announcement,
            author=request.user,
            content=form.cleaned_data['content'],
        )
        broadcast_course_update(course, 'created', item_type='comment', message='comment-created')
        messages.success(request, _('Comentario publicado.'))
        return redirect('lms:course_area', course_id=course.pk, area='announcements')


class AnnouncementAcknowledgeView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, announcement_id, *args, **kwargs):
        course = self.get_course()
        if not is_course_member(request.user, course):
            raise PermissionDenied

        announcement = get_object_or_404(Announcement, pk=announcement_id, course=course)
        if not announcement.requires_acknowledgement:
            messages.info(request, _('Este anuncio no requiere acuse de lectura.'))
            return redirect('lms:course_area', course_id=course.pk, area='announcements')

        acknowledged = request.POST.get('acknowledged') == 'on'
        if not acknowledged:
            messages.error(request, _('Debes confirmar la lectura para dejar constancia de que has revisado el anuncio.'))
            return redirect('lms:course_area', course_id=course.pk, area='announcements')

        pending_announcements_count(course, request.user)
        ack, created = AnnouncementAcknowledgement.objects.get_or_create(
            announcement=announcement,
            user=request.user,
        )
        ack.acknowledged_at = timezone.now()
        ack.save()

        from apps.users.notifications import mark_lms_notifications_read
        mark_lms_notifications_read(Notification.objects.filter(
            recipient=request.user,
            course=course,
            status=Notification.Status.UNREAD,
            event__event_type=NotificationEvent.Type.ANNOUNCEMENT,
        ).filter(
            Q(payload__announcement_id=announcement.pk)
            | Q(payload__announcement_id=str(announcement.pk))
        ))

        broadcast_course_update(course, 'updated', item_type='announcement_ack', message='announcement-acknowledged')
        messages.success(request, _('Acuse de lectura registrado correctamente.'))
        return redirect('lms:course_area', course_id=course.pk, area='announcements')


class AnnouncementCommentDeleteView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, comment_id, *args, **kwargs):
        course = self.get_course()
        comment = get_object_or_404(AnnouncementComment, pk=comment_id, announcement__course=course)
        if not (request.user == comment.author or can_edit_course(request.user, course)):
            raise PermissionDenied

        comment.delete()
        broadcast_course_update(course, 'deleted', item_type='comment', message='comment-deleted')
        messages.success(request, _('Comentario eliminado.'))
        return redirect('lms:course_area', course_id=course.pk, area='announcements')


class CourseModuleCreateView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['get', 'post']

    def get(self, request, course_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        return render(request, 'lms/module_form.html', {
            'course': course,
            'form': ModuleForm(initial={'orden': course.temas.count()}),
            'teacher_view': True,
            **self.get_base_context(),
        })

    def post(self, request, course_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied

        data = request.POST.copy()
        data['titulo'] = request.POST.get('titulo') or request.POST.get('title') or ''
        data['contenido'] = request.POST.get('contenido') or request.POST.get('content') or ''
        if 'orden' not in data:
            data['orden'] = course.temas.count()
        form = ModuleForm(data)
        if not form.is_valid():
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({
                    'ok': False, 'error': ' '.join(
                        str(error) for errors in form.errors.values() for error in errors
                    ),
                }, status=400)
            return render(request, 'lms/module_form.html', {
                'course': course, 'form': form, 'teacher_view': True,
                **self.get_base_context(),
            })
        module = form.save(commit=False)
        module.curso = course
        module.save()

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'ok': True,
                'id': module.pk,
                'title': module.titulo,
                'content': module.contenido,
            })

        messages.success(request, _('El módulo se ha creado.'))
        if request.POST.get('return_to') == 'content':
            return redirect('lms:course_area', course_id=course.pk, area='content')
        if request.POST.get('return_to') == 'activities':
            return redirect('lms:course_activity_create', course_id=course.pk)
        return redirect('lms:course_page_create', course_id=course.pk)


class CourseModuleUpdateView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['get', 'post']

    def get(self, request, course_id, module_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        module = get_object_or_404(Tema, curso=course, pk=module_id)
        return render(request, 'lms/module_form.html', {
            'course': course, 'module': module,
            'form': ModuleForm(instance=module), 'teacher_view': True,
            **self.get_base_context(),
        })

    def post(self, request, course_id, module_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        module = get_object_or_404(Tema, curso=course, pk=module_id)
        form = ModuleForm(request.POST, instance=module)
        if not form.is_valid():
            return render(request, 'lms/module_form.html', {
                'course': course, 'module': module, 'form': form, 'teacher_view': True,
                **self.get_base_context(),
            })
        form.save()
        messages.success(request, _('El módulo se ha actualizado.'))
        return redirect('lms:course_area', course_id=course.pk, area='content')


class CoursePagesListView(LoginRequiredMixin, CoursePageMixin, TemplateView):
    template_name = 'lms/page_list.html'

    def get_context_data(self, **kwargs):
        course = self.get_course()
        teacher_view = self.get_teacher_view(course)
        pages = Page.objects.filter(course=course).select_related(
            'author', 'module', 'current_revision',
        ).order_by('position', 'title')
        query = self.request.GET.get('q', '').strip()
        if not teacher_view:
            pages = [
                page for page in pages
                if page.visibility != Page.Visibility.TEACHERS
                and page.is_currently_published()
                and page.user_can_view(self.request.user)
            ]
        if query and teacher_view:
            pages = pages.filter(title__icontains=query)
        elif query:
            pages = [page for page in pages if query.casefold() in page.title.casefold()]
        context = super().get_context_data(**kwargs)
        context.update({
            'course': course,
            'teacher_view': teacher_view,
            'pages': pages,
            'search_query': query,
            'form': PageForm(course=course),
            'course_areas': COURSE_AREAS,
            'student_area_keys': STUDENT_AREA_KEYS,
            'area_key': 'pages',
            **self.get_base_context(),
        })
        return context


class CourseTopicDeleteView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, topic_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        topic = get_object_or_404(Tema, curso=course, pk=topic_id)
        topic.delete()
        messages.success(request, _('El tema se ha eliminado.'))
        return redirect('lms:course_area', course_id=course.pk, area='content')


class CoursePageCreateView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['get', 'post']

    def get(self, request, course_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        return render(request, 'lms/page_form.html', {
            'course': course,
            'page': None,
            'form': PageForm(course=course),
            'teacher_view': True,
            **self.get_base_context(),
        })

    def post(self, request, course_id, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        form = PageForm(request.POST, course=course)
        if not form.is_valid():
            return render(request, 'lms/page_form.html', {
                'course': course,
                'page': None,
                'form': form,
                'teacher_view': True,
                **self.get_base_context(),
            })
        page = form.save(commit=False)
        page.course = course
        page.author = request.user
        page.slug = (form.cleaned_data.get('slug') or page.slug or page.title).strip()
        if not page.slug:
            page.slug = page.title
        page.save()
        page.create_revision(
            user=request.user,
            title=page.title,
            content=page.content,
            status=page.status,
            visibility=page.visibility,
            summary='Creación inicial',
            published_at=page.publish_at,
        )
        messages.success(request, _('La página se ha guardado.'))
        return redirect('lms:course_page_detail', course_id=course.pk, page_slug=page.slug)


class CoursePageUpdateView(LoginRequiredMixin, CoursePageMixin, View):
    template_name = 'lms/page_form.html'
    http_method_names = ['get', 'post']

    def get(self, request, course_id, page_slug, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        page = get_object_or_404(Page, course=course, slug=page_slug)
        return render(request, self.template_name, {
            'course': course,
            'page': page,
            'form': PageForm(instance=page, course=course),
            'teacher_view': True,
            'is_edit': True,
            **self.get_base_context(),
        })

    def post(self, request, course_id, page_slug, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        page = get_object_or_404(Page, course=course, slug=page_slug)
        form = PageForm(request.POST, instance=page, course=course)
        if not form.is_valid():
            return render(request, self.template_name, {
                'course': course,
                'page': page,
                'form': form,
                'teacher_view': True,
                'is_edit': True,
                **self.get_base_context(),
            })

        page = form.save(commit=False)
        page.course = course
        page.slug = (form.cleaned_data.get('slug') or page.slug or page.title).strip()
        if not page.slug:
            page.slug = page.title
        page.save()
        page.create_revision(
            user=request.user,
            title=page.title,
            content=page.content,
            status=page.status,
            visibility=page.visibility,
            summary='Actualización de contenido',
            published_at=page.publish_at,
        )
        messages.success(request, _('La página se ha actualizado.'))
        return redirect('lms:course_page_detail', course_id=course.pk, page_slug=page.slug)


class CoursePageDeleteView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, page_slug, *args, **kwargs):
        course = self.get_course()
        if not self.get_teacher_view(course):
            raise PermissionDenied
        page = get_object_or_404(Page, course=course, slug=page_slug)
        page.delete()
        messages.success(request, _('La página se ha eliminado.'))
        return redirect('lms:course_pages', course_id=course.pk)


class CoursePageDetailView(LoginRequiredMixin, CoursePageMixin, DetailView):
    model = Page
    template_name = 'lms/page_detail.html'
    slug_url_kwarg = 'page_slug'
    context_object_name = 'page'

    def get_queryset(self):
        return Page.objects.filter(course_id=self.kwargs['course_id']).select_related('author', 'module', 'current_revision')

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if not obj.user_can_view(self.request.user):
            raise PermissionDenied
        if not can_edit_course(self.request.user, obj.course) and not obj.is_currently_published():
            raise PermissionDenied
        from .module_progression import record_open
        record_open(obj, self.request.user)
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = self.object
        teacher_view = can_edit_course(self.request.user, page.course)
        revisions = page.revisions.select_related('created_by').order_by('-revision_number')
        context.update({
            'course': page.course,
            'teacher_view': teacher_view,
            'revisions': revisions,
            'is_homepage': page.is_homepage,
            'is_preview': self.request.GET.get('preview') == '1',
            **self.get_base_context(),
        })
        return context


class CoursePageSetHomepageView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, page_slug, *args, **kwargs):
        course = self.get_course()
        page = get_object_or_404(Page, course=course, slug=page_slug)
        if not can_edit_course(request.user, course):
            raise PermissionDenied
        if not page.is_currently_published():
            messages.error(request, _('La página debe estar publicada para convertirse en la portada del curso.'))
            return redirect('lms:course_page_detail', course_id=course.pk, page_slug=page.slug)
        Page.objects.filter(course=course, is_homepage=True).update(is_homepage=False)
        page.is_homepage = True
        page.save(update_fields=['is_homepage', 'updated_at'])
        messages.success(request, _('La página se ha establecido como inicio del curso.'))
        return redirect('lms:course_page_detail', course_id=course.pk, page_slug=page.slug)


class CoursePagePublishView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, page_slug, *args, **kwargs):
        course = self.get_course()
        page = get_object_or_404(Page, course=course, slug=page_slug)
        if not can_edit_course(request.user, course):
            raise PermissionDenied
        page.status = Page.Status.PUBLISHED
        page.publish_at = page.publish_at or timezone.now()
        page.save(update_fields=['status', 'publish_at', 'updated_at'])
        page.create_revision(
            user=request.user,
            title=page.title,
            content=page.content,
            status=Page.Status.PUBLISHED,
            visibility=page.visibility,
            summary='Publicación del contenido',
            published_at=page.publish_at,
        )
        messages.success(request, _('La página ha sido publicada.'))
        return redirect('lms:course_page_detail', course_id=course.pk, page_slug=page.slug)


class CoursePageRestoreView(LoginRequiredMixin, CoursePageMixin, View):
    http_method_names = ['post']

    def post(self, request, course_id, page_slug, revision_id, *args, **kwargs):
        course = self.get_course()
        page = get_object_or_404(Page, course=course, slug=page_slug)
        if not can_edit_course(request.user, course):
            raise PermissionDenied
        revision = get_object_or_404(PageRevision, pk=revision_id, page=page)
        page.restore_revision(revision, user=request.user)
        messages.success(request, _('La versión anterior se ha restaurado.'))
        return redirect('lms:course_page_detail', course_id=course.pk, page_slug=page.slug)


class LegacyCourseRouteMixin:
    def dispatch(self, request, *args, **kwargs):
        course_settings = get_object_or_404(
            CourseSettingsLMS.objects.only('course_id'),
            legacy_slug=kwargs.pop('course_slug'),
        )
        kwargs['course_id'] = course_settings.course_id
        return super().dispatch(request, *args, **kwargs)


class LegacyCourseUpdateView(LegacyCourseRouteMixin, CourseUpdateView):
    pass


class LegacyCoursePublishView(LegacyCourseRouteMixin, CoursePublishView):
    pass


class LegacyCourseEnrollView(LegacyCourseRouteMixin, CourseEnrollView):
    pass
