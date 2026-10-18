import json

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer


class CourseFeedConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.course_id = self.scope['url_route']['kwargs']['course_id']
        self.group_name = f'course_{self.course_id}_updates'
        self.notification_cursor = None
        self.student_preview = b'preview=1' in self.scope.get('query_string', b'').split(b'&')

        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close()
            return
        if not await self.can_access_course():
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.course_update({'event': 'connected', 'course_id': self.course_id, 'item_type': 'notifications'})

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def course_update(self, event):
        if not await self.can_access_course():
            await self.close(code=4403)
            return
        state = await self.notification_state()
        self.notification_cursor = state['cursor']
        await self.send(text_data=json.dumps({
            'event': event.get('event', 'update'),
            'course_id': event.get('course_id'),
            'item_type': event.get('item_type', 'announcement'),
            'message': event.get('message', ''),
            'notification_state': state,
        }))
        while state['has_more']:
            state = await self.notification_state()
            self.notification_cursor = state['cursor']
            await self.send(text_data=json.dumps({'notification_state': state}))

    @database_sync_to_async
    def notification_state(self):
        from apps.courses.models import Curso
        from .notification_state import notification_feed

        course = Curso.objects.get(pk=self.course_id)
        return notification_feed(
            self.scope['user'], course=course, after=self.notification_cursor,
            teacher_view=False if self.student_preview else None,
        )

    @database_sync_to_async
    def can_access_course(self):
        from apps.courses.models import Curso
        from .policies import can_edit_course, is_course_member

        course = Curso.objects.filter(pk=self.course_id).first()
        user = self.scope.get('user')
        return bool(course and user and (can_edit_course(user, course) or is_course_member(user, course)))


def broadcast_course_update(course, event, item_type='announcement', message=''):
    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    payload = {
        'type': 'course.update',
        'event': event,
        'course_id': course.pk,
        'item_type': item_type,
        'message': message,
    }

    async_to_sync(channel_layer.group_send)(
        f'course_{course.pk}_updates',
        payload,
    )
