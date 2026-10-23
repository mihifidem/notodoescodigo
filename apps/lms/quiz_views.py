import html
from decimal import Decimal
from math import ceil

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.utils import timezone
from django.views import View
from django.urls import reverse

from .models import CourseMembership, GradeEntry, Quiz, QuizAttempt
from .policies import can_edit_course, is_course_member
from .quiz_management import (
    QuizConfigurationForm, QuizPassingScoreForm, QuizReopenForm, change_quiz_status, check_editable,
    delete_quiz, reopen_quiz, save_quiz_configuration, update_quiz_passing_score,
)
from .quiz_attempts import (
    attempt_deadline, attempt_questions, begin_attempt, finish_attempt, require_student, update_attempt,
)
from .views import CourseContextMixin
from .quiz_status import quiz_grade_release_filter, quiz_grade_visible, quiz_is_closed, quiz_solutions_visible, teacher_quiz_status


class QuizContextMixin(CourseContextMixin):
    def get_quiz(self):
        self.course = self.get_course()
        quiz = get_object_or_404(Quiz, course=self.course, pk=self.kwargs['quiz_id'])
        from .module_progression import require_target_access
        require_target_access(quiz, self.request.user)
        return quiz

    def require_teacher(self):
        self.course = self.get_course()
        if not can_edit_course(self.request.user, self.course):
            raise PermissionDenied

    def context(self, **extra):
        return {**self.get_course_context(self.course), 'area_key': 'quizzes', **extra}


class QuizEditView(QuizContextMixin, View):
    def handle(self, request):
        self.require_teacher()
        quiz = self.get_quiz() if 'quiz_id' in self.kwargs else None
        if quiz:
            try:
                check_editable(quiz, allow_published=True)
            except ValidationError as error:
                messages.error(request, ' '.join(error.messages))
                return redirect('lms:quiz_detail', course_id=self.course.pk, quiz_id=quiz.pk)
        form = QuizConfigurationForm(
            request.POST if request.method == 'POST' else None, course=self.course, instance=quiz,
        )
        if request.method == 'POST' and form.is_valid():
            try:
                saved = save_quiz_configuration(course=self.course, actor=request.user, form=form, quiz=quiz)
            except ValidationError as error:
                form.add_error(None, ' '.join(error.messages))
            else:
                messages.success(request, _('La prueba publicada se ha actualizado.') if saved.status == Quiz.Status.PUBLISHED
                                 else _('La prueba se ha guardado como borrador.'))
                return redirect('lms:quiz_detail', course_id=self.course.pk, quiz_id=saved.pk)
        return render(request, 'lms/quiz_form.html', self.context(
            form=form, quiz=quiz, heading=_('Editar prueba') if quiz else _('Crear prueba'),
        ))

    def get(self, request, *args, **kwargs):
        return self.handle(request)

    def post(self, request, *args, **kwargs):
        return self.handle(request)


class QuizPassingScoreView(QuizContextMixin, View):
    def handle(self, request):
        self.require_teacher()
        quiz = self.get_quiz()
        form = QuizPassingScoreForm(
            request.POST if request.method == 'POST' else None,
            quiz=quiz, initial={'passing_score': quiz.passing_score},
        )
        if request.method == 'POST' and form.is_valid():
            try:
                update_quiz_passing_score(
                    course=self.course, actor=request.user, quiz_id=quiz.pk,
                    passing_score=form.cleaned_data['passing_score'],
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, _('Mínimo de aprobado actualizado. Los resultados calificados se han actualizado.'))
                return redirect('lms:quiz_detail', course_id=self.course.pk, quiz_id=quiz.pk)
        return render(request, 'lms/quiz_passing_score.html', self.context(quiz=quiz, form=form))

    def get(self, request, *args, **kwargs):
        return self.handle(request)

    def post(self, request, *args, **kwargs):
        return self.handle(request)


