"""Views for the Curriculum Vitae builder app."""
from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, Http404, JsonResponse
from django.db import transaction
from django.db.models import Count, Q
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy, reverse
from django.views.decorators.http import require_POST
from django.utils import timezone

from .models import (
    CurriculumVitae, PersonalInfo, ProfessionalProfile, WorkExperience,
    Education, ComplementaryTraining, Skill, Language, Project, Achievement,
    Volunteering, SocialNetwork, Interest, OtherInfo, CVProfile, NexusDNAEntry,
    ProfileTemplate, ProfileVisit,
)
from .forms import (
    PersonalInfoForm, ProfessionalProfileForm, WorkExperienceForm,
    EducationForm, ComplementaryTrainingForm, SkillForm, LanguageForm,
    ProjectForm, AchievementForm, VolunteeringForm, SocialNetworkForm,
    InterestForm, OtherInfoForm, CVProfileForm,
    CvPdfUploadForm,
)
from .services.cv_importer import PdfImportError, apply_cv_fields, extract_cv_fields
from .services.profiles import (
    available_templates, create_profile, duplicate_profile, profile_limit,
    publish_profile, reconcile_published_profiles, snapshot_profile, template_access,
)
from .services.pdf import build_cv_pdf


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_or_create_cv(user):
    cv, _ = CurriculumVitae.objects.get_or_create(user=user)
    return cv


def _completeness(cv):
    """Return a dict with section completion booleans."""
    sections = {
        'personal': hasattr(cv, 'personal_info'),
        'perfil': hasattr(cv, 'professional_profile'),
        'experiencia': cv.work_experiences.exists(),
        'educacion': cv.educations.exists(),
        'formacion': cv.trainings.exists(),
        'habilidades': cv.skills.exists(),
        'idiomas': cv.languages.exists(),
        'proyectos': cv.projects.exists(),
        'logros': cv.achievements.exists(),
        'voluntariado': cv.volunteerings.exists(),
        'redes': hasattr(cv, 'social_networks'),
        'intereses': cv.interests.exists(),
        'otros': hasattr(cv, 'other_info'),
    }
    done = sum(sections.values())
    total = len(sections)
    return sections, done, total


# ─── Dashboard ────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    cv = _get_or_create_cv(request.user)
    sections, done, total = _completeness(cv)
    profiles = CVProfile.objects.filter(user=request.user)
    nexus_qs = NexusDNAEntry.objects.filter(cv=cv)
    return render(request, 'curriculum/dashboard.html', {
        'cv': cv,
        'sections': sections,
        'done': done,
        'total': total,
        'pct': int(done / total * 100),
        'profiles': profiles,
        'nexus_entries_count': nexus_qs.count(),
        'nexus_visible_count': nexus_qs.filter(visible_en_cv=True).count(),
    })


@login_required
def import_cv_pdf(request):
    """Extract common CV fields locally and require confirmation before saving."""
    preview_key = 'curriculum_cv_import_preview'

    if request.method == 'POST' and request.POST.get('action') == 'cancel':
        request.session.pop(preview_key, None)
        return redirect('curriculum:import_cv_pdf')

    if request.method == 'POST' and request.POST.get('action') == 'confirm':
        stored_preview = request.session.get(preview_key)
        if not isinstance(stored_preview, dict) or stored_preview.get('user_id') != request.user.pk:
            messages.error(request, 'La vista previa ha caducado. Vuelve a subir el PDF.')
            return redirect('curriculum:import_cv_pdf')

        cv = _get_or_create_cv(request.user)
        with transaction.atomic():
            changed = apply_cv_fields(cv, stored_preview.get('fields', {}))
        request.session.pop(preview_key, None)
        imported = sum(changed.values())
        if imported:
            messages.success(
                request,
                f'Importación completada: se añadieron o completaron {imported} campos o registros.',
            )
        else:
            messages.info(request, 'No había campos nuevos para importar. Tus datos existentes se conservaron.')
        return redirect('curriculum:dashboard')

    form = CvPdfUploadForm(request.POST or None, request.FILES or None)
    if request.method == 'POST':
        if form.is_valid():
            try:
                fields = extract_cv_fields(form.cleaned_data['pdf'])
            except PdfImportError as exc:
                messages.error(request, str(exc))
            else:
                if not fields:
                    messages.warning(
                        request,
                        'No se pudieron identificar campos compatibles en este CV. '
                        'Prueba con un PDF con texto seleccionable y datos de contacto visibles.',
                    )
                else:
                    request.session[preview_key] = {
                        'user_id': request.user.pk,
                        'fields': fields,
                    }
                    return render(request, 'curriculum/import_pdf_preview.html', {
                        'preview_sections': _cv_import_preview(fields),
                    })
    else:
        request.session.pop(preview_key, None)

    return render(request, 'curriculum/import_pdf.html', {'form': form})


