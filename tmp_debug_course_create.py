import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.test import Client, override_settings
from django.contrib.auth.models import Permission
from apps.users.models import User

u = User.objects.create_user('teacherx', password='p')
p = Permission.objects.get(content_type__app_label='courses', codename='add_curso')
u.user_permissions.add(p)
print('HAS', u.has_perm('courses.add_curso'))
client = Client()
client.force_login(u)
with override_settings(ALLOWED_HOSTS=['testserver']):
    response = client.post('/lms/courses/create/', {
        'nombre': 'Aula de Python',
        'descripcion': 'Introducción al lenguaje.',
        'fecha_inicio': '2025-01-01',
        'fecha_fin': '',
        'horas_duracion': 10,
    })
    print('STATUS', response.status_code)
    print('URL', getattr(response, 'url', None))
    print(response.content[:400].decode('utf-8', 'ignore'))
