import hashlib
import mimetypes
import os
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bs4 import BeautifulSoup
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models

from .storage import PrivateMediaStorage
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

ALLOWED_HTML_TAGS = {
    'a', 'b', 'blockquote', 'br', 'code', 'div', 'em', 'figcaption', 'figure', 'h1', 'h2', 'h3',
    'h4', 'h5', 'h6', 'hr', 'i', 'iframe', 'img', 'li', 'ol', 'p', 'pre', 'small', 'source',
    'span', 'strong', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'u', 'ul', 'video'
}
ALLOWED_HTML_ATTRS = {
    'a': {'href', 'rel', 'target'},
    'img': {'alt', 'src', 'title'},
    'iframe': {'allow', 'allowfullscreen', 'height', 'src', 'title', 'width'},
    'source': {'src', 'type'},
    'video': {'controls', 'height', 'poster', 'src', 'width'},
    'div': {'class'},
    'span': {'class'},
}


def sanitize_html_content(value):
    if value is None:
        return ''
    soup = BeautifulSoup(value, 'html.parser')
    for tag in list(soup.find_all(True)):
        if tag.name not in ALLOWED_HTML_TAGS:
            tag.decompose()
            continue
        for attr_name in list(tag.attrs):
            if attr_name not in ALLOWED_HTML_ATTRS.get(tag.name, set()):
                del tag.attrs[attr_name]
                continue
            if attr_name in {'href', 'src'}:
                attr_value = str(tag.attrs[attr_name] or '').strip()
                if not attr_value or attr_value.lower().startswith('javascript:'):
                    del tag.attrs[attr_name]
                    continue
                parsed = urlparse(attr_value)
                if parsed.scheme and parsed.scheme not in {'http', 'https', 'mailto'}:
                    del tag.attrs[attr_name]
        if tag.name == 'a' and not tag.get('href'):
            tag.unwrap()
    return str(soup)


def has_page_styles(value):
    soup = BeautifulSoup(value or '', 'html.parser')
    return bool(soup.find(['html', 'head', 'body', 'style', 'link']) or soup.find(style=True))


def sanitize_page_content(value):
    if not has_page_styles(value):
        return sanitize_html_content(value)
    soup = BeautifulSoup(value or '', 'html.parser')
    allowed_tags = ALLOWED_HTML_TAGS | {
        'html', 'head', 'body', 'title', 'meta', 'style', 'link', 'header', 'footer',
        'main', 'section', 'article', 'aside', 'nav', 'details', 'summary', 'dl',
        'dt', 'dd', 'caption', 'colgroup', 'col', 'tfoot', 'sup', 'sub', 'mark',
    }
    for tag in list(soup.find_all(True)):
        if tag.name is None:
            continue
        if tag.name not in allowed_tags or tag.name == 'iframe':
            tag.decompose()
            continue
        allowed_attrs = ALLOWED_HTML_ATTRS.get(tag.name, set()) | {
            'id', 'class', 'style', 'title', 'lang', 'dir', 'role',
        }
        if tag.name == 'link':
            allowed_attrs |= {'href', 'rel', 'media'}
            if tag.get('rel') != ['stylesheet']:
                tag.decompose()
                continue
        if tag.name == 'meta':
            allowed_attrs |= {'charset', 'name', 'content'}
        if tag.name in ('td', 'th'):
            allowed_attrs |= {'colspan', 'rowspan', 'scope'}
        for attr in list(tag.attrs):
            if attr not in allowed_attrs and not attr.startswith('aria-'):
                del tag.attrs[attr]
            elif attr in ('href', 'src', 'poster'):
                scheme = urlparse(str(tag.attrs[attr]).strip()).scheme.lower()
                if scheme and scheme not in ('http', 'https', 'mailto'):
                    del tag.attrs[attr]
    return str(soup)


def generate_asset_token():
    return uuid4().hex


class UserProfileLMS(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name='lms_profile',
        on_delete=models.CASCADE,
    )
    preferred_language = models.CharField(
        _('idioma preferido'),
        max_length=10,
        default='es',
    )
    time_zone = models.CharField(
        _('zona horaria'),
        max_length=64,
        blank=True,
        default='',
        help_text=_('Opcional. Si se deja vacío, se utiliza la zona horaria del campus.'),
    )
    email_notifications = models.BooleanField(_('notificaciones por correo'), default=True)
    accessibility_preferences = models.JSONField(
        _('preferencias de accesibilidad'),
        default=dict,
        blank=True,
    )
    last_course = models.ForeignKey(
        'courses.Curso',
        verbose_name=_('último curso'),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='lms_last_course_profiles',
    )
    onboarding_completed = models.BooleanField(_('bienvenida completada'), default=False)
    onboarding_dismissed = models.BooleanField(_('bienvenida descartada'), default=False)
    onboarding_completed_at = models.DateTimeField(
        _('fecha de bienvenida'),
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('perfil formativo LMS')
        verbose_name_plural = _('perfiles formativos LMS')

    def __str__(self):
        return f'Preferencias LMS de {self.user}'

    def clean(self):
        super().clean()
        errors = {}
        if self.time_zone:
            try:
                ZoneInfo(self.time_zone)
            except (ZoneInfoNotFoundError, ValueError):
                errors['time_zone'] = _('Selecciona una zona horaria IANA válida.')
        if not isinstance(self.accessibility_preferences, dict):
            errors['accessibility_preferences'] = _('Las preferencias deben ser un objeto JSON.')
        if self.onboarding_completed != (self.onboarding_completed_at is not None):
            errors['onboarding_completed_at'] = _('La fecha debe corresponder a la bienvenida completada.')
        if errors:
            raise ValidationError(errors)


class NotificationPreference(models.Model):
    class DigestFrequency(models.TextChoices):
        INSTANT = 'instant', _('Inmediato')
        DAILY = 'daily', _('Diario')
        WEEKLY = 'weekly', _('Semanal')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_notification_preferences',
        verbose_name=_('usuario'),
    )
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='notification_preferences',
        verbose_name=_('curso'),
        null=True,
        blank=True,
    )
    in_app_enabled = models.BooleanField(_('bandeja habilitada'), default=True)
    email_enabled = models.BooleanField(_('correo habilitado'), default=True)
    push_enabled = models.BooleanField(_('push habilitado'), default=False)
    webhook_enabled = models.BooleanField(_('webhook habilitado'), default=False)
    digest_frequency = models.CharField(
        _('frecuencia de resumen'),
        max_length=12,
        choices=DigestFrequency.choices,
        default=DigestFrequency.INSTANT,
    )
    quiet_hours_start = models.TimeField(_('inicio de horas tranquilas'), null=True, blank=True)
    quiet_hours_end = models.TimeField(_('fin de horas tranquilas'), null=True, blank=True)
    time_zone = models.CharField(_('zona horaria'), max_length=64, default='UTC')
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('preferencia de notificación')
        verbose_name_plural = _('preferencias de notificación')
        constraints = [
            models.UniqueConstraint(fields=['user', 'course'], name='lms_unique_user_notification_pref_course'),
        ]

    def is_channel_enabled(self, channel):
        channel_map = {
            'in_app': self.in_app_enabled,
            'email': self.email_enabled,
            'push': self.push_enabled,
            'webhook': self.webhook_enabled,
        }
        return bool(channel_map.get(channel, False))

    def __str__(self):
        course_label = self.course or 'global'
        return f'{self.user} · {course_label}'


class NotificationEvent(models.Model):
    class Type(models.TextChoices):
        WELCOME = 'welcome', _('Bienvenida')
        ENROLLMENT = 'enrollment', _('Matrícula')
        ANNOUNCEMENT = 'announcement', _('Anuncio publicado')
        MENTION = 'mention', _('Respuesta mencionada')
        MODULE = 'module', _('Nuevo módulo')
        GRADE = 'grade', _('Nota publicada')
        NOTE_PUBLISHED = 'note_published', _('Nota publicada')
        DEADLINE = 'deadline', _('Vencimiento')
        FEEDBACK = 'feedback', _('Feedback')
        NOTE = 'note', _('Nota')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='notification_events',
        verbose_name=_('curso'),
        null=True,
        blank=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='caused_notifications',
        verbose_name=_('actor'),
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='targeted_notifications',
        verbose_name=_('destinatario'),
    )
    event_type = models.CharField(_('tipo de evento'), max_length=24, choices=Type.choices)
    title = models.CharField(_('título'), max_length=200)
    message = models.TextField(_('mensaje'))
    metadata = models.JSONField(_('metadatos'), default=dict, blank=True)
    dedupe_key = models.CharField(_('clave deduplicada'), max_length=128, unique=True, db_index=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    processed_at = models.DateTimeField(_('procesado'), null=True, blank=True)

    class Meta:
        verbose_name = _('evento de notificación')
        verbose_name_plural = _('eventos de notificación')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_event_type_display()} — {self.created_at:%Y-%m-%d %H:%M}'


class Notification(models.Model):
    class Status(models.TextChoices):
        UNREAD = 'unread', _('Sin leer')
        READ = 'read', _('Leída')

    event = models.ForeignKey(
        NotificationEvent,
        on_delete=models.CASCADE,
        related_name='notifications',
        verbose_name=_('evento'),
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_notifications',
        verbose_name=_('destinatario'),
    )
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='notifications',
        verbose_name=_('curso'),
        null=True,
        blank=True,
    )
    title = models.CharField(_('título'), max_length=200)
    message = models.TextField(_('mensaje'))
    status = models.CharField(_('estado'), max_length=12, choices=Status.choices, default=Status.UNREAD)
    payload = models.JSONField(_('payload'), default=dict, blank=True)
    read_at = models.DateTimeField(_('leída el'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('notificación')
        verbose_name_plural = _('notificaciones')
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['event', 'recipient'], name='lms_unique_event_notification_per_user'),
        ]

    def __str__(self):
        return f'{self.recipient} — {self.title}'


class NotificationOutbox(models.Model):
    class Channel(models.TextChoices):
        IN_APP = 'in_app', _('Bandeja')
        EMAIL = 'email', _('Correo')
        PUSH = 'push', _('Push')
        WEBHOOK = 'webhook', _('Webhook')

    class Status(models.TextChoices):
        PENDING = 'pending', _('Pendiente')
        SENT = 'sent', _('Enviada')
        FAILED = 'failed', _('Fallida')
        SKIPPED = 'skipped', _('Omitida')

    event = models.ForeignKey(
        NotificationEvent,
        on_delete=models.CASCADE,
        related_name='outbox_entries',
        verbose_name=_('evento'),
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_outbox',
        verbose_name=_('destinatario'),
    )
    channel = models.CharField(_('canal'), max_length=12, choices=Channel.choices, default=Channel.IN_APP)
    status = models.CharField(_('estado'), max_length=12, choices=Status.choices, default=Status.PENDING)
    dedupe_key = models.CharField(_('clave de deduplicación'), max_length=160, db_index=True)
    scheduled_for = models.DateTimeField(_('programada para'), default=timezone.now)
    sent_at = models.DateTimeField(_('enviada el'), null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(_('intentos'), default=0)
    error = models.TextField(_('error'), blank=True, default='')
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('bandeja de salida de notificación')
        verbose_name_plural = _('bandejas de salida de notificación')
        ordering = ['scheduled_for', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['event', 'recipient', 'channel'], name='lms_unique_outbox_event_recipient_channel'),
        ]

    def __str__(self):
        return f'{self.recipient} — {self.channel} ({self.status})'


class DeliveryLog(models.Model):
    outbox = models.ForeignKey(
        NotificationOutbox,
        on_delete=models.CASCADE,
        related_name='delivery_logs',
        verbose_name=_('bandeja'),
    )
    channel = models.CharField(_('canal'), max_length=12, choices=NotificationOutbox.Channel.choices)
    status = models.CharField(_('estado'), max_length=12, choices=NotificationOutbox.Status.choices, default=NotificationOutbox.Status.SENT)
    provider = models.CharField(_('proveedor'), max_length=64, blank=True, default='')
    external_id = models.CharField(_('identificador externo'), max_length=255, blank=True, default='')
    dedupe_key = models.CharField(_('clave de deduplicación'), max_length=160, unique=True, db_index=True)
    payload = models.JSONField(_('payload'), default=dict, blank=True)
    sent_at = models.DateTimeField(_('enviada el'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('registro de entrega')
        verbose_name_plural = _('registros de entrega')
        ordering = ['-sent_at', '-created_at']

    def __str__(self):
        return f'{self.channel} → {self.status}'


class CalendarEventQuerySet(models.QuerySet):
    def for_user(self, user, *, start=None, end=None):
        qs = self.filter(user=user)
        if start is not None and end is not None:
            qs = qs.filter(starts_at__lt=end, ends_at__gt=start)
        elif start is not None:
            qs = qs.filter(starts_at__gte=start)
        elif end is not None:
            qs = qs.filter(starts_at__lt=end)
        return qs.order_by('starts_at')


class CalendarEvent(models.Model):
    objects = CalendarEventQuerySet.as_manager()

    class EventType(models.TextChoices):
        ASSESSMENT = 'assessment', _('Evaluación')
        ACTIVITY = 'activity', _('Actividad')
        SESSION = 'session', _('Sesión')
        DEADLINE = 'deadline', _('Plazo')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='calendar_events',
        verbose_name=_('curso'),
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='calendar_events',
        verbose_name=_('usuario'),
        null=True,
        blank=True,
    )
    title = models.CharField(_('título'), max_length=200)
    description = models.TextField(_('descripción'), blank=True, default='')
    event_type = models.CharField(_('tipo'), max_length=18, choices=EventType.choices, default=EventType.ACTIVITY)
    starts_at = models.DateTimeField(_('inicio'))
    ends_at = models.DateTimeField(_('fin'), null=True, blank=True)
    all_day = models.BooleanField(_('todo el día'), default=False)
    location = models.CharField(_('ubicación'), max_length=200, blank=True, default='')
    time_zone = models.CharField(_('zona horaria'), max_length=64, default='UTC')
    metadata = models.JSONField(_('metadatos'), default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_calendar_events',
        verbose_name=_('creado por'),
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('evento del calendario')
        verbose_name_plural = _('eventos del calendario')
        ordering = ['starts_at', 'title']

    def clean(self):
        super().clean()
        if self.ends_at and self.ends_at < self.starts_at:
            raise ValidationError({'ends_at': _('La fecha fin debe ser posterior al inicio.')})

    def __str__(self):
        return f'{self.title} — {self.starts_at:%Y-%m-%d %H:%M}'

    @classmethod
    def for_user(cls, user, *, start=None, end=None):
        return cls.objects.for_user(user, start=start, end=end)


class CourseSettingsLMS(models.Model):
    course = models.OneToOneField(
        'courses.Curso',
        verbose_name=_('curso'),
        on_delete=models.CASCADE,
        related_name='lms_settings',
    )
    summary = models.CharField(_('resumen'), max_length=280, blank=True, default='')
    legacy_slug = models.SlugField(max_length=220, blank=True, null=True, unique=True)
    enrollment_open = models.BooleanField(_('inscripción abierta'), default=True)
    archived = models.BooleanField(_('archivado'), default=False)
    starts_at = models.DateTimeField(_('inicio exacto'), blank=True, null=True)
    ends_at = models.DateTimeField(_('fin exacto'), blank=True, null=True)
    sequential_modules = models.BooleanField(_('progresión secuencial de módulos'), default=False)

    class Meta:
        verbose_name = _('configuración del curso LMS')
        verbose_name_plural = _('configuraciones de cursos LMS')

    def __str__(self):
        return f'Configuración LMS de {self.course}'


class Cohort(models.Model):
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='cohorts',
    )
    title = models.CharField(_('nombre'), max_length=160)
    slug = models.SlugField(_('slug'), max_length=180, blank=True)
    description = models.TextField(_('descripción'), blank=True)
    capacity = models.PositiveIntegerField(_('capacidad'), default=0)
    allow_self_enrollment = models.BooleanField(_('autoinscripción'), default=True)
    is_active = models.BooleanField(_('activa'), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='managed_cohorts',
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('cohorte')
        verbose_name_plural = _('cohortes')
        ordering = ['title', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'title'],
                name='lms_unique_cohort_title',
            ),
        ]

    def clean(self):
        super().clean()
        if self.slug and not self.slug.strip():
            self.slug = ''

    def __str__(self):
        return f'{self.course}: {self.title}'


class Section(models.Model):
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_sections',
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sections',
    )
    title = models.CharField(_('nombre'), max_length=160)
    position = models.PositiveIntegerField(_('posición'), default=0)
    capacity = models.PositiveIntegerField(_('capacidad máxima'), default=0)
    description = models.TextField(_('descripción'), blank=True)
    allow_self_enrollment = models.BooleanField(_('autoinscripción'), default=True)
    is_active = models.BooleanField(_('activa'), default=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('sección LMS')
        verbose_name_plural = _('secciones LMS')
        ordering = ['position', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'title'],
                name='lms_unique_section_title',
            ),
        ]

    def clean(self):
        super().clean()
        if self.cohort_id and self.cohort.course_id != self.course_id:
            raise ValidationError({
                'cohort': _('La cohorte debe pertenecer al mismo curso que la sección.'),
            })

    def __str__(self):
        return f'{self.course}: {self.title}'


CourseSection = Section


class CourseMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'owner', _('Propietario')
        TEACHER = 'teacher', _('Profesor')
        ASSISTANT = 'assistant', _('Asistente')
        STUDENT = 'student', _('Alumno')
        OBSERVER = 'observer', _('Observador')

    class Status(models.TextChoices):
        INVITED = 'invited', _('Invitado')
        ACTIVE = 'active', _('Activo')
        SUSPENDED = 'suspended', _('Suspendido')
        COMPLETED = 'completed', _('Completado')
        DROPPED = 'dropped', _('Baja')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='course_memberships',
    )
    section = models.ForeignKey(
        Section,
        verbose_name=_('sección'),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='memberships',
    )
    role = models.CharField(
        _('capacidad en el curso'),
        max_length=12,
        choices=Role.choices,
        default=Role.STUDENT,
    )
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    invited_at = models.DateTimeField(_('invitado'), null=True, blank=True)
    joined_at = models.DateTimeField(_('incorporado'), null=True, blank=True)
    completed_at = models.DateTimeField(_('completado'), null=True, blank=True)
    dropped_at = models.DateTimeField(_('baja'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('pertenencia al curso')
        verbose_name_plural = _('pertenencias a cursos')
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'user'],
                name='lms_unique_course_member',
            ),
            models.CheckConstraint(
                check=models.Q(role__in=['owner', 'teacher', 'assistant', 'student', 'observer']),
                name='lms_member_valid_role',
            ),
            models.CheckConstraint(
                check=models.Q(status__in=['invited', 'active', 'suspended', 'completed', 'dropped']),
                name='lms_member_valid_status',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'status'], name='lms_member_user_status'),
            models.Index(fields=['course', 'role', 'status'], name='lms_member_course_role'),
        ]
        permissions = [
            ('publish_course', _('Puede publicar cursos LMS')),
            ('manage_platform', _('Puede administrar la plataforma LMS')),
        ]

    def clean(self):
        super().clean()
        if self.section_id and self.course_id:
            section_course_id = Section.objects.filter(pk=self.section_id).values_list(
                'course_id',
                flat=True,
            ).first()
            if section_course_id != self.course_id:
                raise ValidationError({
                    'section': _('La sección debe pertenecer al mismo curso.')
                })

    def __str__(self):
        return f'{self.user} — {self.course} ({self.get_role_display()})'


