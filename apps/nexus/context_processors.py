from .models import TestPlantilla


def demo_tests(request):
    """Inject active demo tests into every template for the public navbar."""
    tests = list(
        TestPlantilla.objects
        .filter(activo=True, es_demo=True, nivel_acceso=TestPlantilla.NIVEL_ESSENTIAL)
        .values('pk', 'nombre')
    )
    return {'nexus_demo_tests': tests}
