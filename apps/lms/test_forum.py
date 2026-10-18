from datetime import date

from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.courses.models import Curso, InscripcionCurso
from .consumers import CourseFeedConsumer
from .models import CourseMembership, DiscussionPost, DiscussionTopic, Notification, Section


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ForumConversationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.teacher = User.objects.create_user('forum_teacher')
        self.student = User.objects.create_user('usuario')
        self.peer = User.objects.create_user('forum_peer')
        self.outsider = User.objects.create_user('forum_outsider')
        self.course = Curso.objects.create(nombre='Curso foro', slug='curso-foro', activo=True, fecha_inicio=date(2025, 1, 1))
        self.other = Curso.objects.create(nombre='Otro curso', slug='otro-foro', activo=True, fecha_inicio=date(2025, 1, 1))
        for user, role in [(self.teacher, 'teacher'), (self.student, 'student'), (self.peer, 'student')]:
            CourseMembership.objects.create(course=self.course, user=user, role=role, status='active')
        CourseMembership.objects.create(course=self.other, user=self.outsider, role='student', status='active')
        self.forum = DiscussionTopic.objects.create(course=self.course, author=self.teacher, title='Consultas')
        self.client.force_login(self.student)

    def url(self, route='course_forum_detail', post=None, forum=None):
        forum = forum or self.forum
        kwargs = {'course_id': forum.course_id, 'forum_id': forum.pk}
        if post:
            kwargs['post_id'] = post.pk
        return reverse('lms:' + route, kwargs=kwargs)

    def query(self, author=None, **extra):
        return DiscussionPost.objects.create(topic=self.forum, author=author or self.student, title=extra.pop('title', 'Mi consulta'), content='Mensaje inicial', **extra)

    def count(self, user=None):
        if user:
            self.client.force_login(user)
        return self.client.get(reverse('lms:course_forum_notifications', kwargs={'course_id': self.course.pk})).json()['count']

    def test_usuario_sees_entry_new_thread_and_posts_query(self):
        area = self.client.get(reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'discussion'}))
        self.assertContains(area, 'Entrar al foro')
        self.assertContains(area, 'Abrir hilo / Nueva consulta')
        response = self.client.get(self.url())
        self.assertContains(response, '#forum-new-query')
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url(), {
                'title': 'Bucles Python', 'content': '<p>Consulta</p><script>alert(1)</script>',
                'author': self.teacher.pk, 'topic': 999,
            })
        query = self.forum.posts.get()
        self.assertEqual(query.author, self.student)
        self.assertEqual(query.topic, self.forum)
        self.assertNotIn('<script', query.content)
        self.assertRedirects(response, self.url('course_forum_query', query))
        self.assertEqual(self.count(self.peer), 1)
        self.assertEqual(self.count(self.student), 0)
        self.assertFalse(Notification.objects.filter(recipient=self.outsider).exists())

    def test_cancel_links_do_not_publish_or_edit(self):
        from bs4 import BeautifulSoup

        area_url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'discussion'})
        response = self.client.get(self.url())
        cancel = BeautifulSoup(response.content, 'html.parser').select_one('form.stacked-form .action-row a')
        self.assertEqual(cancel.text, 'Cancelar')
        self.assertEqual(cancel['href'], area_url)
        self.client.get(cancel['href'])
        self.assertFalse(self.forum.posts.exists())
        query = self.query()
        response = self.client.get(self.url('course_forum_query', query))
        cancel = BeautifulSoup(response.content, 'html.parser').select_one('form.stacked-form .action-row a')
        self.assertEqual(cancel['href'], self.url())
        response = self.client.get(self.url('course_forum_post_edit', query))
        links = BeautifulSoup(response.content, 'html.parser').select('form.stacked-form .action-row a')
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['href'], self.url('course_forum_query', query))
        self.client.get(links[0]['href'])
        query.refresh_from_db()
        self.assertEqual(query.content, 'Mensaje inicial')

    def test_teacher_can_edit_forum_without_changing_course_author_or_posts(self):
        query = self.query()
        url = self.url('course_forum_edit')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'title': 'No'}).status_code, 403)
        self.assertNotContains(self.client.get(self.url()), url)
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(self.url()), url)
        response = self.client.get(url)
        self.assertContains(response, 'Editar foro')
        self.assertEqual(response.context['form'].instance.pk, self.forum.pk)
        invalid = self.client.post(url, {'title': '', 'content': 'Texto'})
        self.assertEqual(invalid.status_code, 200)
        self.assertTrue(invalid.context['form'].errors)
        response = self.client.post(url, {
            'title': 'Foro actualizado', 'content': '<p>Nuevo resumen</p>',
            'course': self.other.pk, 'author': self.student.pk, 'status': 'closed',
        })
        self.assertRedirects(response, self.url())
        self.forum.refresh_from_db()
        self.assertEqual(self.forum.title, 'Foro actualizado')
        self.assertEqual(self.forum.course, self.course)
        self.assertEqual(self.forum.author, self.teacher)
        self.assertEqual(self.forum.status, 'open')
        self.assertTrue(self.forum.posts.filter(pk=query.pk).exists())
        foreign = DiscussionTopic.objects.create(course=self.other, title='Foro externo')
        self.assertEqual(self.client.get(self.url('course_forum_edit', forum=foreign)).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.teacher)
        self.assertEqual(csrf.post(url, {'title': 'Sin CSRF'}).status_code, 403)

    def test_thread_reply_counts_include_nested_visible_replies_and_refresh(self):
        query = self.query()
        second = self.query(title='Otro hilo')
        response = self.client.get(self.url())
        self.assertContains(response, '0 respuestas', count=2)
        self.assertContains(response, 'forum-workspace')
        self.assertContains(response, 'forum-hero')
        reply = DiscussionPost.objects.create(
            topic=self.forum, author=self.peer, parent=query, content='Respuesta',
        )
        response = self.client.get(self.url())
        self.assertContains(response, '1 respuesta')
        DiscussionPost.objects.create(topic=self.forum, author=self.student, parent=reply, content='Anidada')
        DiscussionPost.objects.create(topic=self.forum, author=self.peer, parent=query, content='Oculta', is_hidden=True)
        DiscussionPost.objects.create(topic=self.forum, author=self.peer, parent=query, content='Borrada', is_deleted=True)
        DiscussionPost.objects.create(topic=self.forum, author=self.peer, parent=second, content='Otro hilo')
        response = self.client.get(self.url())
        counts = {post.pk: post.reply_count for post in response.context['queries']}
        self.assertEqual(counts, {query.pk: 2, second.pk: 1})
        fragment = self.client.get(self.url(), {'fragment': '1'}).json()['html']
        self.assertIn('data-reply-count="2"', fragment)
        invalid = self.client.post(self.url(), {'title': '', 'content': ''})
        self.assertContains(invalid, 'data-reply-count="2"')
        conversation = self.url('course_forum_query', query)
        response = self.client.get(conversation)
        self.assertEqual(response.context['reply_count'], 2)
        self.assertContains(response, '2 respuestas')
        reply.is_deleted = True
        reply.save()
        self.assertIn('data-reply-count="1"', self.client.get(conversation, {'fragment': '1'}).json()['html'])
        self.forum.require_reply_before_view = True
        self.forum.save()
        self.client.force_login(self.peer)
        response = self.client.get(conversation)
        self.assertFalse(response.context['can_view_replies'])
        self.assertNotContains(response, 'data-reply-count=')

    def test_people_lists_course_members_and_legacy_for_teacher_only(self):
        from .selectors import course_participants

        User = get_user_model()
        legacy = User.objects.create_user('persona_legacy', email='legacy@example.test')
        dropped = User.objects.create_user('persona_baja')
        invited = User.objects.create_user('persona_invitada')
        for user in (legacy, dropped, self.student):
            InscripcionCurso.objects.create(usuario=user, curso=self.course)
        CourseMembership.objects.create(user=dropped, course=self.course, role='student', status='dropped')
        CourseMembership.objects.create(user=invited, course=self.course, role='student', status='invited')
        self.peer.email = 'peer-private@example.test'
        self.peer.save()
        section = Section.objects.create(course=self.course, title='Grupo personas')
        CourseMembership.objects.filter(user=self.peer, course=self.course).update(section=section, status='completed')
        participants = course_participants(self.course)
        ids = [row['user'].pk for row in participants]
        self.assertCountEqual(ids, [self.teacher.pk, self.student.pk, self.peer.pk, legacy.pk])
        self.assertEqual(ids.count(self.student.pk), 1)
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'people'})
        self.client.force_login(self.teacher)
        response = self.client.get(url)
        self.assertContains(response, 'persona_legacy')
        self.assertContains(response, 'peer-private@example.test')
        self.assertContains(response, 'Grupo personas')
        self.assertNotContains(response, 'Editar aula')
        self.assertNotContains(response, 'Ver páginas')
        self.assertNotContains(response, 'persona_baja')
        self.assertNotContains(response, 'persona_invitada')
        self.assertNotContains(response, self.outsider.username)
        self.client.force_login(self.student)
        response = self.client.get(url)
        self.assertContains(response, 'persona_legacy')
        self.assertContains(response, 'forum_peer')
        self.assertTrue(all(set(person) == {'name', 'role'} for person in response.context['course_participants']))
        self.assertNotContains(response, 'peer-private@example.test')
        self.assertNotContains(response, 'legacy@example.test')
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_people_directory_filters_groups_and_scoped_activity(self):
        from bs4 import BeautifulSoup
        from .models import LearningEvent

        section = Section.objects.create(course=self.course, title='Equipo de prácticas')
        CourseMembership.objects.filter(course=self.course, user=self.student).update(section=section)
        event = LearningEvent.objects.create(
            course=self.course, user=self.student, context={'duration_seconds': 125},
        )
        LearningEvent.objects.create(course=self.course, user=self.student, context={'duration_seconds': 60})
        LearningEvent.objects.create(course=self.other, user=self.student, context={'duration_seconds': 9999})
        self.peer.email = 'correo-privado@example.test'
        self.peer.save(update_fields=['email'])
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'people'})
        self.client.force_login(self.teacher)
        response = self.client.get(url, {'q': self.student.username, 'role': 'student'})
        self.assertEqual([person['user'].pk for person in response.context['course_participants']], [self.student.pk])
        self.assertContains(response, '00:03:05')
        self.assertContains(response, 'course-context-grid-full')
        self.assertNotContains(response, 'class="upcoming-panel"')
        self.assertIsNotNone(response.context['course_participants'][0]['last_activity'])
        self.assertGreaterEqual(response.context['course_participants'][0]['last_activity'], event.created_at)
        checkbox = BeautifulSoup(response.content, 'html.parser').select_one('[data-people-select-all]')
        self.assertFalse(checkbox.has_attr('name'))
        response = self.client.post(url + '?q=' + self.student.username, {
            'recipients': [self.student.pk], 'title': 'Aviso filtrado', 'message': 'Solo al alumno mostrado',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(Notification.objects.filter(
            course=self.course, title='Aviso filtrado',
        ).values_list('recipient_id', flat=True)), [self.student.pk])
        self.assertContains(self.client.get(url, {'tab': 'groups'}), section.title)
        self.assertEqual(len(self.client.get(url, {'q': 'no-coincide'}).context['course_participants']), 0)
        self.client.force_login(self.student)
        response = self.client.get(url, {'role': 'teacher'})
        self.assertEqual(response.context['course_participants'], [{'name': self.teacher.username, 'role': 'Profesor'}])
        self.assertNotContains(response, self.peer.email)
        self.assertNotContains(response, 'Actividad total')
        self.assertNotContains(self.client.get(url, {'tab': 'groups'}), section.title)

    def test_query_validation_and_reply_chronology(self):
        response = self.client.post(self.url(), {'title': '', 'content': ' '})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        query = self.query()
        self.client.force_login(self.peer)
        response = self.client.post(self.url('course_forum_query', query), {'content': 'Primera respuesta'})
        self.assertEqual(response.status_code, 302)
        reply = self.forum.posts.exclude(pk=query.pk).get()
        response = self.client.post(self.url('course_forum_query', query), {
            'parent_id': reply.pk, 'content': 'Respuesta anidada',
        })
        self.assertEqual(response.status_code, 302)
        page = self.client.get(self.url('course_forum_query', query))
        self.assertContains(page, 'En respuesta a')
        self.assertLess(page.content.index(b'Primera respuesta'), page.content.index(b'Respuesta anidada'))
        self.assertEqual(self.count(self.peer), 0)
        self.assertEqual(self.count(self.student), 2)

    def test_author_edit_delete_and_teacher_moderation(self):
        query = self.query()
        reply = DiscussionPost.objects.create(topic=self.forum, author=self.peer, parent=query, content='Mi respuesta')
        self.assertEqual(self.client.post(self.url('course_forum_post_edit', reply), {'content': 'Robo'}).status_code, 403)
        response = self.client.post(self.url('course_forum_post_edit', query), {'title': 'Titulo editado', 'content': 'Nuevo texto'})
        self.assertEqual(response.status_code, 302)
        query.refresh_from_db()
        self.assertTrue(query.is_edited)
        self.assertEqual(query.title, 'Titulo editado')
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(self.url('course_forum_post_delete', reply)).status_code, 302)
        reply.refresh_from_db()
        self.assertTrue(reply.is_deleted)
        self.assertNotContains(self.client.get(self.url('course_forum_query', query)), 'Mi respuesta')
        self.client.force_login(self.student)
        self.client.post(self.url('course_forum_post_delete', query))
        self.assertEqual(self.client.get(self.url('course_forum_query', query)).status_code, 404)
        self.assertNotContains(self.client.get(self.url()), 'Titulo editado')

    def test_only_owner_and_teacher_can_accept_solution_close_and_reopen(self):
        query = self.query()
        reply = DiscussionPost.objects.create(topic=self.forum, author=self.peer, parent=query, content='Solucion')
        url = self.url('course_forum_solution', query)
        self.client.force_login(self.peer)
        self.assertEqual(self.client.post(url, {'solution_id': reply.pk}).status_code, 403)
        self.assertNotContains(self.client.get(self.url('course_forum_query', query)), 'Marcar como solución')
        self.client.force_login(self.student)
        self.assertContains(self.client.get(self.url('course_forum_query', query)), 'Marcar como solución y cerrar hilo')
        self.assertEqual(self.client.post(url, {'solution_id': reply.pk}).status_code, 302)
        self.assertEqual(self.client.post(self.url('course_forum_query', query), {'content': 'Hilo cerrado'}).status_code, 403)
        self.assertContains(self.client.get(self.url('course_forum_query', query)), 'data-query-reply-form hidden disabled')
        self.assertEqual(self.client.post(url, {'solution_id': ''}).status_code, 302)
        self.assertContains(self.client.get(self.url('course_forum_query', query)), 'Publicar respuesta')
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(url, {'solution_id': reply.pk}).status_code, 302)
        self.assertEqual(self.client.post(url, {'solution_id': reply.pk}).status_code, 302)
        self.assertEqual(Notification.objects.filter(recipient=self.student, payload__forum_action='solution').count(), 1)
        self.assertContains(self.client.get(self.url('course_forum_query', query)), 'Solución aceptada')
        other_query = DiscussionPost.objects.create(topic=self.forum, author=self.peer, title='Otra consulta', content='Otra')
        other_reply = DiscussionPost.objects.create(topic=self.forum, author=self.peer, parent=other_query, content='Ajena')
        self.assertEqual(self.client.post(url, {'solution_id': other_reply.pk}).status_code, 400)
        self.assertEqual(self.client.post(url, {'solution_id': query.pk}).status_code, 400)
        self.client.force_login(self.peer)
        self.client.post(self.url('course_forum_post_edit', reply), {'content': 'Solucion corregida'})
        query.refresh_from_db()
        self.assertIsNone(query.accepted_solution_id)

    def test_people_notifications_use_global_system_and_private_lms_history(self):
        from apps.users.models import Notificacion
        from .models import NotificationEvent

        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'people'})
        self.assertNotContains(self.client.get(url), 'name="send_all"')
        self.assertEqual(self.client.post(url, {'title': 'No autorizado', 'message': 'No'}).status_code, 403)
        self.client.force_login(self.teacher)
        response = self.client.post(url, {
            'title': 'Mensaje personal', 'message': 'Solo para un alumno',
            'recipients': [self.student.pk],
        })
        self.assertRedirects(response, url)
        self.assertEqual(Notificacion.objects.filter(usuario=self.student, titulo='Mensaje personal').count(), 1)
        self.assertFalse(Notificacion.objects.filter(usuario=self.peer, titulo='Mensaje personal').exists())
        event = NotificationEvent.objects.get(metadata__source='people')
        self.assertEqual(event.course, self.course)
        self.assertEqual(event.actor, self.teacher)
        self.assertCountEqual(event.notifications.values_list('recipient_id', flat=True), [self.student.pk])
        self.assertContains(self.client.get(url), 'Mensaje personal')
        self.client.force_login(self.student)
        self.assertContains(self.client.get(url), 'Mensaje personal')
        self.client.force_login(self.peer)
        self.assertNotContains(self.client.get(url), 'Mensaje personal')
        Notificacion.objects.create(usuario=self.peer, tipo='sistema', titulo='Aviso global ajeno', mensaje='Global')
        self.assertNotContains(self.client.get(url), 'Aviso global ajeno')
        self.assertEqual(NotificationEvent.objects.filter(metadata__source='people').count(), 1)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_people_notification_all_students_scope_validation_and_csrf(self):
        from apps.users.models import Notificacion

        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'people'})
        self.client.force_login(self.teacher)
        for data in (
            {'title': 'Sin destinatario', 'message': 'Texto'},
            {'title': 'Fuera del curso', 'message': 'Texto', 'recipients': [self.outsider.pk]},
            {'title': '', 'message': 'Texto', 'send_all': 'on'},
            {'title': 'Profesor', 'message': 'Texto', 'recipients': [self.teacher.pk]},
        ):
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['people_notification_form'].errors)
        self.assertFalse(Notificacion.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.assertRedirects(self.client.post(url, {
            'title': 'Para todos', 'message': 'Mensaje para el curso', 'send_all': 'on',
        }), url)
        self.assertCountEqual(
            Notificacion.objects.filter(titulo='Para todos').values_list('usuario_id', flat=True),
            [self.student.pk, self.peer.pk],
        )
        self.assertCountEqual(
            Notification.objects.filter(payload__source='people').values_list('recipient_id', flat=True),
            [self.student.pk, self.peer.pk],
        )
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.teacher)
        self.assertEqual(csrf.post(url, {'title': 'CSRF', 'message': 'No', 'send_all': 'on'}).status_code, 403)
        CourseMembership.objects.filter(user=self.teacher, course=self.course).update(role='assistant')
        self.assertEqual(self.client.post(url, {'title': 'No', 'message': 'No', 'send_all': 'on'}).status_code, 403)
        self.assertNotContains(self.client.get(url), 'name="send_all"')

    def test_people_recipient_checkboxes_are_in_rows_and_preserved_on_error(self):
        from bs4 import BeautifulSoup

        self.client.force_login(self.teacher)
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'people'})
        response = self.client.get(url)
        soup = BeautifulSoup(response.content, 'html.parser')
        master = soup.select_one('thead [data-people-select-all]')
        self.assertEqual(master['form'], 'people-notification-form')
        checkboxes = soup.select('tbody [data-people-recipient]')
        self.assertCountEqual([node['value'] for node in checkboxes], [str(self.student.pk), str(self.peer.pk)])
        self.assertTrue(all(node['form'] == 'people-notification-form' for node in checkboxes))
        self.assertFalse(soup.select('#people-notification-form [name="recipients"]'))
        response = self.client.post(url, {
            'title': '', 'message': 'Texto', 'recipients': [self.student.pk],
        })
        soup = BeautifulSoup(response.content, 'html.parser')
        selected = soup.select('[data-people-recipient][checked]')
        self.assertEqual([node['value'] for node in selected], [str(self.student.pk)])
        response = self.client.post(url, {'title': '', 'message': 'Texto', 'send_all': 'on'})
        self.assertEqual(len(BeautifulSoup(response.content, 'html.parser').select('[data-people-recipient][checked]')), 2)
        self.client.force_login(self.student)
        soup = BeautifulSoup(self.client.get(url).content, 'html.parser')
        self.assertFalse(soup.select('[data-people-recipient], [data-people-select-all]'))

    def test_people_notification_global_failure_rolls_back_lms_copy(self):
        from unittest.mock import patch
        from .models import NotificationEvent
        from .services import send_people_notification

        with patch('apps.users.models.Notificacion.objects.get_or_create', side_effect=RuntimeError('Delivery failed')):
            with self.assertRaises(RuntimeError):
                send_people_notification(
                    course=self.course, actor=self.teacher, recipient_ids=[self.student.pk],
                    title='No guardar', message='Error',
                )
        self.assertFalse(NotificationEvent.objects.filter(metadata__source='people').exists())
        self.assertFalse(Notification.objects.filter(payload__source='people').exists())

    def test_cross_course_section_closed_suspended_and_csrf(self):
        query = self.query()
        self.client.force_login(self.outsider)
        for url in (self.url(), self.url('course_forum_query', query)):
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {'title': 'No', 'content': 'No'}).status_code, 403)
        self.client.force_login(self.student)
        self.forum.section = Section.objects.create(course=self.course, title='Seccion privada')
        self.forum.save()
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.forum.section = None
        self.forum.status = 'closed'
        self.forum.save()
        self.assertNotContains(self.client.get(self.url()), '#forum-new-query')
        self.assertEqual(self.client.post(self.url(), {'title': 'No', 'content': 'No'}).status_code, 403)
        self.forum.status = 'open'
        self.forum.save()
        CourseMembership.objects.filter(user=self.student, course=self.course).update(status='suspended')
        self.assertEqual(self.client.post(self.url(), {'title': 'No', 'content': 'No'}).status_code, 403)
        CourseMembership.objects.filter(user=self.student, course=self.course).update(status='active')
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.student)
        self.assertEqual(client.post(self.url(), {'title': 'No', 'content': 'No'}).status_code, 403)
        self.assertEqual(self.client.get(self.url('course_forum_post_delete', query)).status_code, 405)

    def test_reply_parent_scope_depth_and_empty_html(self):
        query = self.query()
        other_query = DiscussionPost.objects.create(topic=self.forum, author=self.student, title='Otra', content='Otra')
        url = self.url('course_forum_query', query)
        self.assertEqual(self.client.post(url, {'parent_id': other_query.pk, 'content': 'No'}).status_code, 403)
        self.assertEqual(self.client.post(url, {'parent_id': 'abc', 'content': 'No'}).status_code, 400)
        self.assertEqual(self.client.post(url, {'content': '<p> </p>'}).status_code, 200)
        reply = DiscussionPost.objects.create(topic=self.forum, author=self.peer, parent=query, content='Respuesta valida')
        invalid_nested = self.client.post(url, {'parent_id': reply.pk, 'content': '<p> </p>'})
        self.assertEqual(invalid_nested.context['reply_parent_id'], reply.pk)
        parent = query
        for _ in range(DiscussionPost.MAX_DEPTH):
            parent = DiscussionPost.objects.create(topic=self.forum, author=self.peer, parent=parent, content='Profunda')
        self.assertEqual(self.client.post(url, {'parent_id': parent.pk, 'content': 'Excesiva'}).status_code, 400)

    def test_live_fragments_notifications_and_explicit_read(self):
        query = self.query()
        self.client.force_login(self.peer)
        self.client.post(self.url('course_forum_query', query), {'content': 'Nueva respuesta'})
        self.client.force_login(self.student)
        fragment = self.client.get(self.url('course_forum_query', query) + '?fragment=1')
        self.assertContains(fragment, 'Nueva respuesta')
        self.assertEqual(set(fragment.json()), {'html', 'revision'})
        self.assertEqual(self.count(), 1)
        self.client.post(self.url('course_forum_query', query), {'_action': 'mark_read'})
        self.assertEqual(self.count(), 0)

    def test_reply_before_view_does_not_leak_response_in_notices(self):
        query = self.query(author=self.teacher)
        self.forum.require_reply_before_view = True
        self.forum.save()
        self.client.force_login(self.peer)
        self.client.post(self.url('course_forum_query', query), {'content': 'Respuesta secreta'})
        self.client.force_login(self.student)
        page = self.client.get(self.url('course_forum_query', query))
        self.assertNotContains(page, 'Respuesta secreta')
        self.assertContains(page, 'Publica una respuesta')
        notifications = self.client.get(reverse('lms:course_forum_notifications', kwargs={'course_id': self.course.pk}))
        self.assertNotContains(notifications, 'Respuesta secreta')
        self.client.post(self.url('course_forum_query', query), {'content': 'Mi intento'})
        self.assertContains(self.client.get(self.url('course_forum_query', query)), 'Respuesta secreta')

    def test_legacy_recipient_is_notified_but_suspended_membership_overrides_legacy(self):
        User = get_user_model()
        legacy = User.objects.create_user('forum_legacy')
        inactive = User.objects.create_user('forum_inactive')
        for user in (legacy, inactive):
            InscripcionCurso.objects.create(usuario=user, curso=self.course)
        CourseMembership.objects.create(user=inactive, course=self.course, role='student', status='suspended')
        self.client.post(self.url(), {'title': 'Aviso', 'content': 'Mensaje'})
        self.assertTrue(Notification.objects.filter(recipient=legacy).exists())
        self.assertFalse(Notification.objects.filter(recipient=inactive).exists())


