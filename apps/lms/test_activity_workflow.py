from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .lifecycle import process_activity
from .models import Announcement, LearningActivity, Notification, Submission, SubmissionAttachment
from .services import notify_announcement_published
from .storage import PrivateMediaStorage
from .tests import LearningActivityStructureTests


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ActivityStudentWorkflowTests(TestCase):
    setUp = LearningActivityStructureTests.setUp

    def test_practice_creation_from_quizzes_and_private_gradebook(self):
        quizzes = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'quizzes'})
        grades = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'grades'})
        create = reverse('lms:course_activity_create', kwargs={'course_id': self.course.pk}) + '?from=quizzes'
        self.client.force_login(self.teacher)
        self.assertNotContains(self.client.get(quizzes), 'Crear práctica')
        self.assertContains(self.client.get(quizzes), 'Crear prueba objetiva')
        response = self.client.get(create)
        self.assertContains(response, 'Crear práctica')
        self.assertEqual(response.context['form']['points'].value(), 10)
        self.assertRedirects(self.client.post(create, {
            'title': 'Práctica evaluable', 'status': 'open', 'points': 10, 'is_evaluable': 'on',
            'feedback_at': (timezone.now() + timedelta(days=1)).isoformat(),
        }), quizzes)
        activity = LearningActivity.objects.get(title='Práctica evaluable')
        self.assertContains(self.client.get(quizzes), activity.title)
        self.course.status = 'published'
        self.course.save(update_fields=['status'])
        self.client.force_login(self.student)
        self.assertContains(self.client.get(quizzes), activity.title)
        self.assertEqual(self.client.get(create).status_code, 403)
        detail = self.url('course_activity_detail', activity)
        self.assertRedirects(self.client.post(detail, {'text_answer': 'Mi entrega https://example.com'}), detail)
        submission = Submission.objects.get(assignment__learning_activity=activity)
        self.client.force_login(self.teacher)
        self.assertRedirects(self.client.post(self.url(
            'course_activity_grade', activity, submission_id=submission.pk,
        ), {'grade': '8', 'feedback': 'Buen trabajo'}), detail)
        self.assertEqual(self.client.get(grades).context['gradebook']['rows'][0]['cells'][0]['grade'], 8)
        self.client.force_login(self.student)
        book = self.client.get(grades).context['gradebook']
        self.assertEqual(len(book['rows']), 1)
        self.assertIsNone(book['rows'][0]['cells'][0]['grade'])
        activity.feedback_at = timezone.now() - timedelta(seconds=1)
        activity.save(update_fields=['feedback_at'])
        self.assertEqual(self.client.get(grades).context['gradebook']['rows'][0]['cells'][0]['grade'], 8)
        draft = LearningActivity.objects.create(
            course=self.course, module=self.module, title='Práctica oculta', status='draft', points=10,
        )
        self.assertNotContains(self.client.get(quizzes), draft.title)
        self.assertNotContains(self.client.get(grades), draft.title)

    def test_optional_reference_can_be_created_edited_and_displayed(self):
        self.client.force_login(self.teacher)
        create = reverse('lms:course_activity_create', kwargs={'course_id': self.course.pk})
        data = {'title': 'Actividad referenciada', 'status': 'open', 'points': 10}
        self.assertEqual(self.client.post(create, data).status_code, 302)
        activity = LearningActivity.objects.get(title=data['title'])
        self.assertEqual(activity.reference_code, '')
        edit = self.url('course_activity_edit', activity)
        self.assertEqual(self.client.post(edit, {**data, 'reference_code': 'ACT-01'}).status_code, 302)
        activity.refresh_from_db()
        self.assertEqual(activity.reference_code, 'ACT-01')
        self.client.force_login(self.student)
        self.assertContains(self.client.get(self.url('course_activity_detail', activity)), 'ACT-01')
        self.assertContains(self.client.get(reverse('lms:course_area', kwargs={
            'course_id': self.course.pk, 'area': 'activities',
        })), 'ACT-01')
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(edit, {**data, 'reference_code': ''}).status_code, 302)
        activity.refresh_from_db()
        self.assertEqual(activity.reference_code, '')

    def activity(self, **kwargs):
        self.course.status = 'published'
        self.course.save(update_fields=['status'])
        return LearningActivity.objects.create(
            course=self.course, module=self.module, title='Práctica',
            **{'status': 'open', 'points': 10, 'due_at': timezone.now() + timedelta(hours=1), **kwargs},
        )

    def url(self, name, activity, **kwargs):
        return reverse(f'lms:{name}', kwargs={
            'course_id': self.course.pk, 'activity_id': activity.pk, **kwargs,
        })

    def test_student_can_submit_only_once_even_before_deadline(self):
        activity = self.activity()
        self.client.force_login(self.student)
        detail = self.url('course_activity_detail', activity)
        self.assertContains(self.client.get(detail), 'Enviar entrega')
        self.assertRedirects(self.client.post(detail, {'text_answer': 'Primera versión'}), detail)
        self.assertNotContains(self.client.get(detail), 'Enviar entrega')
        response = self.client.post(detail, {'text_answer': 'Segunda versión'})
        self.assertContains(response, 'No se permiten nuevas entregas')
        submissions = Submission.objects.filter(assignment__learning_activity=activity).order_by('version')
        self.assertEqual(list(submissions.values_list('version', flat=True)), [1])
        self.assertEqual(submissions.last().text_answer, 'Primera versión')
        assignment = submissions.first().assignment
        assignment.allow_resubmission = True
        assignment.max_attempts = 0
        assignment.save()
        self.assertContains(self.client.post(detail, {'text_answer': 'Intento forzado'}), 'No se permiten nuevas entregas')
        self.assertContains(self.client.get(detail), 'Primera versión')
        activity.due_at = timezone.now()
        activity.save(update_fields=['due_at'])
        response = self.client.post(detail, {'text_answer': 'Fuera de plazo'})
        self.assertContains(response, 'No se permiten nuevas entregas')
        self.assertEqual(submissions.count(), 1)
        self.assertNotContains(self.client.get(detail), 'Enviar entrega')

    def test_activity_detail_tech_modern_preserves_roles_and_live_regions(self):
        from bs4 import BeautifulSoup

        activity = self.activity(
            reference_code='PEC-M1-01', description='<p>Resumen de la practica</p>',
            instructions='<p>Instrucciones de entrega</p>', solution='SOLUCION PRIVADA',
            solution_release_at=timezone.now() + timedelta(days=2),
        )
        detail = self.url('course_activity_detail', activity)
        for user in (self.teacher, self.student):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(detail)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'css/billing.css')
                self.assertContains(response, 'lms/knowledge-tech.css')
                html = BeautifulSoup(response.content, 'html.parser')
                wrapper = html.select_one('.billing-app.knowledge-tech.activity-detail-tech')
                self.assertIsNotNone(wrapper)
                live = wrapper.select_one('[data-quiz-live-region]')
                self.assertEqual(live.select_one('.page-heading h1').text, activity.title)
                self.assertIn('PEC-M1-01', live.select_one('.page-heading').text)
                self.assertIn('Resumen de la practica', live.text)
                self.assertIn('Instrucciones de entrega', live.text)
                self.assertIsNotNone(live.select_one('.activity-meta'))
                if user == self.teacher:
                    self.assertContains(response, self.url('course_activity_edit', activity))
                    self.assertIsNone(wrapper.select_one('[data-activity-delivery-region]'))
                    self.assertContains(response, 'SOLUCION PRIVADA')
                else:
                    delivery = wrapper.select_one('[data-activity-delivery-region]')
                    self.assertIsNotNone(delivery)
                    form = delivery.select_one('form[data-activity-submission-form]')
                    self.assertEqual(form['enctype'], 'multipart/form-data')
                    self.assertIsNotNone(form.select_one('[name="csrfmiddlewaretoken"]'))
                    self.assertIsNotNone(form.select_one('[name="text_answer"]'))
                    self.assertIsNotNone(form.select_one('[type="file"]'))
                    self.assertNotContains(response, self.url('course_activity_edit', activity))
                    self.assertNotContains(response, 'SOLUCION PRIVADA')

    def test_student_files_are_private_and_invalid_files_do_not_create_submissions(self):
        activity = self.activity()
        detail = self.url('course_activity_detail', activity)
        self.client.force_login(self.student)
        field = SubmissionAttachment._meta.get_field('file')
        with TemporaryDirectory() as folder, patch.object(field, 'storage', PrivateMediaStorage(location=folder)):
            self.assertRedirects(self.client.post(detail, {
                'file': SimpleUploadedFile('respuesta.txt', b'Respuesta privada', content_type='text/plain'),
            }), detail)
            attachment = SubmissionAttachment.objects.get()
            download = self.url('course_activity_attachment', activity, attachment_id=attachment.pk)
            response = self.client.get(download)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), b'Respuesta privada')
            response.close()
            self.client.force_login(self.teacher)
            response = self.client.get(download)
            self.assertEqual(response.status_code, 200)
            response.close()
            from django.contrib.auth import get_user_model
            from .models import CourseMembership
            other = get_user_model().objects.create_user('other_activity_student')
            CourseMembership.objects.create(course=self.course, user=other, role='student')
            self.client.force_login(other)
            self.assertEqual(self.client.get(download).status_code, 403)
            self.assertNotContains(self.client.get(detail), 'respuesta.txt')
            response = self.client.post(detail, {
                'file': SimpleUploadedFile('malicioso.exe', b'Not accepted'),
            })
            self.assertContains(response, 'Extensión de archivo no permitida')
            self.assertEqual(Submission.objects.count(), 1)

    def test_student_can_submit_m4a_audio(self):
        activity = self.activity()
        detail = self.url('course_activity_detail', activity)
        self.client.force_login(self.student)
        content = b'\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A '
        field = SubmissionAttachment._meta.get_field('file')
        with TemporaryDirectory() as folder, patch.object(
            field, 'storage', PrivateMediaStorage(location=folder),
        ):
            for name in ('respuesta.m4a', 'RESPUESTA.M4A'):
                with self.subTest(name=name):
                    activity = self.activity()
                    detail = self.url('course_activity_detail', activity)
                    self.assertRedirects(self.client.post(detail, {
                        'file': SimpleUploadedFile(name, content, content_type='audio/mp4'),
                    }), detail)
                    attachment = SubmissionAttachment.objects.get(file_name=name)
                    response = self.client.get(self.url(
                        'course_activity_attachment', activity, attachment_id=attachment.pk,
                    ))
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(b''.join(response.streaming_content), content)
                    response.close()

    def test_grades_feedback_and_solution_obey_release_dates_and_are_private(self):
        now = timezone.now()
        activity = self.activity(
            solution='<p>SOLUCIÓN PRIVADA</p>',
            solution_release_at=now + timedelta(hours=2),
            feedback_at=now + timedelta(hours=2),
        )
        detail = self.url('course_activity_detail', activity)
        self.client.force_login(self.student)
        self.client.post(detail, {'text_answer': 'Mi respuesta'})
        submission = Submission.objects.get()
        grading = self.url('course_activity_grade', activity, submission_id=submission.pk)
        self.assertEqual(self.client.post(grading, {'grade': 9, 'feedback': 'Bien'}).status_code, 403)
        self.client.force_login(self.teacher)
        self.assertContains(self.client.post(grading, {'grade': 11}), '10')
        self.assertRedirects(self.client.post(grading, {'grade': 9, 'feedback': 'FEEDBACK PRIVADO'}), detail)
        self.client.force_login(self.student)
        response = self.client.get(detail)
        self.assertNotContains(response, 'FEEDBACK PRIVADO')
        self.assertNotContains(response, 'SOLUCIÓN PRIVADA')
        self.assertFalse(Notification.objects.filter(recipient=self.student, title='Actividad evaluada').exists())
        activity.feedback_at = activity.solution_release_at = now - timedelta(seconds=1)
        activity.save(update_fields=['feedback_at', 'solution_release_at'])
        process_activity(activity.pk, now=now)
        process_activity(activity.pk, now=now)
        response = self.client.get(detail)
        self.assertContains(response, 'FEEDBACK PRIVADO')
        self.assertContains(response, 'SOLUCIÓN PRIVADA')
        self.assertContains(response, 'Tu nota')
        self.assertEqual(Notification.objects.filter(recipient=self.student, title='Actividad evaluada').count(), 1)
        self.assertFalse(Notification.objects.filter(recipient=self.teacher, title='Actividad evaluada').exists())

    def test_states_and_notification_feed_update_without_duplicates(self):
        now = timezone.now()
        activity = self.activity(starts_at=now + timedelta(minutes=10))
        self.client.force_login(self.student)
        feed = reverse('lms:lifecycle_notifications')
        cursor = self.client.get(feed).json()['cursor']
        process_activity(activity.pk, now=now)
        process_activity(activity.pk, now=now)
        self.assertEqual(Notification.objects.filter(recipient=self.student).count(), 1)
        process_activity(activity.pk, now=activity.starts_at)
        process_activity(activity.pk, now=activity.starts_at)
        process_activity(activity.pk, now=activity.due_at)
        process_activity(activity.pk, now=activity.due_at)
        data = self.client.get(feed, {'after': cursor}).json()
        self.assertEqual([row['title'] for row in data['notifications']], [
            'Actividad creada', 'Actividad abierta', 'Actividad cerrada',
        ])
        self.assertEqual(self.client.get(feed, {'after': data['cursor']}).json()['notifications'], [])
        update = reverse('lms:course_lifecycle_update', kwargs={'course_id': self.course.pk})
        self.assertEqual(self.client.post(update).status_code, 200)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.student)
        self.assertEqual(csrf.post(update).status_code, 403)

    def test_drafts_and_non_students_cannot_submit(self):
        activity = self.activity(status='draft')
        detail = self.url('course_activity_detail', activity)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(detail).status_code, 403)
        process_activity(activity.pk, now=timezone.now())
        self.assertFalse(Notification.objects.filter(recipient=self.student).exists())
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(detail, {'text_answer': 'No soy alumno'}).status_code, 403)
        self.assertFalse(Submission.objects.exists())

    def test_live_feed_includes_announcements_but_hides_expired_ones(self):
        self.client.force_login(self.student)
        feed = reverse('lms:lifecycle_notifications')
        cursor = self.client.get(feed).json()['cursor']
        announcement = Announcement.objects.create(
            course=self.course, author=self.teacher, title='Nuevo aviso',
            status='published', content='Contenido',
        )
        notify_announcement_published(announcement, self.teacher)
        data = self.client.get(feed, {'after': cursor}).json()
        self.assertEqual(len(data['notifications']), 1)
        self.assertIn('Nuevo aviso', data['notifications'][0]['title'])
        announcement.status = 'expired'
        announcement.save(update_fields=['status'])
        self.assertEqual(self.client.get(feed, {'after': cursor}).json()['notifications'], [])
