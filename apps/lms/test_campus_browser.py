import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from .models import CourseMembership
from .test_campus_access import CampusFixture


@skipUnless(os.environ.get('PLAYWRIGHT_MODULE_PATH'), 'Set PLAYWRIGHT_MODULE_PATH to the test-only Playwright module.')
@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class CampusBrowserTests(StaticLiveServerTestCase):
    setUp = CampusFixture.setUp

    def test_visitor_login_gate_enrollment_lesson_resume_and_mobile_completion(self):
        self.client.force_login(self.student)
        environment = {
            **os.environ,
            'CAMPUS_TEST_URL': self.live_server_url,
            'CAMPUS_TEST_COURSE_ID': str(self.course.pk),
            'CAMPUS_TEST_COOKIE_NAME': settings.SESSION_COOKIE_NAME,
            'CAMPUS_TEST_SESSION': self.client.cookies[settings.SESSION_COOKIE_NAME].value,
        }
        result = subprocess.run(
            ['node', str(Path(__file__).with_name('campus_browser.cjs'))],
            env=environment, capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        membership = CourseMembership.objects.get(user=self.student, course=self.course)
        self.assertEqual(membership.last_content_id, self.item.pk)
        self.assertIsNotNone(membership.passed_at)
        self.assertEqual(self.item.progress.filter(user=self.student).count(), 1)