class ForumSocketTests(TransactionTestCase):
    def test_course_websocket_authorizes_members_and_rechecks_revocation(self):
        User = get_user_model()
        student = User.objects.create_user('socket_student')
        outsider = User.objects.create_user('socket_outsider')
        course = Curso.objects.create(nombre='Socket curso', slug='socket-curso', fecha_inicio=date(2025, 1, 1))
        membership = CourseMembership.objects.create(user=student, course=course, role='student', status='active')

        async def rejected():
            communicator = ApplicationCommunicator(CourseFeedConsumer.as_asgi(), {
                'type': 'websocket', 'user': outsider, 'url_route': {'kwargs': {'course_id': str(course.pk)}},
            })
            await communicator.send_input({'type': 'websocket.connect'})
            event = await communicator.receive_output()
            self.assertEqual(event['type'], 'websocket.close')
            self.assertEqual(event['code'], 4403)
            await communicator.send_input({'type': 'websocket.disconnect', 'code': 1000})
            await communicator.wait()

        async_to_sync(rejected)()

        async def connected_then_revoked():
            from channels.db import database_sync_to_async
            communicator = ApplicationCommunicator(CourseFeedConsumer.as_asgi(), {
                'type': 'websocket', 'user': student, 'url_route': {'kwargs': {'course_id': str(course.pk)}},
            })
            await communicator.send_input({'type': 'websocket.connect'})
            self.assertEqual((await communicator.receive_output())['type'], 'websocket.accept')
            self.assertIn('"notification_state"', (await communicator.receive_output())['text'])
            await get_channel_layer().group_send(f'course_{course.pk}_updates', {
                'type': 'course.update', 'event': 'created', 'course_id': course.pk, 'item_type': 'forum',
            })
            event = await communicator.receive_output()
            self.assertEqual(event['type'], 'websocket.send')
            self.assertIn('"item_type": "forum"', event['text'])
            await database_sync_to_async(CourseMembership.objects.filter(pk=membership.pk).update)(status='dropped')
            await get_channel_layer().group_send(f'course_{course.pk}_updates', {
                'type': 'course.update', 'event': 'created', 'course_id': course.pk, 'item_type': 'forum',
            })
            self.assertEqual((await communicator.receive_output())['type'], 'websocket.close')
            await communicator.send_input({'type': 'websocket.disconnect', 'code': 1000})
            await communicator.wait()

        async_to_sync(connected_then_revoked)()
