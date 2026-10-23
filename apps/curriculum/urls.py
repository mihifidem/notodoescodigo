"""URL configuration for the Curriculum Vitae builder app."""
from django.urls import path
from . import views
from . import letter_views
from . import ats_views

app_name = 'curriculum'

urlpatterns = [
    path('verificar/demo/', ats_views.ats_demo, name='ats_demo'),
    path('verificar/', ats_views.ats_list, name='ats_list'),
    path('verificar/nuevo/', ats_views.ats_new, name='ats_new'),
    path('verificar/fuente/<uuid:pk>/', ats_views.ats_review, name='ats_review'),
    path('verificar/fuente/<uuid:pk>/original/', ats_views.ats_original, name='ats_original'),
    path('verificar/fuente/<uuid:pk>/descartar/', ats_views.ats_discard_source, name='ats_discard_source'),
    path('verificar/comparar/', ats_views.ats_compare, name='ats_compare'),
    path('verificar/comparativa/<uuid:pk>/', ats_views.ats_comparison, name='ats_comparison'),
    path('verificar/informe/<uuid:pk>/', ats_views.ats_detail, name='ats_detail'),
    path('verificar/informe/<uuid:pk>/pdf/<str:extent>/', ats_views.ats_download, name='ats_download'),
    path('verificar/informe/<uuid:pk>/ia/', ats_views.ats_ai, name='ats_ai'),
    path('verificar/informe/<uuid:pk>/accion/<str:action>/', ats_views.ats_action, name='ats_action'),
    path('cartas/demo/', letter_views.letter_demo, name='letter_demo'),
    path('cartas/', letter_views.letter_list, name='letter_list'),
    path('cartas/nueva/', letter_views.letter_new, name='letter_new'),
    path('cartas/confirmar/', letter_views.letter_confirm, name='letter_confirm'),
    path('cartas/<uuid:pk>/', letter_views.letter_detail, name='letter_detail'),
    path('cartas/<uuid:pk>/generar/', letter_views.letter_generate, name='letter_generate'),
    path('cartas/<uuid:pk>/version/<int:version>/<str:format>/', letter_views.letter_download, name='letter_download'),
    path('cartas/<uuid:pk>/accion/<str:action>/', letter_views.letter_action, name='letter_action'),
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
    path('plantillas/', views.template_catalog, name='template_catalog'),
    path('plantillas/<slug:code>/vista-previa/', views.template_preview, name='template_preview'),
    path('perfiles/crear/', views.profile_create, name='profile_create'),
    path('perfiles/<int:pk>/editar/', views.profile_edit, name='profile_edit'),
    path('perfiles/<int:pk>/contenido/', views.profile_edit_items, name='profile_edit_items'),
    path('perfiles/<int:pk>/previsualizar/', views.profile_preview, name='profile_preview'),
    path('perfiles/<int:pk>/pdf/', views.private_profile_pdf, name='profile_private_pdf'),
    path('perfiles/<int:pk>/publicar/', views.profile_publish, name='profile_publish'),
    path('perfiles/<int:pk>/hacer-privado/', views.profile_unpublish, name='profile_unpublish'),
    path('perfiles/<int:pk>/duplicar/', views.profile_duplicate, name='profile_duplicate'),
    path('perfiles/<int:pk>/archivar/', views.profile_archive, name='profile_archive'),
    path('perfiles/<int:pk>/restaurar/', views.profile_restore, name='profile_restore'),
    path('perfiles/ajustar-publicaciones/', views.profile_reconcile, name='profile_reconcile'),
    path('perfiles/<int:pk>/eliminar/', views.profile_delete, name='profile_delete'),

    # Nexus DNA
    path('nexus-dna/', views.nexus_dna_list, name='nexus_dna_list'),
    path('nexus-dna/<int:pk>/toggle/', views.nexus_dna_toggle, name='nexus_dna_toggle'),
    path('nexus-dna/<int:pk>/eliminar/', views.nexus_dna_delete, name='nexus_dna_delete'),

    # Internal aliases for public CV and PDF (the real URLs live at /opentowork/ in config/urls.py)
    path('ver/<slug:slug>/', views.public_cv, name='public_cv'),
    path('ver/<slug:slug>/pdf/', views.cv_pdf, name='cv_pdf'),
]
