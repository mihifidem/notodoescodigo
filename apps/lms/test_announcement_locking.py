from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import Curso
from .models import Announcement, CourseMembership
from .services import process_course_announcements


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class AnnouncementLockingTests(TestCase):
    def test_course_area_design_preserves_role_controls(self):
        self.course.creado_por = self.teacher
        self.course.save(update_fields=['creado_por'])
        student = get_user_model().objects.create_user('areas-design-student')
        CourseMembership.objects.create(course=self.course, user=student, role='student')
        for user in (self.teacher, student):
            self.client.force_login(user)
            for area, marker, asset in (
                ('content', 'content-tech', 'lms/content-tech.css'),
                ('discussion', 'discussion-tech', 'lms/knowledge-tech.css'),
                ('grades', 'grades-tech', 'lms/knowledge-tech.css'),
                ('people', 'people-tech', 'lms/knowledge-tech.css'),
                ('syllabus', 'syllabus-tech', 'lms/knowledge-tech.css'),
                ('quizzes', 'quizzes-tech', 'lms/knowledge-tech.css'),
                ('activities', 'activities-tech', 'lms/knowledge-tech.css'),
                ('files', 'files-tech', 'lms/knowledge-tech.css'),
                ('competencies', 'competencies-tech', 'lms/knowledge-tech.css'),
                ('rubrics', 'rubrics-tech', 'lms/knowledge-tech.css'),
                ('collaborations', 'collaborations-tech', 'lms/knowledge-tech.css'),
                ('settings', 'settings-tech', 'lms/knowledge-tech.css'),
            ):
                with self.subTest(user=user.username, area=area):
                    response = self.client.get(reverse('lms:course_area', kwargs={
                        'course_id': self.course.pk, 'area': area,
                    }))
                    if user == student and area in ('rubrics', 'collaborations', 'settings'):
                        self.assertEqual(response.status_code, 403)
                        continue
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, marker)
                    self.assertContains(response, asset)
                    if area == 'competencies':
                        if user == student:
                            self.assertNotContains(response, 'id="badge-form"')
                        else:
                            self.assertContains(response, 'id="badge-form"')
                    if area in ('syllabus', 'quizzes', 'rubrics', 'collaborations', 'competencies'):
                        self.assertContains(response, 'course-context-grid-full')
                        self.assertNotContains(response, '<aside class="upcoming-panel">')
                        self.assertNotContains(response, 'Próximas entregas')
                    elif area in ('announcements', 'discussion', 'activities'):
                        self.assertContains(response, '<aside class="upcoming-panel">')
                    if user == student:
                        self.assertNotContains(response, 'data-module-create-panel')
                        self.assertNotContains(response, reverse(
                            'lms:course_forum_create', args=[self.course.pk],
                        ))
                        self.assertNotContains(response, reverse('lms:quiz_create', args=[self.course.pk]))
                        self.assertNotContains(response, reverse('lms:course_activity_create', args=[self.course.pk]))
            response = self.client.get(reverse('lms:course_pages', args=[self.course.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'pages-tech')
            self.assertContains(response, 'lms/knowledge-tech.css')
            if user == student:
                self.assertNotContains(response, reverse('lms:course_page_create', args=[self.course.pk]))

    def setUp(self):
        self.teacher = get_user_model().objects.create_user('locking-teacher')
        self.course = Curso.objects.create(
            nombre='Announcement locking', descripcion='Test course',
            fecha_inicio=date(2026, 10, 22),
        )
        CourseMembership.objects.create(course=self.course, user=self.teacher, role='owner')

    def test_lock_query_has_no_nullable_author_join(self):
        with patch.object(
            Announcement.objects, 'select_for_update', wraps=Announcement.objects.select_for_update,
        ) as lock, CaptureQueriesContext(connection) as queries:
            process_course_announcements(self.course)
        lock.assert_called_once_with()
        selects = [
            item['sql'] for item in queries.captured_queries
            if item['sql'].lstrip().upper().startswith('SELECT')
            and '"lms_announcement"' in item['sql']
        ]
        self.assertEqual(len(selects), 1)
        self.assertNotIn('JOIN', selects[0].upper())

    def test_publication_expiry_and_notifications_with_or_without_author(self):
        now = timezone.now()
        due = [
            Announcement.objects.create(
                course=self.course, author=author, title=f'Due {index}',
                content='Content', status='scheduled',
                publish_at=now - timedelta(minutes=1),
            )
            for index, author in enumerate((self.teacher, None))
        ]
        future = Announcement.objects.create(
            course=self.course, title='Future', content='Content',
            status='scheduled', publish_at=now + timedelta(hours=1),
        )
        expired = Announcement.objects.create(
            course=self.course, title='Expired', content='Content',
            status='scheduled', publish_at=now - timedelta(hours=2),
            expire_at=now - timedelta(hours=1),
        )
        with patch('apps.lms.services.notify_announcement_published') as notify:
            process_course_announcements(self.course, now=now)
            self.assertEqual(notify.call_count, 2)
            self.assertEqual({call.args[1] for call in notify.call_args_list}, {self.teacher, None})
            process_course_announcements(self.course, now=now)
            self.assertEqual(notify.call_count, 2)
        for announcement in due:
            announcement.refresh_from_db()
            self.assertEqual(announcement.status, 'published')
        future.refresh_from_db()
        expired.refresh_from_db()
        self.assertEqual(future.status, 'scheduled')
        self.assertEqual(expired.status, 'expired')

    def test_new_course_detail_opens_and_publishes_authorless_announcement(self):
        self.client.force_login(self.teacher)
        url = reverse('lms:course_detail', args=[self.course.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lms/course-detail-tech.css')
        self.assertContains(response, 'course-detail-tech')
        self.assertContains(response, 'id="course-detail-heading"')
        self.assertContains(response, reverse('lms:course_edit', args=[self.course.pk]))
        announcement = Announcement.objects.create(
            course=self.course, author=None, title='Automatic welcome',
            content='Content', status='scheduled',
            publish_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertEqual(self.client.get(url).status_code, 200)
        announcement.refresh_from_db()
        self.assertEqual(announcement.status, 'published')

    def test_student_design_does_not_expose_editor_actions(self):
        student = get_user_model().objects.create_user('detail-design-student')
        CourseMembership.objects.create(course=self.course, user=student, role='student')
        self.client.force_login(student)
        response = self.client.get(reverse('lms:course_detail', args=[self.course.pk]))
        self.assertContains(response, 'course-detail-tech')
        self.assertContains(response, 'course-mini-calendar')
        self.assertNotContains(response, reverse('lms:course_edit', args=[self.course.pk]))
        self.assertNotContains(response, reverse('lms:course_announcement_create', args=[self.course.pk]))