class QuizDetailView(QuizContextMixin, View):
    def get(self, request, *args, **kwargs):
        quiz = self.get_quiz()
        teacher = can_edit_course(request.user, self.course)
        if not teacher:
            if not is_course_member(request.user, self.course) or quiz.status in (Quiz.Status.DRAFT, Quiz.Status.SCHEDULED):
                raise PermissionDenied
            if quiz.section_id and not CourseMembership.objects.filter(
                course=self.course, user=request.user, section_id=quiz.section_id,
                role=CourseMembership.Role.STUDENT,
                status__in=(CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED),
            ).exists():
                raise PermissionDenied
        answers_visible = quiz_solutions_visible(quiz, student=None if teacher else request.user)
        from .module_progression import record_open
        record_open(quiz, request.user)
        questions = quiz.questions.select_related('question_version') if teacher or answers_visible else None
        if questions is not None:
            for item in questions:
                version = item.question_version
                item.display_prompt = html.unescape(version.prompt) if version.metadata.get('plain_text') else version.prompt
        count = quiz.questions.count()
        scaled = count > 0 and all(
            metadata.get('grading_scale') == 10 for metadata in quiz.questions.values_list('metadata', flat=True)
        )
        value = Decimal(10) / count if scaled else None
        penalty_metadata = quiz.questions.filter(metadata__penalize_errors=True).values_list('metadata', flat=True).first()
        deduction = (
            Decimal(str(penalty_metadata['error_deduction'])) if penalty_metadata and 'error_deduction' in penalty_metadata
            else value / 3 if penalty_metadata and value else 0
        )
        attempts = quiz.attempts.select_related('user', 'grade_entry').order_by('-started_at', '-pk')
        if not teacher:
            attempts = attempts.filter(user=request.user)
        else:
            from .campus import campus_settings, scoped_students
            if campus_settings(self.course):
                attempts = attempts.filter(user_id__in=scoped_students(request.user, self.course).values('user_id'))
        history = []
        from .lifecycle import student_recipients
        release_student_ids = {
            student.pk for student in student_recipients(self.course, section_id=quiz.section_id)
        } if teacher else set()
        for attempt in attempts:
            entry = getattr(attempt, 'grade_entry', None)
            released = quiz_grade_visible(entry)
            history.append({
                'attempt': attempt, 'show_results': teacher or released,
                'can_release_results': (
                    teacher and not quiz.publish_grade_at and not attempt.results_released_at
                    and attempt.user_id != request.user.pk and attempt.status == QuizAttempt.Status.GRADED
                    and attempt.user_id in release_student_ids
                    and entry is not None and not entry.is_hidden
                    and entry.status in (GradeEntry.Status.DRAFT, GradeEntry.Status.PUBLISHED)
                ),
            })
        active_attempt = quiz.attempts.filter(user=request.user, status=QuizAttempt.Status.IN_PROGRESS).first()
        eligible = CourseMembership.objects.filter(
            course=self.course, user=request.user, role=CourseMembership.Role.STUDENT,
            status__in=(CourseMembership.Status.ACTIVE, CourseMembership.Status.COMPLETED),
        ).exists()
        if not CourseMembership.objects.filter(course=self.course, user=request.user).exists():
            from apps.courses.models import InscripcionCurso
            eligible = InscripcionCurso.objects.filter(curso=self.course, usuario=request.user).exists()
        now = timezone.now()
        deadline = attempt_deadline(quiz)
        quiz.effective_end = deadline
        can_start = (
            not teacher and eligible and scaled and quiz.status == Quiz.Status.PUBLISHED
            and self.course.status == 'published'
            and (not quiz.available_from or quiz.available_from <= now)
            and (not deadline or now < deadline)
            and not active_attempt and quiz.attempts.filter(user=request.user).count() < quiz.max_attempts
        )
        return render(request, 'lms/quiz_detail.html', self.context(
            quiz=quiz, quiz_questions=questions, question_count=count,
            maximum_points=10 if scaled else quiz.questions.aggregate(total=Sum('points'))['total'] or 0,
            scaled_grading=scaled, question_value=value,
            error_penalty=deduction,
            blank_penalty=Decimal(str(quiz.questions.values_list('metadata', flat=True).first().get('blank_deduction', '0'))) if count else Decimal(0),
            quiz_phase_labels=teacher_quiz_status(quiz, student=None if teacher else request.user),
            attempt_history=history, active_attempt=active_attempt, can_start_attempt=can_start,
            answers_visible=answers_visible,
            published_grades=GradeEntry.objects.filter(
                quiz_grade_release_filter(),
                attempt__quiz=quiz, user=request.user, status=GradeEntry.Status.PUBLISHED,
                is_hidden=False,
            ).select_related('attempt'),
            can_edit_quiz=teacher and quiz.status in (Quiz.Status.DRAFT, Quiz.Status.PUBLISHED) and not quiz.attempts.exists(),
            can_reopen_quiz=teacher and quiz_is_closed(quiz) and self.course.status == 'published',
        ))


