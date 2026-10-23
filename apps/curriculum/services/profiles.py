from datetime import date, datetime
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, OuterRef, Q, Subquery
from django.utils import timezone

from apps.users.access import AccessDecision, can, effective_plan
from apps.users.models import PurchasedEntitlement
from apps.curriculum.models import CVProfile, ProfileTemplate


PROFILE_LIMITS = {'free': 1, 'plus': 3, 'pro': 10}
EXTRA_PROFILE_ENTITLEMENT = 'cv-profile-slot'


def available_templates():
    latest_version = ProfileTemplate.objects.filter(
        code=OuterRef('code'),
        status__in=[ProfileTemplate.Status.PUBLISHED, ProfileTemplate.Status.ARCHIVED],
    ).order_by('-version').values('version')[:1]
    return ProfileTemplate.objects.filter(
        status=ProfileTemplate.Status.PUBLISHED,
    ).annotate(latest_version=Subquery(latest_version)).filter(
        version=F('latest_version'),
    )


def template_access(actor, template):
    if template.status != ProfileTemplate.Status.PUBLISHED:
        return AccessDecision(False, 'not_available')
    if not getattr(actor, 'is_authenticated', False):
        return can(actor, 'view_plan_content', required_plan='free')
    for plan in ('free', 'plus', 'pro'):
        if plan in template.allowed_plans and can(
            actor, 'view_plan_content', required_plan=plan,
        ).allowed:
            return can(actor, 'view_plan_content', required_plan=plan)
    purchase = can(actor, 'access_entitlement', entitlement=f'profile-template-{template.code}')
    if purchase.allowed:
        return purchase
    return can(
        actor,
        'view_plan_content',
        required_plan=template.allowed_plans[0] if template.allowed_plans else 'pro',
    )


def profile_limit(actor):
    plan = effective_plan(actor)
    base = PROFILE_LIMITS.get(plan, PROFILE_LIMITS['free'])
    now = timezone.now()
    purchased_slots = PurchasedEntitlement.objects.filter(
        user=actor,
        code=EXTRA_PROFILE_ENTITLEMENT,
        revoked_at__isnull=True,
        starts_at__lte=now,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).count()
    return base + purchased_slots


def create_profile(profile, actor):
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=actor.pk)
        count = CVProfile.objects.filter(
            user_id=actor.pk,
        ).exclude(status=CVProfile.Status.ARCHIVED).count()
        if count >= profile_limit(actor):
            raise OverflowError('Has alcanzado el límite de perfiles de tu plan.')
        profile.user = actor
        profile.save()
        return profile


def duplicate_profile(profile, actor):
    if profile.user_id != actor.pk:
        raise PermissionError('El perfil pertenece a otra cuenta.')
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=actor.pk)
        if profile.profile_template_id and not template_access(actor, profile.profile_template).allowed:
            raise PermissionError('No tienes acceso a la plantilla actual para crear otra versión.')
        clone = CVProfile(
            user=actor,
            cv=profile.cv,
            rama_principal=profile.rama_principal,
            profile_template=profile.profile_template,
            template_version=profile.template_version,
            published_template=None,
            published_template_version=1,
            published_brand_color=profile.brand_color,
            template_config=dict(profile.template_config),
            brand_color=profile.brand_color,
            display_name=profile.display_name,
            professional_headline=profile.professional_headline,
            presentation=profile.presentation,
            profile_location=profile.profile_location,
            contact_email=profile.contact_email,
            contact_phone=profile.contact_phone,
            nombre=f'Copia de {profile.nombre}'[:200],
            slug='',
            status=CVProfile.Status.DRAFT,
            revision=1,
            published_snapshot={},
            show_email=profile.show_email,
            show_phone=profile.show_phone,
            show_location=profile.show_location,
            show_photo=profile.show_photo,
            mostrar_perfil_profesional=profile.mostrar_perfil_profesional,
            mostrar_redes=profile.mostrar_redes,
            mostrar_intereses=profile.mostrar_intereses,
            mostrar_otros=profile.mostrar_otros,
            mostrar_voluntariado=profile.mostrar_voluntariado,
            mostrar_logros=profile.mostrar_logros,
        )
        clone = create_profile(clone, actor)
        for relation in (
            'experiencias', 'educaciones', 'formaciones', 'habilidades', 'idiomas',
            'proyectos', 'logros', 'voluntariados', 'intereses',
        ):
            getattr(clone, relation).set(getattr(profile, relation).all())
        return clone


def reconcile_published_profiles(actor, selected_ids):
    try:
        selected_ids = {int(pk) for pk in selected_ids}
    except (TypeError, ValueError) as error:
        raise ValueError('La selección de perfiles no es válida.') from error
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=actor.pk)
        published = CVProfile.objects.filter(user_id=actor.pk, is_public=True).exclude(
            status=CVProfile.Status.ARCHIVED,
        )
        if not selected_ids.issubset(set(published.values_list('pk', flat=True))):
            raise ValueError('La selección contiene perfiles que no están publicados en tu cuenta.')
        if len(selected_ids) > profile_limit(actor):
            raise OverflowError('La selección supera el límite de perfiles de tu plan.')
        for profile in published:
            should_publish = profile.pk in selected_ids
            profile.is_public = should_publish
            profile.status = (
                CVProfile.Status.PUBLISHED if should_publish else CVProfile.Status.DRAFT
            )
            profile.revision += 1
            profile.save(update_fields=['is_public', 'status', 'revision', 'updated_at'])