class LearningEvent(models.Model):
    class EventType(models.TextChoices):
        VIEW = 'view', _('Vista')
        START = 'start', _('Inicio')
        COMPLETE = 'complete', _('Completado')
        ATTEMPT = 'attempt', _('Intento')
        QUIZ_SUBMIT = 'quiz_submit', _('Entrega de prueba')
        RESOURCE_VIEW = 'resource_view', _('Visualización de recurso')
        FEEDBACK_VIEW = 'feedback_view', _('Consulta de feedback')

    class ObjectType(models.TextChoices):
        MODULE = 'module', _('Módulo')
        TOPIC = 'topic', _('Tema')
        ACTIVITY = 'activity', _('Actividad')
        PAGE = 'page', _('Página')
        RESOURCE = 'resource', _('Recurso')
        QUIZ = 'quiz', _('Quiz')

    ALLOWED_CONTEXT_KEYS = {
        'module_id',
        'topic_id',
        'activity_id',
        'page_id',
        'resource_id',
        'quiz_id',
        'duration_seconds',
        'status',
        'score_band',
        'source',
    }

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_learning_events',
        verbose_name=_('usuario'),
    )
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_learning_events',
        verbose_name=_('curso'),
    )
    object_type = models.CharField(
        _('tipo de objeto'),
        max_length=16,
        choices=ObjectType.choices,
        default=ObjectType.MODULE,
    )
    object_id = models.PositiveIntegerField(_('identificador del objeto'), null=True, blank=True)
    event_type = models.CharField(
        _('tipo de evento'),
        max_length=18,
        choices=EventType.choices,
        default=EventType.VIEW,
    )
    context = models.JSONField(_('contexto mínimo'), default=dict, blank=True)
    created_at = models.DateTimeField(_('fecha'), auto_now_add=True)

    class Meta:
        verbose_name = _('evento de aprendizaje')
        verbose_name_plural = _('eventos de aprendizaje')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['course', 'event_type', 'created_at'], name='lms_event_course_type_time'),
            models.Index(fields=['user', 'course', 'created_at'], name='lms_event_user_course_time'),
        ]

    def clean(self):
        super().clean()
        if not isinstance(self.context, dict):
            raise ValidationError({'context': _('El contexto debe ser un objeto JSON válido.')})

        unexpected = sorted(set(self.context) - self.ALLOWED_CONTEXT_KEYS)
        if unexpected:
            raise ValidationError({
                'context': _('El contexto solo puede incluir claves mínimas de seguimiento: %(keys)s.') % {
                    'keys': ', '.join(sorted(self.ALLOWED_CONTEXT_KEYS)),
                }
            })

        if 'duration_seconds' in self.context and not isinstance(self.context['duration_seconds'], int):
            raise ValidationError({'context': _('duration_seconds debe ser un entero.')} )

        if 'score_band' in self.context and self.context['score_band'] not in {'low', 'mid', 'high', 'pass', 'fail'}:
            raise ValidationError({'context': _('score_band solo admite low, mid, high, pass o fail.')})

        if self.object_id is not None and self.object_type in {self.ObjectType.MODULE, self.ObjectType.TOPIC, self.ObjectType.ACTIVITY, self.ObjectType.PAGE, self.ObjectType.RESOURCE, self.ObjectType.QUIZ}:
            key = {
                self.ObjectType.MODULE: 'module_id',
                self.ObjectType.TOPIC: 'topic_id',
                self.ObjectType.ACTIVITY: 'activity_id',
                self.ObjectType.PAGE: 'page_id',
                self.ObjectType.RESOURCE: 'resource_id',
                self.ObjectType.QUIZ: 'quiz_id',
            }.get(self.object_type)
            if key and self.context.get(key) not in {None, self.object_id}:
                self.context[key] = self.object_id

    def __str__(self):
        return f'{self.user} · {self.course} · {self.get_event_type_display()}'


class ModuleProgress(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = 'not_started', _('Sin iniciar')
        IN_PROGRESS = 'in_progress', _('En progreso')
        COMPLETED = 'completed', _('Completado')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_module_progress',
        verbose_name=_('usuario'),
    )
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_module_progress',
        verbose_name=_('curso'),
    )
    module = models.ForeignKey(
        'CourseModule',
        on_delete=models.CASCADE,
        related_name='student_progress',
        verbose_name=_('módulo'),
    )
    completed_items = models.PositiveIntegerField(_('elementos completados'), default=0)
    total_items = models.PositiveIntegerField(_('total de elementos'), default=0)
    percentage = models.PositiveSmallIntegerField(_('porcentaje'), default=0)
    status = models.CharField(
        _('estado'),
        max_length=16,
        choices=Status.choices,
        default=Status.NOT_STARTED,
    )
    last_activity_at = models.DateTimeField(_('última actividad'), null=True, blank=True)
    completed_at = models.DateTimeField(_('completado en'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('progreso del módulo')
        verbose_name_plural = _('progresos del módulo')
        unique_together = ('user', 'course', 'module')
        ordering = ['-updated_at']

    def clean(self):
        super().clean()
        if self.total_items and self.completed_items > self.total_items:
            raise ValidationError({'completed_items': _('No puede superar el número total de elementos.')})
        if self.total_items:
            self.percentage = min(100, int(round((self.completed_items / self.total_items) * 100)))
        else:
            self.percentage = 0
        if self.completed_items >= self.total_items and self.total_items:
            self.status = self.Status.COMPLETED
            if not self.completed_at:
                self.completed_at = timezone.now()
        elif self.completed_items > 0:
            self.status = self.Status.IN_PROGRESS
        else:
            self.status = self.Status.NOT_STARTED

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.user} · {self.module} · {self.percentage}%'


class LearningProgress(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_learning_progress',
        verbose_name=_('usuario'),
    )
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_learning_progress',
        verbose_name=_('curso'),
    )
    modules_completed = models.PositiveIntegerField(_('módulos completados'), default=0)
    total_modules = models.PositiveIntegerField(_('total de módulos'), default=0)
    completion_percentage = models.PositiveSmallIntegerField(_('porcentaje de finalización'), default=0)
    last_activity_at = models.DateTimeField(_('última actividad'), null=True, blank=True)
    computed_at = models.DateTimeField(_('calculado en'), auto_now=True)

    class Meta:
        verbose_name = _('progreso de aprendizaje')
        verbose_name_plural = _('progresos de aprendizaje')
        unique_together = ('user', 'course')
        ordering = ['-computed_at']

    def clean(self):
        super().clean()
        if self.total_modules:
            self.completion_percentage = min(100, int(round((self.modules_completed / self.total_modules) * 100)))
        else:
            self.completion_percentage = 0

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.user} · {self.course} · {self.completion_percentage}%'


class CompetencyFramework(models.Model):
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_competency_frameworks',
        verbose_name=_('curso'),
        null=True,
        blank=True,
    )
    name = models.CharField(_('nombre del marco'), max_length=180)
    code = models.CharField(_('código del marco'), max_length=60, blank=True, default='')
    description = models.TextField(_('descripción'), blank=True, default='')
    version = models.CharField(_('versión'), max_length=20, default='1.0')
    is_active = models.BooleanField(_('activo'), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lms_competency_frameworks',
        verbose_name=_('creado por'),
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('marco de competencias')
        verbose_name_plural = _('marcos de competencias')
        ordering = ['code', 'name', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['course', 'code'], name='lms_unique_competency_framework_code_per_course'),
        ]

    def __str__(self):
        return self.name or self.code or 'Marco de competencias'


class Competency(models.Model):
    framework = models.ForeignKey(
        CompetencyFramework,
        on_delete=models.CASCADE,
        related_name='competencies',
        verbose_name=_('marco de competencias'),
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='children',
        verbose_name=_('competencia padre'),
    )
    code = models.CharField(_('código'), max_length=80)
    title = models.CharField(_('título'), max_length=200)
    description = models.TextField(_('descripción'), blank=True, default='')
    criterion = models.TextField(_('criterio de adquisición'), blank=True, default='')
    curriculum_unit = models.CharField(_('unidad curricular'), max_length=200, blank=True, default='')
    mastery_levels = models.JSONField(_('niveles de dominio'), default=list, blank=True)
    mastery_threshold = models.DecimalField(_('umbral de dominio'), max_digits=5, decimal_places=2, default=0.75)
    version = models.CharField(_('versión'), max_length=20, default='1.0')
    is_active = models.BooleanField(_('activa'), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lms_competencies',
        verbose_name=_('creado por'),
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('competencia')
        verbose_name_plural = _('competencias')
        ordering = ['code', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['framework', 'code'], name='lms_unique_competency_code_per_framework'),
        ]

    def clean(self):
        super().clean()
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError({'parent': _('La competencia no puede ser padre de sí misma.')})
        if self.parent_id and self.parent.framework_id != self.framework_id:
            raise ValidationError({'parent': _('La competencia padre debe pertenecer al mismo marco.')})
        if not isinstance(self.mastery_levels, list):
            raise ValidationError({'mastery_levels': _('Los niveles de dominio deben ser un array JSON válido.')})
        if self.mastery_threshold < 0 or self.mastery_threshold > 1:
            raise ValidationError({'mastery_threshold': _('El umbral debe estar entre 0 y 1.')})

    def get_level_for_score(self, score):
        if score is None:
            return 'not_started'
        normalized = Decimal(str(score))
        levels = sorted(
            (entry for entry in self.mastery_levels if isinstance(entry, dict)),
            key=lambda entry: Decimal(str(entry.get('min_score', 0))),
        )
        if not levels:
            return 'not_started'
        selected = None
        for entry in levels:
            threshold = Decimal(str(entry.get('min_score', 0)))
            if normalized >= threshold:
                selected = str(entry.get('label', 'Básico')).strip()
        if selected is None:
            return 'not_started'
        return slugify(selected).replace('-', '_') or 'not_started'

    def __str__(self):
        return f'{self.code}: {self.title}'


class CourseCompetency(models.Model):
    class CoverageStatus(models.TextChoices):
        UNTRACKED = 'untracked', _('Sin cobertura')
        PARTIAL = 'partial', _('Parcial')
        ADEQUATE = 'adequate', _('Adecuada')
        ALERT = 'alert', _('Alerta')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_course_competencies',
        verbose_name=_('curso'),
    )
    competency = models.ForeignKey(
        Competency,
        on_delete=models.CASCADE,
        related_name='course_links',
        verbose_name=_('competencia'),
    )
    module = models.ForeignKey(
        'CourseModule',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='course_competencies',
        verbose_name=_('módulo'),
    )
    assignment = models.ForeignKey(
        'Assignment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='course_competencies',
        verbose_name=_('tarea'),
    )
    quiz = models.ForeignKey(
        'Quiz',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='course_competencies',
        verbose_name=_('cuestionario'),
    )
    rubric = models.ForeignKey(
        'Rubric',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='course_competencies',
        verbose_name=_('rúbrica'),
    )
    target_level = models.CharField(_('nivel objetivo'), max_length=24, default='competent')
    criterion = models.TextField(_('criterio de evaluación'), blank=True, default='')
    coverage_status = models.CharField(
        _('estado de cobertura'),
        max_length=12,
        choices=CoverageStatus.choices,
        default=CoverageStatus.UNTRACKED,
    )
    is_required = models.BooleanField(_('obligatoria'), default=True)
    version = models.CharField(_('versión vinculada'), max_length=20, default='1.0')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lms_course_competencies',
        verbose_name=_('creado por'),
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('competencia del curso')
        verbose_name_plural = _('competencias del curso')
        ordering = ['competency__code', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['course', 'competency'], name='lms_unique_course_competency'),
        ]

    def clean(self):
        super().clean()
        if self.module_id and self.module.course_id != self.course_id:
            raise ValidationError({'module': _('El módulo debe pertenecer al mismo curso.')})
        if self.assignment_id and self.assignment.course_id != self.course_id:
            raise ValidationError({'assignment': _('La tarea debe pertenecer al mismo curso.')})
        if self.quiz_id and self.quiz.course_id != self.course_id:
            raise ValidationError({'quiz': _('El quiz debe pertenecer al mismo curso.')})
        if self.rubric_id and self.rubric.course_id != self.course_id:
            raise ValidationError({'rubric': _('La rúbrica debe pertenecer al mismo curso.')})
        if self.competency_id and self.competency.framework_id and self.competency.framework.course_id and self.competency.framework.course_id != self.course_id:
            raise ValidationError({'competency': _('La competencia debe estar vinculada al mismo curso.')})

    def __str__(self):
        return f'{self.course}: {self.competency}'


class Evidence(models.Model):
    class SourceType(models.TextChoices):
        PAGE = 'page', _('Página')
        ACTIVITY = 'activity', _('Actividad')
        QUIZ = 'quiz', _('Cuestionario')
        RUBRIC = 'rubric', _('Rúbrica')
        PROGRESS = 'progress', _('Progreso')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_evidence',
        verbose_name=_('curso'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_evidence',
        verbose_name=_('usuario'),
    )
    competency = models.ForeignKey(
        Competency,
        on_delete=models.CASCADE,
        related_name='evidence',
        verbose_name=_('competencia'),
    )
    source_type = models.CharField(
        _('tipo de origen'),
        max_length=12,
        choices=SourceType.choices,
        default=SourceType.ACTIVITY,
    )
    source_label = models.CharField(_('etiqueta del origen'), max_length=180, blank=True, default='')
    source_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_competency_evidence',
    )
    source_object_id = models.PositiveIntegerField(_('identificador del origen'), null=True, blank=True)
    source_object = GenericForeignKey('source_content_type', 'source_object_id')
    score = models.DecimalField(_('puntuación evidenciada'), max_digits=5, decimal_places=2, default=0)
    max_score = models.DecimalField(_('máxima puntuación'), max_digits=5, decimal_places=2, default=1)
    description = models.TextField(_('descripción de la evidencia'), blank=True, default='')
    evidence_date = models.DateTimeField(_('fecha de evidencia'), null=True, blank=True)
    is_primary = models.BooleanField(_('es la evidencia principal'), default=True)
    is_verified = models.BooleanField(_('verificada'), default=False)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('evidencia de competencia')
        verbose_name_plural = _('evidencias de competencia')
        ordering = ['-evidence_date', '-created_at']

    def clean(self):
        super().clean()
        if self.score < 0:
            raise ValidationError({'score': _('La evidencia no puede ser negativa.')})
        if self.max_score <= 0:
            raise ValidationError({'max_score': _('La máxima puntuación debe ser positiva.')})
        if self.source_type == self.SourceType.PROGRESS and self.score > 1:
            raise ValidationError({'score': _('La evidencia de progreso debe estar normalizada a 0..1.')})
        if self.competency_id and self.competency.framework_id and self.competency.framework.course_id and self.competency.framework.course_id != self.course_id:
            raise ValidationError({'course': _('La evidencia debe pertenecer al mismo curso que la competencia.')})

    def __str__(self):
        return f'{self.user} → {self.competency} ({self.score}/{self.max_score})'


class CompetencyAchievement(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', _('En progreso')
        ACHIEVED = 'achieved', _('Adquirida')
        NOT_MET = 'not_met', _('No alcanzada')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_competency_achievements',
        verbose_name=_('curso'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_competency_achievements',
        verbose_name=_('usuario'),
    )
    competency = models.ForeignKey(
        Competency,
        on_delete=models.CASCADE,
        related_name='achievements',
        verbose_name=_('competencia'),
    )
    status = models.CharField(
        _('estado'),
        max_length=16,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
    )
    score = models.DecimalField(_('puntuación actual'), max_digits=5, decimal_places=2, default=0)
    mastery_level = models.CharField(_('nivel actual'), max_length=32, default='not_started')
    evidence_summary = models.TextField(_('resumen de evidencias'), blank=True, default='')
    is_achieved = models.BooleanField(_('adquirida'), default=False)
    achieved_at = models.DateTimeField(_('adquirida en'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('logro de competencia')
        verbose_name_plural = _('logros de competencia')
        ordering = ['-updated_at', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['course', 'user', 'competency'], name='lms_unique_user_competency_achievement'),
        ]

    def clean(self):
        super().clean()
        if self.competency_id and self.competency.framework_id and self.competency.framework.course_id and self.competency.framework.course_id != self.course_id:
            raise ValidationError({'course': _('La competencia y el logro deben pertenecer al mismo curso.')})

    def refresh_from_evidence(self):
        evidences = Evidence.objects.filter(
            course=self.course,
            user=self.user,
            competency=self.competency,
        )
        if not evidences.exists():
            self.score = Decimal('0')
            self.mastery_level = 'not_started'
            self.status = self.Status.NOT_MET
            self.is_achieved = False
            self.evidence_summary = 'Sin evidencia suficiente.'
            self.save(update_fields=['score', 'mastery_level', 'status', 'evidence_summary', 'is_achieved', 'updated_at'])
            return self

        normalized_scores = [
            (item.score / item.max_score) if item.max_score else Decimal('0')
            for item in evidences
        ]
        self.score = max(Decimal('0'), min(Decimal('1'), max(normalized_scores)))
        self.mastery_level = self.competency.get_level_for_score(self.score)
        self.is_achieved = self.score >= self.competency.mastery_threshold
        self.status = self.Status.ACHIEVED if self.is_achieved else self.Status.IN_PROGRESS if self.score > 0 else self.Status.NOT_MET
        if self.is_achieved and not self.achieved_at:
            self.achieved_at = timezone.now()
        elif not self.is_achieved:
            self.achieved_at = None
        self.evidence_summary = (
            f'{self.mastery_level} ({evidences.count()} evidencias; mejor {self.score.quantize(Decimal("0.01"))})'
        )
        self.save(update_fields=['score', 'mastery_level', 'status', 'evidence_summary', 'is_achieved', 'achieved_at', 'updated_at'])
        return self

    def __str__(self):
        return f'{self.user} → {self.competency} ({self.mastery_level})'


class Badge(models.Model):
    module = models.ForeignKey(
        'courses.Tema', on_delete=models.CASCADE, null=True, blank=True,
        related_name='badges', verbose_name=_('tema'),
    )
    image = models.ImageField(_('imagen de la insignia'), upload_to='lms/badges/', blank=True)
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_badges',
        verbose_name=_('curso'),
        null=True,
        blank=True,
    )
    slug = models.SlugField(_('slug'), max_length=120, unique=True, blank=True)
    name = models.CharField(_('nombre'), max_length=160)
    description = models.TextField(_('descripción'), blank=True, default='')
    icon = models.CharField(_('icono'), max_length=80, default='medal')
    color = models.CharField(_('color'), max_length=16, default='#1f6feb')
    is_active = models.BooleanField(_('activo'), default=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('insignia')
        verbose_name_plural = _('insignias')
        ordering = ['course_id', 'name', 'pk']

    def clean(self):
        super().clean()
        if self.module_id and self.module.curso_id != self.course_id:
            raise ValidationError({'module': _('El tema debe pertenecer al curso de la insignia.')})

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name or 'insignia')[:100]
            self.slug = base
            suffix = 2
            while Badge.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f'{base}-{suffix}'
                suffix += 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name or self.slug


class AchievementRule(models.Model):
    class RuleType(models.TextChoices):
        MODULE_COMPLETION = 'module_completion', _('Módulos completados')
        FINAL_QUIZ_PASS = 'final_quiz_pass', _('Quiz final superado')
        ASSIGNMENT_SUBMITTED = 'assignment_submitted', _('Entrega de práctica')
        COMPETENCY_MASTERY = 'competency_mastery', _('Dominio de competencias')
        ATTENDANCE = 'attendance', _('Asistencia')

    badge = models.ForeignKey(
        Badge,
        on_delete=models.CASCADE,
        related_name='rules',
        verbose_name=_('insignia'),
    )
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_achievement_rules',
        verbose_name=_('curso'),
        null=True,
        blank=True,
    )
    rule_type = models.CharField(
        _('tipo de regla'),
        max_length=32,
        choices=RuleType.choices,
        default=RuleType.MODULE_COMPLETION,
    )
    name = models.CharField(_('nombre'), max_length=180, blank=True, default='')
    description = models.TextField(_('descripción'), blank=True, default='')
    threshold = models.PositiveIntegerField(_('umbral'), default=1)
    is_active = models.BooleanField(_('activa'), default=True)
    metadata = models.JSONField(_('metadatos'), default=dict, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('regla de logro')
        verbose_name_plural = _('reglas de logro')
        ordering = ['badge_id', 'rule_type', 'pk']

    def clean(self):
        super().clean()
        if self.threshold < 1:
            raise ValidationError({'threshold': _('El umbral debe ser positivo.')})

    def matches(self, user, course, *, metrics=None):
        metrics = metrics or {}
        if self.rule_type == self.RuleType.MODULE_COMPLETION:
            return bool(metrics.get('module_completion'))
        if self.rule_type == self.RuleType.FINAL_QUIZ_PASS:
            return bool(metrics.get('final_quiz_passed'))
        if self.rule_type == self.RuleType.ASSIGNMENT_SUBMITTED:
            return bool(metrics.get('assignment_submitted'))
        if self.rule_type == self.RuleType.COMPETENCY_MASTERY:
            return bool(metrics.get('competency_mastery'))
        if self.rule_type == self.RuleType.ATTENDANCE:
            return bool(metrics.get('attendance_recorded'))
        return False

    def __str__(self):
        return self.name or self.get_rule_type_display()


class UserBadge(models.Model):
    class Status(models.TextChoices):
        AWARDED = 'awarded', _('Otorgada')
        REVOKED = 'revoked', _('Revocada')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_user_badges',
        verbose_name=_('usuario'),
    )
    badge = models.ForeignKey(
        Badge,
        on_delete=models.CASCADE,
        related_name='awards',
        verbose_name=_('insignia'),
    )
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_user_badges',
        verbose_name=_('curso'),
        null=True,
        blank=True,
    )
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.AWARDED,
    )
    reason = models.TextField(_('razón'), blank=True, default='')
    awarded_at = models.DateTimeField(_('otorgada en'), auto_now_add=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('insignia del usuario')
        verbose_name_plural = _('insignias del usuario')
        constraints = [
            models.UniqueConstraint(fields=['user', 'badge', 'course'], name='lms_unique_user_badge_per_course'),
        ]
        ordering = ['-awarded_at', '-created_at']

    def revoke(self, *, reason=''):
        self.status = self.Status.REVOKED
        self.reason = reason or self.reason or _('Revocada por auditoría.')
        self.save(update_fields=['status', 'reason', 'updated_at'])
        return self

    def __str__(self):
        return f'{self.user} → {self.badge}'


class CourseCertificate(models.Model):
    class Status(models.TextChoices):
        ISSUED = 'issued', _('Emitido')
        REVOKED = 'revoked', _('Revocado')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_course_certificates',
        verbose_name=_('usuario'),
    )
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_course_certificates',
        verbose_name=_('curso'),
    )
    certificate_code = models.UUIDField(_('código'), default=uuid4, unique=True, editable=False)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issued_lms_course_certificates',
        verbose_name=_('emitido por'),
    )
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.ISSUED,
    )
    issued_at = models.DateTimeField(_('emitido en'), auto_now_add=True)
    revoked_at = models.DateTimeField(_('revocado en'), null=True, blank=True)
    revocation_reason = models.TextField(_('motivo de revocación'), blank=True, default='')
    public_url = models.URLField(_('URL pública'), blank=True, default='')
    pdf_file = models.FileField(_('PDF del certificado'), upload_to='certificados/lms/', null=True, blank=True)
    metadata = models.JSONField(_('metadatos públicos'), default=dict, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('certificado de curso')
        verbose_name_plural = _('certificados de curso')
        constraints = [
            models.UniqueConstraint(fields=['user', 'course'], name='lms_unique_course_certificate_per_user_course'),
        ]
        ordering = ['-issued_at']

    def save(self, *args, **kwargs):
        if not self.public_url:
            self.public_url = self.get_validation_url()
        super().save(*args, **kwargs)

    @property
    def is_valid(self):
        return self.status == self.Status.ISSUED

    def get_validation_url(self):
        site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
        return f'{site_url.rstrip("/")}/certificaciones/verificar/{self.certificate_code}/'

    def revoke(self, *, reason=''):
        self.status = self.Status.REVOKED
        self.revoked_at = timezone.now()
        self.revocation_reason = reason or self.revocation_reason or _('Certificado revocado por auditoría.')
        self.save(update_fields=['status', 'revoked_at', 'revocation_reason', 'updated_at'])
        return self

    def __str__(self):
        return f'{self.user} — {self.course} ({self.status})'


class CertificateVerification(models.Model):
    class Status(models.TextChoices):
        VALID = 'valid', _('Válido')
        INVALID = 'invalid', _('Inválido')
        REVOKED = 'revoked', _('Revocado')

    certificate = models.ForeignKey(
        CourseCertificate,
        on_delete=models.CASCADE,
        related_name='verifications',
        verbose_name=_('certificado'),
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_certificate_verifications',
        verbose_name=_('verificado por'),
    )
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.VALID,
    )
    notes = models.TextField(_('notas'), blank=True, default='')
    request_ip = models.GenericIPAddressField(_('IP'), null=True, blank=True)
    user_agent = models.CharField(_('agente de usuario'), max_length=200, blank=True, default='')
    verified_at = models.DateTimeField(_('verificado en'), auto_now_add=True)

    class Meta:
        verbose_name = _('verificación de certificado')
        verbose_name_plural = _('verificaciones de certificados')
        ordering = ['-verified_at']

    def __str__(self):
        return f'{self.certificate} · {self.status}'


