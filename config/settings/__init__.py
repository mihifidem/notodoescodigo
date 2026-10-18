# Configuración de página de error 403 personalizada
from django.conf import settings
from django.conf.urls import handler403
handler403 = 'django.views.defaults.permission_denied'
