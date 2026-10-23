from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from .activity_workflow import grade_activity, submit_activity
from .campus import freeze_contract, learning_status
from .models import Assignment, AuditEvent, CourseMembership, LearningActivity, ModuleContent, Section, Submission
from .test_campus_access import CampusFixture


class CampusDeliveryTests(CampusFixture):
    def setUp(self):
        super().setUp()
        self.activity = LearningActivity.objects.create(
            course=self.course, module=self.module, title='Trabajo fixture', status='open',
            points=10, is_evaluable=True,
        )
        self.activity_item = ModuleContent.objects.create(
            module=self.module, activity=self.activity, criterion='passed', passing_percentage=60,
        )
        self.membership = self.enroll()

    def submit(self, **data):
        return submit_activity(
            activity_id=self.activity.pk, course=self.course, user=self.student,
            data={'text_answer': 'Respuesta fixture', 'idempotency_key': uuid4(), **data},
        )

    def test_idempotency_version_history_and_published_correction(self):
        key = uuid4()
        submission = self.submit(idempotency_key=key)
        replay = self.submit(idempotency_key=key)
        self.assertEqual(submission.pk, replay.pk)
        with self.assertRaises(ValidationError):
            self.submit(idempotency_key=key, text_answer='Otro contenido')
        with self.assertRaisesMessage(ValidationError, 'No se permiten nuevas entregas'):
            self.submit()
        self.assertEqual(Submission.objects.filter(user=self.student).count(), 1)
        grade_activity(activity_id=self.activity.pk, course=self.course, actor=self.teacher,
                       submission_id=submission.pk, data={'grade': Decimal('7'), 'feedback': 'Aprobado fixture'})
        submission.refresh_from_db()
        self.assertTrue(submission.feedback_published)
        self.assertEqual(submission.grade, 7)
        self.complete_lesson()
        self.membership.refresh_from_db()
        self.assertTrue(learning_status(self.membership)['passed'])
        self.assertIsNotNone(self.membership.passed_at)
        self.assertEqual(AuditEvent.objects.filter(metadata__campus_action='activity_graded').count(), 1)
        self.client.logout()
        url = reverse('lms:course_activity_detail', args=[self.course.pk, self.activity.pk])
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_attempt_limit_and_deadline_do_not_create_extra_versions(self):
        submission = self.submit()
        assignment = submission.assignment
        assignment.max_attempts = 1
        assignment.save()
        with self.assertRaises(ValidationError):
            self.submit()
        self.activity.due_at = timezone.now()
        self.activity.save()
        with self.assertRaises(ValidationError):
            self.submit()
        self.assertEqual(assignment.submissions.count(), 1)

    def test_upload_size_type_and_missing_idempotency(self):
        with self.assertRaises(ValidationError):
            self.submit(file=SimpleUploadedFile('small.txt', b'test'))
        self.settings.campus_max_submission_bytes = 3
        self.settings.save()
        with self.assertRaises(ValidationError):
            self.submit(file=SimpleUploadedFile('small.txt', b'test'))
        with self.assertRaises(ValidationError):
            self.submit(file=SimpleUploadedFile('unsafe.exe', b'test'))
        with self.assertRaises(ValidationError):
            self.submit(idempotency_key=None)
        self.assertEqual(Submission.objects.count(), 0)

    def test_teacher_cannot_grade_own_or_other_group_and_sees_only_assigned_students(self):
        submission = self.submit()
        first = Section.objects.create(course=self.course, title='Grupo docente')
        second = Section.objects.create(course=self.course, title='Otro grupo')
        CourseMembership.objects.filter(course=self.course, user=self.teacher).update(section=first)
        CourseMembership.objects.filter(pk=self.membership.pk).update(section=second)
        with self.assertRaises(PermissionDenied):
            grade_activity(activity_id=self.activity.pk, course=self.course, actor=self.teacher,
                           submission_id=submission.pk, data={'grade': 8, 'feedback': 'No autorizado'})
        self.client.force_login(self.teacher)
        response = self.client.get(reverse('lms:course_activity_detail', args=[self.course.pk, self.activity.pk]))
        self.assertNotContains(response, 'Respuesta fixture')
        response = self.client.get(reverse('lms:campus_teaching'))
        self.assertNotContains(response, self.student.username)
        self.assertEqual(self.client.get(reverse(
            'lms:course_area', args=[self.course.pk, 'grades'],
        )).status_code, 403)
        with self.assertRaises(PermissionDenied):
            grade_activity(activity_id=self.activity.pk, course=self.course, actor=self.student,
                           submission_id=submission.pk, data={'grade': 10, 'feedback': 'Autoevaluación'})