class CourseGroup(models.Model):
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_course_groups',
        verbose_name=_('curso'),
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='groups',
        verbose_name=_('sección'),
    )
    title = models.CharField(_('nombre del grupo'), max_length=180)
    slug = models.SlugField(_('slug'), max_length=220, blank=True)
    description = models.TextField(_('descripción'), blank=True, default='')
    max_members = models.PositiveIntegerField(_('máximo de miembros'), default=5)
    allow_self_management = models.BooleanField(_('permite gestión del grupo por alumnos'), default=True)
    is_active = models.BooleanField(_('activo'), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lms_course_groups',
        verbose_name=_('creado por'),
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('grupo de curso')
        verbose_name_plural = _('grupos de curso')
        ordering = ['title', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['course', 'slug'], name='lms_unique_course_group_slug'),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title or 'grupo')
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.section_id and self.section.course_id != self.course_id:
            raise ValidationError({'section': _('La sección debe pertenecer al mismo curso.')})
        if self.max_members < 1:
            raise ValidationError({'max_members': _('El grupo debe permitir al menos un miembro.')})

    @property
    def member_count(self):
        return self.members.filter(status=GroupMembership.Status.ACTIVE).count()

    def is_member(self, user):
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        return self.members.filter(user=user, status=GroupMembership.Status.ACTIVE).exists()

    def can_accept_member(self, user):
        if self.member_count >= self.max_members:
            return False
        if not CourseMembership.objects.filter(
            course=self.course,
            user=user,
            status=CourseMembership.Status.ACTIVE,
        ).exists():
            return False
        if self.section_id:
            return CourseMembership.objects.filter(
                course=self.course,
                user=user,
                status=CourseMembership.Status.ACTIVE,
                section_id=self.section_id,
            ).exists()
        return True

    def __str__(self):
        return f'{self.course}: {self.title}'


