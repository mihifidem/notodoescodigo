"""Views for the Curriculum Vitae builder app."""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, Http404, JsonResponse
from django.db import transaction
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy, reverse
from django.views.decorators.http import require_POST

from .models import (
    CurriculumVitae, PersonalInfo, ProfessionalProfile, WorkExperience,
    Education, ComplementaryTraining, Skill, Language, Project, Achievement,
    Volunteering, SocialNetwork, Interest, OtherInfo, CVProfile, NexusDNAEntry,
)
from .forms import (
    PersonalInfoForm, ProfessionalProfileForm, WorkExperienceForm,
    EducationForm, ComplementaryTrainingForm, SkillForm, LanguageForm,
    ProjectForm, AchievementForm, VolunteeringForm, SocialNetworkForm,
    InterestForm, OtherInfoForm, CVProfileForm,
    CvPdfUploadForm,
)
from .services.cv_importer import PdfImportError, apply_cv_fields, extract_cv_fields


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
    """AJAX endpoint: generate a professional summary suggestion using OpenAI."""
    from django.conf import settings

    cv = _get_or_create_cv(request.user)

    # ── Collect available data ──────────────────────────────────────────────
    personal = getattr(cv, 'personal_info', None)
    perfil = getattr(cv, 'professional_profile', None)
    experiences = list(cv.work_experiences.order_by('orden', '-fecha_inicio')[:4])
    educations = list(cv.educations.order_by('orden', '-fecha_inicio')[:3])
    skills = list(cv.skills.order_by('orden')[:12])
    languages = list(cv.languages.order_by('orden'))

    # ── Check minimum data ──────────────────────────────────────────────────
    filled_sections = sum([
        personal is not None,
        bool(experiences),
        bool(educations),
        bool(skills),
        bool(languages),
        perfil is not None and bool(perfil.profesion),
    ])
    if filled_sections < 2:
        return JsonResponse({
            'ok': False,
            'warning': (
                'Necesito más información para generar una sugerencia. '
                'Completa al menos tus datos personales y una sección más '
                '(experiencia laboral, educación o habilidades).'
            ),
        })

    # ── Build context string ────────────────────────────────────────────────
    lines = []
    if personal:
        lines.append(f'Nombre: {personal.nombre} {personal.apellidos}')
        if personal.ciudad:
            lines.append(f'Ciudad: {personal.ciudad}')
    if perfil and perfil.profesion:
        lines.append(f'Profesión/especialidad: {perfil.profesion}')
    if perfil and perfil.objetivo:
        lines.append(f'Objetivo declarado: {perfil.objetivo}')
    if experiences:
        lines.append('Experiencia laboral:')
        for exp in experiences:
            current = ' (puesto actual)' if exp.trabajo_actual else ''
            lines.append(f'  - {exp.puesto} en {exp.empresa}{current}')
            if exp.descripcion:
                lines.append(f'    {exp.descripcion[:200]}')
    if educations:
        lines.append('Formación académica:')
        for edu in educations:
            lines.append(f'  - {edu.titulo} en {edu.centro}')
    if skills:
        hard = [s.nombre for s in skills if s.tipo == 'hard']
        soft = [s.nombre for s in skills if s.tipo == 'soft']
        if hard:
            lines.append(f'Habilidades técnicas: {", ".join(hard)}')
        if soft:
            lines.append(f'Habilidades blandas: {", ".join(soft)}')
    if languages:
        lines.append('Idiomas: ' + ', '.join(f'{l.idioma} ({l.get_nivel_display()})' for l in languages))

    context_text = '\n'.join(lines)

    # ── Call OpenAI ─────────────────────────────────────────────────────────
    api_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key:
        return JsonResponse({
            'ok': False,
            'warning': 'La IA no está configurada. Añade OPENAI_API_KEY a tu archivo .env.',
        })

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {
                    'role': 'system',
                    'content': (
                        'Eres un experto en recursos humanos y redacción de currículums. '
                        'Tu tarea es generar un resumen profesional conciso (3-5 frases) '
                        'en primera persona, en español, adaptado al perfil del candidato. '
                        'Destaca los puntos fuertes, experiencia clave y propuesta de valor. '
                        'No uses frases genéricas vacías. Sé directo y específico.'
                    ),
                },
                {
                    'role': 'user',
                    'content': (
                        f'Genera un resumen profesional para el siguiente perfil:\n\n{context_text}'
                    ),
                },
            ],
            max_tokens=300,
            temperature=0.7,
        )
        suggestion = response.choices[0].message.content.strip()
        return JsonResponse({'ok': True, 'suggestion': suggestion})

    except Exception as exc:  # noqa: BLE001
        return JsonResponse({
            'ok': False,
            'warning': f'Error al contactar con la IA: {exc}',
        })


# ─── Section: Social networks ─────────────────────────────────────────────────

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
    profiles = CVProfile.objects.filter(user=request.user)
    return render(request, 'curriculum/profiles/list.html', {'profiles': profiles})


@login_required
def profile_create(request):
    cv = _get_or_create_cv(request.user)
    form = CVProfileForm(request.POST or None, user=request.user)
    if form.is_valid():
        profile = form.save(commit=False)
        profile.user = request.user
        profile.cv = cv
        profile.save()
        return redirect('curriculum:profile_edit_items', pk=profile.pk)
    return render(request, 'curriculum/profiles/form.html', {'form': form, 'action': 'Crear perfil'})


@login_required
def profile_edit(request, pk):
    profile = get_object_or_404(CVProfile, pk=pk, user=request.user)
    form = CVProfileForm(request.POST or None, instance=profile, user=request.user)
    if form.is_valid():
        form.save()
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


# ─── Public CV & PDF ──────────────────────────────────────────────────────────

def public_cv(request, slug):
    """Render the public profile with the chosen template."""
    profile = get_object_or_404(CVProfile, slug=slug, is_public=True)
    if profile.templatecv:
        template_name = f"curriculum/cv_templates/{profile.templatecv.archivo}"
    else:
        template_map = {
            'minimalist': 'curriculum/cv_templates/minimalist.html',
            'tech': 'curriculum/cv_templates/tech.html',
            'normal': 'curriculum/cv_templates/normal.html',
            'test01': 'curriculum/cv_templates/test01.html',
        }
        template_name = template_map.get(profile.template, 'curriculum/cv_templates/minimalist.html')
    return render(request, template_name, {'profile': profile})


def cv_pdf(request, slug):
    """Redirect to the public CV page with ?print=1 to trigger browser print dialog."""
    profile = get_object_or_404(CVProfile, slug=slug, is_public=True)
    from django.http import HttpResponseRedirect
    return HttpResponseRedirect(
        reverse('curriculum:public_cv', kwargs={'slug': slug}) + '?print=1'
    )


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
