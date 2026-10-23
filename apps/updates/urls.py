from django.urls import path

from .views import TechDNAUpdateView


app_name = 'updates'

urlpatterns = [
    path('tech-dna/', TechDNAUpdateView.as_view(), name='tech_dna'),
    path('formacion-hub/', TechDNAUpdateView.as_view(
        reference='DAC_002', section_title='Formación HUB',
    ), name='formacion_hub'),
    path('duckyshop/', TechDNAUpdateView.as_view(
        reference='DSH_003', section_title='DuckyShop',
    ), name='duckyshop'),
    path('tech2hand/', TechDNAUpdateView.as_view(
        reference='DSH_004', section_title='Tech2Hand',
    ), name='tech2hand'),
    path('empleos-para-ti/', TechDNAUpdateView.as_view(
        reference='DJO_011', section_title='Empleos para ti',
    ), name='empleos_para_ti'),
    path('impulsa-tu-carrera/', TechDNAUpdateView.as_view(
        reference='DJO_012', section_title='Impulsa tu carrera',
    ), name='impulsa_tu_carrera'),
    path('entrena-tu-entrevista/', TechDNAUpdateView.as_view(
        reference='DJO_013', section_title='Entrena tu entrevista',
    ), name='entrena_tu_entrevista'),
    path('explora-oportunidades/', TechDNAUpdateView.as_view(
        reference='DJO_014', section_title='Explora oportunidades',
    ), name='explora_oportunidades'),
    path('descubre-empresas/', TechDNAUpdateView.as_view(
        reference='DJO_015', section_title='Descubre empresas',
    ), name='descubre_empresas'),
]