class GroupMembership(models.Model):
    class Role(models.TextChoices):
        LEADER = 'leader', _('Líder')
        MEMBER = 'member', _('Miembro')
        OBSERVER = 'observer', _('Observador')

    class Status(models.TextChoices):
        ACTIVE = 'active', _('Activo')
        PENDING = 'pending', _('Pendiente')
        REMOVED = 'removed', _('Eliminado')

    group = models.ForeignKey(
        CourseGroup,
        on_delete=models.CASCADE,
        related_name='members',
        verbose_name=_('grupo'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_group_memberships',
        verbose_name=_('usuario'),
    )
    role = models.CharField(
        _('rol dentro del grupo'),
        max_length=12,
        choices=Role.choices,
        default=Role.MEMBER,
    )
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    joined_at = models.DateTimeField(_('incorporado en'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('miembro del grupo')
        verbose_name_plural = _('miembros del grupo')
        constraints = [
            models.UniqueConstraint(fields=['group', 'user'], name='lms_unique_group_member'),
        ]

    def clean(self):
        super().clean()
        if self.group_id and self.user_id:
            if not CourseMembership.objects.filter(
                course=self.group.course,
                user=self.user,
                status=CourseMembership.Status.ACTIVE,
            ).exists():
                raise ValidationError({'user': _('El usuario debe estar matriculado en el curso del grupo.')})
            if self.group.section_id:
                membership = CourseMembership.objects.filter(
                    course=self.group.course,
                    user=self.user,
                    status=CourseMembership.Status.ACTIVE,
                ).first()
                if membership and membership.section_id and membership.section_id != self.group.section_id:
                    raise ValidationError({'group': _('El alumno debe pertenecer a la misma sección que el grupo.')})

            active_group_memberships = GroupMembership.objects.filter(
                user=self.user,
                status=self.Status.ACTIVE,
                group__course=self.group.course,
            ).exclude(pk=self.pk)
            if active_group_memberships.exists():
                raise ValidationError({'user': _('El usuario ya pertenece a otro grupo activo del mismo curso.')})

            if self.group.member_count >= self.group.max_members and self.pk is None:
                raise ValidationError({'group': _('El grupo ya alcanzó su máximo de miembros.')})
        if self.role == self.Role.LEADER and self.status == self.Status.REMOVED:
            raise ValidationError({'role': _('Un líder no puede quedar eliminado.')})

    def __str__(self):
        return f'{self.user} → {self.group}'


class GroupProject(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        ACTIVE = 'active', _('Activo')
        SUBMITTED = 'submitted', _('Entregado')
        CLOSED = 'closed', _('Cerrado')

    assignment = models.ForeignKey(
        'Assignment',
        on_delete=models.CASCADE,
        related_name='group_projects',
        verbose_name=_('tarea'),
    )
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_group_projects',
        verbose_name=_('curso'),
    )
    group = models.ForeignKey(
        CourseGroup,
        on_delete=models.CASCADE,
        related_name='projects',
        verbose_name=_('grupo'),
    )
    title = models.CharField(_('título del proyecto'), max_length=200)
    description = models.TextField(_('descripción'), blank=True, default='')
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lms_group_projects',
        verbose_name=_('creado por'),
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('proyecto de grupo')
        verbose_name_plural = _('proyectos de grupo')
        ordering = ['created_at', 'pk']

    def clean(self):
        super().clean()
        if self.assignment_id and self.assignment.course_id != self.course_id:
            raise ValidationError({'assignment': _('La tarea debe pertenecer al mismo curso.')})
        if self.group_id and self.group.course_id != self.course_id:
            raise ValidationError({'group': _('El grupo debe pertenecer al mismo curso.')})

    def __str__(self):
        return f'{self.group}: {self.title}'


class GroupSubmission(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        SUBMITTED = 'submitted', _('Entregada')
        LATE = 'late', _('Tarde')
        GRADED = 'graded', _('Calificada')
        RETURNED = 'returned', _('Devuelta')

    project = models.ForeignKey(
        GroupProject,
        on_delete=models.CASCADE,
        related_name='submissions',
        verbose_name=_('proyecto'),
    )
    group = models.ForeignKey(
        CourseGroup,
        on_delete=models.CASCADE,
        related_name='submissions',
        verbose_name=_('grupo'),
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_group_submissions',
        verbose_name=_('entregado por'),
    )
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    text_answer = models.TextField(_('respuesta textual'), blank=True, default='')
    file_url = models.URLField(_('URL de entrega'), blank=True, default='')
    grade = models.DecimalField(_('nota'), max_digits=8, decimal_places=2, default=0)
    feedback = models.TextField(_('retroalimentación'), blank=True, default='')
    feedback_published = models.BooleanField(_('feedback publicado'), default=False)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('entrega de grupo')
        verbose_name_plural = _('entregas de grupo')
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['project', 'group'], name='lms_unique_group_submission_per_project_group'),
        ]

    def clean(self):
        super().clean()
        if self.project_id and self.group_id and self.project.group_id != self.group_id:
            raise ValidationError({'group': _('La entrega debe pertenecer al mismo grupo del proyecto.')})
        if self.project_id and self.project.course_id and self.group_id and self.project.course_id != self.group.course_id:
            raise ValidationError({'group': _('El grupo del proyecto y la entrega deben compartir curso.')})
        if self.submitted_by_id:
            if not GroupMembership.objects.filter(
                group=self.group,
                user=self.submitted_by,
                status=GroupMembership.Status.ACTIVE,
            ).exists():
                raise ValidationError({'submitted_by': _('El usuario debe pertenecer al grupo que entrega.')})

    def is_visible_to_user(self, user):
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if user.pk == self.submitted_by_id:
            return True
        if CourseMembership.objects.filter(
            course=self.project.course,
            user=user,
            status=CourseMembership.Status.ACTIVE,
            role__in={CourseMembership.Role.OWNER, CourseMembership.Role.TEACHER, CourseMembership.Role.ASSISTANT},
        ).exists():
            return True
        return GroupMembership.objects.filter(
            group=self.group,
            user=user,
            status=GroupMembership.Status.ACTIVE,
        ).exists()

    def __str__(self):
        return f'{self.group}: {self.project}'


class FileAsset(models.Model):
    class Visibility(models.TextChoices):
        COURSE = 'course', _('Curso')
        STUDENTS = 'students', _('Alumnos')
        TEACHERS = 'teachers', _('Profesores')
        PRIVATE = 'private', _('Privado')
        PUBLIC = 'public', _('Público')

    class AccessPolicy(models.TextChoices):
        COURSE = 'course', _('Miembros del curso')
        TEACHERS = 'teachers', _('Solo profesores')
        PRIVATE = 'private', _('Privado')
        PUBLIC = 'public', _('Público')

    ALLOWED_EXTENSIONS = {
        '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.mp4', '.webm', '.mp3', '.wav',
        '.zip', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt', '.html', '.svg', '.ppt', '.pptx',
    }

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_files',
        verbose_name=_('curso'),
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='owned_lms_files',
        verbose_name=_('propietario'),
    )
    title = models.CharField(_('título'), max_length=200)
    slug = models.SlugField(_('slug'), max_length=220, blank=True)
    folder = models.CharField(_('carpeta lógica'), max_length=120, blank=True, default='general')
    file = models.FileField(
        _('archivo'),
        upload_to='lms/private/%Y/%m/%d/',
        storage=PrivateMediaStorage(),
        max_length=500,
    )
    file_name = models.CharField(_('nombre de archivo'), max_length=250, blank=True)
    mime_type = models.CharField(_('tipo MIME'), max_length=150, blank=True, default='')
    size = models.PositiveBigIntegerField(_('tamaño'), default=0)
    checksum = models.CharField(_('checksum SHA-256'), max_length=64, blank=True, default='')
    visibility = models.CharField(
        _('visibilidad'),
        max_length=12,
        choices=Visibility.choices,
        default=Visibility.COURSE,
    )
    access_policy = models.CharField(
        _('política de acceso'),
        max_length=12,
        choices=AccessPolicy.choices,
        default=AccessPolicy.COURSE,
    )
    allow_download = models.BooleanField(_('permite descarga'), default=True)
    allow_preview = models.BooleanField(_('permite vista previa'), default=True)
    is_active = models.BooleanField(_('activo'), default=True)
    metadata = models.JSONField(_('metadatos'), default=dict, blank=True)
    preview_url = models.URLField(_('vista previa'), blank=True, default='')
    version = models.PositiveIntegerField(_('versión'), default=1)
    is_latest = models.BooleanField(_('última versión'), default=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('archivo del curso')
        verbose_name_plural = _('archivos del curso')
        ordering = ['folder', 'title', 'pk']

    def __str__(self):
        return f'{self.course}: {self.title}'

    def clean(self):
        super().clean()
        if self.file:
            filename = (self.file_name or getattr(self.file, 'name', '') or '').lower()
            _, ext = os.path.splitext(filename)
            if ext and ext.lower() not in self.ALLOWED_EXTENSIONS:
                raise ValidationError({'file': _('Extensión no permitida para este repositorio de archivos.')})
            if not self.mime_type:
                self.mime_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title or 'archivo')
        if self.file and not self.file_name:
            self.file_name = os.path.basename(self.file.name)
        if self.file:
            original_name = self.file_name or os.path.basename(self.file.name)
            ext = os.path.splitext(original_name)[1].lower()
            if ext and ext not in self.ALLOWED_EXTENSIONS:
                raise ValidationError({'file': _('Extensión no permitida para este repositorio de archivos.')})
            self.file_name = original_name
            self.mime_type = self.mime_type or (mimetypes.guess_type(original_name)[0] or 'application/octet-stream')
            if self.size <= 0:
                try:
                    self.file.seek(0)
                    self.size = self.file.size
                except (AttributeError, ValueError):
                    self.size = 0
            if not self.checksum:
                try:
                    self.file.seek(0)
                    digest = hashlib.sha256(self.file.read()).hexdigest()
                    self.checksum = digest
                    self.file.seek(0)
                except (AttributeError, ValueError, OSError):
                    self.checksum = ''
        super().save(*args, **kwargs)

    def user_can_access(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.has_perm('lms.manage_platform'):
            return True
        if not self.is_active:
            return False
        from .policies import can_edit_course, is_course_member, is_course_teacher

        if self.visibility == self.Visibility.PUBLIC or self.access_policy == self.AccessPolicy.PUBLIC:
            return True
        if can_edit_course(user, self.course):
            return True
        if self.visibility == self.Visibility.TEACHERS or self.access_policy == self.AccessPolicy.TEACHERS:
            return is_course_teacher(user, self.course)
        if self.visibility == self.Visibility.PRIVATE or self.access_policy == self.AccessPolicy.PRIVATE:
            return (
                self.owner_id == user.pk
                or can_edit_course(user, self.course)
                or is_course_member(user, self.course)
            )
        if self.visibility == self.Visibility.STUDENTS:
            return is_course_member(user, self.course)
        return is_course_member(user, self.course)

    def create_expiring_link(self, *, created_by, expires_in=None, expires_at=None):
        if expires_in is None:
            expires_in = timedelta(hours=2)
        if expires_at is None:
            expires_at = timezone.now() + expires_in
        return FileAssetLink.objects.create(
            asset=self,
            created_by=created_by,
            expires_at=expires_at,
            token=uuid4().hex,
        )


class FileAssetLink(models.Model):
    asset = models.ForeignKey(FileAsset, on_delete=models.CASCADE, related_name='links')
    token = models.CharField(_('token'), max_length=64, unique=True, default=generate_asset_token)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_file_links',
        verbose_name=_('creado por'),
    )
    expires_at = models.DateTimeField(_('expira en'))
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    used_at = models.DateTimeField(_('usado'), null=True, blank=True)

    class Meta:
        verbose_name = _('enlace temporal de archivo')
        verbose_name_plural = _('enlaces temporales de archivo')
        ordering = ['-created_at']

    def is_valid(self):
        if not self.asset.is_active:
            return False
        return timezone.now() < self.expires_at

    def __str__(self):
        return f'{self.asset} ({self.token[:8]})'


class Page(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        PUBLISHED = 'published', _('Publicado')

    class Visibility(models.TextChoices):
        COURSE = 'course', _('Curso')
        STUDENTS = 'students', _('Estudiantes')
        TEACHERS = 'teachers', _('Profesores')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_pages',
        verbose_name=_('curso'),
    )
    module = models.ForeignKey(
        'courses.Tema',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_pages',
        verbose_name=_('módulo'),
    )
    title = models.CharField(_('título'), max_length=200)
    slug = models.SlugField(_('slug'), max_length=200, blank=True)
    content = models.TextField(_('contenido'), blank=True, default='')
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    visibility = models.CharField(
        _('visibilidad'),
        max_length=12,
        choices=Visibility.choices,
        default=Visibility.COURSE,
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_pages',
        verbose_name=_('autor'),
    )
    position = models.PositiveIntegerField(_('orden'), default=0)
    publish_at = models.DateTimeField(_('publicación programada'), null=True, blank=True)
    expires_at = models.DateTimeField(_('caducidad'), null=True, blank=True)
    seo_title = models.CharField(_('título SEO'), max_length=80, blank=True, default='')
    seo_description = models.CharField(_('descripción SEO'), max_length=160, blank=True, default='')
    seo_keywords = models.CharField(_('keywords SEO'), max_length=200, blank=True, default='')
    is_homepage = models.BooleanField(_('página de inicio'), default=False)
    allow_pdf_export = models.BooleanField(_('exportación PDF'), default=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)
    current_revision = models.ForeignKey(
        'PageRevision',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        verbose_name = _('página del curso')
        verbose_name_plural = _('páginas del curso')
        ordering = ['position', 'title']
        constraints = [
            models.UniqueConstraint(fields=['course', 'slug'], name='lms_unique_page_slug_per_course'),
        ]

    def clean(self):
        super().clean()
        content = getattr(self, 'learning_content', None) if self.pk else None
        if content and self.module_id != content.module_id:
            raise ValidationError({'module': _('Retira primero el recurso de su unidad formativa antes de cambiarlo de módulo.')})
        if self.slug and not self.slug.strip():
            self.slug = ''
        if self.publish_at and self.expires_at and self.expires_at < self.publish_at:
            raise ValidationError({'expires_at': _('La caducidad debe ser posterior a la programación.')})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title or 'pagina')
        self.content = sanitize_page_content(self.content)
        super().save(*args, **kwargs)
        if self.is_homepage:
            Page.objects.filter(course=self.course, is_homepage=True).exclude(pk=self.pk).update(is_homepage=False)

    def __str__(self):
        return f'{self.course}: {self.title}'

    @property
    def has_own_styles(self):
        return has_page_styles(self.content)

    def is_currently_published(self, *, now=None):
        now = now or timezone.now()
        if self.status != self.Status.PUBLISHED:
            return False
        publish_at = self.publish_at
        expires_at = self.expires_at
        if isinstance(publish_at, str):
            try:
                publish_at = datetime.fromisoformat(publish_at.replace('Z', '+00:00'))
            except (TypeError, ValueError):
                publish_at = None
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            except (TypeError, ValueError):
                expires_at = None
        if publish_at and publish_at > now:
            return False
        if expires_at and expires_at <= now:
            return False
        return True

    @classmethod
    def get_homepage_for_course(cls, course, *, now=None):
        now = now or timezone.now()
        homepage = cls.objects.filter(course=course, is_homepage=True).order_by('position', 'title').first()
        if homepage and homepage.is_currently_published(now=now):
            return homepage
        return cls.objects.filter(
            course=course,
            status=cls.Status.PUBLISHED,
            publish_at__lte=now,
        ).exclude(
            expires_at__isnull=False,
            expires_at__lte=now,
        ).order_by('position', 'title').first()

    def user_can_view(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.has_perm('lms.manage_platform'):
            return True
        from .module_progression import target_lock_reason
        if target_lock_reason(self, user):
            return False
        if self.course.activo and self.status == self.Status.PUBLISHED:
            from .policies import can_edit_course, is_course_member
            if can_edit_course(user, self.course):
                return True
            return is_course_member(user, self.course)
        from .policies import can_edit_course
        return can_edit_course(user, self.course)

    def create_revision(self, *, user, title=None, content=None, status=None, visibility=None, summary='', published_at=None):
        from django.db.models import Max

        title = title or self.title
        content = sanitize_page_content(content if content is not None else self.content)
        status = status or self.status
        visibility = visibility or self.visibility
        revision_number = (
            self.revisions.aggregate(max_number=Max('revision_number'))['max_number'] or 0
        ) + 1
        revision = PageRevision.objects.create(
            page=self,
            revision_number=revision_number,
            title=title,
            slug=self.slug,
            content=content,
            summary=summary,
            status=status,
            visibility=visibility,
            created_by=user,
            published_at=published_at or self.publish_at,
        )
        self.title = title
        self.slug = revision.slug
        self.content = content
        self.status = status
        self.visibility = visibility
        self.author = user or self.author
        if published_at is not None:
            self.publish_at = published_at
        self.current_revision = revision
        self.save(update_fields=['title', 'slug', 'content', 'status', 'visibility', 'author', 'publish_at', 'updated_at', 'current_revision'])
        return revision

    def restore_revision(self, revision, *, user):
        return self.create_revision(
            user=user,
            title=revision.title,
            content=revision.content,
            status=revision.status,
            visibility=revision.visibility,
            summary=f'Restaurada desde la versión {revision.revision_number}',
            published_at=revision.published_at,
        )


class PageRevision(models.Model):
    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name='revisions')
    revision_number = models.PositiveIntegerField(_('versión'), default=1)
    title = models.CharField(_('título'), max_length=200)
    slug = models.SlugField(_('slug'), max_length=200, blank=True)
    content = models.TextField(_('contenido'), blank=True, default='')
    summary = models.TextField(_('resumen'), blank=True, default='')
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Page.Status.choices,
        default=Page.Status.DRAFT,
    )
    visibility = models.CharField(
        _('visibilidad'),
        max_length=12,
        choices=Page.Visibility.choices,
        default=Page.Visibility.COURSE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_page_revisions',
    )
    published_at = models.DateTimeField(_('publicado en'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('versión de la página')
        verbose_name_plural = _('versiones de la página')
        ordering = ['-revision_number']
        constraints = [
            models.UniqueConstraint(fields=['page', 'revision_number'], name='lms_unique_page_revision_number'),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title or 'pagina')
        self.content = sanitize_page_content(self.content)
        super().save(*args, **kwargs)
        self.page.current_revision = self
        self.page.title = self.title
        self.page.slug = self.slug
        self.page.content = self.content
        self.page.status = self.status
        self.page.visibility = self.visibility
        self.page.author = self.created_by or self.page.author
        if self.published_at:
            self.page.publish_at = self.published_at
        self.page.save(update_fields=['title', 'slug', 'content', 'status', 'visibility', 'author', 'publish_at', 'updated_at', 'current_revision'])

    def __str__(self):
        return f'{self.page} v{self.revision_number}'


class Announcement(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        SCHEDULED = 'scheduled', _('Programado')
        PUBLISHED = 'published', _('Publicado')
        EXPIRED = 'expired', _('Caducado')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_announcements',
        verbose_name=_('curso'),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_announcements',
        verbose_name=_('autor'),
    )
    title = models.CharField(_('título'), max_length=200)
    slug = models.SlugField(_('slug'), max_length=220, blank=True)
    content = models.TextField(_('contenido'), blank=True, default='')
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    is_pinned = models.BooleanField(_('fijado'), default=False)
    publish_at = models.DateTimeField(_('publicado en'), null=True, blank=True)
    expire_at = models.DateTimeField(_('caduca en'), null=True, blank=True)
    allow_comments = models.BooleanField(_('comentarios permitidos'), default=True)
    allow_likes = models.BooleanField(_('me gusta permitidos'), default=True)
    requires_acknowledgement = models.BooleanField(_('requiere acuse de lectura'), default=False)
    has_audio = models.BooleanField(_('audio/podcast disponible'), default=False)
    audio_url = models.URLField(_('audio/podcast'), blank=True, default='')
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('anuncio del curso')
        verbose_name_plural = _('anuncios del curso')
        ordering = ['-is_pinned', '-publish_at', '-created_at']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title or 'anuncio')
        self.content = sanitize_html_content(self.content)
        if self.status == self.Status.PUBLISHED and not self.publish_at:
            self.publish_at = timezone.now()
        if self.status == self.Status.EXPIRED and not self.expire_at:
            self.expire_at = timezone.now()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.publish_at and self.expire_at and self.expire_at < self.publish_at:
            raise ValidationError({'expire_at': _('La fecha de caducidad debe ser posterior a la de publicación.')})

    def is_currently_published(self, *, now=None):
        now = now or timezone.now()
        if self.status == self.Status.DRAFT:
            return False
        if self.status == self.Status.EXPIRED:
            return False
        if self.publish_at and self.publish_at > now:
            return False
        if self.expire_at and self.expire_at <= now:
            return False
        return self.status in {self.Status.PUBLISHED, self.Status.SCHEDULED}

    def is_visible_to_user(self, user, *, now=None):
        from .policies import can_edit_course, is_course_member

        now = now or timezone.now()
        if not user or not user.is_authenticated:
            return False
        if can_edit_course(user, self.course):
            return True
        if not is_course_member(user, self.course):
            return False
        if not self.is_currently_published(now=now):
            return False
        targets = list(self.targets.all())
        if not targets:
            return True
        for target in targets:
            if target.matches_user(user):
                return True
        return False

    @classmethod
    def visible_for_course(cls, course, user, *, now=None):
        now = now or timezone.now()
        if not user or not user.is_authenticated:
            return []
        from .policies import can_edit_course, is_course_member
        from .services import process_course_announcements

        if not is_course_member(user, course) and not can_edit_course(user, course):
            return []
        process_course_announcements(course, now=now)
        queryset = cls.objects.filter(course=course).select_related('author', 'course')
        visible = []
        for announcement in queryset.order_by('-is_pinned', '-publish_at', '-created_at'):
            if can_edit_course(user, course):
                visible.append(announcement)
                continue
            if not announcement.is_currently_published(now=now):
                continue
            if announcement.is_visible_to_user(user, now=now):
                visible.append(announcement)
        return visible

    def user_has_acknowledged(self, user):
        if not user or not user.is_authenticated:
            return False
        return self.acknowledgements.filter(user=user, acknowledged_at__isnull=False).exists()

    def __str__(self):
        return self.title


class AnnouncementAcknowledgement(models.Model):
    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name='acknowledgements',
        verbose_name=_('anuncio'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_announcement_acknowledgements',
        verbose_name=_('usuario'),
    )
    acknowledged_at = models.DateTimeField(_('confirmado el'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('acuse de lectura del anuncio')
        verbose_name_plural = _('acuse de lectura de anuncios')
        unique_together = [('announcement', 'user')]

    def __str__(self):
        return f'{self.user} confirmó {self.announcement}'


class AnnouncementTarget(models.Model):
    class TargetType(models.TextChoices):
        ALL = 'all', _('Todo el curso')
        SECTION = 'section', _('Sección')

    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name='targets',
        verbose_name=_('anuncio'),
    )
    target_type = models.CharField(
        _('tipo de objetivo'),
        max_length=12,
        choices=TargetType.choices,
        default=TargetType.ALL,
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='announcement_targets',
        verbose_name=_('sección'),
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('destino del anuncio')
        verbose_name_plural = _('destinos del anuncio')

    def clean(self):
        super().clean()
        if self.target_type == self.TargetType.SECTION and not self.section_id:
            raise ValidationError({'section': _('Debes indicar una sección para este destino.')})
        if self.section_id and self.section.course_id != self.announcement.course_id:
            raise ValidationError({'section': _('La sección debe pertenecer al mismo curso que el anuncio.')})

    def matches_user(self, user):
        from .policies import ACTIVE_READ_STATUSES

        if self.target_type == self.TargetType.ALL:
            return True
        if not self.section_id:
            return False
        return CourseMembership.objects.filter(
            user=user,
            course=self.announcement.course,
            section=self.section,
            status__in=ACTIVE_READ_STATUSES,
        ).exists()

    def __str__(self):
        if self.target_type == self.TargetType.SECTION and self.section_id:
            return f'{self.announcement}: {self.section}'
        return f'{self.announcement}: {self.TargetType.ALL.label}'


class AnnouncementComment(models.Model):
    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name='comments',
        verbose_name=_('anuncio'),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_announcement_comments',
        verbose_name=_('autor'),
    )
    content = models.TextField(_('comentario'))
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    edited_at = models.DateTimeField(_('editado'), null=True, blank=True, auto_now=True)

    class Meta:
        verbose_name = _('comentario del anuncio')
        verbose_name_plural = _('comentarios del anuncio')
        ordering = ['created_at']

    def __str__(self):
        return f'Comentario en {self.announcement.title}'


class AnnouncementReaction(models.Model):
    class Kind(models.TextChoices):
        LIKE = 'like', _('Me gusta')

    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name='reactions',
        verbose_name=_('anuncio'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_announcement_reactions',
        verbose_name=_('usuario'),
    )
    kind = models.CharField(_('tipo'), max_length=12, choices=Kind.choices, default=Kind.LIKE)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('reacción al anuncio')
        verbose_name_plural = _('reacciones al anuncio')
        constraints = [
            models.UniqueConstraint(fields=['announcement', 'user', 'kind'], name='lms_unique_announcement_reaction'),
        ]

    def __str__(self):
        return f'{self.user} likes {self.announcement.title}'


class DiscussionTag(models.Model):
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_discussion_tags',
        verbose_name=_('curso'),
    )
    name = models.CharField(_('etiqueta'), max_length=60)
    slug = models.SlugField(_('slug'), max_length=80, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('etiqueta del foro')
        verbose_name_plural = _('etiquetas del foro')
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['course', 'slug'], name='lms_unique_discussion_tag_slug'),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name or 'etiqueta')
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class DiscussionTopic(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        OPEN = 'open', _('Abierto')
        CLOSED = 'closed', _('Cerrado')
        ARCHIVED = 'archived', _('Archivado')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_discussion_topics',
        verbose_name=_('curso'),
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='discussion_topics',
        verbose_name=_('sección'),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_discussion_topics',
        verbose_name=_('autor'),
    )
    title = models.CharField(_('título'), max_length=200)
    slug = models.SlugField(_('slug'), max_length=220, blank=True)
    content = models.TextField(_('contenido'), blank=True, default='')
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.OPEN,
    )
    is_pinned = models.BooleanField(_('fijado'), default=False)
    is_locked = models.BooleanField(_('bloqueado'), default=False)
    allow_replies = models.BooleanField(_('permite respuestas'), default=True)
    allow_attachments = models.BooleanField(_('permite adjuntos'), default=True)
    require_reply_before_view = models.BooleanField(_('requerir responder antes de ver'), default=False)
    tags = models.ManyToManyField(DiscussionTag, blank=True, related_name='topics')
    view_count = models.PositiveIntegerField(_('visitas'), default=0)
    last_activity_at = models.DateTimeField(_('última actividad'), null=True, blank=True)
    published_at = models.DateTimeField(_('publicado en'), null=True, blank=True)
    is_deleted = models.BooleanField(_('eliminado lógicamente'), default=False)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('tema de discusión')
        verbose_name_plural = _('temas de discusión')
        ordering = ['-is_pinned', '-last_activity_at', '-created_at']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title or 'tema')
        self.content = sanitize_html_content(self.content)
        if self.status == self.Status.OPEN and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.section_id and self.section.course_id != self.course_id:
            raise ValidationError({'section': _('La sección debe pertenecer al mismo curso que el tema.')})

    def is_visible_to_user(self, user, *, now=None):
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.has_perm('lms.manage_platform'):
            return True
        from .policies import ACTIVE_READ_STATUSES, can_edit_course, is_course_member

        if self.is_deleted:
            return False
        if can_edit_course(user, self.course):
            return True
        if not is_course_member(user, self.course):
            return False
        if self.section_id:
            return CourseMembership.objects.filter(
                user=user,
                course=self.course,
                section=self.section,
                status__in=ACTIVE_READ_STATUSES,
            ).exists()
        return True

    def can_participate(self, user):
        from .policies import ACTIVE_READ_STATUSES, can_edit_course, is_course_member

        if not user or not user.is_authenticated:
            return False
        if can_edit_course(user, self.course):
            return True
        if CourseMembership.objects.filter(
            user=user, course=self.course, status=CourseMembership.Status.SUSPENDED,
        ).exists():
            return False
        if not is_course_member(user, self.course):
            return False
        if self.section_id:
            return CourseMembership.objects.filter(
                user=user,
                course=self.course,
                section=self.section,
                status__in=ACTIVE_READ_STATUSES,
            ).exists()
        return True

    def get_root_posts(self):
        return self.posts.filter(parent__isnull=True, is_deleted=False).select_related('author').order_by('created_at')

    def __str__(self):
        return self.title


