from django.contrib.auth import get_user_model
from apps.bug_reports.models import BugReport

User = get_user_model()

# Crear usuarios de prueba si no existen
def get_or_create_user(username, email):
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            'email': email,
            'role': 'alumno',
            'is_staff': False,
            'is_active': True,
        }
    )
    return user

usuarios = [
    get_or_create_user('alice', 'alice@example.com'),
    get_or_create_user('bob', 'bob@example.com'),
    get_or_create_user('carol', 'carol@example.com'),
]

# Crear reportes de bugs de prueba
def crear_reportes():
    titulos = [
        'Error al guardar evaluaciones',
        'Pantalla en blanco al iniciar sesión',
        'No se cargan los recursos del curso',
        'Problema con la subida de archivos',
        'Botón de siguiente no funciona',
    ]
    descripciones = [
        'Al intentar guardar una evaluación, aparece un error 500.',
        'Después de ingresar usuario y contraseña, la pantalla queda en blanco.',
        'Los recursos del curso no aparecen en la lista.',
        'Al subir un archivo grande, la carga nunca termina.',
        'El botón de siguiente en el quiz no responde al hacer clic.',
    ]
    for i, user in enumerate(usuarios):
        for j in range(2):
            idx = (i*2 + j) % len(titulos)
            BugReport.objects.create(
                alumno=user,
                titulo=titulos[idx],
                descripcion=descripciones[idx],
                pasos_reproduccion='1. Ir a la sección correspondiente. 2. Realizar la acción. 3. Observar el error.',
                url_afectada='/ruta/prueba/',
                estado='pendiente',
                puntos_premio=10,
            )

if __name__ == '__main__':
    crear_reportes()
    print('Reportes de bugs de prueba creados.')
