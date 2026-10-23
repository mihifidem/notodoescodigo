from django.contrib import messages
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _

from apps.courses.models import Curso, InscripcionCurso
from apps.users.access import effective_plan
from .campus import (
    access_message, campus_settings, course_right, issue_certificate, learning_status,
    material_access, scoped_students,
)
from .models import CourseCertificate, CourseMembership, ModuleContent
from .module_progression import content_lock_reason, content_url
from .selectors import teacher_memberships
from .services import EnrollmentUnavailable, enroll_student


@method_decorator(never_cache, name='dispatch')
class CampusView(View):
    def get(self, request):
        context = {
            'campus_rows': [], 'campus_plan': 'visitor',
            'campus_login_url': settings.LOGIN_URL,
        }
        if request.user.is_authenticated:
            memberships = CourseMembership.objects.filter(
                user=request.user,
                role__in=(CourseMembership.Role.STUDENT, CourseMembership.Role.OBSERVER),
            ).select_related('course', 'course__lms_settings', 'section', 'last_content')
            rows = []
            for membership in memberships:
                course = membership.course
                decision = material_access(request.user, course)
                managed = bool(campus_settings(course))
                rows.append({
                    'course': course, 'membership': membership,
                    'managed': managed, 'allowed': decision.allowed,
                    'reason': access_message(decision.reason) if not decision.allowed else '',
                    'progress': learning_status(membership) if managed else None,
                    'url': (
                        redirect('lms:campus_course', course_id=course.pk).url if managed
                        else redirect('lms:course_detail', course_id=course.pk).url
                    ),
                })
            known = [row['course'].pk for row in rows]
            for enrollment in InscripcionCurso.objects.filter(
                usuario=request.user,
            ).exclude(curso_id__in=known).select_related('curso', 'curso__lms_settings'):
                managed = bool(campus_settings(enrollment.curso))
                rows.append({
                    'course': enrollment.curso, 'legacy': True, 'managed': managed,
                    'url': redirect(
                        'lms:campus_course' if managed else 'lms:course_detail',
                        course_id=enrollment.curso_id,
                    ).url,
                })
            context.update(campus_rows=rows, campus_plan=effective_plan(request.user))
        return render(request, 'lms/campus.html', context)


@method_decorator(never_cache, name='dispatch')
class CampusCourseView(LoginRequiredMixin, View):
    def get(self, request, course_id):
        course = get_object_or_404(Curso.objects.select_related('lms_settings'), pk=course_id)
        if not campus_settings(course):
            return redirect('lms:course_detail', course_id=course.pk)
        membership = CourseMembership.objects.filter(course=course, user=request.user).first()
        decision = material_access(request.user, course)
        status = learning_status(membership) if membership and membership.learning_contract else None
        context = {
            'campus_course': course, 'membership': membership, 'status': status,
            'allowed': decision.allowed, 'access_reason': access_message(decision.reason),
            'can_enroll': not membership and course_right(request.user, course).allowed,
            'certificate': CourseCertificate.objects.filter(course=course, user=request.user).first(),
            'certificate_included': bool(membership and membership.learning_contract.get('certificate_included')),
        }
        if decision.allowed and membership and membership.learning_contract:
            frozen = {item['id']: item for item in membership.learning_contract['items']}
            contents = ModuleContent.objects.filter(
                pk__in=frozen, module__curso=course,
            ).select_related('module', 'page', 'file', 'quiz', 'activity').order_by('module__orden', 'position', 'pk')
            completed = {item['id'] for item in status['requirements'] if item['complete']}
            lessons = []
            for content in contents:
                reason = content_lock_reason(content, request.user)
                lessons.append({
                    'id': content.pk, 'title': frozen[content.pk]['title'],
                    'module': content.module.titulo, 'url': content_url(content) if not reason else '',
                    'reason': reason, 'complete': content.pk in completed,
                    'criterion': content.get_criterion_display(),
                    'required': frozen[content.pk]['required'],
                    'manual': frozen[content.pk]['criterion'] == 'manual',
                    'opened': content.progress.filter(user=request.user, opened_at__isnull=False).exists(),
                })
            context['lessons'] = lessons
            resume = next((row for row in lessons if row['id'] == membership.last_content_id and row['url']), None)
            context['resume'] = resume or next((row for row in lessons if not row['complete'] and row['url']), None)
        return render(request, 'lms/campus_course.html', context)

    def post(self, request, course_id):
        course = get_object_or_404(Curso, pk=course_id, lms_settings__campus_enabled=True)
        action = request.POST.get('action')
        try:
            if action == 'enroll':
                enroll_student(actor=request.user, course=course)
                messages.success(request, _('Matrícula registrada con sus requisitos educativos.'))
            elif action == 'certificate':
                issue_certificate(request.user, course, actor=request.user)
                messages.success(request, _('Certificado disponible en Mis certificaciones.'))
            else:
                raise PermissionDenied
        except (EnrollmentUnavailable, ValidationError) as error:
            messages.error(request, '; '.join(error.messages) if isinstance(error, ValidationError) else str(error))
        return redirect('lms:campus_course', course_id=course.pk)


@method_decorator(never_cache, name='dispatch')
class CampusTeachingView(LoginRequiredMixin, View):
    def get(self, request):
        courses = []
        for teaching in teacher_memberships(request.user):
            if campus_settings(teaching.course):
                students = [
                    {'membership': student, 'progress': learning_status(student)}
                    for student in scoped_students(request.user, teaching.course).select_related('user', 'course', 'section')
                ]
                courses.append({'course': teaching.course, 'section': teaching.section, 'students': students})
        return render(request, 'lms/campus_teaching.html', {'teaching_courses': courses})