def _cv_import_preview(fields):
    labels = {
        'personal': ('Datos personales', {
            'nombre': 'Nombre',
            'apellidos': 'Apellidos',
            'email_profesional1': 'Correo electrónico',
            'telefono1': 'Teléfono',
            'ciudad': 'Ciudad',
            'pais': 'País',
        }),
        'professional': ('Perfil profesional', {
            'profesion': 'Profesión',
            'resumen': 'Resumen',
        }),
        'social': ('Redes profesionales', {
            'linkedin': 'LinkedIn',
            'github': 'GitHub',
            'portfolio': 'Portfolio',
            'twitter': 'X / Twitter',
        }),
        'skills': ('Habilidades', {}),
        'experience': ('Experiencia profesional', {
            'puesto': 'Puesto',
            'empresa': 'Empresa',
            'ubicacion': 'Ubicación',
            'fecha_inicio': 'Fecha de inicio',
            'fecha_fin': 'Fecha de fin',
            'trabajo_actual': 'Trabajo actual',
            'descripcion': 'Descripción',
        }),
        'education': ('Formación académica', {
            'titulo': 'Título',
            'centro': 'Centro educativo',
            'ubicacion': 'Ubicación',
            'fecha_inicio': 'Fecha de inicio',
            'fecha_fin': 'Fecha de fin',
            'en_curso': 'En curso',
        }),
    }
    sections = []
    for key, (title, field_labels) in labels.items():
        values = fields.get(key)
        if not values:
            continue
        if key == 'skills':
            rows = [('Habilidades detectadas', ', '.join(values))]
        elif key in ('experience', 'education'):
            for index, entry in enumerate(values, start=1):
                rows = [
                    (field_labels[field], 'Sí' if value is True else value)
                    for field, value in entry.items()
                    if value is not False
                ]
                sections.append({
                    'title': f'{title} {index}',
                    'rows': rows,
                })
            continue
        else:
            rows = [(field_labels[field], value) for field, value in values.items()]
        sections.append({'title': title, 'rows': rows})
    return sections


# ─── Section: Personal info ───────────────────────────────────────────────────

@login_required
def edit_personal_info(request):
    cv = _get_or_create_cv(request.user)
    instance = getattr(cv, 'personal_info', None)
    is_new = instance is None
    form = PersonalInfoForm(request.POST or None, request.FILES or None, instance=instance)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.cv = cv
        obj.save()
        messages.success(request, 'Datos personales guardados.')
        if is_new:
            messages.success(request, '🎓 ¡+25 puntos por completar tus datos personales!')
        return redirect('curriculum:dashboard')
    return render(request, 'curriculum/sections/personal_info.html', {'form': form})


# ─── Section: Professional profile ───────────────────────────────────────────

@login_required
def edit_professional_profile(request):
    cv = _get_or_create_cv(request.user)
    instance = getattr(cv, 'professional_profile', None)
    is_new = instance is None
    form = ProfessionalProfileForm(request.POST or None, instance=instance)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.cv = cv
        obj.save()
        messages.success(request, 'Perfil profesional guardado.')
        if is_new:
            messages.success(request, '🎓 ¡+25 puntos por completar tu perfil profesional!')
        return redirect('curriculum:dashboard')
    return render(request, 'curriculum/sections/professional_profile.html', {'form': form})


@login_required
@require_POST
def suggest_resumen_ia(request):
    """Keep CV assistance unavailable until it can reserve shared Ducky Energy."""
    return JsonResponse({
        'ok': False,
        'warning': (
            'La sugerencia con IA estará disponible cuando se integre con Energía Ducky. '
            'No se ha enviado ningún dato del CV a un proveedor de IA.'
        ),
    }, status=503)


