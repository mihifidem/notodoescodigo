import json
from datetime import date, timedelta

from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from channels.db import database_sync_to_async

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notificacion
from apps.users.notifications import platform_notifications
from apps.courses.models import Curso
from .consumers import CourseFeedConsumer
from .models import Announcement, AnnouncementTarget, CourseMembership, LearningActivity, Notification, NotificationEvent, Quiz, Section
from .notification_state import notification_counts
from .services import notify_announcement_published, queue_notification_event, send_people_notification
from .tests import LearningActivityStructureTests


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class UnifiedNotificationTests(TestCase):
    def setUp(self):
        LearningActivityStructureTests.setUp(self)
        self.course.status = 'published'
        self.course.save(update_fields=['status'])
        self.activity = LearningActivity.objects.create(course=self.course, title='Actividad', status='open')
        self.quiz = Quiz.objects.create(course=self.course, title='Prueba', status=Quiz.Status.PUBLISHED)
        self.client.force_login(self.student)
        self.feed = reverse('lms:lifecycle_notifications')
        self.read = reverse('lms:course_notification_read', kwargs={'course_id': self.course.pk})

    def item_url(self, name, **kwargs):
        return reverse(f'lms:{name}', kwargs={'course_id': self.course.pk, **kwargs})

    def notice(self, event_type=NotificationEvent.Type.NOTE, *, area=None, target=None, **metadata):
        target = target or self.item_url('course_area', area=area or 'people')
        return queue_notification_event(
            course=self.course, recipients=[self.student], event_type=event_type,
            title=f'Aviso {NotificationEvent.objects.count()}', message='Mensaje',
            metadata={**metadata, 'url': target},
        ).notifications.get(recipient=self.student)

    def test_single_platform_entry_per_lms_notification_and_repeated_event(self):
        notice = self.notice()
        platform = Notificacion.objects.get(lms_notification=notice)
        queue_notification_event(
            course=self.course, recipients=[self.student], event_type=notice.event.event_type,
            title=notice.title, message=notice.message, metadata=notice.payload,
            dedupe_key=notice.event.dedupe_key,
        )
        self.assertEqual(Notificacion.objects.filter(lms_notification=notice).count(), 1)
        self.assertEqual(platform.usuario_id, self.student.pk)
        self.assertContains(self.client.get(reverse('users:notificaciones')), notice.title)

    def test_initial_feed_has_no_toasts_then_includes_all_sources_and_counts(self):
        cursor = self.client.get(self.feed).json()['cursor']
        self.notice(target=self.item_url('course_activity_detail', activity_id=self.activity.pk))
        self.notice(target=self.item_url('quiz_detail', quiz_id=self.quiz.pk))
        self.notice(NotificationEvent.Type.GRADE, target=self.item_url('quiz_detail', quiz_id=self.quiz.pk))
        self.notice(NotificationEvent.Type.FEEDBACK, area='grades', kind='quiz_feedback', quiz_id=self.quiz.pk)
        self.notice(source='people')
        result = self.client.get(self.feed, {'after': cursor, 'course_id': self.course.pk}).json()
        self.assertEqual(len(result['notifications']), 5)
        self.assertEqual(result['count'], 5)
        self.assertEqual(result['course_counts']['activities'], 1)
        self.assertEqual(result['course_counts']['quizzes'], 1)
        self.assertEqual(result['course_counts']['grades'], 2)
        self.assertEqual(result['course_counts']['people'], 1)
        self.assertEqual(self.client.get(self.feed, {'after': result['cursor']}).json()['notifications'], [])

    def test_reading_an_item_syncs_center_global_count_and_only_its_area(self):
        activity_url = self.item_url('course_activity_detail', activity_id=self.activity.pk)
        first = self.notice(target=activity_url)
        other_activity = LearningActivity.objects.create(course=self.course, title='Otra', status='open')
        other = self.notice(target=self.item_url('course_activity_detail', activity_id=other_activity.pk))
        self.notice(target=self.item_url('quiz_detail', quiz_id=self.quiz.pk))
        self.assertEqual(self.client.get(activity_url).status_code, 200)
        first.refresh_from_db()
        self.assertEqual(first.status, Notification.Status.UNREAD)
        result = self.client.post(self.read, {'target': activity_url}).json()
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['course_counts']['activities'], 1)
        first.refresh_from_db()
        self.assertEqual(first.status, Notification.Status.READ)
        self.assertTrue(first.platform_notification.leida)
        other.refresh_from_db()
        self.assertEqual(other.status, Notification.Status.UNREAD)

    def test_page_notifications_count_in_pages_area(self):
        self.notice(target=self.item_url('course_page_detail', page_slug='introduccion'))
        counts = notification_counts(self.course, self.student)
        self.assertEqual(counts['pages'], 1)
        self.assertEqual(counts['home'], 0)

    def test_teacher_badge_counts_students_not_announcements_and_tracks_reading(self):
        peer = get_user_model().objects.create_user('announcement_peer')
        CourseMembership.objects.create(course=self.course, user=peer, role='student', status='active')
        announcements = [
            Announcement.objects.create(course=self.course, author=self.teacher, title=f'Anuncio {number}', status='published')
            for number in range(2)
        ]
        for announcement in announcements:
            notify_announcement_published(announcement, self.teacher)
        self.client.force_login(self.teacher)
        data = self.client.get(self.feed, {'course_id': self.course.pk}).json()
        self.assertEqual(data['course_counts']['announcements'], 2)
        self.assertEqual(data['announcement_reading']['pending_students'], 2)
        self.assertEqual(data['announcement_reading']['announcements'][str(announcements[0].pk)]['pending'], 2)
        self.assertContains(self.client.get(self.item_url('course_area', area='announcements')), 'data-announcement-reading-count')
        self.client.force_login(self.student)
        for announcement in announcements:
            self.client.post(self.item_url('course_announcement_read', announcement_id=announcement.pk))
        self.assertEqual(notification_counts(self.course, self.teacher)['announcements'], 1)
        CourseMembership.objects.filter(user=peer, course=self.course).update(status='suspended')
        self.assertEqual(notification_counts(self.course, self.teacher)['announcements'], 0)
        private = self.client.get(self.feed, {'course_id': self.course.pk}).json()['announcement_reading']
        self.assertEqual(private['announcements'], {})

    def test_teacher_reading_excludes_expired_announcements(self):
        announcement = Announcement.objects.create(course=self.course, author=self.teacher, title='Caducado', status='published')
        notify_announcement_published(announcement, self.teacher)
        self.assertEqual(notification_counts(self.course, self.teacher)['announcements'], 1)
        announcement.expire_at = timezone.now() - timedelta(seconds=1)
        announcement.save()
        self.assertEqual(notification_counts(self.course, self.teacher)['announcements'], 0)

    def test_teacher_reading_respects_section_targets_and_student_preview(self):
        peer = get_user_model().objects.create_user('target_peer')
        CourseMembership.objects.create(course=self.course, user=peer, role='student', status='active')
        section = Section.objects.create(course=self.course, title='Grupo A')
        CourseMembership.objects.filter(course=self.course, user=self.student).update(section=section)
        announcement = Announcement.objects.create(course=self.course, author=self.teacher, title='Para grupo A', status='published')
        AnnouncementTarget.objects.create(announcement=announcement, target_type='section', section=section)
        notify_announcement_published(announcement, self.teacher)
        self.client.force_login(self.teacher)
        data = self.client.get(self.feed, {'course_id': self.course.pk}).json()
        self.assertEqual(data['announcement_reading']['announcements'][str(announcement.pk)]['total'], 1)
        preview = self.client.get(self.feed, {'course_id': self.course.pk, 'preview': '1'}).json()
        self.assertIsNone(preview['announcement_reading'])

    def test_center_design_filters_search_stats_and_explicit_reading(self):
        first = self.notice(target=self.item_url('course_activity_detail', activity_id=self.activity.pk))
        read = Notificacion.objects.create(usuario=self.student, titulo='Aviso general leído', mensaje='Histórico', tipo='sistema', leida=True)
        other = Notificacion.objects.create(usuario=self.teacher, titulo='Aviso secreto', mensaje='No visible', tipo='sistema')
        center = reverse('users:notificaciones')
        response = self.client.get(center)
        self.assertContains(response, 'notification-hero')
        self.assertContains(response, 'Buscar por título o mensaje')
        self.assertContains(response, 'Actividades')
        self.assertNotContains(response, other.titulo)
        self.assertEqual(response.context['notification_stats'], {'total': 2, 'pending': 1, 'read': 1})
        pending = self.client.get(center, {'estado': 'pendientes', 'curso': self.course.pk, 'buscar': 'Aviso'})
        self.assertEqual(list(pending.context['notificaciones']), [first.platform_notification])
        self.assertEqual(list(self.client.get(center, {'estado': 'leidas'}).context['notificaciones']), [read])
        self.assertContains(self.client.get(center, {'buscar': 'no coincide'}), 'No hay avisos que coincidan')
        for parameters in ({'estado': 'inválido'}, {'curso': 'no-numérico'}, {'buscar': 'x' * 201}):
            self.assertEqual(self.client.get(center, parameters).status_code, 400)
        endpoint = reverse('users:marcar_leida', kwargs={'pk': first.platform_notification.pk})
        for _ in range(2):
            result = self.client.post(endpoint, {'read': '1', 'estado': 'pendientes'})
            self.assertEqual(result.url, center + '?estado=pendientes')
        first.refresh_from_db()
        self.assertEqual(first.status, Notification.Status.READ)
        self.assertEqual(self.client.post(endpoint, {'read': 'invalid'}).status_code, 400)

    def test_center_pagination_preserves_filters(self):
        Notificacion.objects.bulk_create([
            Notificacion(usuario=self.student, titulo=f'Pendiente {number}', mensaje='Mensaje', tipo='sistema')
            for number in range(21)
        ])
        response = self.client.get(reverse('users:notificaciones'), {'estado': 'pendientes', 'buscar': 'Pendiente'})
        self.assertContains(response, 'page=2&amp;estado=pendientes&amp;buscar=Pendiente')
        self.assertEqual(len(response.context['notificaciones']), 20)

    def test_center_read_and_unread_and_mark_all_sync_course_counts(self):
        notice = self.notice(target=self.item_url('quiz_detail', quiz_id=self.quiz.pk))
        platform = notice.platform_notification
        read = reverse('users:marcar_leida', kwargs={'pk': platform.pk})
        self.client.post(read)
        notice.refresh_from_db()
        self.assertEqual(notice.status, Notification.Status.READ)
        self.assertEqual(notification_counts(self.course, self.student)['quizzes'], 0)
        self.client.post(read)
        notice.refresh_from_db()
        self.assertEqual(notice.status, Notification.Status.UNREAD)
        self.client.post(reverse('users:marcar_todas_leidas'))
        self.assertEqual(notification_counts(self.course, self.student)['quizzes'], 0)

    def test_announcement_read_syncs_platform_and_hidden_announcements_are_excluded(self):
        announcement = Announcement.objects.create(
            course=self.course, author=self.teacher, title='Anuncio', content='Texto',
            status=Announcement.Status.PUBLISHED, publish_at=timezone.now(),
        )
        notify_announcement_published(announcement, self.teacher)
        self.assertEqual(notification_counts(self.course, self.student)['announcements'], 1)
        self.client.post(self.item_url('course_announcement_read', announcement_id=announcement.pk))
        notice = Notification.objects.get(recipient=self.student, event__metadata__announcement_id=announcement.pk)
        self.assertTrue(notice.platform_notification.leida)
        announcement.expire_at = timezone.now() - timedelta(seconds=1)
        announcement.save()
        self.assertFalse(platform_notifications(self.student).filter(lms_notification=notice).exists())

    def test_feedback_group_only_reads_grades_and_people_only_reads_opened_event(self):
        quiz_url = self.item_url('quiz_detail', quiz_id=self.quiz.pk)
        status = self.notice(target=quiz_url)
        grade = self.notice(NotificationEvent.Type.GRADE, target=quiz_url)
        feedback = self.notice(NotificationEvent.Type.FEEDBACK, area='grades', kind='quiz_feedback', quiz_id=self.quiz.pk)
        self.client.post(self.read, {'target': quiz_url, 'area': 'grades'})
        for notice in (grade, feedback):
            notice.refresh_from_db()
            self.assertEqual(notice.status, Notification.Status.READ)
        status.refresh_from_db()
        self.assertEqual(status.status, Notification.Status.UNREAD)
        event = send_people_notification(
            course=self.course, actor=self.teacher, recipient_ids=[self.student.pk],
            title='Privado', message='Mensaje',
        )
        other = self.notice(source='people')
        self.client.post(self.read, {'event_id': event.pk})
        self.assertEqual(event.notifications.get(recipient=self.student).status, Notification.Status.READ)
        other.refresh_from_db()
        self.assertEqual(other.status, Notification.Status.UNREAD)

    def test_permissions_csrf_private_feed_and_invalid_target(self):
        notice = self.notice(target=self.item_url('quiz_detail', quiz_id=self.quiz.pk))
        outsider = get_user_model().objects.create_user('outside_notifications')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.feed, {'after': 0}).json()['notifications'], [])
        self.assertEqual(self.client.get(self.feed, {'course_id': self.course.pk}).status_code, 403)
        self.assertEqual(self.client.post(self.read, {'event_id': notice.event_id}).status_code, 403)
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(self.read, {'target': self.item_url('course_area', area='activities')}).status_code, 400)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.student)
        self.assertEqual(csrf.post(self.read, {'target': self.item_url('quiz_detail', quiz_id=self.quiz.pk)}).status_code, 403)
        self.quiz.status = Quiz.Status.DRAFT
        self.quiz.save()
        self.assertEqual(self.client.get(self.feed, {'after': 0}).json()['notifications'], [])
        self.assertEqual(notification_counts(self.course, self.student)['quizzes'], 0)

    def test_navbar_exists_exactly_once_on_details_forms_and_course_sections(self):
        for url in (
            self.item_url('course_activity_detail', activity_id=self.activity.pk),
            self.item_url('quiz_detail', quiz_id=self.quiz.pk),
            self.item_url('course_area', area='announcements'),
        ):
            response = self.client.get(url)
            self.assertContains(response, 'class="course-nav-panel"', count=1)
            self.assertContains(response, 'data-notification-area-count="activities"')
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(self.item_url('course_activity_create')), 'class="course-nav-panel"', count=1)

    def test_migration_links_existing_platform_notice_and_preserves_read_status(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection

        event = NotificationEvent.objects.create(
            course=self.course, title='Anterior', message='Texto', event_type='note', dedupe_key='legacy',
        )
        notice = Notification.objects.create(
            event=event, course=self.course, recipient=self.student, title=event.title, message=event.message,
        )
        platform = Notificacion.objects.create(
            usuario=self.student, tipo='sistema', titulo=event.title, mensaje=event.message, leida=True,
        )
        migration = import_module('apps.users.migrations.0010_notificacion_lms_notification')
        migration.link_notifications(apps, SimpleNamespace(connection=connection))
        platform.refresh_from_db()
        notice.refresh_from_db()
        self.assertEqual(platform.lms_notification_id, notice.pk)
        self.assertEqual(notice.status, Notification.Status.READ)
        self.assertEqual(Notificacion.objects.filter(usuario=self.student).count(), 1)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class NotificationSocketTests(TransactionTestCase):
    def test_publication_pushes_student_badge_and_teacher_reading_updates_without_feed_request(self):
        User = get_user_model()
        teacher = User.objects.create_user('live_teacher')
        student = User.objects.create_user('live_student')
        course = Curso.objects.create(nombre='Curso en vivo', slug='curso-en-vivo', fecha_inicio=date(2025, 1, 1), status='published')
        for user, role in ((teacher, 'teacher'), (student, 'student')):
            CourseMembership.objects.create(course=course, user=user, role=role, status='active')
        teacher_client = Client()
        teacher_client.force_login(teacher)
        student_client = Client()
        student_client.force_login(student)

        async def run():
            connections = []
            try:
                for user in (student, teacher):
                    socket = ApplicationCommunicator(CourseFeedConsumer.as_asgi(), {
                        'type': 'websocket', 'user': user, 'url_route': {'kwargs': {'course_id': str(course.pk)}},
                    })
                    connections.append(socket)
                    await socket.send_input({'type': 'websocket.connect'})
                    self.assertEqual((await socket.receive_output())['type'], 'websocket.accept')
                    state = json.loads((await socket.receive_output())['text'])['notification_state']
                    self.assertEqual(state['course_counts']['announcements'], 0)
                response = await database_sync_to_async(teacher_client.post)(
                    reverse('lms:course_announcement_create', kwargs={'course_id': course.pk}),
                    {'title': 'Nuevo anuncio en vivo', 'content': 'Contenido', 'status': 'published'},
                )
                self.assertEqual(response.status_code, 302)
                for socket in connections:
                    state = json.loads((await socket.receive_output())['text'])['notification_state']
                    self.assertEqual(state['course_counts']['announcements'], 1)
                    self.assertEqual(state['notifications'][0]['title'], 'Nuevo anuncio: Nuevo anuncio en vivo')
                    # The view also broadcasts the content update after publication.
                    await socket.receive_output()
                announcement_id = await database_sync_to_async(
                    lambda: Announcement.objects.get(course=course).pk,
                )()
                response = await database_sync_to_async(student_client.post)(
                    reverse('lms:course_announcement_read', kwargs={'course_id': course.pk, 'announcement_id': announcement_id}),
                )
                self.assertEqual(response.status_code, 200)
                for socket in connections:
                    state = json.loads((await socket.receive_output())['text'])['notification_state']
                    self.assertEqual(state['course_counts']['announcements'], 0)
            finally:
                for socket in connections:
                    await socket.send_input({'type': 'websocket.disconnect', 'code': 1000})
                    await socket.wait()

        async_to_sync(run)()
