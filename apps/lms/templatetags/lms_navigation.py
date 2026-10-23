from django import template
from django.middleware.csrf import get_token

from apps.lms.models import html_content_text
from apps.lms.notification_state import notification_counts
from apps.lms.policies import can_edit_course, is_course_member
from apps.lms.ui import COURSE_AREAS, STUDENT_AREA_KEYS

register = template.Library()


@register.filter
def content_text(value):
    return html_content_text(value)


@register.inclusion_tag('lms/course_chrome.html')
def course_navigation(course, request, area_key='', preview_mode=False):
    match = request.resolver_match
    if not area_key and match:
        area_key = match.kwargs.get('area', '')
        if not area_key:
            keys = {
                'activity_id': 'activities', 'quiz_id': 'quizzes',
                'forum_id': 'discussion', 'page_id': 'pages', 'file_id': 'files',
                'bank_id': 'item-bank', 'topic_id': 'content',
            }
            area_key = next((key for argument, key in keys.items() if argument in match.kwargs), 'home')
            if area_key == 'home':
                prefixes = {
                    'course_activity_': 'activities', 'quiz_': 'quizzes', 'course_forum_': 'discussion',
                    'course_page': 'pages', 'course_file_': 'files', 'course_announcement_': 'announcements',
                    'knowledge_': 'item-bank',
                }
                area_key = next((key for prefix, key in prefixes.items() if match.url_name.startswith(prefix)), 'home')
    return {
        'course': course, 'request': request, 'area_key': area_key,
        'preview_mode': preview_mode,
        'csrf_token': get_token(request),
        'can_receive_notifications': can_edit_course(request.user, course) or is_course_member(request.user, course),
        'teacher_view': can_edit_course(request.user, course) and not preview_mode,
        'course_areas': COURSE_AREAS, 'student_area_keys': STUDENT_AREA_KEYS,
        'course_notification_counts': notification_counts(course, request.user, teacher_view=False if preview_mode else None),
    }