@login_required
def edit_social_networks(request):
    cv = _get_or_create_cv(request.user)
    instance = getattr(cv, 'social_networks', None)
    is_new = instance is None
    form = SocialNetworkForm(request.POST or None, instance=instance)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.cv = cv
        obj.save()
        messages.success(request, 'Redes profesionales guardadas.')
        if is_new:
            messages.success(request, '🎓 ¡+15 puntos por añadir tus redes profesionales!')
        return redirect('curriculum:dashboard')
    return render(request, 'curriculum/sections/social_networks.html', {'form': form})


# ─── Section: Other info ──────────────────────────────────────────────────────

@login_required
def edit_other_info(request):
    cv = _get_or_create_cv(request.user)
    instance = getattr(cv, 'other_info', None)
    is_new = instance is None
    form = OtherInfoForm(request.POST or None, instance=instance)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.cv = cv
        obj.save()
        messages.success(request, 'Otros datos guardados.')
        if is_new:
            messages.success(request, '🎓 ¡+10 puntos por completar otros datos!')
        return redirect('curriculum:dashboard')
    return render(request, 'curriculum/sections/other_info.html', {'form': form})


# ─── Generic list / create / edit / delete for repeatable sections ────────────

class _SectionListView(View):
    """Base view: list items for a section."""
    model = None
    template = None
    section_name = ''

    @method_decorator(login_required)
    def get(self, request):
        cv = _get_or_create_cv(request.user)
        items = self.model.objects.filter(cv=cv)
        return render(request, self.template, {'items': items, 'section': self.section_name})


# Points awarded for the first item in each FK section
_PUNTOS_PRIMERA_VEZ = {
    'WorkExperience': (30, 'experiencia laboral'),
    'Education': (25, 'formación académica'),
    'ComplementaryTraining': (20, 'formación complementaria'),
    'Skill': (20, 'habilidades'),
    'Language': (20, 'idiomas'),
    'Project': (25, 'proyectos'),
    'Achievement': (20, 'logros'),
    'Volunteering': (20, 'voluntariado'),
    'Interest': (10, 'intereses'),
}