class QuizReleaseResultsView(QuizContextMixin, View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        self.require_teacher()
        quiz = self.get_quiz()
        get_object_or_404(QuizAttempt, pk=self.kwargs['attempt_id'], quiz=quiz)
        from .quiz_results import release_attempt_results
        try:
            release_attempt_results(
                course=self.course, actor=request.user, quiz_id=quiz.pk,
                attempt_id=self.kwargs['attempt_id'],
            )
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        else:
            messages.success(request, _('Resultado y solución publicados para el alumno.'))
        return redirect(
            reverse('lms:quiz_detail', kwargs={'course_id': self.course.pk, 'quiz_id': quiz.pk})
            + '#quiz-attempt-history'
        )


class QuizStartView(QuizContextMixin, View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        quiz = self.get_quiz()
        try:
            attempt = begin_attempt(user=request.user, quiz_id=quiz.pk, course_id=self.course.pk)
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
            return redirect('lms:quiz_detail', course_id=self.course.pk, quiz_id=quiz.pk)
        return redirect('lms:quiz_attempt', course_id=self.course.pk, quiz_id=quiz.pk, attempt_id=attempt.pk)


class QuizAttemptView(QuizContextMixin, View):
    def get_attempt(self):
        quiz = self.get_quiz()
        require_student(self.request.user, quiz)
        return get_object_or_404(QuizAttempt, pk=self.kwargs['attempt_id'], quiz=quiz, user=self.request.user)

    @transaction.atomic
    def get(self, request, *args, **kwargs):
        attempt = self.get_attempt()
        quiz = Quiz.objects.select_for_update().select_related('course').get(pk=attempt.quiz_id)
        require_student(request.user, quiz)
        attempt = QuizAttempt.objects.select_for_update().select_related('quiz').get(pk=attempt.pk)
        now = timezone.now()
        deadline = attempt_deadline(quiz, attempt)
        if attempt.status == QuizAttempt.Status.IN_PROGRESS and (
            quiz.status == Quiz.Status.CLOSED or quiz.course.status == 'archived' or (deadline and now >= deadline)
        ):
            finish_attempt(quiz, attempt, now=now)
        if attempt.status != QuizAttempt.Status.IN_PROGRESS:
            return redirect('lms:quiz_detail', course_id=self.course.pk, quiz_id=quiz.pk)
        if (quiz.status != Quiz.Status.PUBLISHED or quiz.course.status != 'published'
                or (quiz.available_from and now < quiz.available_from)):
            messages.error(request, _('La prueba no está disponible.'))
            return redirect('lms:quiz_detail', course_id=self.course.pk, quiz_id=quiz.pk)
        try:
            questions = attempt_questions(attempt)
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
            return redirect('lms:quiz_detail', course_id=self.course.pk, quiz_id=quiz.pk)
        grading = attempt.configuration_snapshot.get('question_grading', {})
        metadata = next(iter(grading.values()), None)
        if metadata is None:
            metadata = quiz.questions.values_list('metadata', flat=True).first() or {}
        return render(request, 'lms/quiz_attempt.html', self.context(
            quiz=quiz, attempt=attempt, attempt_questions=questions, deadline=deadline,
            blank_penalty=Decimal(str(metadata.get('blank_deduction', '0'))),
            seconds_remaining=max(0, ceil((deadline - now).total_seconds())) if deadline else None,
        ))

    def post(self, request, *args, **kwargs):
        attempt = self.get_attempt()
        action = request.POST.get('action')
        ajax = request.headers.get('Accept') == 'application/json'
        if action not in ('save', 'submit'):
            if ajax:
                return JsonResponse({'error': _('Acción inválida.')}, status=400)
            messages.error(request, _('Acción inválida.'))
            return redirect('lms:quiz_attempt', course_id=self.course.pk, quiz_id=attempt.quiz_id, attempt_id=attempt.pk)
        try:
            attempt, finished = update_attempt(
                user=request.user, quiz_id=attempt.quiz_id, course_id=self.course.pk,
                attempt_id=attempt.pk, data=request.POST, submit=action == 'submit',
            )
        except ValidationError as error:
            if ajax:
                return JsonResponse({'error': ' '.join(error.messages)}, status=400)
            messages.error(request, ' '.join(error.messages))
            return redirect('lms:quiz_attempt', course_id=self.course.pk, quiz_id=attempt.quiz_id, attempt_id=attempt.pk)
        result_url = reverse(
            'lms:quiz_detail', kwargs={'course_id': self.course.pk, 'quiz_id': attempt.quiz_id},
        )
        if ajax:
            return JsonResponse({'saved': True, 'finished': finished, 'url': result_url,
                                 'saved_at': attempt.last_autosave_at.isoformat() if attempt.last_autosave_at else None})
        if finished:
            messages.success(request, _('La prueba se ha entregado y la calificación ha quedado registrada.'))
            return redirect(result_url)
        messages.success(request, _('Respuestas guardadas.'))
        return redirect('lms:quiz_attempt', course_id=self.course.pk, quiz_id=attempt.quiz_id, attempt_id=attempt.pk)


class QuizStatusView(QuizContextMixin, View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        self.require_teacher()
        quiz = self.get_quiz()
        try:
            change_quiz_status(course=self.course, actor=request.user, quiz_id=quiz.pk, action=request.POST.get('action'))
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        else:
            messages.success(request, _('Se ha actualizado el estado de la prueba.'))
        return redirect('lms:quiz_detail', course_id=self.course.pk, quiz_id=quiz.pk)


class QuizReopenView(QuizContextMixin, View):
    def handle(self, request):
        self.require_teacher()
        quiz = self.get_quiz()
        if not quiz_is_closed(quiz) or self.course.status != 'published':
            messages.error(request, _('Solo se pueden reabrir pruebas cerradas de un curso publicado.'))
            return redirect('lms:quiz_detail', course_id=self.course.pk, quiz_id=quiz.pk)
        form = QuizReopenForm(request.POST if request.method == 'POST' else None)
        if request.method == 'POST' and form.is_valid():
            try:
                reopen_quiz(course=self.course, actor=request.user, quiz_id=quiz.pk,
                            grace_until=form.cleaned_data['grace_until'])
            except ValidationError as error:
                form.add_error(None, ' '.join(error.messages))
            else:
                messages.success(request, _('La prueba se ha reabierto durante el periodo de gracia.'))
                return redirect('lms:quiz_detail', course_id=self.course.pk, quiz_id=quiz.pk)
        return render(request, 'lms/quiz_reopen.html', self.context(quiz=quiz, form=form))

    def get(self, request, *args, **kwargs):
        return self.handle(request)

    def post(self, request, *args, **kwargs):
        return self.handle(request)


class QuizDeleteView(QuizContextMixin, View):
    def get(self, request, *args, **kwargs):
        self.require_teacher()
        quiz = self.get_quiz()
        return render(request, 'lms/quiz_delete.html', self.context(quiz=quiz))

    def post(self, request, *args, **kwargs):
        self.require_teacher()
        quiz = self.get_quiz()
        if request.POST.get('confirm') != 'delete':
            messages.error(request, _('Debes confirmar la eliminación.'))
            return redirect('lms:quiz_delete', course_id=self.course.pk, quiz_id=quiz.pk)
        try:
            delete_quiz(course=self.course, actor=request.user, quiz_id=quiz.pk)
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
            return redirect('lms:quiz_detail', course_id=self.course.pk, quiz_id=quiz.pk)
        except ProtectedError:
            messages.error(request, _('No se puede eliminar la prueba porque está vinculada a registros académicos.'))
            return redirect('lms:quiz_detail', course_id=self.course.pk, quiz_id=quiz.pk)
        messages.success(request, _('La prueba se ha eliminado. Las preguntas del banco se conservan.'))
        return redirect('lms:course_area', course_id=self.course.pk, area='quizzes')
