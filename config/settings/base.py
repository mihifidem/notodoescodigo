"""
Base settings shared across all environments.
"""
import os
from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)

# Read .env file if it exists
environ.Env.read_env(BASE_DIR / '.env')

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env('SECRET_KEY', default='django-insecure-change-me-in-production')
DEBUG = env('DEBUG', default=False)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])

STRIPE_SECRET_KEY = env('STRIPE_SECRET_KEY', default='')
STRIPE_PUBLISHABLE_KEY = env('STRIPE_PUBLISHABLE_KEY', default='')
STRIPE_BILLING_WEBHOOK_SECRET = env('STRIPE_BILLING_WEBHOOK_SECRET', default='')
BILLING_PUBLIC_BASE_URL = env('BILLING_PUBLIC_BASE_URL', default='')
PAYPAL_CLIENT_ID = env('PAYPAL_CLIENT_ID', default='')
PAYPAL_CLIENT_SECRET = env('PAYPAL_CLIENT_SECRET', default='')
PAYPAL_MODE = env('PAYPAL_MODE', default='sandbox')
PAYPAL_BASE_URL = env('PAYPAL_BASE_URL', default='https://api-m.sandbox.paypal.com')

SITE_ID = 1

# ---------------------------------------------------------------------------
# Installed apps
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
]

THIRD_PARTY_APPS = [
    'channels',
    'rest_framework',
    'rest_framework.authtoken',
    'drf_spectacular',
    'django_filters',
    'corsheaders',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'django_celery_beat',
    'django_celery_results',
    'django_prose_editor',
]

LOCAL_APPS = [
    'apps.users',
    'apps.courses',
    'apps.evaluations',
    'apps.blog',
    'apps.gamification',
    'apps.rewards',
    'apps.analytics',
    'apps.certifications',
    'apps.ai_tutor',
    'apps.enlaces',
    'apps.prompts',
    'apps.api',
    'apps.curriculum',
    'apps.bug_reports',
    'apps.cv_templates',
    'apps.nexus',
    'apps.minigames',
    'apps.ducky_arenas',
    'apps.duckyclash',
    'apps.data_admin',
    'apps.devtools',
    'apps.updates',
    'apps.lms',
    'apps.energy',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'apps.users.middleware.StreakMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.users.context_processors.notificaciones_no_leidas',
                'apps.nexus.context_processors.demo_tests',
                'apps.lms.context_processors.lms_teacher_nav',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = 'users.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# ---------------------------------------------------------------------------
# django-allauth
# ---------------------------------------------------------------------------
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USERNAME_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = 'optional'
ACCOUNT_AUTHENTICATION_METHOD = 'username_email'
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_FORMS = {
    'reset_password': 'apps.users.forms.BootstrapResetPasswordForm',
    'reset_password_from_key': 'apps.users.forms.BootstrapResetPasswordKeyForm',
}

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'APP': {
            'client_id': env('GOOGLE_CLIENT_ID', default=''),
            'secret': env('GOOGLE_CLIENT_SECRET', default=''),
            'key': '',
        },
    }
}
SOCIALACCOUNT_LOGIN_ON_GET = True

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = 'es-es'
TIME_ZONE = 'Europe/Madrid'
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & Media
# ---------------------------------------------------------------------------
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
CERTIFICATIONS_EVIDENCE_MAX_BYTES = env.int(
    'CERTIFICATIONS_EVIDENCE_MAX_BYTES',
    default=0,
)
CERTIFICATIONS_ALLOWED_EVIDENCE_MIME_TYPES = env.list(
    'CERTIFICATIONS_ALLOWED_EVIDENCE_MIME_TYPES',
    default=['application/pdf', 'image/png', 'image/jpeg'],
)
CERTIFICATIONS_EXPIRY_WARNING_DAYS = env.int(
    'CERTIFICATIONS_EXPIRY_WARNING_DAYS',
    default=0,
)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='MihiTraining <no-reply@mihitraining.com>')

