"""URL configuration for the Curriculum Vitae builder app."""
from django.urls import path
from . import views

app_name = 'curriculum'

urlpatterns = [
    # Dashboard
    path('', views.dashboard, name='dashboard'),
    path('importar-pdf/', views.import_cv_pdf, name='import_cv_pdf'),

    # Personal info
    path('datos-personales/', views.edit_personal_info, name='personal_info'),
    # Professional profile
    path('perfil-profesional/', views.edit_professional_profile, name='professional_profile'),
    path('perfil-profesional/sugerir-resumen/', views.suggest_resumen_ia, name='suggest_resumen_ia'),
    # Social networks
    path('redes/', views.edit_social_networks, name='social_networks'),
    # Other info
    path('otros/', views.edit_other_info, name='other_info'),

    # Work experience
    path('experiencia/', views.WorkExperienceListView.as_view(), name='work_experience_list'),
    path('experiencia/nueva/', views.WorkExperienceCreateView.as_view(), name='work_experience_create'),
    path('experiencia/<int:pk>/editar/', views.WorkExperienceUpdateView.as_view(), name='work_experience_edit'),
    path('experiencia/<int:pk>/eliminar/', views.WorkExperienceDeleteView.as_view(), name='work_experience_delete'),

    # Education
    path('educacion/', views.EducationListView.as_view(), name='education_list'),
    path('educacion/nueva/', views.EducationCreateView.as_view(), name='education_create'),
    path('educacion/<int:pk>/editar/', views.EducationUpdateView.as_view(), name='education_edit'),
    path('educacion/<int:pk>/eliminar/', views.EducationDeleteView.as_view(), name='education_delete'),

    # Complementary training
    path('formacion/', views.TrainingListView.as_view(), name='training_list'),
    path('formacion/nueva/', views.TrainingCreateView.as_view(), name='training_create'),
    path('formacion/<int:pk>/editar/', views.TrainingUpdateView.as_view(), name='training_edit'),
    path('formacion/<int:pk>/eliminar/', views.TrainingDeleteView.as_view(), name='training_delete'),

    # Skills
    path('habilidades/', views.SkillListView.as_view(), name='skill_list'),
    path('habilidades/nueva/', views.SkillCreateView.as_view(), name='skill_create'),
    path('habilidades/<int:pk>/editar/', views.SkillUpdateView.as_view(), name='skill_edit'),
    path('habilidades/<int:pk>/eliminar/', views.SkillDeleteView.as_view(), name='skill_delete'),

    # Languages
    path('idiomas/', views.LanguageListView.as_view(), name='language_list'),
    path('idiomas/nuevo/', views.LanguageCreateView.as_view(), name='language_create'),
    path('idiomas/<int:pk>/editar/', views.LanguageUpdateView.as_view(), name='language_edit'),
    path('idiomas/<int:pk>/eliminar/', views.LanguageDeleteView.as_view(), name='language_delete'),

    # Projects
    path('proyectos/', views.ProjectListView.as_view(), name='project_list'),
    path('proyectos/nuevo/', views.ProjectCreateView.as_view(), name='project_create'),
    path('proyectos/<int:pk>/editar/', views.ProjectUpdateView.as_view(), name='project_edit'),
    path('proyectos/<int:pk>/eliminar/', views.ProjectDeleteView.as_view(), name='project_delete'),

    # Achievements
    path('logros/', views.AchievementListView.as_view(), name='achievement_list'),
    path('logros/nuevo/', views.AchievementCreateView.as_view(), name='achievement_create'),
    path('logros/<int:pk>/editar/', views.AchievementUpdateView.as_view(), name='achievement_edit'),
    path('logros/<int:pk>/eliminar/', views.AchievementDeleteView.as_view(), name='achievement_delete'),

    # Volunteering
    path('voluntariado/', views.VolunteeringListView.as_view(), name='volunteering_list'),
    path('voluntariado/nuevo/', views.VolunteeringCreateView.as_view(), name='volunteering_create'),
    path('voluntariado/<int:pk>/editar/', views.VolunteeringUpdateView.as_view(), name='volunteering_edit'),
    path('voluntariado/<int:pk>/eliminar/', views.VolunteeringDeleteView.as_view(), name='volunteering_delete'),

    # Interests
    path('intereses/', views.InterestListView.as_view(), name='interest_list'),
    path('intereses/nuevo/', views.InterestCreateView.as_view(), name='interest_create'),
    path('intereses/<int:pk>/editar/', views.InterestUpdateView.as_view(), name='interest_edit'),
    path('intereses/<int:pk>/eliminar/', views.InterestDeleteView.as_view(), name='interest_delete'),

    # Profiles
    path('perfiles/', views.profile_list, name='profile_list'),
    path('perfiles/crear/', views.profile_create, name='profile_create'),
    path('perfiles/<int:pk>/editar/', views.profile_edit, name='profile_edit'),
    path('perfiles/<int:pk>/contenido/', views.profile_edit_items, name='profile_edit_items'),
    path('perfiles/<int:pk>/eliminar/', views.profile_delete, name='profile_delete'),

    # Nexus DNA
    path('nexus-dna/', views.nexus_dna_list, name='nexus_dna_list'),
    path('nexus-dna/<int:pk>/toggle/', views.nexus_dna_toggle, name='nexus_dna_toggle'),
    path('nexus-dna/<int:pk>/eliminar/', views.nexus_dna_delete, name='nexus_dna_delete'),

    # Internal aliases for public CV and PDF (the real URLs live at /opentowork/ in config/urls.py)
    path('ver/<slug:slug>/', views.public_cv, name='public_cv'),
    path('ver/<slug:slug>/pdf/', views.cv_pdf, name='cv_pdf'),
]
