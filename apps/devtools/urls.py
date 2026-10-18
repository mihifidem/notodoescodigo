from django.urls import path

from . import views

app_name = 'devtools'

urlpatterns = [
    path('', views.DevToolsHubView.as_view(), name='hub'),
    path('admin/<str:slug>/toggle/', views.devtool_admin_toggle, name='tool_toggle'),
    path('votar/<str:tool_slug>/', views.vote_tool, name='votar'),
    path('medidas/', views.UniversalMeasureConverterView.as_view(), name='medidas'),
    path('colores/', views.ColorToolView.as_view(), name='colores'),
    path('sombras/', views.ShadowToolView.as_view(), name='sombras'),
    path('analizador-contraste/', views.ColorContrastAnalyzerView.as_view(), name='analizador_contraste'),
    path('espaciado/', views.SpacingToolView.as_view(), name='espaciado'),
    path('slugify/', views.SlugifyToolView.as_view(), name='slugify'),
    path('tamanos/', views.SizeEstimatorView.as_view(), name='tamanos'),
    path('css/', views.CssSnippetGeneratorView.as_view(), name='css'),
    path('gradientes/', views.GradientToolView.as_view(), name='gradientes'),
    path('tipografia/', views.TypographyToolView.as_view(), name='tipografia'),
    path('paleta/', views.PaletteToolView.as_view(), name='paleta'),
    path('bordes/', views.BorderRadiusToolView.as_view(), name='bordes'),
    path('rem/', views.RemConverterToolView.as_view(), name='rem'),
    path('botones/', views.ButtonGeneratorToolView.as_view(), name='botones'),
    path('layout/', views.LayoutCalculatorToolView.as_view(), name='layout'),
    path('json/', views.JsonFormatterToolView.as_view(), name='json'),
    path('base64/', views.Base64ToolView.as_view(), name='base64'),
    path('texto/', views.TextToolView.as_view(), name='texto'),
    path('text_counter/', views.TextCounterToolView.as_view(), name='text_counter'),
    path('hash/', views.HashToolView.as_view(), name='hash'),
    path('css_vars/', views.CssVariablesToolView.as_view(), name='css_vars'),
    path('breakpoints/', views.BreakpointsToolView.as_view(), name='breakpoints'),
    path('metadata_img/', views.MetadataImageToolView.as_view(), name='metadata_img'),
    path('qr/', views.QrToolView.as_view(), name='qr'),
    path('url_shortener/', views.UrlShortenerToolView.as_view(), name='url_shortener'),
]