# ---------------------------------------------------------------------------
# Optional AI integrations
# ---------------------------------------------------------------------------
LMS_ENABLE_AI = env.bool('LMS_ENABLE_AI', default=False)
LMS_AI_PROVIDER = env('LMS_AI_PROVIDER', default='disabled')
LMS_AI_API_KEY = env('LMS_AI_API_KEY', default='')
LMS_AI_MODEL = env('LMS_AI_MODEL', default='gpt-4o-mini')
LMS_AI_MAX_TOKENS = env.int('LMS_AI_MAX_TOKENS', default=2000)
LMS_AI_MAX_MONTHLY_COST = env.float('LMS_AI_MAX_MONTHLY_COST', default=0.0)

# ---------------------------------------------------------------------------
# LMS logging
# ---------------------------------------------------------------------------
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'lms_console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'apps.lms': {
            'handlers': ['lms_console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = 'django-db'
CELERY_CACHE_BACKEND = 'django-cache'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    'clash-duel-engine': {
        'task': 'apps.duckyclash.tasks.advance_duel_engines',
        'schedule': 1.0,
    },
    'clash-combat-resolution': {
        'task': 'apps.duckyclash.tasks.resolve_closed_rounds',
        'schedule': 5.0,
    },
    'clash-bank-reconciliation': {
        'task': 'apps.duckyclash.tasks.reconcile_bank_operations',
        'schedule': 30.0,
    },
    'clash-open-expiry': {
        'task': 'apps.duckyclash.tasks.expire_open_challenges',
        'schedule': 30.0,
    },
    'lms-lifecycle': {
        'task': 'apps.lms.tasks.advance_lms_lifecycle',
        'schedule': 5.0,
    },
    'energy-reconciliation': {
        'task': 'apps.energy.tasks.reconcile_energy',
        'schedule': 300.0,
    },
}

# Energía Ducky: zona del día para límites por actividad y ejecutores IA reales por herramienta
# (ruta a una función). Sin ejecutor configurado la herramienta no se ejecuta ni reserva energía.
ENERGY_DAY_TIMEZONE = 'Europe/Madrid'
ENERGY_TOOL_EXECUTORS = {}

DUCKYCLASH_PAYMENT_GATEWAY = env('DUCKYCLASH_PAYMENT_GATEWAY', default='')
DUCKYCLASH_KNOWLEDGE_GATEWAY = env(
    'DUCKYCLASH_KNOWLEDGE_GATEWAY', default='apps.duckyclash.knowledge.LMSKnowledgeGateway',
)
DUCKYCLASH_BANK_GATEWAY = env('DUCKYCLASH_BANK_GATEWAY', default='')
DUCKYCLASH_ECOMOTOR_GATEWAY = env('DUCKYCLASH_ECOMOTOR_GATEWAY', default='')
DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER = env(
    'DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER',
    default='',
)
DUCKY_ARENAS_ECOMOTOR_CACHE_SECONDS = env.int(
    'DUCKY_ARENAS_ECOMOTOR_CACHE_SECONDS',
    default=30,
)
DUCKYCLASH_DOUBLE_KO = env('DUCKYCLASH_DOUBLE_KO', default='draw')
DUCKYCLASH_MAX_TIEBREAKS = env.int('DUCKYCLASH_MAX_TIEBREAKS', default=1)
DUCKYCLASH_ITEM_RULES = {}
DUCKYCLASH_ENGINE_RULES = {}

# ---------------------------------------------------------------------------
# Cache (Redis)
# ---------------------------------------------------------------------------
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': env('REDIS_URL', default='redis://localhost:6379/1'),
        'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
    }
}

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# ---------------------------------------------------------------------------
# drf-spectacular
# ---------------------------------------------------------------------------
SPECTACULAR_SETTINGS = {
    'TITLE': 'MihiTraining API',
    'DESCRIPTION': 'API REST de la plataforma educativa MihiTraining',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
OPENAI_API_KEY = env('OPENAI_API_KEY', default='')

# ---------------------------------------------------------------------------
# Site config
# ---------------------------------------------------------------------------
SITE_URL = env('SITE_URL', default='http://localhost:8000')
SITE_NAME = env('SITE_NAME', default='MihiTraining')
GOOGLE_CLIENT_ID = env('GOOGLE_CLIENT_ID', default='')
GOOGLE_CLIENT_SECRET = env('GOOGLE_CLIENT_SECRET', default='')