from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ProximaActualizacion


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ProximaActualizacionTests(TestCase):
    def test_jobs_links_show_matching_updates_without_team(self):
        entries = (
            ('DJO_011', 'Empleos para ti', 'updates:empleos_para_ti'),
            ('DJO_012', 'Impulsa tu carrera', 'updates:impulsa_tu_carrera'),
            ('DJO_013', 'Entrena tu entrevista', 'updates:entrena_tu_entrevista'),
            ('DJO_014', 'Explora oportunidades', 'updates:explora_oportunidades'),
            ('DJO_015', 'Descubre empresas', 'updates:descubre_empresas'),
        )
        for reference, title, route in entries:
            ProximaActualizacion.objects.create(**self.datos(
                referencia=reference, titulo=f'Novedades {title}', equipo='PRIVATE-JOBS-TEAM',
            ))
            self.assertEqual(self.client.get(reverse(route)).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user(username='jobs-updates-user'))
        for reference, title, route in entries:
            with self.subTest(reference=reference):
                response = self.client.get(reverse(route))
                for text in (reference, f'Novedades {title}', '22/10/2026', '30/10/2026', 'Análisis'):
                    self.assertContains(response, text)
                self.assertNotContains(response, 'PRIVATE-JOBS-TEAM')
                for other, _, _ in entries:
                    if other != reference:
                        self.assertNotContains(response, other)
                ProximaActualizacion.objects.filter(referencia=reference).update(descripcion='Nuevo contenido')
                self.assertContains(self.client.get(reverse(route)), 'Nuevo contenido')
                ProximaActualizacion.objects.filter(referencia=reference).delete()
                self.assertEqual(self.client.get(reverse(route)).status_code, 404)

    def test_shop_links_show_their_reference_without_team(self):
        for reference, title, route in (
            ('DSH_003', 'DuckyShop', 'updates:duckyshop'),
            ('DSH_004', 'Tech2Hand', 'updates:tech2hand'),
        ):
            ProximaActualizacion.objects.create(**self.datos(
                referencia=reference, titulo=f'Novedades {title}', equipo='PRIVATE-SHOP-TEAM',
            ))
            self.assertEqual(self.client.get(reverse(route)).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user(username='shops-user'))
        for reference, title, route, other in (
            ('DSH_003', 'DuckyShop', 'updates:duckyshop', 'DSH_004'),
            ('DSH_004', 'Tech2Hand', 'updates:tech2hand', 'DSH_003'),
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertContains(response, reference)
                self.assertContains(response, f'Novedades {title}')
                self.assertContains(response, '22/10/2026')
                self.assertContains(response, 'Análisis')
                self.assertNotContains(response, other)
                self.assertNotContains(response, 'PRIVATE-SHOP-TEAM')
                ProximaActualizacion.objects.filter(referencia=reference).delete()
                self.assertEqual(self.client.get(reverse(route)).status_code, 404)

    def test_formacion_hub_muestra_dac_002_sin_equipo(self):
        ProximaActualizacion.objects.create(**self.datos(referencia='DPR_001', titulo='Solo Tech DNA'))
        actualizacion = ProximaActualizacion.objects.create(**self.datos(
            referencia='DAC_002', titulo='Novedades formativas', equipo='EQUIPO-PRIVADO-HUB',
        ))
        url = reverse('updates:formacion_hub')
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user(username='hub-user'))
        response = self.client.get(url)
        for texto in ('Formación HUB', 'DAC_002', actualizacion.titulo, actualizacion.descripcion, 'Análisis', '22/10/2026', '30/10/2026'):
            self.assertContains(response, texto)
        self.assertContains(response, f'href="{url}"')
        self.assertNotContains(response, actualizacion.equipo)
        self.assertNotContains(response, 'Solo Tech DNA')
        actualizacion.delete()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_tech_dna_muestra_solo_actualizacion_por_referencia_sin_equipo(self):
        ProximaActualizacion.objects.create(**self.datos(
            referencia='OTRA', titulo='No mostrar otra actualizacion',
        ))
        actualizacion = ProximaActualizacion.objects.create(**self.datos(
            referencia='DPR_001', equipo='EQUIPO-INTERNO-NO-PUBLICAR',
        ))
        url = reverse('updates:tech_dna')
        self.assertEqual(self.client.get(url).status_code, 302)
        usuario = get_user_model().objects.create_user(username='techdna-user')
        self.client.force_login(usuario)
        response = self.client.get(url)
        for texto in ('DPR_001', actualizacion.titulo, actualizacion.descripcion, 'Análisis', '22/10/2026', '30/10/2026'):
            self.assertContains(response, texto)
        self.assertContains(response, f'href="{url}"')
        self.assertNotContains(response, actualizacion.equipo)
        self.assertNotContains(response, 'No mostrar otra actualizacion')
        actualizacion.descripcion = 'Descripcion actualizada'
        actualizacion.save()
        self.assertContains(self.client.get(url), 'Descripcion actualizada')
        actualizacion.delete()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_dashboard_admin_muestra_acceso_segun_permisos(self):
        usuario = get_user_model().objects.create_user(username='updates-dashboard', is_staff=True)
        dashboard = reverse('analytics:dashboard_admin')
        destino = reverse('admin:updates_proximaactualizacion_changelist')
        self.client.force_login(usuario)
        self.assertNotContains(self.client.get(dashboard), f'href="{destino}"')
        self.assertNotContains(self.client.get(reverse('admin:index')), 'Cosas pendientes')
        for codename in ('view_proximaactualizacion', 'change_proximaactualizacion'):
            usuario.user_permissions.set(Permission.objects.filter(
                content_type__app_label='updates', codename=codename,
            ))
            self.assertContains(self.client.get(dashboard), f'href="{destino}"')
            portada = self.client.get(reverse('admin:index'))
            self.assertContains(portada, 'Cosas pendientes')
            self.assertContains(portada, f'href="{destino}"')
            self.assertNotContains(portada, 'Añadir próxima actualización')
            self.assertEqual(self.client.get(destino).status_code, 200)
        usuario.is_superuser = True
        usuario.save()
        usuario.user_permissions.clear()
        self.assertContains(self.client.get(dashboard), f'href="{destino}"')
        self.assertContains(self.client.get(reverse('admin:index')), 'Añadir próxima actualización')

    def datos(self, **cambios):
        datos = {
            'referencia': 'ACT-001',
            'titulo': 'Mejoras del campus',
            'descripcion': 'Nuevas herramientas de aprendizaje.',
            'equipo': 'Equipo LMS',
            'fecha_inicio': date(2026, 10, 22),
            'fecha_fin': date(2026, 10, 30),
        }
        datos.update(cambios)
        return datos

    def test_guarda_campos_y_estados(self):
        actualizacion = ProximaActualizacion.objects.create(**self.datos())
        self.assertEqual(actualizacion.estado, ProximaActualizacion.Estado.ANALISIS)
        self.assertEqual(str(actualizacion), 'ACT-001 — Mejoras del campus')
        for estado in ProximaActualizacion.Estado.values:
            actualizacion.estado = estado
            actualizacion.full_clean()
            actualizacion.save()
            actualizacion.refresh_from_db()
            self.assertEqual(actualizacion.estado, estado)

    def test_referencia_unica(self):
        ProximaActualizacion.objects.create(**self.datos())
        with self.assertRaises(ValidationError) as error:
            ProximaActualizacion(**self.datos()).full_clean()
        self.assertIn('referencia', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProximaActualizacion.objects.create(**self.datos())

    def test_valida_fechas_y_permite_mismo_dia(self):
        actualizacion = ProximaActualizacion(**self.datos(fecha_fin=date(2026, 10, 21)))
        with self.assertRaises(ValidationError) as error:
            actualizacion.full_clean()
        self.assertIn('fecha_fin', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            actualizacion.save()
        actualizacion.fecha_fin = actualizacion.fecha_inicio
        actualizacion.full_clean()
        actualizacion.save()

    def test_rechaza_estado_desconocido(self):
        actualizacion = ProximaActualizacion(**self.datos(estado='desconocido'))
        with self.assertRaises(ValidationError):
            actualizacion.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            actualizacion.save()

    def test_campos_obligatorios(self):
        with self.assertRaises(ValidationError) as error:
            ProximaActualizacion().full_clean()
        for campo in ('referencia', 'titulo', 'descripcion', 'equipo', 'fecha_inicio', 'fecha_fin'):
            self.assertIn(campo, error.exception.message_dict)

    def test_admin_crea_edita_filtra_y_elimina(self):
        admin = get_user_model().objects.create_superuser(
            username='updates-admin', email='updates@example.test', password='test-password',
        )
        self.client.force_login(admin)
        lista = reverse('admin:updates_proximaactualizacion_changelist')
        crear = reverse('admin:updates_proximaactualizacion_add')
        self.assertEqual(self.client.get(crear).status_code, 200)
        datos = self.datos(estado='analisis')
        self.assertRedirects(self.client.post(crear, datos), lista)
        actualizacion = ProximaActualizacion.objects.get()
        cambiar = reverse('admin:updates_proximaactualizacion_change', args=[actualizacion.pk])
        self.assertRedirects(self.client.post(cambiar, {**datos, 'estado': 'test'}), lista)
        actualizacion.refresh_from_db()
        self.assertEqual(actualizacion.estado, 'test')
        response = self.client.get(lista, {'q': 'ACT-001', 'estado__exact': 'test'})
        self.assertContains(response, 'Mejoras del campus')
        self.assertNotContains(self.client.get(lista, {'estado__exact': 'produccion'}), 'Mejoras del campus')
        borrar = reverse('admin:updates_proximaactualizacion_delete', args=[actualizacion.pk])
        self.assertRedirects(self.client.post(borrar, {'post': 'yes'}), lista)
        self.assertFalse(ProximaActualizacion.objects.exists())

    def test_admin_muestra_errores_del_formulario(self):
        admin = get_user_model().objects.create_superuser(
            username='updates-validation', email='validation@example.test', password='test-password',
        )
        self.client.force_login(admin)
        response = self.client.post(
            reverse('admin:updates_proximaactualizacion_add'),
            self.datos(fecha_fin=date(2026, 10, 21), estado='analisis'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('fecha_fin', response.context['adminform'].form.errors)
        self.assertFalse(ProximaActualizacion.objects.exists())

    def test_staff_sin_permisos_no_puede_gestionar(self):
        actualizacion = ProximaActualizacion.objects.create(**self.datos())
        usuario = get_user_model().objects.create_user(username='updates-staff', is_staff=True)
        lista = reverse('admin:updates_proximaactualizacion_changelist')
        self.assertEqual(self.client.get(lista).status_code, 302)
        self.client.force_login(usuario)
        for nombre, args in (
            ('changelist', []), ('add', []),
            ('change', [actualizacion.pk]), ('delete', [actualizacion.pk]),
        ):
            url = reverse(f'admin:updates_proximaactualizacion_{nombre}', args=args)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, self.datos()).status_code, 403)
