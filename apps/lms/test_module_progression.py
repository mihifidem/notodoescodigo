from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import Curso, Progreso, Tema
from .calendar_dates import calendar_dates
from .forms import ModuleContentForm, ModuleScheduleForm
from .models import (
    Assignment, CourseMembership, CourseSettingsLMS, FileAsset, LearningActivity, ModuleContent,
    ModuleContentProgress, ModuleSchedule, Page, Quiz, QuizAttempt, Submission,
)
from .module_progression import content_complete, content_lock_reason, module_complete, module_lock_reason
from .quiz_attempts import attempt_deadline, require_student


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ModuleProgressionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.teacher = User.objects.create_user('module_teacher')
        self.student = User.objects.create_user('module_student')
        self.other = User.objects.create_user('module_other')
        self.course = Curso.objects.create(
            nombre='Curso secuencial', descripcion='Curso', fecha_inicio=date(2026, 10, 1),
            fecha_fin=date(2026, 12, 1), status='published', activo=True,
        )
        for user, role in [(self.teacher, CourseMembership.Role.OWNER), (self.student, CourseMembership.Role.STUDENT)]:
            CourseMembership.objects.create(course=self.course, user=user, role=role, status=CourseMembership.Status.ACTIVE)
        self.settings = CourseSettingsLMS.objects.create(course=self.course, sequential_modules=True)
        self.first = Tema.objects.create(curso=self.course, titulo='Primero', contenido='Tema', orden=1)
        self.second = Tema.objects.create(curso=self.course, titulo='Segundo', contenido='Tema', orden=2)
        self.page = Page.objects.create(
            course=self.course, module=self.first, title='Página previa', slug='previa',
            content='Lectura', status=Page.Status.PUBLISHED, author=self.teacher,
        )
        self.item = ModuleContent.objects.create(module=self.first, page=self.page)
        self.client.force_login(self.student)

    def complete(self, item):
        ModuleContentProgress.objects.update_or_create(
            content=item, user=self.student,
            defaults={'opened_at': timezone.now(), 'completed_at': timezone.now()},
        )

    def test_sequence_and_direct_module_access(self):
        self.assertTrue(module_lock_reason(self.second, self.student))
        self.assertEqual(self.client.get(reverse('courses:tema_detail', args=[self.second.pk])).status_code, 403)
        self.complete(self.item)
        self.assertTrue(module_complete(self.first, self.student))
        self.assertFalse(module_lock_reason(self.second, self.student))
        self.assertEqual(self.client.get(reverse('courses:tema_detail', args=[self.second.pk])).status_code, 200)

    def test_manual_completion_requires_open_and_is_per_student(self):
        url = reverse('lms:module_content_complete', kwargs={'course_id': self.course.pk, 'content_id': self.item.pk})
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(content_complete(self.item, self.student))
        self.assertEqual(self.client.get(reverse('lms:course_page_detail', kwargs={
            'course_id': self.course.pk, 'page_slug': self.page.slug,
        })).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertTrue(content_complete(self.item, self.student))
        self.assertFalse(content_complete(self.item, self.other))

    def test_prerequisite_blocks_page_and_cannot_cycle(self):
        page = Page.objects.create(course=self.course, module=self.first, title='Dependiente', slug='dependiente',
                                   content='Lectura', status=Page.Status.PUBLISHED, author=self.teacher)
        dependent = ModuleContent.objects.create(module=self.first, page=page, prerequisite=self.item)
        self.assertFalse(page.user_can_view(self.student))
        self.assertTrue(content_lock_reason(dependent, self.student))
        self.item.prerequisite = dependent
        with self.assertRaises(ValidationError):
            self.item.full_clean()
        self.item.prerequisite = None
        self.complete(self.item)
        self.assertTrue(page.user_can_view(self.student))

    def test_dates_exact_boundaries_and_teacher_bypass(self):
        now = timezone.now()
        ModuleSchedule.objects.create(module=self.first, starts_at=now, ends_at=now + timedelta(hours=1), estimated_minutes=120)
        module = Tema.objects.get(pk=self.first.pk)
        self.assertTrue(module_lock_reason(module, self.student, now=now - timedelta(microseconds=1)))
        self.assertFalse(module_lock_reason(module, self.student, now=now))
        self.assertTrue(module_lock_reason(module, self.student, now=now + timedelta(hours=1)))
        self.assertFalse(module_lock_reason(module, self.teacher, now=now + timedelta(hours=2)))
        self.assertFalse(ModuleScheduleForm({'starts_at': now, 'ends_at': now, 'estimated_minutes': 20}).is_valid())

    def test_disabled_progression_preserves_legacy_access(self):
        self.settings.sequential_modules = False
        self.settings.save()
        self.assertFalse(module_lock_reason(self.second, self.student))
        self.assertTrue(self.page.user_can_view(self.student))

    def test_cross_course_and_cross_module_prerequisites_rejected(self):
        page = Page.objects.create(course=self.course, module=self.second, title='Segunda', slug='segunda',
                                   content='Lectura', status=Page.Status.PUBLISHED, author=self.teacher)
        second = ModuleContent(module=self.second, page=page, prerequisite=self.item)
        with self.assertRaises(ValidationError):
            second.full_clean()
        foreign = Curso.objects.create(nombre='Otro curso', descripcion='Otro', fecha_inicio=date(2026, 1, 1))
        foreign_page = Page.objects.create(course=foreign, title='Fuera', slug='fuera', content='Otro', author=self.teacher)
        form = ModuleContentForm({
            'page': foreign_page.pk, 'criterion': 'manual', 'position': 1,
            'required': 'on', 'passing_percentage': 50,
        }, module=self.first)
        self.assertFalse(form.is_valid())
        self.assertIn('page', form.errors)

    def test_content_management_links_activity_and_prevents_deleting_prerequisite(self):
        self.client.force_login(self.teacher)
        activity = LearningActivity.objects.create(course=self.course, title='Actividad nueva', status='open', points=10)
        url = reverse('lms:module_contents', kwargs={'course_id': self.course.pk, 'module_id': self.first.pk})
        self.assertContains(self.client.get(url), 'Guardar planificación')
        response = self.client.post(url, {
            'action': 'content', 'activity': activity.pk, 'criterion': 'submitted', 'position': 1,
            'required': 'on', 'passing_percentage': 50, 'prerequisite': self.item.pk,
        })
        self.assertEqual(response.status_code, 302)
        activity.refresh_from_db()
        self.assertEqual(activity.module_id, self.first.pk)
        self.assertEqual(self.client.post(url, {'action': 'remove', 'content_id': self.item.pk}).status_code, 302)
        self.assertTrue(ModuleContent.objects.filter(pk=self.item.pk).exists())
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_required_optional_and_no_manual_module_bypass(self):
        url = reverse('courses:completar_tema', args=[self.first.pk])
        self.assertEqual(self.client.post(url).status_code, 400)
        self.complete(self.item)
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertTrue(Progreso.objects.get(usuario=self.student, tema=self.first).completado)

    def test_activity_submitted_and_percentage_pass_uses_released_latest_grade(self):
        activity = LearningActivity.objects.create(course=self.course, title='Entrega', status='open', points=20)
        item = ModuleContent.objects.create(module=self.first, activity=activity, criterion='submitted')
        assignment = Assignment.objects.create(course=self.course, title='Entrega', learning_activity=activity)
        submission = Submission.objects.create(
            assignment=assignment, user=self.student, submitted_at=timezone.now(),
            status=Submission.Status.SUBMITTED, receipt_identifier='receipt',
        )
        self.assertTrue(content_complete(item, self.student))
        item.criterion = 'passed'
        item.passing_percentage = 60
        item.save()
        self.assertFalse(content_complete(item, self.student))
        submission.status = Submission.Status.GRADED
        submission.grade = Decimal('12')
        submission.feedback_published = True
        submission.save()
        self.assertTrue(content_complete(item, self.student))
        activity.feedback_at = timezone.now() + timedelta(days=1)
        activity.save()
        item.refresh_from_db()
        self.assertFalse(content_complete(item, self.student))

    def test_quiz_criteria_and_deadline_and_service_access(self):
        quiz = Quiz.objects.create(course=self.course, title='Objetiva', status=Quiz.Status.PUBLISHED)
        item = ModuleContent.objects.create(module=self.second, quiz=quiz, criterion='finished')
        with self.assertRaises(PermissionDenied):
            require_student(self.student, quiz)
        self.complete(self.item)
        require_student(self.student, quiz)
        end = timezone.now() + timedelta(hours=2)
        ModuleSchedule.objects.create(module=self.second, ends_at=end, estimated_minutes=180)
        self.assertEqual(attempt_deadline(Quiz.objects.get(pk=quiz.pk)), end)
        attempt = QuizAttempt.objects.create(quiz=quiz, user=self.student, status=QuizAttempt.Status.GRADED, is_passed=False)
        self.assertTrue(content_complete(item, self.student))
        item.criterion = 'passed'
        item.save()
        self.assertFalse(content_complete(item, self.student))
        attempt.is_passed = True
        attempt.save()
        self.assertTrue(content_complete(item, self.student))

    def test_calendar_includes_all_dates_updates_without_duplicates_and_hides_drafts(self):
        now = timezone.now()
        ModuleSchedule.objects.create(module=self.first, starts_at=now, ends_at=now + timedelta(hours=3))
        quiz = Quiz.objects.create(course=self.course, title='Prueba calendario', status=Quiz.Status.PUBLISHED,
                                   available_from=now, available_until=now + timedelta(days=1),
                                   due_at=now + timedelta(hours=4), publish_grade_at=now + timedelta(days=2))
        LearningActivity.objects.create(course=self.course, title='Actividad calendario', status='open',
                                        starts_at=now, due_at=now + timedelta(days=1), feedback_at=now + timedelta(days=2))
        Quiz.objects.create(course=self.course, title='Borrador oculto', available_from=now)
        rows = calendar_dates(self.student)
        keys = [row['key'] for row in rows]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn(f'course:{self.course.pk}:start', keys)
        self.assertIn(f'course:{self.course.pk}:end', keys)
        self.assertIn(f'module:{self.first.pk}:starts_at', keys)
        self.assertIn(f'quiz:{quiz.pk}:available_from', keys)
        self.assertFalse(any('Borrador oculto' in row['title'] for row in rows))
        self.assertTrue(any('Borrador oculto' in row['title'] for row in calendar_dates(self.teacher)))
        quiz.available_from = now + timedelta(days=3)
        quiz.save()
        refreshed = {row['key']: row for row in calendar_dates(self.student)}
        self.assertEqual(refreshed[f'quiz:{quiz.pk}:available_from']['moment'], quiz.available_from)
        self.assertEqual(calendar_dates(self.other), [])
        self.assertContains(self.client.get(reverse('lms:global_area', kwargs={'area': 'calendar'})), 'Inicio del curso')

    def test_home_calendar_circles_course_dates_and_groups_visible_events(self):
        from bs4 import BeautifulSoup
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from .calendar_dates import course_calendar_days
        now = timezone.now()
        today = timezone.localdate()
        self.course.fecha_inicio = today
        self.course.fecha_fin = today + timedelta(days=1)
        self.course.save()
        first = LearningActivity.objects.create(
            course=self.course, title='Actividad visible', status='open', starts_at=now, due_at=now,
        )
        hidden = Quiz.objects.create(course=self.course, title='Prueba borrador', available_from=now)
        Quiz.objects.create(course=self.course, title='Prueba visible', status='published', available_from=now)
        ModuleSchedule.objects.create(module=self.first, starts_at=now)
        response = self.client.get(reverse('lms:course_detail', kwargs={'course_id': self.course.pk}))
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        marked = soup.select('.course-mini-calendar .calendar-event-day')
        labels = ' '.join(link['title'] for link in marked)
        for label in ('Inicio del curso', 'Fin del curso', 'Actividad visible', 'Prueba visible', 'Inicio del módulo'):
            self.assertIn(label, labels)
        self.assertNotIn(hidden.title, labels)
        dates = [link.find('time')['datetime'] for link in marked]
        self.assertEqual(len(dates), len(set(dates)))
        self.assertIn(today.isoformat(), dates)
        global_response = self.client.get(reverse('lms:global_area', kwargs={'area': 'calendar'}))
        global_soup = BeautifulSoup(global_response.content, 'html.parser')
        for link in marked:
            self.assertIsNotNone(global_soup.find(id=link['href'].split('#')[1]))
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(reverse('lms:course_detail', kwargs={'course_id': self.course.pk})), hidden.title)
        # Midnight UTC falls on the previous date in this course's time zone.
        self.course.zona_horaria = 'America/New_York'
        self.course.save()
        first.starts_at = timezone.make_aware(datetime(2026, 10, 14, 1), ZoneInfo('UTC'))
        first.save()
        days = course_calendar_days(self.student, self.course, [[date(2026, 10, 13), date(2026, 10, 14)]])[0]
        self.assertIn(f'activity:{first.pk}:starts_at', [event['key'] for event in days[0]['events']])
        self.assertNotIn(f'activity:{first.pk}:starts_at', [event['key'] for event in days[1]['events']])

    def test_content_screen_renders_states_and_settings_toggle(self):
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'content'})
        self.assertContains(self.client.get(url), 'Bloqueado')
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(url), 'Gestionar contenidos y requisitos')
        settings_url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'settings'})
        response = self.client.get(settings_url)
        form = response.context['settings_form']
        self.assertTrue(form['sequential_modules'].value())
        data = {name: value if value is not None else '' for name, value in form.initial.items()
                if name not in ('imagen', 'sequential_modules')}
        saved = self.client.post(settings_url, data)
        self.assertEqual(saved.status_code, 302, saved.context['settings_form'].errors if saved.status_code == 200 else '')
        self.settings.refresh_from_db()
        self.assertFalse(self.settings.sequential_modules)

    def test_content_drawer_save_and_validation(self):
        self.client.force_login(self.teacher)
        url = reverse('lms:module_contents', kwargs={'course_id': self.course.pk, 'module_id': self.first.pk})
        panel = url + '?panel=content'
        headers = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}
        response = self.client.get(panel, **headers)
        self.assertContains(response, 'data-drawer-form')
        self.assertContains(response, f'action="{panel}"')
        self.assertNotContains(response, '<html')
        self.assertContains(response, 'Prerrequisito')
        response = self.client.post(panel, {'action': 'content', 'criterion': 'manual'}, **headers)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Selecciona al menos un contenido', status_code=400)
        activity = LearningActivity.objects.create(course=self.course, title='Panel actividad', status='open', points=10)
        response = self.client.post(panel, {
            'action': 'content', 'activity': activity.pk, 'position': 1,
            'criterion': 'submitted', 'required': 'on', 'passing_percentage': 50,
            'prerequisite': self.item.pk,
        }, **headers)
        self.assertEqual(response.json(), {'ok': True})
        item = ModuleContent.objects.get(activity=activity)
        self.assertEqual(item.prerequisite_id, self.item.pk)
        response = self.client.get(panel + f'&edit={item.pk}', **headers)
        self.assertContains(response, 'Guardar cambios')
        self.assertContains(response, 'Panel actividad')
        self.assertContains(self.client.get(url + '?panel=schedule', **headers), 'Guardar planificación')
        response = self.client.post(panel + f'&edit={item.pk}', {
            'action': 'content', 'activity': activity.pk, 'position': 2,
            'criterion': 'submitted', 'required': 'on', 'passing_percentage': 50,
        }, **headers)
        self.assertEqual(response.json(), {'ok': True})
        item.refresh_from_db()
        self.assertEqual(item.position, 2)
        self.assertIsNone(item.prerequisite_id)
        self.assertEqual(ModuleContent.objects.filter(activity=activity).count(), 1)
        response = self.client.post(url + '?panel=schedule', {
            'action': 'schedule', 'estimated_minutes': 90,
        }, **headers)
        self.assertEqual(response.json(), {'ok': True})
        self.assertEqual(ModuleSchedule.objects.get(module=self.first).estimated_minutes, 90)
        self.assertContains(self.client.get(panel), '<html')
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(panel, **headers).status_code, 403)

    def test_content_compact_layout_and_student_has_no_edit_controls(self):
        url = reverse('lms:course_area', kwargs={'course_id': self.course.pk, 'area': 'content'})
        response = self.client.get(url)
        self.assertContains(response, 'learning-content-row')
        self.assertContains(response, 'Página previa')
        self.assertContains(response, 'data-module-toggle')
        self.assertNotContains(response, 'data-content-drawer')
        self.assertNotContains(response, 'Próximas entregas')
        self.client.force_login(self.teacher)
        response = self.client.get(url)
        self.assertContains(response, 'data-content-drawer')
        self.assertContains(response, 'Añadir contenido a Primero')
        self.assertContains(response, 'Fechas y duración')
        self.assertContains(response, 'data-modules-collapse')

    def test_batch_adds_multiple_resources_of_every_type_and_persists(self):
        self.client.force_login(self.teacher)
        url = reverse('lms:module_contents', kwargs={'course_id': self.course.pk, 'module_id': self.first.pk})
        data = {'action': 'content', 'criterion': 'manual', 'required': 'on',
                'prerequisite': self.item.pk, 'position': '', 'passing_percentage': ''}
        targets = {}
        for name, model in [('page', Page), ('file', FileAsset), ('quiz', Quiz), ('activity', LearningActivity)]:
            targets[name] = []
            for index in range(2):
                kwargs = {'course': self.course, 'title': f'{name} {index}'}
                if name == 'page':
                    kwargs['slug'] = f'page-{index}'
                if name == 'file':
                    kwargs['file'] = f'test-{index}.pdf'
                    kwargs['size'] = 1
                    kwargs['checksum'] = 'fixture'
                target = model.objects.create(**kwargs)
                targets[name].append(target)
            data[name] = [target.pk for target in targets[name]]
        response = self.client.post(url + '?panel=content', data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True})
        contents = self.first.learning_contents.exclude(pk=self.item.pk)
        self.assertEqual(contents.count(), 8)
        self.assertEqual(list(contents.values_list('position', flat=True)), list(range(8)))
        for name, selected in targets.items():
            for target in selected:
                item = ModuleContent.objects.get(**{name: target})
                self.assertEqual(item.module, self.first)
                self.assertEqual(item.prerequisite, self.item)
                self.assertEqual(item.passing_percentage, 50)
                target.refresh_from_db()
                if name in ('page', 'activity'):
                    self.assertEqual(target.module_id, self.first.pk)
        for name in ('page', 'file', 'quiz', 'activity'):
            self.assertContains(self.client.get(url), f'{name} 0')
        response = self.client.post(url + '?panel=content', data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(contents.count(), 8)

    def test_invalid_batch_retains_selection_and_saves_nothing(self):
        self.client.force_login(self.teacher)
        url = reverse('lms:module_contents', kwargs={'course_id': self.course.pk, 'module_id': self.first.pk})
        page = Page.objects.create(course=self.course, title='Lectura lote', slug='lote')
        quiz = Quiz.objects.create(course=self.course, title='Prueba lote')
        data = {'action': 'content', 'page': [page.pk], 'quiz': [quiz.pk],
                'criterion': 'finished', 'position': 1, 'passing_percentage': 50}
        response = self.client.post(url + '?panel=content', data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Este criterio no corresponde al tipo de contenido', status_code=400)
        self.assertEqual(response.context['content_form']['page'].value(), [str(page.pk)])
        self.assertContains(response, 'checked', status_code=400)
        self.assertFalse(ModuleContent.objects.filter(quiz=quiz).exists())
        self.assertFalse(ModuleContent.objects.filter(page=page).exists())
        data['criterion'] = 'manual'
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertTrue(ModuleContent.objects.filter(page=page, module=self.first).exists())
        self.assertTrue(ModuleContent.objects.filter(quiz=quiz, module=self.first).exists())

    def test_batch_rejects_resources_from_other_modules_or_courses(self):
        self.client.force_login(self.teacher)
        url = reverse('lms:module_contents', kwargs={'course_id': self.course.pk, 'module_id': self.first.pk})
        available = Page.objects.create(course=self.course, title='Disponible', slug='disponible')
        elsewhere = Page.objects.create(course=self.course, module=self.second, title='Otro módulo', slug='otro')
        other_course = Curso.objects.create(
            nombre='Otro curso', descripcion='Otro',
            fecha_inicio=self.course.fecha_inicio, fecha_fin=self.course.fecha_fin,
        )
        foreign = Quiz.objects.create(course=other_course, title='Otra aula')
        response = self.client.post(url + '?panel=content', {
            'action': 'content', 'page': [available.pk, elsewhere.pk],
            'quiz': [foreign.pk], 'criterion': 'manual',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context['content_form'].errors['page'])
        self.assertTrue(response.context['content_form'].errors['quiz'])
        self.assertFalse(ModuleContent.objects.filter(page=available).exists())

    def test_content_pdf_opens_preview_without_enabling_download_or_bypassing_locks(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .module_progression import content_url, module_rows
        from .storage import PrivateMediaStorage

        with TemporaryDirectory() as folder, patch.object(
            FileAsset._meta.get_field('file'), 'storage', PrivateMediaStorage(location=folder),
        ):
            asset = FileAsset.objects.create(
                course=self.course, title='PDF del módulo',
                file=SimpleUploadedFile('modulo.pdf', b'%PDF-1.4\ncontenido', content_type='application/pdf'),
                allow_preview=True, allow_download=False,
            )
            item = ModuleContent.objects.create(module=self.first, file=asset, criterion='viewed')
            url = content_url(item)
            self.assertTrue(url.endswith('?inline=1'))
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response['Content-Disposition'].startswith('inline'))
            response.close()
            self.assertTrue(content_complete(item, self.student))
            self.assertEqual(self.client.get(url.split('?')[0]).status_code, 403)
            item.prerequisite = self.item
            item.save()
            self.assertEqual(self.client.get(url).status_code, 403)
            self.complete(self.item)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            response.close()
            asset.allow_preview = False
            asset.allow_download = True
            asset.save(update_fields=['allow_preview', 'allow_download'])
            item.refresh_from_db()
            self.assertNotIn('?inline', content_url(item))
            response = self.client.get(content_url(item))
            self.assertEqual(response.status_code, 200)
            response.close()
            asset.allow_download = False
            asset.save(update_fields=['allow_download'])
            row = next(row for row in module_rows(self.course, self.student)[0]['items']
                       if row['content'].pk == item.pk)
            self.assertIn('no permite vista previa ni descarga', row['reason'])
