from django.urls import path
from . import views
from . import versioned_views as versioned

app_name = 'nexus'

urlpatterns = [
    path('catalogo/', versioned.catalog, name='catalog'),
    path('evaluacion/<int:pk>/', versioned.info, name='versioned_info'),
    path('evaluacion/<int:pk>/comenzar/', versioned.start, name='versioned_start'),
    path('intento/<int:pk>/', versioned.take, name='versioned_take'),
    path('intento/<int:pk>/borrar/', versioned.delete_attempt, name='versioned_delete'),
    path('intento/<int:pk>/informe/', versioned.report, name='versioned_report'),
    path('intento/<int:pk>/objetivo/', versioned.goal, name='versioned_goal'),
    path('intento/<int:pk>/ia/', versioned.ai, name='versioned_ai'),
    path('historial/', versioned.history, name='versioned_history'),
    path('evolucion/', versioned.evolution, name='versioned_evolution'),
    # ── Dashboard admin ───────────────────────────────────────────────────────
    path('', views.NexusDashboardView.as_view(), name='dashboard'),

    # ── Tests (plantillas HTML) — CRUD admin ──────────────────────────────────
    path('tests/', views.TestPlantillaListView.as_view(), name='test_list'),
    path('tests/crear/', views.TestPlantillaCreateView.as_view(), name='test_create'),
    path('tests/<int:pk>/editar/', views.TestPlantillaUpdateView.as_view(), name='test_edit'),
    path('tests/<int:pk>/eliminar/', views.TestPlantillaDeleteView.as_view(), name='test_delete'),

    # ── Módulos CRUD admin ────────────────────────────────────────────────────
    path('modulos/', views.ModuleListView.as_view(), name='module_list'),
    path('modulos/crear/', views.ModuleCreateView.as_view(), name='module_create'),
    path('modulos/<int:pk>/', views.ModuleDetailView.as_view(), name='module_detail'),
    path('modulos/<int:pk>/editar/', views.ModuleUpdateView.as_view(), name='module_edit'),
    path('modulos/<int:pk>/eliminar/', views.ModuleDeleteView.as_view(), name='module_delete'),

    # ── Preguntas CRUD admin (anidadas bajo módulo) ───────────────────────────
    path('modulos/<int:module_pk>/preguntas/crear/', views.QuestionCreateView.as_view(), name='question_create'),
    path('modulos/<int:module_pk>/preguntas/<int:pk>/editar/', views.QuestionUpdateView.as_view(), name='question_edit'),
    path('modulos/<int:module_pk>/preguntas/<int:pk>/eliminar/', views.QuestionDeleteView.as_view(), name='question_delete'),

    # ── Perfiles DNA — CRUD admin ─────────────────────────────────────────────
    path('resultados/', views.DNAProfileListView.as_view(), name='profile_list'),
    path('resultados/crear/', views.DNAProfileCreateView.as_view(), name='profile_create'),
    path('resultados/<int:pk>/', views.DNAProfileDetailView.as_view(), name='profile_detail'),
    path('resultados/<int:pk>/editar/', views.DNAProfileUpdateView.as_view(), name='profile_edit'),
    path('resultados/<int:pk>/eliminar/', views.DNAProfileDeleteView.as_view(), name='profile_delete'),

    # ── Recursos de tests — CRUD admin ───────────────────────────────────────
    path('tests/<int:test_pk>/recursos/crear/', views.RecursoCreateView.as_view(), name='recurso_create'),
    path('recursos/<int:pk>/editar/', views.RecursoUpdateView.as_view(), name='recurso_edit'),
    path('recursos/<int:pk>/eliminar/', views.RecursoDeleteView.as_view(), name='recurso_delete'),

    # ── Demo / acceso libre (sin login) ──────────────────────────────────────
    path('demo/',                         versioned.demo,       name='demo_list'),
    path('demo/test/<int:pk>/',           versioned.demo,       name='demo_take'),
    path('demo/enviar/<int:pk>/',         versioned.demo,     name='demo_submit'),
    path('demo/resultados/',              versioned.demo, name='demo_results'),

    # ── Área alumnos (login requerido, sin restricción de staff) ─────────────
    path('mis-tests/', views.AlumnoListaTestsView.as_view(), name='student_list'),
    path('test/<int:pk>/info/', views.TestInfoView.as_view(), name='test_info'),
    path('test/<int:pk>/', versioned.legacy_take_unavailable, name='student_take'),
    path('sesion/<int:pk>/enviar/', versioned.legacy_submit_unavailable, name='student_submit'),
    path('sesion/<int:pk>/resultados/', views.ResultadosTestView.as_view(), name='student_results'),
    path('sesion/<int:pk>/guardar-perfil/', views.GuardarEnPerfilView.as_view(), name='student_save_profile'),
    path('sesion/<int:pk>/borrar/', views.BorrarSesionView.as_view(), name='student_delete_session'),
    path('test/<int:pk>/historial/', views.HistorialTestView.as_view(), name='student_history'),
    path('mi-dna/', views.MiDNAView.as_view(), name='student_my_dna'),

    # ── Informes ─────────────────────────────────────────────────────────────
    path('sesion/<int:pk>/guardar-informe/', versioned.legacy_report_unavailable, name='guardar_informe'),
    path('sesion/<int:pk>/generar-informe-ai/', versioned.legacy_ai_unavailable, name='generar_informe_ai'),
    path('informe/<int:pk>/checkout/', views.CheckoutInformeView.as_view(), name='checkout_informe'),
    path('informe/<int:pk>/checkout/stripe/', views.StripeCheckoutView.as_view(), name='checkout_informe_stripe'),
    path('informe/<int:pk>/checkout/paypal/', views.PayPalCheckoutView.as_view(), name='checkout_informe_paypal'),
    path('informe/<int:pk>/checkout/dev-skip/', views.DevSkipInformeCheckoutView.as_view(), name='checkout_informe_dev_skip'),
    path('informe/<int:pk>/checkout/stripe/success/', views.StripeSuccessView.as_view(), name='checkout_informe_stripe_success'),
    path('informe/<int:pk>/checkout/paypal/success/', views.PayPalSuccessView.as_view(), name='checkout_informe_paypal_success'),
    path('informe/<int:pk>/checkout/cancel/', views.InformeCheckoutCancelView.as_view(), name='checkout_informe_cancel'),
    path('informe/<int:pk>/borrar/', views.BorrarInformeView.as_view(), name='delete_informe'),
    path('informe/<int:pk>/', views.VerInformeView.as_view(), name='ver_informe'),
]
