from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View

from .activity_workflow import (
    ActivityGradeForm, ActivitySubmissionForm, activity_state, can_read_activity,
    can_submit_activity, feedback_visible, grade_activity, submit_activity,
)
from .models import LearningActivity, Submission, SubmissionAttachment
from .policies import can_edit_course
from .views import CourseContextMixin


class ActivityDetailView(CourseContextMixin, View):
    http_method_names = ['get', 'post']

    def get_activity(self):
        course = self.get_course()
        activity = get_object_or_404(
            LearningActivity.objects.select_related('course', 'module'),
            pk=self.kwargs['activity_id'], course=course,
        )
        if not can_read_activity(self.request.user, activity):
            raise PermissionDenied
        return activity

    def render_activity(self, activity, form):
        teacher_view = can_edit_course(self.request.user, activity.course)
        submissions = Submission.objects.filter(
            assignment__learning_activity=activity,
        ).select_related('user').prefetch_related('attachments').order_by('user_id', '-version')
        if not teacher_view:
            submissions = submissions.filter(user=self.request.user)
        else:
            from .campus import scoped_students
            submissions = submissions.filter(user_id__in=scoped_students(
                self.request.user, activity.course,
            ).values('user_id'))
        latest = {}
        rows = []
        for submission in submissions:
            is_latest = submission.user_id not in latest
            latest.setdefault(submission.user_id, submission)
            rows.append({
                'submission': submission,
                'feedback_visible': teacher_view or feedback_visible(activity, submission),
                'can_grade': teacher_view and is_latest and activity.is_evaluable
                and submission.user_id != self.request.user.pk,
            })
        return render(self.request, 'lms/activity_detail.html', {
            **self.get_course_context(activity.course),
            'area_key': 'activities',
            'course': activity.course, 'activity': activity, 'teacher_view': teacher_view,
            'is_course_member': True,
            'form': form, 'submission_rows': rows,
            'has_submitted': not teacher_view and bool(rows),
            'can_submit': not teacher_view and can_submit_activity(self.request.user, activity),
            'solution_visible': teacher_view or bool(activity.solution and activity.can_show_solution()),
            'activity_state_label': {
                'created': _('Creada'), 'open': _('Abierta'), 'closed': _('Cerrada'),
            }[activity_state(activity)],
        })

    def get(self, request, *args, **kwargs):
        activity = self.get_activity()
        from .module_progression import record_open
        record_open(activity, request.user)
        return self.render_activity(activity, ActivitySubmissionForm())

    def post(self, request, *args, **kwargs):
        activity = self.get_activity()
        if can_edit_course(request.user, activity.course):
            raise PermissionDenied
        form = ActivitySubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                submit_activity(
                    activity_id=activity.pk, course=activity.course, user=request.user,
                    data=form.cleaned_data,
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, _('Entrega guardada. No se permiten nuevas entregas de esta actividad.'))
                return redirect('lms:course_activity_detail', course_id=activity.course_id, activity_id=activity.pk)
        return self.render_activity(activity, form)


class ActivityGradeView(CourseContextMixin, View):
    http_method_names = ['get', 'post']

    def get_objects(self):
        course = self.get_course()
        if not can_edit_course(self.request.user, course):
            raise PermissionDenied
        activity = get_object_or_404(LearningActivity, course=course, pk=self.kwargs['activity_id'])
        submission = get_object_or_404(
            Submission.objects.select_related('user').prefetch_related('attachments'),
            assignment__learning_activity=activity, pk=self.kwargs['submission_id'],
        )
        from .campus import can_review_submission
        if not can_review_submission(self.request.user, submission):
            raise PermissionDenied
        return activity, submission

    def render_form(self, activity, submission, form):
        return render(self.request, 'lms/activity_grade.html', {
            **self.get_base_context(), 'course': activity.course, 'activity': activity,
            'submission': submission, 'form': form, 'teacher_view': True,
        })

    def get(self, request, *args, **kwargs):
        activity, submission = self.get_objects()
        return self.render_form(activity, submission, ActivityGradeForm(
            activity=activity, initial={'grade': submission.grade, 'feedback': submission.feedback},
        ))

    def post(self, request, *args, **kwargs):
        activity, submission = self.get_objects()
        form = ActivityGradeForm(request.POST, activity=activity)
        if form.is_valid():
            try:
                grade_activity(
                    activity_id=activity.pk, course=activity.course, actor=request.user,
                    submission_id=submission.pk, data=form.cleaned_data,
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, _('Evaluación guardada. El alumno la verá desde la fecha de feedback.'))
                return redirect('lms:course_activity_detail', course_id=activity.course_id, activity_id=activity.pk)
        return self.render_form(activity, submission, form)


class ActivityAttachmentView(CourseContextMixin, View):
    http_method_names = ['get']

    def get(self, request, *args, **kwargs):
        course = self.get_course()
        attachment = get_object_or_404(
            SubmissionAttachment.objects.select_related('submission__assignment__learning_activity'),
            pk=kwargs['attachment_id'],
            submission__assignment__learning_activity_id=kwargs['activity_id'],
            submission__assignment__course=course,
        )
        activity = attachment.submission.assignment.learning_activity
        from .campus import can_review_submission
        if not can_read_activity(request.user, activity) or not (
            request.user.pk == attachment.submission.user_id or can_review_submission(request.user, attachment.submission)
        ):
            raise PermissionDenied
        response = FileResponse(
            attachment.file.open('rb'), as_attachment=True, filename=attachment.file_name,
            content_type='application/octet-stream',
        )
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