class DiscussionPost(models.Model):
    MAX_DEPTH = 5

    title = models.CharField(_('título de la consulta'), max_length=200, blank=True, default='')
    accepted_solution = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='solves_queries', verbose_name=_('solución aceptada'),
    )

    topic = models.ForeignKey(
        DiscussionTopic,
        on_delete=models.CASCADE,
        related_name='posts',
        verbose_name=_('tema'),
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name=_('respuesta a'),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_discussion_posts',
        verbose_name=_('autor'),
    )
    content = models.TextField(_('contenido'))
    is_edited = models.BooleanField(_('editado'), default=False)
    edited_at = models.DateTimeField(_('editado en'), null=True, blank=True)
    edit_history = models.JSONField(_('historial de edición'), default=list, blank=True)
    mentions = models.JSONField(_('menciones'), default=list, blank=True)
    is_deleted = models.BooleanField(_('eliminado lógicamente'), default=False)
    is_hidden = models.BooleanField(_('oculto por moderación'), default=False)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('respuesta del foro')
        verbose_name_plural = _('respuestas del foro')
        ordering = ['created_at']

    @property
    def depth(self):
        if not self.parent_id:
            return 0
        return self.parent.depth + 1

    @property
    def history(self):
        return self.edit_history or []

    def clean(self):
        super().clean()
        if self.parent_id and self.parent.topic_id != self.topic_id:
            raise ValidationError({'parent': _('La respuesta debe pertenecer al mismo tema.')})
        if self.accepted_solution_id:
            solution = self.accepted_solution
            if self.parent_id or solution.topic_id != self.topic_id or not solution.parent_id:
                raise ValidationError({'accepted_solution': _('La solución debe ser una respuesta de esta consulta.')})
            root = solution.parent
            while root.parent_id:
                root = root.parent
            if root.pk != self.pk or solution.is_deleted or solution.is_hidden:
                raise ValidationError({'accepted_solution': _('La solución debe ser una respuesta visible de esta consulta.')})
        if self.parent_id and self.depth > self.MAX_DEPTH:
            raise ValidationError({'parent': _('La profundidad máxima permitida es de 5 niveles.')})
        if not self.content or not self.content.strip():
            raise ValidationError({'content': _('El contenido no puede estar vacío.')})

    def save(self, *args, **kwargs):
        self.content = sanitize_html_content(self.content)
        if self.pk:
            previous = DiscussionPost.objects.filter(pk=self.pk).first()
            if previous and previous.content != self.content:
                history = list(previous.edit_history or [])
                history.append({
                    'content': previous.content,
                    'edited_at': (previous.edited_at or timezone.now()).isoformat(),
                })
                self.edit_history = history
                self.is_edited = True
                self.edited_at = timezone.now()
        super().save(*args, **kwargs)

    def user_can_edit(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.has_perm('lms.manage_platform'):
            return True
        if user == self.author:
            return True
        from .policies import can_edit_course

        return can_edit_course(user, self.topic.course)

    def __str__(self):
        return f'Respuesta en {self.topic.title}'


class DiscussionPostLike(models.Model):
    post = models.ForeignKey(
        DiscussionPost,
        on_delete=models.CASCADE,
        related_name='likes',
        verbose_name=_('respuesta'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_discussion_post_likes',
        verbose_name=_('usuario'),
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('me gusta del foro')
        verbose_name_plural = _('me gusta del foro')
        constraints = [
            models.UniqueConstraint(fields=['post', 'user'], name='lms_unique_discussion_post_like'),
        ]

    def __str__(self):
        return f'{self.user} likes {self.post}'


class DiscussionSubscription(models.Model):
    topic = models.ForeignKey(
        DiscussionTopic,
        on_delete=models.CASCADE,
        related_name='subscriptions',
        verbose_name=_('tema'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_discussion_subscriptions',
        verbose_name=_('usuario'),
    )
    is_active = models.BooleanField(_('activa'), default=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('suscripción al tema')
        verbose_name_plural = _('suscripciones al tema')
        constraints = [
            models.UniqueConstraint(fields=['topic', 'user'], name='lms_unique_discussion_subscription'),
        ]

    def __str__(self):
        return f'{self.user} → {self.topic}'


class DiscussionReport(models.Model):
    class Status(models.TextChoices):
        OPEN = 'open', _('Abierta')
        RESOLVED = 'resolved', _('Resuelta')
        REJECTED = 'rejected', _('Rechazada')

    post = models.ForeignKey(
        DiscussionPost,
        on_delete=models.CASCADE,
        related_name='reports',
        verbose_name=_('respuesta'),
        null=True,
        blank=True,
    )
    topic = models.ForeignKey(
        DiscussionTopic,
        on_delete=models.CASCADE,
        related_name='reports',
        verbose_name=_('tema'),
        null=True,
        blank=True,
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_discussion_reports',
        verbose_name=_('reportero'),
    )
    reason = models.CharField(_('motivo'), max_length=120)
    details = models.TextField(_('detalles'), blank=True, default='')
    status = models.CharField(_('estado'), max_length=12, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    resolved_at = models.DateTimeField(_('resuelta en'), null=True, blank=True)

    class Meta:
        verbose_name = _('denuncia del foro')
        verbose_name_plural = _('denuncias del foro')
        ordering = ['-created_at']

    def clean(self):
        super().clean()
        if self.post_id and self.post.topic_id != (self.topic_id or self.post.topic_id):
            raise ValidationError({'post': _('La denuncia debe corresponder al mismo tema.')})

    def __str__(self):
        return f'{self.get_status_display()} — {self.reason}'


class DiscussionModerationLog(models.Model):
    topic = models.ForeignKey(
        DiscussionTopic,
        on_delete=models.CASCADE,
        related_name='moderation_logs',
        verbose_name=_('tema'),
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_discussion_moderation_logs',
        verbose_name=_('moderador'),
    )
    action = models.CharField(_('acción'), max_length=80)
    details = models.TextField(_('detalles'), blank=True, default='')
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('registro de moderación')
        verbose_name_plural = _('registros de moderación')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.action} — {self.topic}'


class QuestionBank(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        PUBLISHED = 'published', _('Publicado')
        ARCHIVED = 'archived', _('Archivado')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_question_banks',
        verbose_name=_('curso'),
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='owned_question_banks',
        verbose_name=_('propietario'),
    )
    title = models.CharField(_('título'), max_length=200)
    slug = models.SlugField(_('slug'), max_length=220, blank=True)
    description = models.TextField(_('descripción'), blank=True, default='')
    import_errors = models.JSONField(_('errores de importación CSV'), default=list, blank=True)
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('banco de preguntas')
        verbose_name_plural = _('bancos de preguntas')
        ordering = ['title', 'pk']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title or 'banco')
        super().save(*args, **kwargs)

    def randomize_questions(self, count, *, seed=None):
        queryset = self.questions.filter(status=Question.Status.PUBLISHED).select_related('current_version')
        items = list(queryset)
        if not items:
            return []
        if seed is not None:
            rng = __import__('random').Random(seed)
            rng.shuffle(items)
        else:
            import random
            random.shuffle(items)
        return items[:max(0, min(count, len(items)))]

    def __str__(self):
        return self.title


class QuestionTag(models.Model):
    bank = models.ForeignKey(
        QuestionBank,
        on_delete=models.CASCADE,
        related_name='tags',
        verbose_name=_('banco'),
    )
    name = models.CharField(_('etiqueta'), max_length=80)
    slug = models.SlugField(_('slug'), max_length=100, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('etiqueta de pregunta')
        verbose_name_plural = _('etiquetas de pregunta')
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['bank', 'slug'], name='lms_unique_question_tag_slug'),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name or 'etiqueta')
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Question(models.Model):
    class Type(models.TextChoices):
        SINGLE_CHOICE = 'single_choice', _('Opción única')
        MULTIPLE_CHOICE = 'multiple_choice', _('Opción múltiple')
        TRUE_FALSE = 'true_false', _('Verdadero/Falso')
        SHORT_TEXT = 'short_text', _('Texto breve')
        NUMERIC = 'numeric', _('Numérica')
        MATCHING = 'matching', _('Emparejamiento')
        ESSAY = 'essay', _('Texto libre')

    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        PUBLISHED = 'published', _('Publicado')
        ARCHIVED = 'archived', _('Archivado')

    bank = models.ForeignKey(
        QuestionBank,
        on_delete=models.CASCADE,
        related_name='questions',
        verbose_name=_('banco'),
    )
    competency = models.ForeignKey(
        'curriculum.Skill',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_questions',
        verbose_name=_('competencia'),
    )
    topic = models.ForeignKey(
        'courses.Tema',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_questions',
        verbose_name=_('tema'),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lms_questions',
        verbose_name=_('creado por'),
    )
    title = models.CharField(_('título'), max_length=200, blank=True, default='')
    slug = models.SlugField(_('slug'), max_length=220, blank=True)
    question_type = models.CharField(
        _('tipo'),
        max_length=18,
        choices=Type.choices,
        default=Type.SINGLE_CHOICE,
    )
    difficulty = models.PositiveSmallIntegerField(_('dificultad'), default=1)
    points = models.PositiveIntegerField(_('puntos'), default=1)
    penalty = models.DecimalField(_('penalización'), max_digits=5, decimal_places=2, default=0)
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    tags = models.ManyToManyField(QuestionTag, blank=True, related_name='questions')
    current_version = models.ForeignKey(
        'QuestionVersion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='active_question',
        verbose_name=_('versión actual'),
    )
    usage_count = models.PositiveIntegerField(_('veces usada'), default=0)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('pregunta')
        verbose_name_plural = _('preguntas')
        ordering = ['difficulty', 'pk']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title or self.question_type or 'pregunta')
        super().save(*args, **kwargs)

    def create_version(self, *, prompt, options=None, correct_answers=None, explanation='', files=None, metadata=None, published=False, tolerance=0):
        version_number = (self.versions.aggregate(models.Max('version_number')).get('version_number__max') or 0) + 1
        version = QuestionVersion.objects.create(
            question=self,
            version_number=version_number,
            prompt=prompt,
            options=options or [],
            correct_answers=correct_answers or [],
            explanation=explanation,
            files=files or [],
            metadata=metadata or {},
            tolerance=Decimal(str(tolerance)) if tolerance is not None else 0,
            is_published=published,
        )
        self.current_version = version
        self.status = Question.Status.PUBLISHED if published else self.status
        self.save(update_fields=['current_version', 'status', 'updated_at'])
        return version

    def evaluate_response(self, submitted_value):
        if not self.current_version:
            return {'correct': False, 'score': 0, 'max_score': self.points, 'manual_review': False}
        return self.current_version.evaluate_response(submitted_value, question=self)

    def __str__(self):
        return self.title or f'Pregunta {self.pk}'


class QuestionVersion(models.Model):
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='versions',
        verbose_name=_('pregunta'),
    )
    version_number = models.PositiveIntegerField(_('número de versión'), default=1)
    prompt = models.TextField(_('enunciado'))
    options = models.JSONField(_('opciones'), default=list, blank=True)
    correct_answers = models.JSONField(_('respuestas correctas'), default=list, blank=True)
    explanation = models.TextField(_('explicación'), blank=True, default='')
    files = models.JSONField(_('archivos adjuntos'), default=list, blank=True)
    metadata = models.JSONField(_('metadatos'), default=dict, blank=True)
    tolerance = models.DecimalField(_('tolerancia'), max_digits=10, decimal_places=4, default=0)
    is_published = models.BooleanField(_('publicada'), default=False)
    published_at = models.DateTimeField(_('publicada en'), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_question_versions',
        verbose_name=_('creado por'),
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('versión de pregunta')
        verbose_name_plural = _('versiones de pregunta')
        ordering = ['version_number']
        constraints = [
            models.UniqueConstraint(fields=['question', 'version_number'], name='lms_unique_question_version_number'),
        ]

    def save(self, *args, **kwargs):
        self.prompt = sanitize_html_content(self.prompt)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.question_id:
            question_type = self.question.question_type
            if question_type in {Question.Type.SINGLE_CHOICE, Question.Type.MULTIPLE_CHOICE, Question.Type.TRUE_FALSE}:
                if not self.options:
                    raise ValidationError({'options': _('Las preguntas de opción múltiple requieren opciones definidas.')})
            if question_type == Question.Type.SINGLE_CHOICE and len(self.options) < 2:
                raise ValidationError({'options': _('La opción única requiere al menos dos opciones.')})
            if question_type == Question.Type.MULTIPLE_CHOICE and len(self.options) < 2:
                raise ValidationError({'options': _('La opción múltiple requiere al menos dos opciones.')})
            if question_type == Question.Type.TRUE_FALSE and len(self.options) != 2:
                raise ValidationError({'options': _('Verdadero/Falso requiere exactamente dos opciones.')})
            if question_type == Question.Type.NUMERIC and self.tolerance < 0:
                raise ValidationError({'tolerance': _('La tolerancia numérica no puede ser negativa.')})
            if question_type == Question.Type.SHORT_TEXT and not self.correct_answers:
                raise ValidationError({'correct_answers': _('La respuesta corta requiere una respuesta correcta válida.')})
            if question_type == Question.Type.MATCHING and not isinstance(self.correct_answers, dict):
                raise ValidationError({'correct_answers': _('El emparejamiento requiere un mapa clave→valor.')})

    def _normalise_text(self, value):
        if value is None:
            return ''
        if isinstance(value, (list, tuple, set)):
            return [self._normalise_text(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._normalise_text(item) for key, item in value.items()}
        return str(value).strip().casefold()

    def _extract_answer_value(self, value):
        if isinstance(value, dict):
            for key in ('value', 'answer', 'selected', 'response', 'selected_option'):
                if key in value:
                    return self._extract_answer_value(value[key])
            if 'choices' in value:
                return self._extract_answer_value(value['choices'])
            if 'items' in value:
                return self._extract_answer_value(value['items'])
            return value
        if isinstance(value, (list, tuple, set)):
            if len(value) == 1:
                return self._extract_answer_value(next(iter(value)))
            return [self._extract_answer_value(item) for item in value]
        return value

    def evaluate_response(self, submitted_value, *, question=None):
        question = question or self.question
        value = self._extract_answer_value(submitted_value)
        if question.question_type == Question.Type.SINGLE_CHOICE:
            expected = self._normalise_text(self.correct_answers)
            submitted = self._normalise_text(value)
            correct = submitted in expected if isinstance(expected, list) else submitted == expected
            return {'correct': correct, 'score': question.points if correct else 0, 'max_score': question.points, 'manual_review': False}
        if question.question_type == Question.Type.MULTIPLE_CHOICE:
            expected = set(self._normalise_text(self.correct_answers))
            submitted = set(self._normalise_text(value or []))
            correct = submitted == expected and bool(expected)
            score = question.points if correct else 0
            return {'correct': correct, 'score': score, 'max_score': question.points, 'manual_review': False}
        if question.question_type == Question.Type.TRUE_FALSE:
            submitted = self._normalise_text(value)
            expected = self._normalise_text(self.correct_answers)
            correct = submitted == expected if isinstance(expected, list) else submitted == expected
            return {'correct': correct, 'score': question.points if correct else 0, 'max_score': question.points, 'manual_review': False}
        if question.question_type == Question.Type.SHORT_TEXT:
            accepted = {self._normalise_text(item) for item in self.correct_answers}
            submitted = self._normalise_text(value)
            correct = submitted in accepted
            return {'correct': correct, 'score': question.points if correct else 0, 'max_score': question.points, 'manual_review': False}
        if question.question_type == Question.Type.NUMERIC:
            try:
                submitted = float(value)
                expected = float(self.correct_answers[0]) if self.correct_answers else 0
                correct = abs(submitted - expected) <= float(self.tolerance or 0)
                score = question.points if correct else 0
                return {'correct': correct, 'score': score, 'max_score': question.points, 'manual_review': False}
            except (TypeError, ValueError):
                return {'correct': False, 'score': 0, 'max_score': question.points, 'manual_review': False}
        if question.question_type == Question.Type.MATCHING:
            expected = self._normalise_text(self.correct_answers or {})
            submitted = self._normalise_text(value or {})
            correct = expected == submitted
            return {'correct': correct, 'score': question.points if correct else 0, 'max_score': question.points, 'manual_review': False}
        return {'correct': False, 'score': 0, 'max_score': question.points, 'manual_review': True}

    def __str__(self):
        return f'{self.question} v{self.version_number}'


class QuestionAttempt(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', _('En curso')
        SUBMITTED = 'submitted', _('Enviado')
        GRADED = 'graded', _('Calificado')

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='attempts',
        verbose_name=_('pregunta'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_question_attempts',
        verbose_name=_('usuario'),
    )
    version = models.ForeignKey(
        QuestionVersion,
        on_delete=models.PROTECT,
        related_name='attempts',
        verbose_name=_('versión'),
    )
    response = models.JSONField(_('respuesta entregada'), default=dict, blank=True)
    score = models.DecimalField(_('puntuación'), max_digits=8, decimal_places=2, default=0)
    is_correct = models.BooleanField(_('correcta'), default=False)
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
    )
    submitted_at = models.DateTimeField(_('enviado en'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('intento de pregunta')
        verbose_name_plural = _('intentos de pregunta')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} → {self.question}'


def generate_quiz_reference():
    return 'QZ-' + uuid4().hex.upper()


class Quiz(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        SCHEDULED = 'scheduled', _('Programado')
        PUBLISHED = 'published', _('Publicado')
        CLOSED = 'closed', _('Cerrado')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_quizzes',
        verbose_name=_('curso'),
    )
    section = models.ForeignKey(
        'Section',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='quizzes',
        verbose_name=_('sección'),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lms_quizzes',
        verbose_name=_('creado por'),
    )
    title = models.CharField(_('título'), max_length=200)
    reference_code = models.CharField(
        _('código de referencia'), max_length=64, unique=True, default=generate_quiz_reference,
    )
    slug = models.SlugField(_('slug'), max_length=220, blank=True)
    description = models.TextField(_('descripción'), blank=True, default='')
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    available_from = models.DateTimeField(_('disponible desde'), null=True, blank=True)
    available_until = models.DateTimeField(_('disponible hasta'), null=True, blank=True)
    due_at = models.DateTimeField(_('fecha de entrega'), null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(_('duración (minutos)'), default=0)
    max_attempts = models.PositiveIntegerField(_('máximo de intentos'), default=1)
    allow_resume = models.BooleanField(_('permite reanudar'), default=True)
    allow_time_extension = models.BooleanField(_('permite ampliación de tiempo'), default=False)
    shuffle_questions = models.BooleanField(_('orden aleatorio'), default=False)
    shuffle_seed = models.CharField(_('semilla aleatoria'), max_length=80, blank=True, default='')
    publish_answers_at = models.DateTimeField(_('publicación de respuestas'), null=True, blank=True)
    answers_released_at = models.DateTimeField(_('respuestas publicadas en'), null=True, blank=True, editable=False)
    publish_grade_at = models.DateTimeField(_('publicación de nota'), null=True, blank=True)
    passing_score = models.DecimalField(_('nota mínima'), max_digits=5, decimal_places=2, default=0)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('cuestionario')
        verbose_name_plural = _('cuestionarios')
        ordering = ['available_from', 'title']

    def clean(self):
        super().clean()
        if self.section_id and self.section.course_id != self.course_id:
            raise ValidationError({'section': _('La sección debe pertenecer al mismo curso del cuestionario.')})
        if self.available_from and self.available_until and self.available_until < self.available_from:
            raise ValidationError({'available_until': _('La fecha de fin debe ser posterior a la de inicio.')})
        if self.duration_minutes < 0:
            raise ValidationError({'duration_minutes': _('La duración no puede ser negativa.')})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title or 'quiz')
        super().save(*args, **kwargs)

    @property
    def questions_count(self):
        return self.questions.count()

    def question_order(self, *, user=None):
        queryset = self.questions.select_related('question_version__question').order_by('order', 'pk')
        items = list(queryset)
        if not self.shuffle_questions:
            return items
        seed = self.shuffle_seed or (user.username if user and getattr(user, 'username', None) else 'default')
        rng = __import__('random').Random(seed)
        rng.shuffle(items)
        return items

    def can_attempt(self, user, *, now=None):
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if self.status != self.Status.PUBLISHED:
            return False
        timestamp = now or timezone.now()
        if self.course.status == 'archived':
            return False
        if self.available_from and timestamp < self.available_from:
            return False
        if self.available_until and timestamp >= self.available_until:
            return False
        if self.due_at and timestamp >= self.due_at:
            return False
        membership = CourseMembership.objects.filter(
            course=self.course,
            user=user,
            status=CourseMembership.Status.ACTIVE,
            role=CourseMembership.Role.STUDENT,
        ).first()
        if not membership:
            return False
        if self.section_id and membership.section_id != self.section_id:
            return False
        return True

    def start_attempt(self, user, *, now=None):
        if not self.can_attempt(user, now=now):
            raise ValueError(_('No se puede iniciar este cuestionario en este momento.'))
        if self.max_attempts <= 0:
            raise ValueError(_('El número máximo de intentos debe ser positivo.'))
        timestamp = now or timezone.now()
        existing = self.attempts.filter(user=user).order_by('-created_at')
        if existing.exclude(status=QuizAttempt.Status.DRAFT).exists():
            raise ValueError(_('Ya existe un intento activo o finalizado para este usuario en este cuestionario.'))
        count = existing.count()
        if count >= self.max_attempts:
            raise ValueError(_('Se ha alcanzado el límite máximo de intentos para este cuestionario.'))
        attempt = QuizAttempt.objects.create(
            quiz=self,
            user=user,
            started_at=timestamp,
            deadline_at=(timestamp + timedelta(minutes=self.duration_minutes)) if self.duration_minutes else None,
            status=QuizAttempt.Status.IN_PROGRESS,
            configuration_snapshot={
                'status': self.status,
                'duration_minutes': self.duration_minutes,
                'passing_score': str(self.passing_score),
                'allowed_resume': self.allow_resume,
                'shuffle_questions': self.shuffle_questions,
                'shuffle_seed': self.shuffle_seed,
                'publish_answers_at': self.publish_answers_at.isoformat() if self.publish_answers_at else None,
                'publish_grade_at': self.publish_grade_at.isoformat() if self.publish_grade_at else None,
            },
        )
        return attempt

    def evaluate_attempt(self, attempt, *, now=None):
        if attempt.quiz_id != self.pk:
            raise ValueError(_('El intento no corresponde a este cuestionario.'))
        timestamp = now or timezone.now()
        grading_metadata = list(self.questions.values_list('metadata', flat=True))
        if grading_metadata and all(metadata.get('grading_scale') == 10 for metadata in grading_metadata):
            from .quiz_grading import grade_scaled_attempt
            return grade_scaled_attempt(self, attempt, timestamp=timestamp)
        total_score = Decimal('0')
        total_possible = Decimal('0')
        for answer in attempt.answers.select_related('question__question_version'):
            question_version = answer.question.question_version
            result = question_version.evaluate_response(answer.response, question=answer.question.question_version.question)
            answer.score = Decimal(str(result.get('score', 0)))
            answer.is_correct = bool(result.get('correct'))
            answer.manual_review = bool(result.get('manual_review'))
            answer.submitted_at = answer.submitted_at or timestamp
            answer.save(update_fields=['score', 'is_correct', 'manual_review', 'submitted_at', 'updated_at'])
            total_score += Decimal(str(answer.score))
            total_possible += Decimal(str(answer.question.points))
        attempt.score = total_score
        attempt.is_passed = self.passing_score <= total_score
        attempt.status = QuizAttempt.Status.GRADED if not any(a.manual_review for a in attempt.answers.all()) else QuizAttempt.Status.SUBMITTED
        attempt.graded_at = timestamp
        attempt.save(update_fields=['score', 'is_passed', 'status', 'graded_at', 'updated_at'])

        grade_entry, created = GradeEntry.objects.update_or_create(
            attempt=attempt,
            defaults={
                'user': attempt.user,
                'course': self.course,
                'grade': total_score,
                'max_grade': total_possible or Decimal('0'),
                'status': GradeEntry.Status.PUBLISHED if self.publish_grade_at and timestamp >= self.publish_grade_at else GradeEntry.Status.DRAFT,
                'published_at': timestamp if self.publish_grade_at and timestamp >= self.publish_grade_at else None,
            },
        )
        return grade_entry

    def __str__(self):
        return self.title


class QuizQuestion(models.Model):
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='questions',
        verbose_name=_('cuestionario'),
    )
    question_version = models.ForeignKey(
        QuestionVersion,
        on_delete=models.PROTECT,
        related_name='quiz_questions',
        verbose_name=_('versión de pregunta'),
    )
    order = models.PositiveIntegerField(_('orden'), default=1)
    points = models.PositiveIntegerField(_('puntos'), default=1)
    required = models.BooleanField(_('obligatoria'), default=True)
    metadata = models.JSONField(_('metadatos'), default=dict, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('pregunta del cuestionario')
        verbose_name_plural = _('preguntas del cuestionario')
        ordering = ['order', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['quiz', 'order'], name='lms_unique_quiz_question_order'),
        ]

    @property
    def version(self):
        return self.question_version

    @property
    def question(self):
        return self.question_version.question

    def __str__(self):
        return f'{self.quiz.title} · {self.question_version}'


class QuizAttempt(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        IN_PROGRESS = 'in_progress', _('En curso')
        SUBMITTED = 'submitted', _('Enviado')
        GRADED = 'graded', _('Calificado')
        EXPIRED = 'expired', _('Caducado')

    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='attempts',
        verbose_name=_('cuestionario'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_quiz_attempts',
        verbose_name=_('usuario'),
    )
    status = models.CharField(
        _('estado'),
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    started_at = models.DateTimeField(_('iniciado en'), null=True, blank=True)
    submitted_at = models.DateTimeField(_('enviado en'), null=True, blank=True)
    graded_at = models.DateTimeField(_('calificado en'), null=True, blank=True)
    deadline_at = models.DateTimeField(_('plazo final'), null=True, blank=True)
    score = models.DecimalField(_('puntuación'), max_digits=8, decimal_places=2, default=0)
    is_passed = models.BooleanField(_('aprobado'), default=False)
    correct_count = models.PositiveIntegerField(_('aciertos'), null=True, blank=True, editable=False)
    wrong_count = models.PositiveIntegerField(_('fallos'), null=True, blank=True, editable=False)
    blank_count = models.PositiveIntegerField(_('en blanco'), null=True, blank=True, editable=False)
    configuration_snapshot = models.JSONField(_('instantánea de configuración'), default=dict, blank=True)
    last_autosave_at = models.DateTimeField(_('último guardado automático'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('intento de cuestionario')
        verbose_name_plural = _('intentos de cuestionario')
        ordering = ['-created_at']

    def save_answer(self, question, response, *, autosave=False):
        if not self.quiz_id:
            raise ValueError(_('El intento debe pertenecer a un cuestionario.'))
        if self.status in {self.Status.SUBMITTED, self.Status.GRADED, self.Status.EXPIRED}:
            raise ValueError(_('No se puede modificar un intento ya enviado o cerrado.'))
        if question.quiz_id != self.quiz_id:
            raise ValueError(_('La pregunta no pertenece a este cuestionario.'))
        answer, _created = QuizAnswer.objects.get_or_create(
            attempt=self,
            question=question,
            defaults={'question_version': question.question_version, 'response': response or {}, 'autosave': bool(autosave)},
        )
        answer.response = response or {}
        answer.autosave = bool(autosave)
        answer.question_version = question.question_version
        if not autosave:
            answer.submitted_at = timezone.now()
        answer.save(update_fields=['response', 'autosave', 'question_version', 'submitted_at', 'updated_at'])
        self.last_autosave_at = timezone.now()
        self.save(update_fields=['last_autosave_at', 'updated_at'])
        return answer

    def submit(self, *, now=None):
        if self.status in {self.Status.SUBMITTED, self.Status.GRADED, self.Status.EXPIRED}:
            raise ValueError(_('Este intento ya ha sido enviado o cerrado.'))
        timestamp = now or timezone.now()
        if self.deadline_at and timestamp > self.deadline_at and not self.quiz.allow_time_extension:
            self.status = self.Status.EXPIRED
            self.submitted_at = timestamp
            self.save(update_fields=['status', 'submitted_at', 'updated_at'])
            raise ValueError(_('El intento ha caducado.'))
        self.status = self.Status.SUBMITTED
        self.submitted_at = timestamp
        self.save(update_fields=['status', 'submitted_at', 'updated_at'])
        return self

    def __str__(self):
        return f'{self.user} → {self.quiz}'


class QuizAnswer(models.Model):
    attempt = models.ForeignKey(
        QuizAttempt,
        on_delete=models.CASCADE,
        related_name='answers',
        verbose_name=_('intento'),
    )
    question = models.ForeignKey(
        QuizQuestion,
        on_delete=models.CASCADE,
        related_name='answers',
        verbose_name=_('pregunta'),
    )
    question_version = models.ForeignKey(
        QuestionVersion,
        on_delete=models.PROTECT,
        related_name='quiz_answers',
        verbose_name=_('versión de pregunta'),
    )
    response = models.JSONField(_('respuesta'), default=dict, blank=True)
    score = models.DecimalField(_('puntuación'), max_digits=8, decimal_places=2, default=0)
    is_correct = models.BooleanField(_('correcta'), default=False)
    manual_review = models.BooleanField(_('revisión manual'), default=False)
    autosave = models.BooleanField(_('guardado automático'), default=False)
    submitted_at = models.DateTimeField(_('enviado en'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('respuesta del cuestionario')
        verbose_name_plural = _('respuestas del cuestionario')
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(fields=['attempt', 'question'], name='lms_unique_quiz_answer_per_question'),
        ]

    def __str__(self):
        return f'{self.attempt.user} · {self.question}'


class GradeCategory(models.Model):
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_grade_categories',
        verbose_name=_('curso'),
    )
    name = models.CharField(_('nombre'), max_length=120)
    weight = models.DecimalField(_('ponderación (%)'), max_digits=5, decimal_places=2, default=0)
    order = models.PositiveIntegerField(_('orden'), default=0)
    is_active = models.BooleanField(_('activa'), default=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('categoría del libro de notas')
        verbose_name_plural = _('categorías del libro de notas')
        ordering = ['order', 'pk']

    def clean(self):
        super().clean()
        if self.weight < 0:
            raise ValidationError({'weight': _('La ponderación no puede ser negativa.')})

    def compute_weighted_average_for(self, user):
        items = self.items.filter(is_active=True)
        if not items.exists():
            return Decimal('0')
        total = Decimal('0')
        denominator = Decimal('0')
        for item in items:
            grade = item.get_grade_for_user(user)
            if grade is None:
                continue
            total += grade
            denominator += item.weight
        if denominator == 0:
            return Decimal('0')
        return (total / denominator).quantize(Decimal('0.01'))

    def __str__(self):
        return f'{self.course}: {self.name}'


class GradeItem(models.Model):
    class SourceType(models.TextChoices):
        QUIZ = 'quiz', _('Cuestionario')
        ASSIGNMENT = 'assignment', _('Tarea / PEC')
        RUBRIC = 'rubric', _('Rúbrica')
        MANUAL = 'manual', _('Manual')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_grade_items',
        verbose_name=_('curso'),
    )
    category = models.ForeignKey(
        GradeCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='items',
        verbose_name=_('categoría'),
    )
    title = models.CharField(_('título'), max_length=200)
    source_type = models.CharField(
        _('tipo de origen'),
        max_length=12,
        choices=SourceType.choices,
        default=SourceType.MANUAL,
    )
    source_id = models.PositiveIntegerField(_('id origen'), default=0)
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='grade_items',
        verbose_name=_('cuestionario'),
    )
    assignment = models.ForeignKey(
        'Assignment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='grade_items',
        verbose_name=_('tarea'),
    )
    rubric = models.ForeignKey(
        'Rubric',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='grade_items',
        verbose_name=_('rúbrica'),
    )
    rubric_assessment = models.ForeignKey(
        'RubricAssessment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='grade_items',
        verbose_name=_('evaluación de rúbrica'),
    )
    weight = models.DecimalField(_('ponderación'), max_digits=5, decimal_places=2, default=0)
    max_grade = models.DecimalField(_('nota máxima'), max_digits=8, decimal_places=2, default=0)
    is_active = models.BooleanField(_('activa'), default=True)
    is_hidden = models.BooleanField(_('oculta'), default=False)
    is_exempt = models.BooleanField(_('exenta'), default=False)
    is_frozen = models.BooleanField(_('congelada'), default=False)
    allow_manual_override = models.BooleanField(_('permite sobreescritura manual'), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lms_grade_items',
        verbose_name=_('creado por'),
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('ítem del libro de notas')
        verbose_name_plural = _('ítems del libro de notas')
        ordering = ['title', 'pk']

    def clean(self):
        super().clean()
        if self.weight < 0:
            raise ValidationError({'weight': _('La ponderación no puede ser negativa.')})
        if self.max_grade < 0:
            raise ValidationError({'max_grade': _('La nota máxima no puede ser negativa.')})

    def get_grade_for_user(self, user):
        if not user:
            return None
        entry = self.entries.filter(user=user).order_by('-updated_at', '-created_at').first()
        if not entry:
            return None
        if entry.is_exempt:
            return None
        return entry.grade

    def has_student_grade(self, user):
        return self.entries.filter(user=user).exists()

    def __str__(self):
        return f'{self.course}: {self.title}'


class GradeEntry(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        PUBLISHED = 'published', _('Publicado')
        HIDDEN = 'hidden', _('Oculta')
        EXEMPT = 'exempt', _('Exento')
        NO_SHOW = 'no_show', _('No presentado')

    item = models.ForeignKey(
        GradeItem,
        on_delete=models.CASCADE,
        related_name='entries',
        verbose_name=_('ítem'),
        null=True,
        blank=True,
    )
    attempt = models.OneToOneField(
        QuizAttempt,
        on_delete=models.CASCADE,
        related_name='grade_entry',
        verbose_name=_('intento'),
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_grade_entries',
        verbose_name=_('usuario'),
    )
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_grade_entries',
        verbose_name=_('curso'),
    )
    grade = models.DecimalField(_('nota'), max_digits=8, decimal_places=2, default=0)
    max_grade = models.DecimalField(_('nota máxima'), max_digits=8, decimal_places=2, default=0)
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    is_exempt = models.BooleanField(_('exento'), default=False)
    is_hidden = models.BooleanField(_('oculta'), default=False)
    is_published = models.BooleanField(_('publicada'), default=False)
    published_at = models.DateTimeField(_('publicada en'), null=True, blank=True)
    comment = models.TextField(_('comentario'), blank=True, default='')
    evidence_url = models.URLField(_('evidencia'), blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lms_grade_entries',
        verbose_name=_('creado por'),
    )
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('entrada del libro de notas')
        verbose_name_plural = _('entradas del libro de notas')
        constraints = [
            models.UniqueConstraint(fields=['item', 'user'], name='lms_unique_grade_entry_per_item_user'),
        ]

    @property
    def score(self):
        return self.grade

    @property
    def max_score(self):
        return self.max_grade

    def clean(self):
        super().clean()
        if self.item_id and self.course_id and self.item.course_id != self.course_id:
            raise ValidationError({'course': _('La nota debe pertenecer al mismo curso que el ítem.')})
        if self.max_grade < 0:
            raise ValidationError({'max_grade': _('La nota máxima no puede ser negativa.')})
        if self.is_exempt and self.grade != 0:
            self.grade = Decimal('0')
        if self.status == self.Status.EXEMPT:
            self.is_exempt = True
            self.is_hidden = False
            self.is_published = False

    def publish(self, *, now=None):
        if self.item.is_frozen:
            raise ValueError(_('Este ítem está congelado y no admite cambios.'))
        previous_status = self.status
        previous_grade = self.grade
        timestamp = now or timezone.now()
        self.status = self.Status.PUBLISHED
        self.is_published = True
        self.is_hidden = False
        self.published_at = timestamp
        self.save(update_fields=['status', 'is_published', 'is_hidden', 'published_at', 'updated_at'])
        GradeChangeLog.objects.create(
            course=self.course,
            item=self.item,
            entry=self,
            changed_by=self.created_by or self.user,
            previous_grade=previous_grade,
            new_grade=self.grade,
            previous_status=previous_status,
            new_status=self.Status.PUBLISHED,
            reason=_('Publicación del libro de notas.'),
        )
        return self

    def hide(self, *, now=None):
        previous_status = self.status
        previous_grade = self.grade
        self.status = self.Status.HIDDEN
        self.is_hidden = True
        self.is_published = False
        self.save(update_fields=['status', 'is_hidden', 'is_published', 'updated_at'])
        GradeChangeLog.objects.create(
            course=self.course,
            item=self.item,
            entry=self,
            changed_by=self.created_by or self.user,
            previous_grade=previous_grade,
            new_grade=self.grade,
            previous_status=previous_status,
            new_status=self.Status.HIDDEN,
            reason=_('Nota ocultada.'),
        )
        return self

    def set_exempt(self, *, reason=''):
        if self.item.is_frozen:
            raise ValueError(_('Este ítem está congelado y no admite cambios.'))
        previous_grade = self.grade
        previous_status = self.status
        self.grade = Decimal('0')
        self.is_exempt = True
        self.status = self.Status.EXEMPT
        self.is_published = False
        self.is_hidden = False
        self.save(update_fields=['grade', 'is_exempt', 'status', 'is_published', 'is_hidden', 'updated_at'])
        GradeChangeLog.objects.create(
            course=self.course,
            item=self.item,
            entry=self,
            changed_by=self.created_by or self.user,
            previous_grade=previous_grade,
            new_grade=self.grade,
            previous_status=previous_status,
            new_status=self.Status.EXEMPT,
            reason=reason or _('Marcado como exento.'),
        )
        return self

    def percentage(self):
        if not self.max_grade:
            return Decimal('0')
        return (self.grade / self.max_grade * Decimal('100')).quantize(Decimal('0.01'))

    def __str__(self):
        return f'{self.user} · {self.course} · {self.grade}'


class GradeChangeLog(models.Model):
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_grade_change_logs',
        verbose_name=_('curso'),
    )
    item = models.ForeignKey(
        GradeItem,
        on_delete=models.CASCADE,
        related_name='change_logs',
        verbose_name=_('ítem'),
    )
    entry = models.ForeignKey(
        GradeEntry,
        on_delete=models.CASCADE,
        related_name='change_logs',
        verbose_name=_('entrada'),
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lms_grade_change_logs',
        verbose_name=_('modificado por'),
    )
    previous_grade = models.DecimalField(_('nota anterior'), max_digits=8, decimal_places=2, default=0)
    new_grade = models.DecimalField(_('nota nueva'), max_digits=8, decimal_places=2, default=0)
    previous_status = models.CharField(_('estado anterior'), max_length=12, choices=GradeEntry.Status.choices, default=GradeEntry.Status.DRAFT)
    new_status = models.CharField(_('estado nuevo'), max_length=12, choices=GradeEntry.Status.choices, default=GradeEntry.Status.DRAFT)
    reason = models.TextField(_('motivo'), blank=True, default='')
    changed_at = models.DateTimeField(_('modificado en'), auto_now_add=True)

    class Meta:
        verbose_name = _('historial de nota')
        verbose_name_plural = _('historicos de notas')
        ordering = ['-changed_at']

    def __str__(self):
        return f'{self.entry} · {self.previous_grade} → {self.new_grade}'


class CourseSyllabus(models.Model):
    course = models.OneToOneField(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_syllabus',
        verbose_name=_('curso'),
    )
    title = models.CharField(_('título'), max_length=200, blank=True, default='')
    overview = models.TextField(_('resumen general'), blank=True, default='')
    objectives = models.TextField(_('objetivos'), blank=True, default='')
    methodology = models.TextField(_('metodología'), blank=True, default='')
    calendar = models.TextField(_('calendario'), blank=True, default='')
    competencies = models.TextField(_('competencias'), blank=True, default='')
    evaluation_criteria = models.TextField(_('criterios de evaluación'), blank=True, default='')
    bibliography = models.TextField(_('bibliografía'), blank=True, default='')
    teacher_name = models.CharField(_('docente'), max_length=200, blank=True, default='')
    estimated_hours = models.PositiveIntegerField(_('horas estimadas'), default=0)
    documents = models.JSONField(_('documentos'), default=list, blank=True)
    auto_generated_from_modules = models.BooleanField(_('generar a partir de módulos'), default=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('programa del curso')
        verbose_name_plural = _('programas de los cursos')

    def __str__(self):
        return f'Programa de {self.course}'

    def sync_from_course(self):
        if not self.title:
            self.title = f'Programa de {self.course.nombre}'
        if not self.overview:
            self.overview = self.course.descripcion or self.course.resumen or ''
        if not self.teacher_name and self.course.creado_por:
            self.teacher_name = self.course.creado_por.get_full_name() or self.course.creado_por.username
        if self.auto_generated_from_modules:
            if not self.objectives:
                topic_names = [
                    topic.titulo for topic in self.course.temas.order_by('orden', 'pk')
                ]
                self.objectives = '\n'.join(f'- {name}' for name in topic_names) or self.overview
            if not self.methodology:
                self.methodology = _('El curso combina actividades guiadas, trabajo práctico y seguimiento del progreso del alumnado.')
            if not self.competencies:
                self.competencies = _('Aplicar conceptos clave del curso, comprender la metodología y resolver actividades de forma autónoma.')
            if not self.evaluation_criteria:
                self.evaluation_criteria = _('Se evalúa la consecución de los objetivos, la calidad de las entregas y la participación activa.')
            if not self.bibliography:
                self.bibliography = _('Bibliografía del curso disponible en los materiales del aula.')
        self.save()

    def get_units(self):
        units = []
        for index, topic in enumerate(self.course.temas.order_by('orden', 'pk'), start=1):
            units.append({
                'index': index,
                'title': topic.titulo,
                'content': topic.contenido,
                'id': topic.pk,
            })
        return units

    def get_activity_timeline(self):
        activities = list(self.course.learning_activities.select_related('module').all())
        return sorted(
            activities,
            key=lambda activity: (
                activity.starts_at or timezone.now(),
                activity.due_at or timezone.now(),
                activity.title,
            ),
        )


class ModuleSchedule(models.Model):
    module = models.OneToOneField('courses.Tema', on_delete=models.CASCADE, related_name='learning_schedule')
    starts_at = models.DateTimeField(_('apertura del módulo'), null=True, blank=True)
    ends_at = models.DateTimeField(_('cierre del módulo'), null=True, blank=True)
    estimated_minutes = models.PositiveIntegerField(_('duración estimada (minutos)'), default=0)

    def clean(self):
        super().clean()
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({'ends_at': _('El cierre debe ser posterior a la apertura.')})


class FormativeUnit(models.Model):
    module = models.ForeignKey('courses.Tema', on_delete=models.CASCADE, related_name='formative_units')
    title = models.CharField(_('nombre de la unidad formativa'), max_length=200)
    description = models.TextField(_('descripción'), blank=True, default='')
    position = models.PositiveIntegerField(_('orden'), default=0)

    class Meta:
        verbose_name = _('unidad formativa')
        verbose_name_plural = _('unidades formativas')
        ordering = ['position', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['module', 'title'], name='lms_unit_title_per_module'),
        ]

    def __str__(self):
        return f'{self.module.titulo} — {self.title}'

    def clean(self):
        super().clean()
        if self.pk and self.contents.exclude(module_id=self.module_id).exists():
            raise ValidationError({'module': _('Mueve primero los recursos; una unidad con recursos no puede cambiar de módulo.')})


class ModuleContent(models.Model):
    class Criterion(models.TextChoices):
        MANUAL = 'manual', _('Marcar como completado')
        VIEWED = 'viewed', _('Abrir el contenido')
        SUBMITTED = 'submitted', _('Entregar la actividad')
        FINISHED = 'finished', _('Finalizar la prueba')
        PASSED = 'passed', _('Aprobar')

    module = models.ForeignKey('courses.Tema', on_delete=models.CASCADE, related_name='learning_contents')
    unit = models.ForeignKey(
        FormativeUnit, on_delete=models.RESTRICT, related_name='contents',
        verbose_name=_('unidad formativa'), blank=True,
    )
    page = models.OneToOneField('Page', on_delete=models.CASCADE, null=True, blank=True, related_name='learning_content')
    file = models.OneToOneField('FileAsset', on_delete=models.CASCADE, null=True, blank=True, related_name='learning_content')
    quiz = models.OneToOneField('Quiz', on_delete=models.CASCADE, null=True, blank=True, related_name='learning_content')
    activity = models.OneToOneField('LearningActivity', on_delete=models.CASCADE, null=True, blank=True, related_name='learning_content')
    position = models.PositiveIntegerField(_('orden'), default=0)
    required = models.BooleanField(_('obligatorio para completar el módulo'), default=True)
    criterion = models.CharField(_('criterio de finalización'), max_length=16, choices=Criterion.choices, default=Criterion.MANUAL)
    passing_percentage = models.PositiveSmallIntegerField(
        _('porcentaje mínimo para aprobar la actividad'), default=50,
        validators=[MaxValueValidator(100)],
    )
    prerequisite = models.ForeignKey(
        'self', on_delete=models.RESTRICT, null=True, blank=True,
        related_name='dependent_contents', verbose_name=_('contenido previo requerido'),
    )

    class Meta:
        ordering = ['position', 'pk']
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(page__isnull=False, file__isnull=True, quiz__isnull=True, activity__isnull=True)
                    | models.Q(page__isnull=True, file__isnull=False, quiz__isnull=True, activity__isnull=True)
                    | models.Q(page__isnull=True, file__isnull=True, quiz__isnull=False, activity__isnull=True)
                    | models.Q(page__isnull=True, file__isnull=True, quiz__isnull=True, activity__isnull=False)
                ),
                name='lms_module_content_one_target',
            ),
        ]

    @property
    def target(self):
        return self.page or self.file or self.quiz or self.activity

    @property
    def title(self):
        return self.target.title

    @property
    def kind(self):
        return next(name for name in ('page', 'file', 'quiz', 'activity') if getattr(self, f'{name}_id'))

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.unit_id:
            self.unit, _created = FormativeUnit.objects.get_or_create(
                module_id=self.module_id, title='General', defaults={'position': 0},
            )
        if self.unit.module_id != self.module_id:
            raise ValidationError({'unit': _('La unidad formativa debe pertenecer al módulo del contenido.')})
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'unit'}
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.unit_id and self.unit.module_id != self.module_id:
            raise ValidationError({'unit': _('La unidad formativa debe pertenecer al módulo del contenido.')})
        targets = [getattr(self, name) for name in ('page', 'file', 'quiz', 'activity') if getattr(self, f'{name}_id')]
        if len(targets) != 1:
            raise ValidationError(_('Selecciona exactamente un contenido.'))
        if self.module_id and targets[0].course_id != self.module.curso_id:
            raise ValidationError(_('El contenido debe pertenecer al curso del módulo.'))
        if self.kind in ('page', 'activity') and targets[0].module_id not in (None, self.module_id):
            raise ValidationError(_('El contenido ya pertenece a otro módulo.'))
        allowed = {self.Criterion.MANUAL, self.Criterion.VIEWED}
        if self.activity_id:
            allowed |= {self.Criterion.SUBMITTED, self.Criterion.PASSED}
        if self.quiz_id:
            allowed |= {self.Criterion.FINISHED, self.Criterion.PASSED}
        if self.criterion not in allowed:
            raise ValidationError({'criterion': _('Este criterio no corresponde al tipo de contenido.')})
        if self.activity_id and self.criterion == self.Criterion.PASSED and (
            not self.activity.is_evaluable or not self.activity.points
        ):
            raise ValidationError({'criterion': _('Para exigir aprobar, la actividad debe ser evaluable y tener puntos.')})
        seen = {self.pk} if self.pk else set()
        previous = self.prerequisite
        while previous:
            if previous.module_id != self.module_id:
                raise ValidationError({'prerequisite': _('El prerrequisito debe estar en el mismo módulo.')})
            if previous.pk in seen:
                raise ValidationError({'prerequisite': _('Los prerrequisitos no pueden formar ciclos.')})
            seen.add(previous.pk)
            previous = previous.prerequisite


class ModuleContentProgress(models.Model):
    content = models.ForeignKey(ModuleContent, on_delete=models.CASCADE, related_name='progress')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='module_content_progress')
    opened_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['content', 'user'], name='lms_unique_content_user_progress')]