class _SectionCreateView(View):
    form_class = None
    template = None
    success_name = ''

    @method_decorator(login_required)
    def get(self, request):
        return render(request, self.template, {'form': self.form_class()})

    @method_decorator(login_required)
    def post(self, request):
        cv = _get_or_create_cv(request.user)
        form = self.form_class(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.cv = cv
            obj.save()
            model_name = form.Meta.model.__name__
            if model_name in _PUNTOS_PRIMERA_VEZ:
                pts, nombre = _PUNTOS_PRIMERA_VEZ[model_name]
                related_count = form.Meta.model.objects.filter(cv=cv).count()
                if related_count == 1:
                    messages.success(request, f'🎓 ¡+{pts} puntos por añadir {nombre} a tu CV!')
                else:
                    messages.success(request, 'Elemento guardado.')
            else:
                messages.success(request, 'Elemento guardado.')
            return redirect(self.success_name)
        return render(request, self.template, {'form': form})


class _SectionUpdateView(View):
    model = None
    form_class = None
    template = None
    success_name = ''

    def _get_obj(self, request, pk):
        cv = _get_or_create_cv(request.user)
        return get_object_or_404(self.model, pk=pk, cv=cv)

    @method_decorator(login_required)
    def get(self, request, pk):
        obj = self._get_obj(request, pk)
        return render(request, self.template, {'form': self.form_class(instance=obj), 'object': obj})

    @method_decorator(login_required)
    def post(self, request, pk):
        obj = self._get_obj(request, pk)
        form = self.form_class(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Elemento actualizado.')
            return redirect(self.success_name)
        return render(request, self.template, {'form': form, 'object': obj})


class _SectionDeleteView(View):
    model = None
    success_name = ''

    @method_decorator(login_required)
    def post(self, request, pk):
        cv = _get_or_create_cv(request.user)
        obj = get_object_or_404(self.model, pk=pk, cv=cv)
        obj.delete()
        messages.success(request, 'Elemento eliminado.')
        return redirect(self.success_name)


# ─── Work Experience ──────────────────────────────────────────────────────────

class WorkExperienceListView(_SectionListView):
    model = WorkExperience
    template = 'curriculum/sections/work_experience_list.html'
    section_name = 'Experiencia laboral'


class WorkExperienceCreateView(_SectionCreateView):
    form_class = WorkExperienceForm
    template = 'curriculum/sections/work_experience_form.html'
    success_name = 'curriculum:work_experience_list'


class WorkExperienceUpdateView(_SectionUpdateView):
    model = WorkExperience
    form_class = WorkExperienceForm
    template = 'curriculum/sections/work_experience_form.html'
    success_name = 'curriculum:work_experience_list'


class WorkExperienceDeleteView(_SectionDeleteView):
    model = WorkExperience
    success_name = 'curriculum:work_experience_list'


# ─── Education ────────────────────────────────────────────────────────────────

class EducationListView(_SectionListView):
    model = Education
    template = 'curriculum/sections/education_list.html'
    section_name = 'Formación académica'


class EducationCreateView(_SectionCreateView):
    form_class = EducationForm
    template = 'curriculum/sections/education_form.html'
    success_name = 'curriculum:education_list'


class EducationUpdateView(_SectionUpdateView):
    model = Education
    form_class = EducationForm
    template = 'curriculum/sections/education_form.html'
    success_name = 'curriculum:education_list'


class EducationDeleteView(_SectionDeleteView):
    model = Education
    success_name = 'curriculum:education_list'


# ─── Complementary Training ───────────────────────────────────────────────────

class TrainingListView(_SectionListView):
    model = ComplementaryTraining
    template = 'curriculum/sections/training_list.html'
    section_name = 'Formación complementaria'


class TrainingCreateView(_SectionCreateView):
    form_class = ComplementaryTrainingForm
    template = 'curriculum/sections/training_form.html'
    success_name = 'curriculum:training_list'


class TrainingUpdateView(_SectionUpdateView):
    model = ComplementaryTraining
    form_class = ComplementaryTrainingForm
    template = 'curriculum/sections/training_form.html'
    success_name = 'curriculum:training_list'


class TrainingDeleteView(_SectionDeleteView):
    model = ComplementaryTraining
    success_name = 'curriculum:training_list'


# ─── Skills ───────────────────────────────────────────────────────────────────

class SkillListView(_SectionListView):
    model = Skill
    template = 'curriculum/sections/skill_list.html'
    section_name = 'Habilidades'


class SkillCreateView(_SectionCreateView):
    form_class = SkillForm
    template = 'curriculum/sections/skill_form.html'
    success_name = 'curriculum:skill_list'


class SkillUpdateView(_SectionUpdateView):
    model = Skill
    form_class = SkillForm
    template = 'curriculum/sections/skill_form.html'
    success_name = 'curriculum:skill_list'


class SkillDeleteView(_SectionDeleteView):
    model = Skill
    success_name = 'curriculum:skill_list'


# ─── Languages ────────────────────────────────────────────────────────────────

class LanguageListView(_SectionListView):
    model = Language
    template = 'curriculum/sections/language_list.html'
    section_name = 'Idiomas'


class LanguageCreateView(_SectionCreateView):
    form_class = LanguageForm
    template = 'curriculum/sections/language_form.html'
    success_name = 'curriculum:language_list'


class LanguageUpdateView(_SectionUpdateView):
    model = Language
    form_class = LanguageForm
    template = 'curriculum/sections/language_form.html'
    success_name = 'curriculum:language_list'


class LanguageDeleteView(_SectionDeleteView):
    model = Language
    success_name = 'curriculum:language_list'


# ─── Projects ─────────────────────────────────────────────────────────────────

class ProjectListView(_SectionListView):
    model = Project
    template = 'curriculum/sections/project_list.html'
    section_name = 'Proyectos'


class ProjectCreateView(_SectionCreateView):
    form_class = ProjectForm
    template = 'curriculum/sections/project_form.html'
    success_name = 'curriculum:project_list'


class ProjectUpdateView(_SectionUpdateView):
    model = Project
    form_class = ProjectForm
    template = 'curriculum/sections/project_form.html'
    success_name = 'curriculum:project_list'


class ProjectDeleteView(_SectionDeleteView):
    model = Project
    success_name = 'curriculum:project_list'


# ─── Achievements ─────────────────────────────────────────────────────────────

class AchievementListView(_SectionListView):
    model = Achievement
    template = 'curriculum/sections/achievement_list.html'
    section_name = 'Logros y reconocimientos'


class AchievementCreateView(_SectionCreateView):
    form_class = AchievementForm
    template = 'curriculum/sections/achievement_form.html'
    success_name = 'curriculum:achievement_list'


class AchievementUpdateView(_SectionUpdateView):
    model = Achievement
    form_class = AchievementForm
    template = 'curriculum/sections/achievement_form.html'
    success_name = 'curriculum:achievement_list'


class AchievementDeleteView(_SectionDeleteView):
    model = Achievement
    success_name = 'curriculum:achievement_list'


# ─── Volunteering ─────────────────────────────────────────────────────────────

class VolunteeringListView(_SectionListView):
    model = Volunteering
    template = 'curriculum/sections/volunteering_list.html'
    section_name = 'Voluntariado'


class VolunteeringCreateView(_SectionCreateView):
    form_class = VolunteeringForm
    template = 'curriculum/sections/volunteering_form.html'
    success_name = 'curriculum:volunteering_list'


class VolunteeringUpdateView(_SectionUpdateView):
    model = Volunteering
    form_class = VolunteeringForm
    template = 'curriculum/sections/volunteering_form.html'
    success_name = 'curriculum:volunteering_list'


class VolunteeringDeleteView(_SectionDeleteView):
    model = Volunteering
    success_name = 'curriculum:volunteering_list'


# ─── Interests ────────────────────────────────────────────────────────────────

class InterestListView(_SectionListView):
    model = Interest
    template = 'curriculum/sections/interest_list.html'
    section_name = 'Intereses'


class InterestCreateView(_SectionCreateView):
    form_class = InterestForm
    template = 'curriculum/sections/interest_form.html'
    success_name = 'curriculum:interest_list'


class InterestUpdateView(_SectionUpdateView):
    model = Interest
    form_class = InterestForm
    template = 'curriculum/sections/interest_form.html'
    success_name = 'curriculum:interest_list'


class InterestDeleteView(_SectionDeleteView):
    model = Interest
    success_name = 'curriculum:interest_list'


# ─── Profiles ─────────────────────────────────────────────────────────────────

@login_required
def profile_list(request):
    profiles = CVProfile.objects.filter(user=request.user).select_related('profile_template')
    from apps.users.access import effective_plan
    is_pro = effective_plan(request.user) == 'pro'
    if is_pro:
        month_ago = timezone.now() - timedelta(days=30)
        profiles = profiles.annotate(
            visit_count=Count('visits', filter=Q(visits__visited_at__gte=month_ago)),
        )
    return render(request, 'curriculum/profiles/list.html', {
        'profiles': profiles,
        'profile_limit': profile_limit(request.user),
        'profile_count': CVProfile.objects.filter(user=request.user).exclude(
            status=CVProfile.Status.ARCHIVED,
        ).count(),
        'is_pro': is_pro,
        'over_published_limit': CVProfile.objects.filter(
            user=request.user, is_public=True,
        ).exclude(status=CVProfile.Status.ARCHIVED).count() > profile_limit(request.user),
        'published_profiles': CVProfile.objects.filter(
            user=request.user, is_public=True,
        ).exclude(status=CVProfile.Status.ARCHIVED),
    })


@login_required
def profile_create(request):
    cv = _get_or_create_cv(request.user)
    form = CVProfileForm(request.POST or None, user=request.user)
    if form.is_valid():
        profile = form.save(commit=False)
        profile.cv = cv
        try:
            profile = create_profile(profile, request.user)
        except OverflowError as error:
            form.add_error(None, str(error))
        else:
            return redirect('curriculum:profile_edit_items', pk=profile.pk)
    return render(request, 'curriculum/profiles/form.html', {
        'form': form, 'action': 'Crear perfil', 'profile_limit': profile_limit(request.user),
    })


@login_required
def profile_edit(request, pk):
    profile = get_object_or_404(CVProfile, pk=pk, user=request.user)
    form = CVProfileForm(request.POST or None, instance=profile, user=request.user)
    if form.is_valid():
        updated = form.save(commit=False)
        updated.revision += 1
        updated.save()
        messages.success(request, 'Perfil actualizado.')
        return redirect('curriculum:profile_edit_items', pk=profile.pk)
    return render(request, 'curriculum/profiles/form.html', {
        'form': form, 'profile': profile, 'action': 'Editar perfil',
    })


@login_required
def profile_edit_items(request, pk):
    """Select which CV items appear in this profile."""
    profile = get_object_or_404(CVProfile, pk=pk, user=request.user)
    cv = profile.cv

    if request.method == 'POST':
        # Update M2M selections
        profile.experiencias.set(
            cv.work_experiences.filter(pk__in=request.POST.getlist('experiencias'))
        )
        profile.educaciones.set(
            cv.educations.filter(pk__in=request.POST.getlist('educaciones'))
        )
        profile.formaciones.set(
            cv.trainings.filter(pk__in=request.POST.getlist('formaciones'))
        )
        profile.habilidades.set(
            cv.skills.filter(pk__in=request.POST.getlist('habilidades'))
        )
        profile.idiomas.set(
            cv.languages.filter(pk__in=request.POST.getlist('idiomas'))
        )
        profile.proyectos.set(
            cv.projects.filter(pk__in=request.POST.getlist('proyectos'))
        )
        profile.logros.set(
            cv.achievements.filter(pk__in=request.POST.getlist('logros'))
        )
        profile.voluntariados.set(
            cv.volunteerings.filter(pk__in=request.POST.getlist('voluntariados'))
        )
        profile.intereses.set(
            cv.interests.filter(pk__in=request.POST.getlist('intereses'))
        )
        profile.revision += 1
        profile.save(update_fields=['revision', 'updated_at'])
        messages.success(request, 'Contenido del perfil guardado.')
        return redirect('curriculum:profile_list')

    return render(request, 'curriculum/profiles/edit_items.html', {
        'profile': profile,
        'cv': cv,
        'all_experiencias': cv.work_experiences.all(),
        'all_educaciones': cv.educations.all(),
        'all_formaciones': cv.trainings.all(),
        'all_habilidades': cv.skills.all(),
        'all_idiomas': cv.languages.all(),
        'all_proyectos': cv.projects.all(),
        'all_logros': cv.achievements.all(),
        'all_voluntariados': cv.volunteerings.all(),
        'all_intereses': cv.interests.all(),
    })


@login_required
def profile_delete(request, pk):
    profile = get_object_or_404(CVProfile, pk=pk, user=request.user)
    if request.method == 'POST':
        profile.delete()
        messages.success(request, 'Perfil eliminado.')
        return redirect('curriculum:profile_list')
    return render(request, 'curriculum/profiles/confirm_delete.html', {'profile': profile})


@login_required
def profile_publish(request, pk):
    profile = get_object_or_404(CVProfile, pk=pk, user=request.user)
    if request.method != 'POST':
        return redirect('curriculum:profile_list')
    try:
        publish_profile(profile, request.user)
    except PermissionError as error:
        messages.error(request, {
            'plan_required': 'Tu nivel actual no incluye la plantilla del perfil.',
            'subscription_inactive': 'Tu suscripción no está activa para esta plantilla.',
            'purchase_required': 'Necesitas el derecho de compra vigente para esta plantilla.',
            'not_available': 'La plantilla seleccionada ya no está disponible.',
        }.get(str(error), 'No tienes permiso para publicar esta plantilla.'))
    except (OverflowError, ValueError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'Perfil publicado. La versión visible queda guardada; vuelve a publicar los cambios para actualizarlos.')
    return redirect('curriculum:profile_list')


@login_required
def profile_unpublish(request, pk):
    profile = get_object_or_404(CVProfile, pk=pk, user=request.user)
    if request.method == 'POST':
        profile.is_public = False
        profile.status = CVProfile.Status.DRAFT
        profile.revision += 1
        profile.save(update_fields=['is_public', 'status', 'revision', 'updated_at'])
        messages.success(request, 'El perfil está ahora en borrador y ya no es público.')
    return redirect('curriculum:profile_list')


@login_required
def profile_duplicate(request, pk):
    profile = get_object_or_404(CVProfile, pk=pk, user=request.user)
    if request.method == 'POST':
        try:
            duplicate_profile(profile, request.user)
        except (PermissionError, OverflowError) as error:
            messages.error(request, str(error))
        else:
            messages.success(request, 'Se ha creado una copia privada e independiente del perfil.')
    return redirect('curriculum:profile_list')


@login_required
def profile_archive(request, pk):
    profile = get_object_or_404(CVProfile, pk=pk, user=request.user)
    if request.method == 'POST':
        profile.is_public = False
        profile.status = CVProfile.Status.ARCHIVED
        profile.revision += 1
        profile.save(update_fields=['is_public', 'status', 'revision', 'updated_at'])
        messages.success(request, 'Perfil archivado; sus datos se conservan y deja de ser público.')
    return redirect('curriculum:profile_list')


@login_required
def profile_restore(request, pk):
    profile = get_object_or_404(CVProfile, pk=pk, user=request.user)
    if request.method == 'POST':
        profile.status = CVProfile.Status.DRAFT
        profile.is_public = False
        profile.revision += 1
        profile.save(update_fields=['status', 'is_public', 'revision', 'updated_at'])
        messages.success(request, 'Perfil restaurado como borrador privado.')
    return redirect('curriculum:profile_list')


@login_required
def profile_reconcile(request):
    if request.method == 'POST':
        try:
            reconcile_published_profiles(request.user, request.POST.getlist('profiles'))
        except (OverflowError, ValueError) as error:
            messages.error(request, str(error))
        else:
            messages.success(request, 'Tu selección publicada se ha actualizado; los demás perfiles siguen como borradores.')
    return redirect('curriculum:profile_list')


@login_required
def profile_preview(request, pk):
    profile = get_object_or_404(
        CVProfile.objects.select_related('profile_template'), pk=pk, user=request.user,
    )
    template = profile.profile_template
    if not template:
        raise Http404('Selecciona primero una plantilla del catálogo.')
    decision = template_access(request.user, template)
    if not decision.allowed:
        messages.error(request, {
            'plan_required': 'Tu nivel actual no permite previsualizar esta plantilla.',
            'subscription_inactive': 'Tu suscripción no está activa para esta plantilla.',
            'purchase_required': 'Necesitas el derecho de compra vigente para esta plantilla.',
            'not_available': 'Esta plantilla ya no está disponible.',
        }.get(decision.reason, 'No puedes previsualizar esta plantilla ahora.'))
        return redirect('curriculum:profile_edit', pk=profile.pk)
    return render(request, 'curriculum/cv_templates/matrix.html' if template.renderer == 'matrix' else 'curriculum/cv_templates/catalog_profile.html', {
        'profile': profile,
        'snapshot': snapshot_profile(profile),
        'template_renderer': template.renderer,
        'brand_color': profile.brand_color,
        'preview': True,
    })


def template_catalog(request):
    templates = available_templates()
    from apps.users.models import RamaProfesional
    branch_names = dict(
        RamaProfesional.objects.filter(is_active=True).values_list('code', 'name')
    )
    for item in templates:
        item.recommended_branch_names = [
            branch_names[code] for code in item.recommended_branch_codes if code in branch_names
        ]
        item.can_apply = bool(
            request.user.is_authenticated and template_access(request.user, item).allowed
        )
    return render(request, 'curriculum/profiles/template_catalog.html', {'templates': templates})


def template_preview(request, code):
    template = available_templates().filter(code=code).order_by('-version').first()
    if not template:
        raise Http404('La plantilla no está publicada.')
    demo = {
        'name': 'Lucía Martín',
        'profession': 'Diseñadora de producto digital',
        'summary': 'Ejemplo ficticio para mostrar la composición de esta plantilla.',
        'location': 'Madrid, España',
        'email': '', 'phone': '', 'photo': '',
        'experiences': [{'puesto': 'Diseñadora UX', 'empresa': 'Estudio Demo',
                         'ubicacion': 'Madrid', 'fecha_inicio': '2022-01',
                         'fecha_fin': '', 'trabajo_actual': 'True',
                         'descripcion': 'Diseño de productos digitales de ejemplo.', 'logros': ''}],
        'education': [], 'training': [], 'skills': [{'nombre': 'Diseño UX', 'tipo': 'hard', 'nivel': '4'}],
        'languages': [], 'projects': [], 'achievements': [], 'volunteering': [],
        'interests': [], 'links': [],
    }
    return render(request, 'curriculum/cv_templates/matrix.html' if template.renderer == 'matrix' else 'curriculum/cv_templates/catalog_profile.html', {
        'profile': None,
        'snapshot': demo,
        'template_renderer': template.renderer,
        'brand_color': template.default_color,
        'template_name': template.name,
        'preview': True,
    })


# ─── Public CV & PDF ──────────────────────────────────────────────────────────

def public_cv(request, slug):
    profile = get_object_or_404(
        CVProfile.objects.select_related('profile_template', 'published_template', 'user'),
        slug=slug, is_public=True,
    )
    if profile.status == CVProfile.Status.ARCHIVED:
        raise Http404('Este perfil ya no está publicado.')
    if CVProfile.objects.filter(
        user_id=profile.user_id, is_public=True,
    ).exclude(status=CVProfile.Status.ARCHIVED).count() > profile_limit(profile.user):
        raise Http404('El propietario debe elegir qué perfiles mantener publicados tras cambiar de nivel.')
    template = profile.published_template or profile.profile_template
    if template and template_access(profile.user, template).allowed:
        renderer = template.renderer
        snapshot = profile.published_snapshot or snapshot_profile(profile)
    else:
        renderer = 'basic'
        snapshot = profile.published_snapshot or snapshot_profile(profile)
    if request.method == 'GET' and (
        not request.user.is_authenticated or request.user.pk != profile.user_id
    ):
        ProfileVisit.objects.create(profile=profile)
    return render(request, 'curriculum/cv_templates/matrix.html' if renderer == 'matrix' else 'curriculum/cv_templates/catalog_profile.html', {
        'profile': profile, 'snapshot': snapshot, 'template_renderer': renderer,
        'brand_color': profile.published_brand_color,
    })


def cv_pdf(request, slug):
    profile = get_object_or_404(
        CVProfile.objects.select_related('profile_template', 'published_template', 'user'),
        slug=slug, is_public=True,
    )
    if profile.status == CVProfile.Status.ARCHIVED:
        raise Http404('Este perfil ya no está publicado.')
    if CVProfile.objects.filter(
        user_id=profile.user_id, is_public=True,
    ).exclude(status=CVProfile.Status.ARCHIVED).count() > profile_limit(profile.user):
        raise Http404('El propietario debe elegir qué perfiles mantener publicados tras cambiar de nivel.')
    snapshot = profile.published_snapshot or snapshot_profile(profile)
    return build_cv_pdf(profile, snapshot)


@login_required
def private_profile_pdf(request, pk):
    profile = get_object_or_404(
        CVProfile.objects.select_related('profile_template', 'user'),
        pk=pk, user=request.user,
    )
    if profile.status == CVProfile.Status.ARCHIVED:
        raise Http404('Restaura el perfil antes de exportarlo.')
    return build_cv_pdf(profile, snapshot_profile(profile))


# ─── Nexus DNA section ────────────────────────────────────────────────────────

@login_required
def nexus_dna_list(request):
    """List all Nexus DNA entries for the logged-in user and let them toggle visibility."""
    import json as _json
    from apps.nexus.models import DimensionDefinicion

    cv = _get_or_create_cv(request.user)
    entries = NexusDNAEntry.objects.filter(cv=cv).order_by('-completado_en')

    defs = DimensionDefinicion.objects.prefetch_related('rangos').all()
    dim_info_db = {}
    for d in defs:
        dim_info_db[d.label] = {
            'descripcion': d.descripcion,
            'icono': d.icono,
            'rangos': [
                {
                    'min': r.rango_min,
                    'max': r.rango_max,
                    'titulo': r.titulo,
                    'explicacion': r.explicacion,
                    'recomendacion': r.recomendacion,
                    'color': r.color_nivel,
                }
                for r in d.rangos.all()
            ],
        }

    return render(request, 'curriculum/sections/nexus_dna.html', {
        'entries': entries,
        'cv': cv,
        'dim_info_db_json': _json.dumps(dim_info_db, ensure_ascii=False),
    })


@login_required
@require_POST
def nexus_dna_toggle(request, pk):
    """Toggle the visible_en_cv flag for a Nexus DNA entry."""
    cv = _get_or_create_cv(request.user)
    entry = get_object_or_404(NexusDNAEntry, pk=pk, cv=cv)
    entry.visible_en_cv = not entry.visible_en_cv
    entry.save(update_fields=['visible_en_cv'])
    return redirect('curriculum:nexus_dna_list')


@login_required
@require_POST
def nexus_dna_delete(request, pk):
    """Remove a Nexus DNA entry from the CV."""
    cv = _get_or_create_cv(request.user)
    entry = get_object_or_404(NexusDNAEntry, pk=pk, cv=cv)
    entry.delete()
    messages.success(request, 'Entrada Nexus DNA eliminada del CV.')
    return redirect('curriculum:nexus_dna_list')