def snapshot_profile(profile):
    cv = profile.cv
    personal = getattr(cv, 'personal_info', None)
    professional = getattr(cv, 'professional_profile', None)

    def text(value):
        if value is None:
            return ''
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return str(value)

    data = {
        'name': profile.display_name or (personal.nombre_completo if personal else ''),
        'profession': profile.professional_headline or (professional.profesion if professional else ''),
        'summary': (
            profile.presentation or (professional.resumen if professional else '')
        ) if profile.mostrar_perfil_profesional else '',
        'location': (
            profile.profile_location or (
                ', '.join(filter(None, [personal.ciudad, personal.pais])) if personal else ''
            )
        ) if profile.show_location else '',
        'email': (profile.contact_email or (
            personal.email_profesional1 if personal else ''
        ))
        if profile.show_email else '',
        'phone': (profile.contact_phone or (
            personal.telefono1 if personal else ''
        ))
        if profile.show_phone else '',
        'photo': personal.foto.name if personal and personal.foto and profile.show_photo else '',
        'photo_url': personal.foto.url if personal and personal.foto and profile.show_photo else '',
        'experiences': [],
        'education': [],
        'training': [],
        'skills': [],
        'languages': [],
        'projects': [],
        'achievements': [],
        'volunteering': [],
        'interests': [],
        'links': [],
    }

    def values(obj, fields):
        return {field: text(getattr(obj, field, '')) for field in fields}

    def safe_url(value):
        return value if value and urlparse(value).scheme in {'http', 'https'} else ''

    for item in profile.experiencias.all():
        data['experiences'].append(values(item, [
            'puesto', 'empresa', 'ubicacion', 'fecha_inicio', 'fecha_fin',
            'trabajo_actual', 'descripcion', 'logros',
        ]))
    for item in profile.educaciones.all():
        data['education'].append(values(item, [
            'titulo', 'centro', 'ubicacion', 'fecha_inicio', 'fecha_fin', 'en_curso',
        ]))
    for item in profile.formaciones.all():
        data['training'].append(values(item, ['nombre', 'entidad', 'fecha', 'descripcion']))
    for item in profile.habilidades.all():
        data['skills'].append(values(item, ['nombre', 'tipo', 'nivel']))
    for item in profile.idiomas.all():
        data['languages'].append(values(item, ['idioma', 'nivel', 'certificacion']))
    for item in profile.proyectos.all():
        project = values(item, ['nombre', 'descripcion', 'tecnologias'])
        project['link'] = safe_url(item.link)
        data['projects'].append(project)
    for item in profile.logros.all() if profile.mostrar_logros else []:
        data['achievements'].append(values(item, ['titulo', 'descripcion', 'fecha']))
    for item in profile.voluntariados.all() if profile.mostrar_voluntariado else []:
        data['volunteering'].append(values(item, ['organizacion', 'funcion', 'impacto']))
    for item in profile.intereses.all() if profile.mostrar_intereses else []:
        data['interests'].append({'name': item.nombre})
    if profile.mostrar_redes and hasattr(cv, 'social_networks'):
        social = cv.social_networks
        data['links'] = [
            {'label': label, 'url': url}
            for label, url in (
                ('LinkedIn', social.linkedin), ('GitHub', social.github),
                ('Portfolio', social.portfolio), ('X', social.twitter),
            ) if safe_url(url)
        ]
    return data


def publish_profile(profile, actor):
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=actor.pk)
        locked = CVProfile.objects.select_for_update().select_related(
            'profile_template',
        ).get(pk=profile.pk, user_id=actor.pk)
        if not locked.profile_template_id:
            raise ValueError('Selecciona una plantilla publicada antes de publicar.')
        access = template_access(actor, locked.profile_template)
        if not access.allowed:
            raise PermissionError(access.reason)
        if not locked.is_public:
            active_profiles = CVProfile.objects.filter(
                user_id=actor.pk, is_public=True,
            ).exclude(status=CVProfile.Status.ARCHIVED).count()
            if active_profiles >= profile_limit(actor):
                raise OverflowError('Has alcanzado el límite de perfiles publicados de tu plan.')
        locked.published_snapshot = snapshot_profile(locked)
        locked.published_template = locked.profile_template
        locked.published_template_version = locked.profile_template.version
        locked.published_brand_color = locked.brand_color
        locked.revision += 1
        locked.published_at = timezone.now()
        from apps.curriculum.models import CVProfileRevision
        CVProfileRevision.objects.create(
            profile=locked,
            revision=locked.revision,
            snapshot=locked.published_snapshot,
            template=locked.profile_template,
            template_version=locked.profile_template.version,
            brand_color=locked.brand_color,
        )
        locked.status = CVProfile.Status.PUBLISHED
        locked.is_public = True
        locked.save(update_fields=[
            'published_snapshot', 'published_template', 'published_template_version',
            'published_brand_color', 'published_at', 'status', 'is_public', 'revision',
        ])
        return locked