class CourseModule(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        PUBLISHED = 'published', _('Publicado')
        ARCHIVED = 'archived', _('Archivado')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_modules',
        verbose_name=_('curso'),
    )
    title = models.CharField(_('título'), max_length=200)
    slug = models.SlugField(_('slug'), max_length=220, blank=True)
    summary = models.TextField(_('resumen'), blank=True, default='')
    description = models.TextField(_('descripción'), blank=True, default='')
    position = models.PositiveIntegerField(_('orden'), default=0)
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    starts_at = models.DateTimeField(_('inicio'), null=True, blank=True)
    ends_at = models.DateTimeField(_('fin'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('módulo del curso')
        verbose_name_plural = _('módulos del curso')
        ordering = ['position', 'title', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'slug'],
                name='lms_unique_module_slug_per_course',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title or 'modulo')
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.course}: {self.title}'

    def is_available(self, *, now=None):
        now = now or timezone.now()
        if self.status == self.Status.ARCHIVED:
            return False
        if self.starts_at and self.starts_at > now:
            return False
        if self.ends_at and self.ends_at < now:
            return False
        return True


class ModuleItem(models.Model):
    class ItemType(models.TextChoices):
        ACTIVITY = 'activity', _('Actividad')
        PAGE = 'page', _('Página')
        QUIZ = 'quiz', _('Quiz')
        RESOURCE = 'resource', _('Recurso')
        LINK = 'link', _('Enlace')

    module = models.ForeignKey(
        CourseModule,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name=_('módulo'),
    )
    title = models.CharField(_('título'), max_length=200)
    item_type = models.CharField(
        _('tipo de contenido'),
        max_length=12,
        choices=ItemType.choices,
        default=ItemType.ACTIVITY,
    )
    description = models.TextField(_('descripción'), blank=True, default='')
    content = models.TextField(_('contenido'), blank=True, default='')
    page = models.ForeignKey(
        Page,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='module_items',
        verbose_name=_('página'),
    )
    learning_activity = models.ForeignKey(
        'LearningActivity',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='module_items',
        verbose_name=_('actividad'),
    )
    external_url = models.URLField(_('enlace externo'), blank=True, default='')
    position = models.PositiveIntegerField(_('orden'), default=0)
    required = models.BooleanField(_('obligatorio'), default=True)
    due_at = models.DateTimeField(_('vencimiento'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('elemento del módulo')
        verbose_name_plural = _('elementos del módulo')
        ordering = ['position', 'title', 'pk']

    def clean(self):
        super().clean()
        targets = [self.page_id, self.learning_activity_id]
        if not self.external_url and not any(targets):
            raise ValidationError({'item_type': _('Debes asociar un recurso o un enlace al elemento del módulo.')})
        if self.item_type == self.ItemType.ACTIVITY and not self.learning_activity_id:
            raise ValidationError({'learning_activity': _('La actividad debe apuntar a una actividad de aprendizaje existente.')})
        if self.item_type == self.ItemType.PAGE and not self.page_id:
            raise ValidationError({'page': _('La página debe apuntar a una página existente del curso.')})
        if self.item_type == self.ItemType.LINK and not self.external_url:
            raise ValidationError({'external_url': _('Añade la URL del enlace externo.')})

    def __str__(self):
        return f'{self.module}: {self.title}'


class CodeExercise(models.Model):
    class Language(models.TextChoices):
        PYTHON = 'python', _('Python')
        JAVASCRIPT = 'javascript', _('JavaScript')
        TYPESCRIPT = 'typescript', _('TypeScript')

    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        PUBLISHED = 'published', _('Publicado')
        ARCHIVED = 'archived', _('Archivado')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='code_exercises',
        verbose_name=_('curso'),
    )
    module = models.ForeignKey(
        'CourseModule',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='code_exercises',
        verbose_name=_('módulo'),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lms_code_exercises',
        verbose_name=_('creado por'),
    )
    title = models.CharField(_('título'), max_length=200)
    slug = models.SlugField(_('slug'), max_length=220, blank=True)
    description = models.TextField(_('descripción'), blank=True, default='')
    instructions = models.TextField(_('instrucciones'), blank=True, default='')
    language = models.CharField(_('lenguaje'), max_length=20, choices=Language.choices, default=Language.PYTHON)
    starter_code = models.TextField(_('código inicial'), blank=True, default='')
    public_tests = models.JSONField(_('tests públicos'), default=list, blank=True)
    hidden_tests = models.JSONField(_('tests ocultos'), default=list, blank=True)
    max_attempts = models.PositiveIntegerField(_('máximo de intentos'), default=3)
    points = models.PositiveIntegerField(_('puntos'), default=0)
    cpu_limit_ms = models.PositiveIntegerField(_('límite CPU (ms)'), default=500)
    memory_limit_mb = models.PositiveIntegerField(_('límite memoria (MB)'), default=256)
    timeout_seconds = models.PositiveIntegerField(_('tiempo límite (s)'), default=10)
    sandbox_enabled = models.BooleanField(_('sandbox activa'), default=False)
    allow_network = models.BooleanField(_('permitir red'), default=False)
    status = models.CharField(_('estado'), max_length=12, choices=Status.choices, default=Status.DRAFT)
    available_from = models.DateTimeField(_('disponible desde'), null=True, blank=True)
    due_at = models.DateTimeField(_('fecha límite'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('ejercicio de código')
        verbose_name_plural = _('ejercicios de código')
        ordering = ['available_from', 'due_at', 'title', 'pk']

    def clean(self):
        super().clean()
        if self.available_from and self.due_at and self.due_at < self.available_from:
            raise ValidationError({'due_at': _('La fecha límite debe ser posterior a la disponibilidad.')})
        if not isinstance(self.public_tests, list):
            raise ValidationError({'public_tests': _('Los tests públicos deben ser una lista JSON.')})
        if not isinstance(self.hidden_tests, list):
            raise ValidationError({'hidden_tests': _('Los tests ocultos deben ser una lista JSON.')})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title or 'ejercicio-codigo')
        super().save(*args, **kwargs)

    def to_public_summary(self):
        public_tests = []
        for item in self.public_tests:
            if not isinstance(item, dict):
                continue
            sanitized = dict(item)
            sanitized.pop('expected', None)
            sanitized.pop('stdout', None)
            sanitized.pop('stderr', None)
            sanitized.pop('secret', None)
            public_tests.append(sanitized)
        return {
            'id': self.pk,
            'title': self.title,
            'language': self.language,
            'description': self.description,
            'instructions': self.instructions,
            'starter_code': self.starter_code,
            'public_tests': public_tests,
            'max_attempts': self.max_attempts,
            'points': self.points,
            'sandbox_enabled': self.sandbox_enabled,
            'allows_network': self.allow_network,
        }

    def __str__(self):
        return f'{self.course}: {self.title}'


class CodeSubmission(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        SUBMITTED = 'submitted', _('Enviado')
        RUNNING = 'running', _('Ejecución en curso')
        PASSED = 'passed', _('Aprobado')
        FAILED = 'failed', _('Fallido')
        BLOCKED = 'blocked', _('Bloqueado')

    exercise = models.ForeignKey(
        CodeExercise,
        on_delete=models.CASCADE,
        related_name='submissions',
        verbose_name=_('ejercicio'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='code_exercise_submissions',
        verbose_name=_('usuario'),
    )
    source_code = models.TextField(_('código entregado'))
    attempt_number = models.PositiveIntegerField(_('intento'), default=1)
    status = models.CharField(_('estado'), max_length=12, choices=Status.choices, default=Status.DRAFT)
    score = models.PositiveSmallIntegerField(_('puntuación'), default=0)
    feedback = models.TextField(_('feedback'), blank=True, default='')
    submitted_at = models.DateTimeField(_('enviado en'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('entrega de ejercicio de código')
        verbose_name_plural = _('entregas de ejercicios de código')
        ordering = ['-submitted_at', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['exercise', 'user', 'attempt_number'], name='lms_unique_code_submission_attempt'),
        ]

    def clean(self):
        super().clean()
        if not self.source_code.strip():
            raise ValidationError({'source_code': _('Debes enviar código válido.')})

    def __str__(self):
        return f'{self.user} · {self.exercise} · intento {self.attempt_number}'


class CodeExecutionResult(models.Model):
    class Status(models.TextChoices):
        BLOCKED = 'blocked', _('Bloqueado por seguridad')
        SUCCESS = 'success', _('Correcto')
        FAILED = 'failed', _('Fallido')
        TIMEOUT = 'timeout', _('Tiempo agotado')
        RUNTIME_ERROR = 'runtime_error', _('Error de ejecución')

    submission = models.ForeignKey(
        CodeSubmission,
        on_delete=models.CASCADE,
        related_name='execution_results',
        verbose_name=_('entrega'),
    )
    status = models.CharField(_('estado'), max_length=16, choices=Status.choices, default=Status.BLOCKED)
    stdout = models.TextField(_('salida estándar'), blank=True, default='')
    stderr = models.TextField(_('error estándar'), blank=True, default='')
    exit_code = models.IntegerField(_('código de salida'), default=0)
    duration_ms = models.PositiveIntegerField(_('duración (ms)'), default=0)
    memory_used_mb = models.PositiveIntegerField(_('memoria usada (MB)'), default=0)
    summary = models.TextField(_('resumen'), blank=True, default='')
    sandbox_version = models.CharField(_('versión de sandbox'), max_length=80, blank=True, default='')
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('resultado de ejecución')
        verbose_name_plural = _('resultados de ejecución')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.submission} · {self.get_status_display()}'


class LearningActivity(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        OPEN = 'open', _('Abierta')
        CLOSED = 'closed', _('Cerrada')

    class TimelineState(models.TextChoices):
        FUTURE = 'future', _('Futuro')
        IN_PROGRESS = 'in_progress', _('En curso')
        OVERDUE = 'overdue', _('Vencido')
        COMPLETED = 'completed', _('Completado')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='learning_activities',
        verbose_name=_('curso'),
    )
    module = models.ForeignKey(
        'courses.Tema',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='learning_activities',
        verbose_name=_('módulo'),
    )
    title = models.CharField(_('título'), max_length=200)
    reference_code = models.CharField(_('referencia'), max_length=64, blank=True, default='')
    description = models.TextField(_('descripción'), blank=True, default='')
    instructions = models.TextField(_('instrucciones'), blank=True, default='')
    solution = models.TextField(_('solución'), blank=True, default='')
    lifecycle_state = models.CharField(max_length=12, blank=True, default='', editable=False)
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    starts_at = models.DateTimeField(_('inicio'), null=True, blank=True)
    due_at = models.DateTimeField(_('entrega'), null=True, blank=True)
    feedback_at = models.DateTimeField(_('fecha de feedback'), null=True, blank=True)
    solution_release_at = models.DateTimeField(_('solución disponible'), null=True, blank=True)
    points = models.PositiveIntegerField(_('puntos'), default=0)
    is_evaluable = models.BooleanField(_('evaluable'), default=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('actividad de aprendizaje')
        verbose_name_plural = _('actividades de aprendizaje')
        ordering = ['starts_at', 'due_at', 'title', 'pk']

    def clean(self):
        super().clean()
        content = getattr(self, 'learning_content', None) if self.pk else None
        if content and self.module_id != content.module_id:
            raise ValidationError({'module': _('Retira primero el recurso de su unidad formativa antes de cambiarlo de módulo.')})
        if self.module_id and self.course_id and self.module.curso_id != self.course_id:
            raise ValidationError({'module': _('El módulo debe pertenecer al curso de la actividad.')})
        if self.starts_at and self.due_at and self.due_at < self.starts_at:
            raise ValidationError({'due_at': _('La fecha de entrega debe ser posterior al inicio.')})
        if self.feedback_at and self.due_at and self.feedback_at < self.due_at:
            raise ValidationError({'feedback_at': _('El feedback debe publicarse después de la entrega.')})
        if self.solution_release_at and self.feedback_at and self.solution_release_at < self.feedback_at:
            raise ValidationError({'solution_release_at': _('La solución debe publicarse después del feedback.')})

    def __str__(self):
        return f'{self.course}: {self.title}'

    def save(self, *args, **kwargs):
        self.description = sanitize_html_content(self.description)
        self.instructions = sanitize_html_content(self.instructions)
        self.solution = sanitize_html_content(self.solution)
        super().save(*args, **kwargs)

    @property
    def safe_description(self):
        return sanitize_html_content(self.description)

    @property
    def safe_instructions(self):
        return sanitize_html_content(self.instructions)

    @property
    def safe_solution(self):
        return sanitize_html_content(self.solution)

    def is_available(self, *, now=None):
        now = now or timezone.now()
        if self.status == self.Status.CLOSED:
            return False
        if self.starts_at and self.starts_at > now:
            return False
        if self.due_at and self.due_at < now and self.status != self.Status.OPEN:
            return False
        return True

    def timeline_state(self, *, now=None):
        now = now or timezone.now()
        if self.status == self.Status.CLOSED:
            return self.TimelineState.COMPLETED
        if self.starts_at and now < self.starts_at:
            return self.TimelineState.FUTURE
        if self.due_at and now > self.due_at:
            return self.TimelineState.OVERDUE
        return self.TimelineState.IN_PROGRESS

    def can_show_solution(self, *, now=None):
        now = now or timezone.now()
        return bool(
            self.solution_release_at and self.solution_release_at <= now
        )

    def get_visible_sections(self, *, now=None, include_solution=False):
        sections = self.sections.order_by('position', 'pk')
        if include_solution or self.can_show_solution(now=now):
            return list(sections)
        return [section for section in sections if not section.is_solution_section]


class LearningActivitySection(models.Model):
    class SectionType(models.TextChoices):
        DESCRIPTION = 'description', _('Descripción y enunciado')
        OBJECTIVES = 'objectives', _('Competencias y objetivos')
        CONTENT = 'content', _('Contenidos y recursos')
        LEARNING_RESOURCES = 'learning_resources', _('Recursos de aprendizaje')
        SOLUTION = 'solution', _('Solución')

    activity = models.ForeignKey(
        LearningActivity,
        on_delete=models.CASCADE,
        related_name='sections',
        verbose_name=_('actividad'),
    )
    title = models.CharField(_('título'), max_length=200)
    section_type = models.CharField(
        _('tipo de sección'),
        max_length=24,
        choices=SectionType.choices,
        default=SectionType.DESCRIPTION,
    )
    body = models.TextField(_('contenido'), blank=True, default='')
    position = models.PositiveIntegerField(_('orden'), default=0)
    is_solution_section = models.BooleanField(_('es sección de solución'), default=False)
    allow_attachments = models.BooleanField(_('admite adjuntos'), default=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('sección de actividad')
        verbose_name_plural = _('secciones de actividad')
        ordering = ['position', 'pk']

    def clean(self):
        super().clean()
        if self.section_type == self.SectionType.SOLUTION or self.is_solution_section:
            self.is_solution_section = True

    def save(self, *args, **kwargs):
        if self.section_type == self.SectionType.SOLUTION:
            self.is_solution_section = True
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.activity}: {self.title}'


class Assignment(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        PUBLISHED = 'published', _('Publicado')
        ARCHIVED = 'archived', _('Archivado')
        CLOSED = 'closed', _('Cerrado')

    class SubmissionType(models.TextChoices):
        TEXT = 'text', _('Texto')
        FILE = 'file', _('Archivo')
        LINK = 'link', _('Enlace')
        REPOSITORY = 'repository', _('Repositorio')
        GROUP = 'group', _('Entrega grupal')

    class LatePolicy(models.TextChoices):
        NONE = 'none', _('Sin penalización')
        PENALIZE = 'penalize', _('Penalizar')
        LOCK = 'lock', _('Bloquear después del plazo')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_assignments',
        verbose_name=_('curso'),
    )
    learning_activity = models.OneToOneField(
        LearningActivity, on_delete=models.CASCADE, null=True, blank=True,
        related_name='assignment', verbose_name=_('actividad de aprendizaje'),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lms_assignments',
        verbose_name=_('creado por'),
    )
    title = models.CharField(_('título'), max_length=200)
    slug = models.SlugField(_('slug'), max_length=220, blank=True)
    description = models.TextField(_('descripción'), blank=True, default='')
    instructions = models.TextField(_('instrucciones'), blank=True, default='')
    points = models.PositiveIntegerField(_('puntos'), default=0)
    available_at = models.DateTimeField(_('disponible desde'), null=True, blank=True)
    due_at = models.DateTimeField(_('fecha de entrega'), null=True, blank=True)
    lock_at = models.DateTimeField(_('bloqueo de entrega'), null=True, blank=True)
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    is_pec = models.BooleanField(_('es PEC'), default=False)
    pec_statement = models.TextField(_('enunciado PEC'), blank=True, default='')
    learning_objectives = models.TextField(_('objetivos de aprendizaje'), blank=True, default='')
    competencies = models.TextField(_('competencias'), blank=True, default='')
    resource_links = models.JSONField(_('recursos de la PEC'), default=list, blank=True)
    submission_types = models.JSONField(_('tipos de entrega'), default=list, blank=True)
    max_attempts = models.PositiveIntegerField(_('máximo de entregas'), default=1)
    allow_group_submission = models.BooleanField(_('permite entrega grupal'), default=False)
    allow_resubmission = models.BooleanField(_('permite reentrega'), default=False)
    date_adjustment_allowed = models.BooleanField(_('permite adaptación de fechas'), default=False)
    justification_required = models.BooleanField(_('requiere justificación'), default=False)
    realization_start = models.DateTimeField(_('inicio de realización'), null=True, blank=True)
    realization_end = models.DateTimeField(_('fin de realización'), null=True, blank=True)
    solution_release_at = models.DateTimeField(_('fecha de publicación de solución'), null=True, blank=True)
    feedback_published_at = models.DateTimeField(_('fecha de publicación de feedback'), null=True, blank=True)
    rubric = models.ForeignKey(
        'Rubric',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assignments',
        verbose_name=_('rúbrica'),
    )
    rubric_snapshot = models.JSONField(_('instantánea de rúbrica'), default=dict, blank=True)
    late_submission_policy = models.CharField(
        _('política de retraso'),
        max_length=12,
        choices=LatePolicy.choices,
        default=LatePolicy.NONE,
    )
    late_penalty_percent = models.PositiveIntegerField(_('penalización por retraso (%)'), default=0)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('tarea/PEC')
        verbose_name_plural = _('tareas/PEC')
        ordering = ['available_at', 'due_at', 'title', 'pk']

    def clean(self):
        super().clean()
        if self.available_at and self.due_at and self.due_at < self.available_at:
            raise ValidationError({'due_at': _('La fecha de entrega debe ser posterior a la fecha de apertura.')})
        if self.lock_at and self.due_at and self.lock_at < self.due_at:
            raise ValidationError({'lock_at': _('El cierre de entregas debe ser posterior a la fecha de entrega.')})
        if self.late_penalty_percent < 0 or self.late_penalty_percent > 100:
            raise ValidationError({'late_penalty_percent': _('La penalización por retraso debe estar entre 0 y 100%.')})
        if not isinstance(self.submission_types, list) or not self.submission_types:
            raise ValidationError({'submission_types': _('Debes indicar al menos un formato de entrega válido.')})

    def save(self, *args, **kwargs):
        self.instructions = sanitize_html_content(self.instructions)
        self.description = sanitize_html_content(self.description)
        if not self.slug:
            self.slug = slugify(self.title or 'tarea')
        super().save(*args, **kwargs)

    def can_submit(self, user, *, now=None):
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if self.status != self.Status.PUBLISHED:
            return False
        timestamp = now or timezone.now()
        if self.available_at and timestamp < self.available_at:
            return False
        if self.lock_at and timestamp > self.lock_at:
            return False
        if not CourseMembership.objects.filter(
            course=self.course,
            user=user,
            status=CourseMembership.Status.ACTIVE,
        ).exists():
            return False
        current_attempts = self.submissions.filter(user=user).count()
        return current_attempts < self.max_attempts if self.max_attempts else True

    def create_submission(self, user, *, now=None, course=None):
        if course and course.pk != self.course_id:
            raise ValueError(_('La entrega no pertenece al curso de la tarea.'))
        if not self.can_submit(user, now=now):
            raise ValueError(_('No se puede entregar esta tarea en este momento.'))
        existing = self.submissions.filter(user=user).order_by('-version', '-created_at')
        if self.max_attempts and existing.count() >= self.max_attempts:
            raise ValueError(_('Se ha alcanzado el máximo de entregas para esta tarea.'))
        version = (existing.first().version if existing.exists() else 0) + 1
        if self.rubric_id and not self.rubric_snapshot:
            criteria = [
                {
                    'id': criterion.pk,
                    'title': criterion.title,
                    'description': criterion.description,
                    'points': float(criterion.points),
                    'weight': float(criterion.weight),
                    'levels': [
                        {
                            'id': level.pk,
                            'label': level.label,
                            'description': level.description,
                            'points': float(level.points),
                            'order': level.order,
                        }
                        for level in criterion.levels.all()
                    ],
                }
                for criterion in self.rubric.criteria.all()
            ]
            max_points = sum((float(criterion.points) for criterion in self.rubric.criteria.all()), 0.0)
            self.rubric_snapshot = {'name': self.rubric.name, 'criteria': criteria, 'max_points': max_points}
            self.save(update_fields=['rubric_snapshot', 'updated_at'])
        return Submission.objects.create(
            assignment=self,
            user=user,
            version=version,
            status=Submission.Status.DRAFT,
            receipt_identifier=f'{uuid4().hex}-{self.pk}-{user.pk}',
            rubrica_snapshot=self.rubric_snapshot,
        )

    def rec_rows(self, *, user=None):
        rows = []
        for membership in CourseMembership.objects.filter(
            course=self.course,
            status=CourseMembership.Status.ACTIVE,
            role=CourseMembership.Role.STUDENT,
        ).select_related('user'):
            submission = self.submissions.filter(user=membership.user).order_by('-version', '-created_at').first()
            row = {
                'user': membership.user,
                'submission': submission,
                'status': submission.status if submission else 'not_started',
                'grade': submission.grade if submission else Decimal('0'),
                'feedback': submission.feedback if submission else '',
                'visible': submission.is_feedback_visible_to_user(user) if submission and user else False,
            }
            rows.append(row)
        return rows

    def __str__(self):
        return f'{self.course}: {self.title}'


class Submission(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        SUBMITTED = 'submitted', _('Entregada')
        LATE = 'late', _('Tarde')
        GRADED = 'graded', _('Calificada')
        RETURNED = 'returned', _('Devuelta')

    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name='submissions',
        verbose_name=_('tarea'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_submissions',
        verbose_name=_('usuario'),
    )
    version = models.PositiveIntegerField(_('versión'), default=1)
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    receipt_identifier = models.CharField(_('identificador de recibo'), max_length=120, unique=True)
    submitted_at = models.DateTimeField(_('entregado en'), null=True, blank=True)
    graded_at = models.DateTimeField(_('calificado en'), null=True, blank=True)
    returned_at = models.DateTimeField(_('devuelto en'), null=True, blank=True)
    text_answer = models.TextField(_('respuesta textual'), blank=True, default='')
    file_url = models.URLField(_('URL del archivo'), blank=True, default='')
    grade = models.DecimalField(_('nota'), max_digits=8, decimal_places=2, default=0)
    feedback = models.TextField(_('retroalimentación'), blank=True, default='')
    feedback_published = models.BooleanField(_('feedback publicado'), default=False)
    feedback_published_at = models.DateTimeField(_('feedback publicado en'), null=True, blank=True)
    rubrica_snapshot = models.JSONField(_('instantánea de rúbrica'), default=dict, blank=True)
    review_status = models.CharField(_('estado de revisión'), max_length=20, default='pending', blank=True)
    metadata = models.JSONField(_('metadatos'), default=dict, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('entrega')
        verbose_name_plural = _('entregas')
        ordering = ['-submitted_at', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['assignment', 'user', 'version'], name='lms_unique_submission_version_per_user'),
        ]

    def clean(self):
        super().clean()
        if self.assignment_id and self.user_id and self.assignment.course_id and self.assignment.course_id != CourseMembership.objects.filter(user=self.user, course_id=self.assignment.course_id).values_list('course_id', flat=True).first():
            raise ValidationError({'user': _('La entrega debe corresponder a un alumno del curso de la tarea.')})

    def submit(self, *, now=None):
        if self.status in {self.Status.SUBMITTED, self.Status.LATE, self.Status.GRADED, self.Status.RETURNED}:
            raise ValueError(_('Esta entrega ya ha sido enviada o calificada.'))
        timestamp = now or timezone.now()
        if self.assignment.due_at and timestamp > self.assignment.due_at:
            self.status = self.Status.LATE if self.assignment.late_submission_policy != self.assignment.LatePolicy.LOCK else self.Status.SUBMITTED
            if self.assignment.late_submission_policy == self.assignment.LatePolicy.LOCK:
                raise ValueError(_('La entrega tardía está bloqueada por la política del curso.'))
        else:
            self.status = self.Status.SUBMITTED
        self.submitted_at = timestamp
        self.save(update_fields=['status', 'submitted_at', 'updated_at'])
        return self

    def is_visible_to_user(self, user):
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if user.pk == self.user_id:
            return True
        membership = CourseMembership.objects.filter(
            course=self.assignment.course,
            user=user,
            status=CourseMembership.Status.ACTIVE,
        ).first()
        if not membership:
            return False
        return membership.role in {CourseMembership.Role.OWNER, CourseMembership.Role.TEACHER, CourseMembership.Role.ASSISTANT}

    def is_feedback_visible_to_user(self, user):
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if user.pk == self.user_id:
            return bool(self.feedback_published or self.returned_at or self.status in {self.Status.GRADED, self.Status.RETURNED})
        return self.is_visible_to_user(user) and bool(self.feedback_published or self.returned_at)

    def add_attachment(self, *, file=None, file_name='', mime_type='', size=None):
        if file is None:
            raise ValueError(_('Debes adjuntar un archivo válido.'))
        attachment = SubmissionAttachment.objects.create(
            submission=self,
            file=file,
            file_name=file_name or getattr(file, 'name', ''),
            mime_type=mime_type or getattr(file, 'content_type', 'application/octet-stream'),
            size=size or getattr(file, 'size', 0),
        )
        return attachment

    def publish_feedback(self, *, now=None):
        timestamp = now or timezone.now()
        self.feedback_published = True
        self.feedback_published_at = timestamp
        self.status = self.Status.RETURNED
        self.returned_at = self.returned_at or timestamp
        self.save(update_fields=['feedback_published', 'feedback_published_at', 'status', 'returned_at', 'updated_at'])
        return self

    def __str__(self):
        return f'{self.user} → {self.assignment} (v{self.version})'


class SubmissionAttachment(models.Model):
    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name=_('entrega'),
    )
    file = models.FileField(
        _('archivo adjunto'),
        upload_to='lms/submissions/%Y/%m/%d/',
        storage=PrivateMediaStorage(),
        max_length=500,
    )
    file_name = models.CharField(_('nombre del archivo'), max_length=250, default='')
    mime_type = models.CharField(_('tipo MIME'), max_length=150, default='')
    size = models.PositiveBigIntegerField(_('tamaño'), default=0)
    uploaded_at = models.DateTimeField(_('subido en'), auto_now_add=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)

    class Meta:
        verbose_name = _('archivo de entrega')
        verbose_name_plural = _('archivos de entrega')
        ordering = ['uploaded_at', 'pk']

    def __str__(self):
        return self.file_name or str(self.file)


class Rubric(models.Model):
    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_rubrics',
        verbose_name=_('curso'),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lms_rubrics',
        verbose_name=_('creado por'),
    )
    name = models.CharField(_('nombre'), max_length=200)
    description = models.TextField(_('descripción'), blank=True, default='')
    is_template = models.BooleanField(_('es plantilla reutilizable'), default=False)
    published_snapshot = models.JSONField(_('instantánea publicada'), default=dict, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('rúbrica')
        verbose_name_plural = _('rúbricas')
        ordering = ['name', 'pk']

    def get_snapshot(self):
        criteria = []
        for criterion in self.criteria.order_by('order', 'pk').all():
            levels = [
                {
                    'id': level.pk,
                    'label': level.label,
                    'description': level.description,
                    'points': float(level.points),
                    'order': level.order,
                }
                for level in criterion.levels.order_by('order', 'pk').all()
            ]
            criteria.append({
                'id': criterion.pk,
                'title': criterion.title,
                'description': criterion.description,
                'points': float(criterion.points),
                'weight': float(criterion.weight),
                'order': criterion.order,
                'levels': levels,
            })
        max_points = sum((Decimal(str(item['points'])) for item in criteria), Decimal('0'))
        return {'name': self.name, 'description': self.description, 'max_points': float(max_points), 'criteria': criteria}

    def publish_snapshot(self):
        self.published_snapshot = self.get_snapshot()
        self.save(update_fields=['published_snapshot', 'updated_at'])
        return self.published_snapshot

    def clean(self):
        super().clean()
        snapshot = self.get_snapshot()
        max_points = Decimal(str(snapshot['max_points']))
        if max_points < 0:
            raise ValidationError({'name': _('La rúbrica no puede tener un máximo negativo.')})
        if not snapshot['criteria']:
            raise ValidationError({'name': _('La rúbrica debe incluir al menos un criterio.')})
        if self.published_snapshot and self.published_snapshot.get('max_points') is not None:
            self.published_snapshot = snapshot

    def __str__(self):
        return self.name


class RubricCriterion(models.Model):
    rubric = models.ForeignKey(
        Rubric,
        on_delete=models.CASCADE,
        related_name='criteria',
        verbose_name=_('rúbrica'),
    )
    title = models.CharField(_('título'), max_length=200)
    description = models.TextField(_('descripción'), blank=True, default='')
    points = models.DecimalField(_('puntos'), max_digits=8, decimal_places=2, default=0)
    weight = models.DecimalField(_('ponderación'), max_digits=5, decimal_places=2, default=1)
    order = models.PositiveIntegerField(_('orden'), default=0)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('criterio de rúbrica')
        verbose_name_plural = _('criterios de rúbrica')
        ordering = ['order', 'pk']

    def snapshot_data(self):
        levels = [
            {
                'id': level.pk,
                'label': level.label,
                'description': level.description,
                'points': float(level.points),
                'order': level.order,
            }
            for level in self.levels.order_by('order', 'pk').all()
        ]
        return {
            'id': self.pk,
            'title': self.title,
            'description': self.description,
            'points': float(self.points),
            'weight': float(self.weight),
            'order': self.order,
            'levels': levels,
        }

    def __str__(self):
        return f'{self.rubric}: {self.title}'


class RubricLevel(models.Model):
    criterion = models.ForeignKey(
        RubricCriterion,
        on_delete=models.CASCADE,
        related_name='levels',
        verbose_name=_('criterio'),
    )
    label = models.CharField(_('nivel'), max_length=120)
    description = models.TextField(_('descripción'), blank=True, default='')
    points = models.DecimalField(_('puntos'), max_digits=8, decimal_places=2, default=0)
    order = models.PositiveIntegerField(_('orden'), default=0)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('nivel de rúbrica')
        verbose_name_plural = _('niveles de rúbrica')
        ordering = ['order', 'pk']

    def __str__(self):
        return f'{self.criterion}: {self.label}'


class RubricAssessment(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        SUBMITTED = 'submitted', _('Enviada')
        PUBLISHED = 'published', _('Publicada')

    rubric = models.ForeignKey(
        Rubric,
        on_delete=models.CASCADE,
        related_name='assessments',
        verbose_name=_('rúbrica'),
    )
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name='rubric_assessments',
        verbose_name=_('tarea/PEC'),
        null=True,
        blank=True,
    )
    submission = models.OneToOneField(
        Submission,
        on_delete=models.CASCADE,
        related_name='rubric_assessment',
        verbose_name=_('entrega'),
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lms_rubric_assessments',
        verbose_name=_('alumno'),
    )
    snapshot = models.JSONField(_('instantánea de evaluación'), default=dict, blank=True)
    criterion_scores = models.JSONField(_('puntuaciones por criterio'), default=dict, blank=True)
    feedback = models.TextField(_('comentarios'), blank=True, default='')
    score = models.DecimalField(_('nota final'), max_digits=8, decimal_places=2, default=0)
    max_score = models.DecimalField(_('máximo posible'), max_digits=8, decimal_places=2, default=0)
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    published_at = models.DateTimeField(_('publicada en'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)

    class Meta:
        verbose_name = _('evaluación de rúbrica')
        verbose_name_plural = _('evaluaciones de rúbrica')
        ordering = ['-created_at']

    def freeze_snapshot(self):
        if not self.snapshot:
            self.snapshot = self.rubric.get_snapshot()
        return self.snapshot

    def calculate_score(self, *, criterion_scores=None):
        snapshot = self.freeze_snapshot()
        criteria = snapshot.get('criteria', [])
        score_total = Decimal('0')
        max_total = Decimal('0')
        selected = criterion_scores or self.criterion_scores or {}
        for criterion in criteria:
            criterion_id = str(criterion['id'])
            criterion_points = Decimal(str(criterion.get('points', 0) or 0))
            criterion_weight = Decimal(str(criterion.get('weight', 1) or 1))
            level_points = Decimal(str((selected.get(criterion_id) or {}).get('points', 0) or 0))
            max_total += criterion_points * criterion_weight
            score_total += min(level_points, criterion_points) * criterion_weight
        self.score = score_total.quantize(Decimal('0.01'))
        self.max_score = max_total.quantize(Decimal('0.01'))
        self.criterion_scores = selected
        return self.score, self.max_score

    def publish(self, *, now=None):
        if not self.snapshot:
            self.freeze_snapshot()
        self.calculate_score()
        self.status = self.Status.PUBLISHED
        self.published_at = now or timezone.now()
        self.save(update_fields=['snapshot', 'criterion_scores', 'feedback', 'score', 'max_score', 'status', 'published_at', 'updated_at'])
        return self

    def __str__(self):
        return f'{self.user} · {self.rubric}'


class CourseInvitation(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', _('Pendiente')
        ACCEPTED = 'accepted', _('Aceptada')
        DECLINED = 'declined', _('Rechazada')
        EXPIRED = 'expired', _('Caducada')

    course = models.ForeignKey(
        'courses.Curso',
        on_delete=models.CASCADE,
        related_name='lms_invitations',
    )
    email = models.EmailField(_('email'))
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='course_invitations',
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_course_invitations',
    )
    role = models.CharField(
        _('rol en el curso'),
        max_length=12,
        choices=CourseMembership.Role.choices,
        default=CourseMembership.Role.STUDENT,
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invitations',
    )
    status = models.CharField(
        _('estado'),
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    token = models.CharField(_('token'), max_length=64, blank=True, default='')
    expires_at = models.DateTimeField(_('caduca'), null=True, blank=True)
    created_at = models.DateTimeField(_('creado'), auto_now_add=True)
    updated_at = models.DateTimeField(_('actualizado'), auto_now=True)
    metadata = models.JSONField(_('metadatos'), default=dict, blank=True)

    class Meta:
        verbose_name = _('invitación del curso')
        verbose_name_plural = _('invitaciones del curso')
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'email'],
                name='lms_unique_course_invite_email',
            ),
        ]

    def __str__(self):
        return f'{self.email} → {self.course}'


class AuditEvent(models.Model):
    class Action(models.TextChoices):
        COURSE_CREATED = 'course_created', _('Curso creado')
        COURSE_UPDATED = 'course_updated', _('Curso actualizado')
        COURSE_PUBLISHED = 'course_published', _('Curso publicado')
        MEMBER_ENROLLED = 'member_enrolled', _('Estudiante inscrito')
        MEMBER_INVITED = 'member_invited', _('Miembro invitado')
        MEMBER_STATUS_CHANGED = 'member_status_changed', _('Estado de pertenencia cambiado')

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='lms_audit_events',
    )
    course = models.ForeignKey(
        'courses.Curso',
        null=True,
        on_delete=models.SET_NULL,
        related_name='lms_audit_events',
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='lms_audit_targets',
    )
    action = models.CharField(_('acción'), max_length=32, choices=Action.choices)
    metadata = models.JSONField(_('metadatos'), default=dict, blank=True)
    created_at = models.DateTimeField(_('fecha'), auto_now_add=True)

    class Meta:
        verbose_name = _('evento de auditoría LMS')
        verbose_name_plural = _('eventos de auditoría LMS')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['course', 'created_at'], name='lms_audit_course_time'),
        ]

    def __str__(self):
        return f'{self.get_action_display()} — {self.created_at:%Y-%m-%d %H:%M}'
