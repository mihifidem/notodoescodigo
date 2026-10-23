from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import Curso, InscripcionCurso, Progreso
from apps.courses.models import Quiz, ResultadoQuiz
from apps.gamification.badge_services import can_grant_badge, grant_manual_badge
from apps.gamification.models import (
    Insignia,
    InsigniaAuditEvent,
    InsigniaUsuario,
    InsigniaVersion,
    RankingAdjustment,
    RankingAuditEvent,
    RankingDefinition,
    RankingEntry,
    RankingParticipation,
    RankingRuleVersion,
    RankingSeason,
)
from apps.gamification.ranking_services import (
    activate_season,
    close_season,
    recalculate_closed_season,
    ranking_rows,
    update_participation,
)
from apps.users.models import (
    AccessRoleAssignment,
    Aula,
    Organization,
    OrganizationMembership,
)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class InsigniasViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.aula = Aula.objects.create(
            nombre='Aula Gamificacion',
            direccion='Calle Badge 1',
            horario='Manana',
        )
        self.user = user_model.objects.create_user(
            username='usuario_insignias',
            password='test1234',
            aula=self.aula,
        )
        self.curso = Curso.objects.create(
            nombre='Curso con insignia',
            descripcion='Descripcion',
            fecha_inicio=date.today(),
            horas_duracion=2,
            activo=True,
        )
        self.tema = self.curso.temas.create(
            titulo='Tema con insignia',
            contenido='Contenido',
            orden=1,
            puntos_otorgados=10,
        )
        self.insignia = Insignia.objects.create(
            nombre='Insignia del tema',
            descripcion='Se activa al completar el tema',
            tipo='tema',
            tema_objetivo=self.tema,
            visible=True,
            status=Insignia.Status.PUBLISHED,
        )
        self.version = InsigniaVersion.objects.create(
            badge=self.insignia,
            version=1,
            rule_type=InsigniaVersion.RuleType.TOPIC_COMPLETED,
            topic=self.tema,
            threshold=1,
            public_requirements='Completar el tema.',
            source_description='Progreso del curso',
            verification_description='Progreso almacenado por servidor',
        )
        self.aula.cursos.add(self.curso)
        InscripcionCurso.objects.create(usuario=self.user, curso=self.curso)
        self.client.force_login(self.user)

    def test_insignia_de_tema_completado_aparece_activa_en_color(self):
        Progreso.objects.create(usuario=self.user, tema=self.tema, completado=True)

        response = self.client.get(reverse('gamification:insignias'))

        self.assertEqual(response.status_code, 200)
        insignias_con_estado = dict(response.context['insignias_con_estado'])
        self.assertTrue(insignias_con_estado[self.insignia])
        self.assertTrue(InsigniaUsuario.objects.filter(
            usuario=self.user,
            insignia=self.insignia,
            status=InsigniaUsuario.Status.VALID,
        ).exists())
        self.assertContains(response, 'Vigente · Obtenida')

    def test_catalogo_es_publico_y_no_muestra_datos_de_concesiones_privadas(self):
        InsigniaUsuario.objects.create(
            usuario=self.user,
            insignia=self.insignia,
            version=self.version,
            origin=InsigniaUsuario.Origin.MANUAL,
            reason='Motivo privado de prueba',
            evidence_id='evidencia-privada-123',
        )
        self.client.logout()

        response = self.client.get(reverse('gamification:insignias'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.insignia.nombre)
        self.assertNotContains(response, 'Motivo privado de prueba')
        self.assertNotContains(response, 'evidencia-privada-123')

        collection = self.client.get(
            reverse('gamification:coleccion_publica', args=[self.user.username])
        )
        self.assertEqual(collection.status_code, 200)
        self.assertNotContains(collection, self.insignia.nombre)

    def test_coleccion_publica_solo_incluye_concesiones_que_el_dueno_comparte(self):
        InsigniaUsuario.objects.create(
            usuario=self.user,
            insignia=self.insignia,
            version=self.version,
            origin=InsigniaUsuario.Origin.AUTOMATIC,
            is_public=True,
            reason='Motivo que no debe publicarse',
        )
        self.client.logout()

        response = self.client.get(
            reverse('gamification:coleccion_publica', args=[self.user.username])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.insignia.nombre)
        self.assertNotContains(response, 'Motivo que no debe publicarse')

    def test_api_de_mis_insignias_solo_devuelve_concesiones_del_actor(self):
        other = get_user_model().objects.create_user(
            username='otro_usuario_insignias',
            password='test1234',
        )
        InsigniaUsuario.objects.create(
            usuario=self.user,
            insignia=self.insignia,
            version=self.version,
            origin=InsigniaUsuario.Origin.AUTOMATIC,
        )
        other_badge = Insignia.objects.create(
            nombre='Insignia privada de otra persona',
            descripcion='Privada',
            tipo='especial',
            visible=True,
        )
        InsigniaUsuario.objects.create(
            usuario=other,
            insignia=other_badge,
            origin=InsigniaUsuario.Origin.HISTORICAL,
        )

        response = self.client.get(reverse('my-badge-list'))

        self.assertEqual(response.status_code, 200)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['insignia']['id'], self.insignia.pk)
        self.assertNotIn('evidence_id', results[0])

    def test_usuario_solo_puede_cambiar_visibilidad_de_su_concesion(self):
        award = InsigniaUsuario.objects.create(
            usuario=self.user,
            insignia=self.insignia,
            version=self.version,
            origin=InsigniaUsuario.Origin.AUTOMATIC,
        )

        response = self.client.post(
            reverse('gamification:visibilidad_insignia', args=[award.pk]),
            {'is_public': 'true'},
        )

        self.assertEqual(response.status_code, 302)
        award.refresh_from_db()
        self.assertTrue(award.is_public)

        other = get_user_model().objects.create_user(username='otro_propietario')
        self.client.force_login(other)
        response = self.client.post(
            reverse('gamification:visibilidad_insignia', args=[award.pk]),
            {'is_public': 'false'},
        )
        self.assertEqual(response.status_code, 404)

    def test_usuario_sin_rol_no_puede_publicar_definiciones(self):
        self.client.raise_request_exception = False

        response = self.client.post(
            reverse(
                'gamification:estado_insignia',
                kwargs={'pk': self.insignia.pk, 'action': 'publish'},
            ),
        )

        self.assertEqual(response.status_code, 403)

    def test_catalogo_api_no_expone_insignias_no_publicadas(self):
        draft = Insignia.objects.create(
            nombre='Borrador privado',
            descripcion='No pública',
            tipo='especial',
            status=Insignia.Status.DRAFT,
        )

        response = self.client.get(reverse('insignia-list'))
        results = response.data.get('results', response.data)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(draft.pk, [item['id'] for item in results])


class ManualBadgePermissionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.actor = user_model.objects.create_user(username='badge_manager')
        self.recipient = user_model.objects.create_user(username='badge_recipient')
        self.badge = Insignia.objects.create(
            nombre='Insignia manual',
            descripcion='Concesión manual para prueba',
            tipo='especial',
            issue_method=Insignia.IssueMethod.MANUAL,
            status=Insignia.Status.PUBLISHED,
            issuer_name='Organización de prueba',
        )
        self.version = InsigniaVersion.objects.create(
            badge=self.badge,
            version=1,
            rule_type=InsigniaVersion.RuleType.STREAK_DAYS,
            threshold=1,
            public_requirements='Requisito comprobado por la organización.',
            source_description='Registro de formación',
            verification_description='Revisión del responsable',
        )

    def test_nivel_o_identidad_sin_rol_no_permite_concesion_manual(self):
        self.assertFalse(can_grant_badge(self.actor, self.badge, self.recipient))
        self.assertFalse(can_grant_badge(self.actor, self.badge, self.actor))

    def test_academia_no_puede_conceder_insignia_a_otra_organizacion(self):
        own_org = Organization.objects.create(
            name='Academia propia',
            kind=Organization.Kind.ACADEMY,
        )
        other_org = Organization.objects.create(
            name='Academia ajena',
            kind=Organization.Kind.ACADEMY,
        )
        self.badge.organization = own_org
        self.badge.save()
        OrganizationMembership.objects.create(
            organization=own_org,
            user=self.actor,
            status=OrganizationMembership.Status.ACTIVE,
        )
        AccessRoleAssignment.objects.create(
            user=self.actor,
            role=AccessRoleAssignment.Role.ACADEMIA,
            organization=own_org,
        )
        OrganizationMembership.objects.create(
            organization=other_org,
            user=self.recipient,
            status=OrganizationMembership.Status.ACTIVE,
        )

        self.assertFalse(can_grant_badge(self.actor, self.badge, self.recipient))

    def test_admin_concede_con_auditoria_y_evidencia(self):
        AccessRoleAssignment.objects.create(
            user=self.actor,
            role=AccessRoleAssignment.Role.ADMIN,
        )

        award = grant_manual_badge(
            self.actor,
            self.recipient,
            self.badge,
            reason='Participación verificada',
            evidence='Registro de actividad interno',
        )

        self.assertEqual(award.granted_by, self.actor)
        self.assertEqual(award.status, InsigniaUsuario.Status.VALID)
        event = InsigniaAuditEvent.objects.get(award=award)
        self.assertEqual(event.actor, self.actor)
        self.assertEqual(event.metadata['evidence'], 'Registro de actividad interno')


class AutomaticBadgeTriggerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='automatic_badge_user')

    def create_badge(self, *, name, legacy_type, rule_type, threshold):
        badge = Insignia.objects.create(
            nombre=name,
            descripcion='Insignia automática de prueba',
            tipo=legacy_type,
            requisito_valor=threshold,
            status=Insignia.Status.PUBLISHED,
            issue_method=Insignia.IssueMethod.AUTOMATIC,
        )
        InsigniaVersion.objects.create(
            badge=badge,
            version=1,
            rule_type=rule_type,
            threshold=threshold,
            public_requirements='Alcanzar el objetivo verificable.',
            source_description='Datos del perfil guardados por servidor',
            verification_description='Evaluación automática server-side',
        )
        return badge

    def test_sumar_puntos_evalua_reglas_automaticas_de_puntos_totales(self):
        badge = self.create_badge(
            name='Acumulación comprobada',
            legacy_type='puntos',
            rule_type=InsigniaVersion.RuleType.TOTAL_POINTS,
            threshold=2,
        )

        self.user.agregar_puntos(2)

        self.assertTrue(InsigniaUsuario.objects.filter(
            usuario=self.user,
            insignia=badge,
            status=InsigniaUsuario.Status.VALID,
        ).exists())

    def test_actualizar_racha_evalua_reglas_automaticas(self):
        badge = self.create_badge(
            name='Racha comprobada',
            legacy_type='streak',
            rule_type=InsigniaVersion.RuleType.STREAK_DAYS,
            threshold=3,
        )
        self.user.streak = 2
        self.user.fecha_ultimo_acceso = timezone.localdate() - timedelta(days=1)
        self.user.save(update_fields=['streak', 'fecha_ultimo_acceso'])

        self.user.actualizar_streak()

        self.assertEqual(self.user.streak, 3)
        self.assertTrue(InsigniaUsuario.objects.filter(
            usuario=self.user,
            insignia=badge,
            status=InsigniaUsuario.Status.VALID,
        ).exists())

    def test_resultado_aprobado_concede_insignia_de_evaluacion_verificada(self):
        course = Curso.objects.create(
            nombre='Curso de evaluación',
            descripcion='Curso para probar una insignia de evaluación',
            fecha_inicio=date.today(),
            horas_duracion=1,
            activo=True,
        )
        topic = course.temas.create(
            titulo='Tema de evaluación',
            contenido='Contenido',
            orden=1,
            puntos_otorgados=0,
        )
        quiz = Quiz.objects.create(
            tema=topic,
            titulo='Evaluación verificada',
            porcentaje_aprobacion=70,
        )
        badge = Insignia.objects.create(
            nombre='Evaluación superada',
            descripcion='Insignia de evaluación aprobada',
            tipo='especial',
            requisito_valor=70,
            status=Insignia.Status.PUBLISHED,
            issue_method=Insignia.IssueMethod.AUTOMATIC,
        )
        version = InsigniaVersion.objects.create(
            badge=badge,
            version=1,
            rule_type=InsigniaVersion.RuleType.QUIZ_PASSED,
            quiz=quiz,
            threshold=70,
            public_requirements='Aprobar la evaluación.',
            source_description='ResultadoQuiz',
            verification_description='Resultado aprobado guardado por servidor',
        )

        result = ResultadoQuiz.objects.create(
            usuario=self.user,
            quiz=quiz,
            puntuacion=8,
            total_preguntas=10,
            aprobado=True,
        )

        award = InsigniaUsuario.objects.get(usuario=self.user, insignia=badge)
        self.assertEqual(award.version, version)
        self.assertEqual(award.evidence_type, 'courses.ResultadoQuiz')
        self.assertEqual(award.evidence_id, str(result.pk))


class PublicRankingTests(TestCase):
    def test_season_lifecycle_does_not_lock_nullable_rule_join(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.season.status = RankingSeason.Status.PREPARED
        self.season.save()
        with CaptureQueriesContext(connection) as captured:
            self.season = activate_season(self.admin, self.season)
            self.season.starts_on = self.today - timedelta(days=30)
            self.season.ends_on = self.today - timedelta(days=1)
            self.season.save()
            self.season = close_season(self.admin, self.season)
            self.season = recalculate_closed_season(self.admin, self.season, reason='Verificación')
        selects = [
            item['sql'] for item in captured.captured_queries
            if item['sql'].startswith('SELECT') and 'FROM "gamification_rankingseason"' in item['sql']
        ]
        self.assertGreaterEqual(len(selects), 3)
        for sql in selects:
            self.assertNotIn('LEFT OUTER JOIN', sql)
        self.assertEqual(self.season.status, RankingSeason.Status.CLOSED)

    def test_dashboard_explicit_participation_and_withdrawal_return_to_dashboard(self):
        self.client.force_login(self.first)
        dashboard = reverse('users:dashboard')
        response = self.client.get(dashboard)
        self.assertContains(response, 'Activar participación')
        self.assertFalse(RankingParticipation.objects.filter(user=self.first).exists())
        endpoint = reverse('gamification:ranking_participation', args=[self.ranking.slug])
        response = self.client.post(endpoint, {
            'enabled': 'true', 'alias': 'Mi alias elegido', 'source': 'dashboard',
            'user': self.second.pk,
        })
        self.assertRedirects(response, f'{dashboard}#ranking-participation')
        participation = RankingParticipation.objects.get(user=self.first, ranking=self.ranking)
        self.assertTrue(participation.is_active)
        self.assertEqual(participation.alias, 'Mi alias elegido')
        self.assertFalse(RankingParticipation.objects.filter(user=self.second).exists())
        self.assertContains(self.client.get(dashboard), 'Desactivar participación')
        response = self.client.post(endpoint, {'enabled': 'false', 'source': 'dashboard'})
        self.assertRedirects(response, f'{dashboard}#ranking-participation')
        participation.refresh_from_db()
        self.assertFalse(participation.is_active)

    def test_dashboard_does_not_offer_join_without_open_season(self):
        self.client.force_login(self.first)
        self.season.status = RankingSeason.Status.CLOSED
        self.season.save()
        response = self.client.get(reverse('users:dashboard'))
        self.assertContains(response, 'Sin temporada abierta')
        self.assertNotContains(response, 'Activar participación')
        self.assertFalse(RankingParticipation.objects.filter(user=self.first).exists())

    def test_dashboard_join_requires_alias_and_csrf(self):
        from django.test import Client
        self.client.force_login(self.first)
        endpoint = reverse('gamification:ranking_participation', args=[self.ranking.slug])
        response = self.client.post(endpoint, {'enabled': 'true', 'source': 'dashboard', 'alias': ''})
        self.assertRedirects(response, f"{reverse('users:dashboard')}#ranking-participation")
        self.assertFalse(RankingParticipation.objects.filter(user=self.first).exists())
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.first)
        self.assertEqual(client.post(endpoint, {
            'enabled': 'true', 'alias': 'Sin CSRF', 'source': 'dashboard',
        }).status_code, 403)

    def setUp(self):
        self.today = timezone.localdate()
        self.admin = get_user_model().objects.create_user(username='ranking_admin')
        AccessRoleAssignment.objects.create(
            user=self.admin,
            role=AccessRoleAssignment.Role.ADMIN,
        )
        self.first = get_user_model().objects.create_user(username='rank_first')
        self.second = get_user_model().objects.create_user(username='rank_second')
        self.ranking = RankingDefinition.objects.create(
            name='Cursos completados por temporada',
            slug='cursos-temporada-test',
            description='Clasificación por matrículas completadas.',
            metric=RankingDefinition.Metric.COMPLETED_COURSES,
            unit='cursos',
        )
        self.rule = RankingRuleVersion.objects.create(
            ranking=self.ranking,
            version=1,
            metric=self.ranking.metric,
            created_by=self.admin,
        )
        self.ranking.status = RankingDefinition.Status.PUBLISHED
        self.ranking.save()
        self.season = RankingSeason.objects.create(
            ranking=self.ranking,
            rule_version=self.rule,
            name='Temporada de prueba',
            starts_on=self.today - timedelta(days=30),
            ends_on=self.today + timedelta(days=30),
            status=RankingSeason.Status.ACTIVE,
            created_by=self.admin,
        )

    def _completed_course(self, user, name, *, days_ago=2):
        course = Curso.objects.create(
            nombre=name,
            descripcion='Curso de ranking',
            fecha_inicio=date.today(),
            horas_duracion=1,
            activo=True,
        )
        return InscripcionCurso.objects.create(
            usuario=user,
            curso=course,
            completado=True,
            fecha_completado=timezone.now() - timedelta(days=days_ago),
        )

    def test_solo_participantes_voluntarios_aparecen_y_empatan_por_metrica_igual(self):
        self._completed_course(self.first, 'Curso A')
        self._completed_course(self.second, 'Curso B')
        update_participation(self.first, self.ranking, enabled=True, alias='Ducky Uno')
        update_participation(self.second, self.ranking, enabled=True, alias='Ducky Dos')

        rows = ranking_rows(self.season)

        self.assertEqual(len(rows), 2)
        self.assertEqual([row['score'] for row in rows], [1, 1])
        self.assertEqual([row['position'] for row in rows], [1, 1])
        self.assertEqual(
            RankingAuditEvent.objects.filter(
                ranking=self.ranking,
                action=RankingAuditEvent.Action.PARTICIPATION_ENABLED,
            ).count(),
            2,
        )

    def test_retirar_participacion_oculta_ranking_en_vivo_y_conserva_auditoria(self):
        self._completed_course(self.first, 'Curso A')
        update_participation(self.first, self.ranking, enabled=True, alias='Alias público')

        update_participation(self.first, self.ranking, enabled=False, alias='')

        self.assertEqual(ranking_rows(self.season), [])
        self.assertFalse(RankingParticipation.objects.get(
            ranking=self.ranking,
            user=self.first,
        ).is_active)
        self.assertTrue(RankingAuditEvent.objects.filter(
            actor=self.first,
            action=RankingAuditEvent.Action.PARTICIPATION_DISABLED,
        ).exists())

    def test_resultado_pendiente_de_revision_no_suma_hasta_resolverse(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        enrollment = self._completed_course(self.first, 'Curso con incidencia')
        RankingAdjustment.objects.create(
            source_model='courses.InscripcionCurso',
            source_record_id=str(enrollment.pk),
            reason='Revisión de integridad solicitada',
        )
        update_participation(self.first, self.ranking, enabled=True, alias='Alias')

        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(ranking_rows(self.season)[0]['score'], 0)
        source_queries = [
            item['sql'] for item in captured.captured_queries
            if 'FROM "courses_inscripcioncurso"' in item['sql']
        ]
        self.assertTrue(source_queries)
        self.assertTrue(any('CAST(' in sql for sql in source_queries))

    def test_cierre_guarda_snapshot_y_recalculo_explicito_queda_auditado(self):
        self._completed_course(self.first, 'Curso en temporada')
        update_participation(self.first, self.ranking, enabled=True, alias='Alias')
        self.season.ends_on = self.today - timedelta(days=1)
        self.season.starts_on = self.today - timedelta(days=20)
        self.season.save(update_fields=['starts_on', 'ends_on'])

        close_season(self.admin, self.season)
        self.season.refresh_from_db()
        self.assertEqual(ranking_rows(self.season)[0].score, 1)

        enrollment = InscripcionCurso.objects.get(usuario=self.first)
        enrollment.completado = False
        enrollment.save(update_fields=['completado'])
        self.assertEqual(ranking_rows(self.season)[0].score, 1)

        recalculate_closed_season(self.admin, self.season, reason='Corrección de resultado fuente')

        recalculated_rows = ranking_rows(self.season)
        self.assertEqual(len(recalculated_rows), 1)
        self.assertEqual(recalculated_rows[0].score, 0)
        self.assertTrue(RankingAuditEvent.objects.filter(
            season=self.season,
            action=RankingAuditEvent.Action.SEASON_RECALCULATED,
            reason='Corrección de resultado fuente',
        ).exists())

    def test_retirar_consentimiento_despues_del_cierre_oculta_snapshot_publico(self):
        self._completed_course(self.first, 'Curso histórico')
        update_participation(self.first, self.ranking, enabled=True, alias='Alias histórico')
        self.season.starts_on = self.today - timedelta(days=20)
        self.season.ends_on = self.today - timedelta(days=1)
        self.season.save(update_fields=['starts_on', 'ends_on'])
        close_season(self.admin, self.season)
        self.season.refresh_from_db()
        self.assertEqual(len(ranking_rows(self.season)), 1)

        update_participation(self.first, self.ranking, enabled=False, alias='')

        self.assertEqual(ranking_rows(self.season), [])
        self.assertTrue(RankingEntry.objects.filter(
            season=self.season,
            alias='Alias histórico',
        ).exists())

    def test_usuario_no_admin_no_puede_activar_temporada(self):
        self.season.status = RankingSeason.Status.PREPARED
        self.season.starts_on = self.today
        self.season.ends_on = self.today + timedelta(days=1)
        self.season.save(update_fields=['status', 'starts_on', 'ends_on'])

        with self.assertRaises(PermissionDenied):
            activate_season(self.first, self.season)

    def test_metricas_no_se_pueden_combinar_en_regla_publicada(self):
        with self.assertRaises(ValidationError):
            RankingRuleVersion(
                ranking=self.ranking,
                version=2,
                metric=RankingDefinition.Metric.COMPLETED_LESSONS,
            ).full_clean()


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class ComoGanarPuntosViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.aula = Aula.objects.create(
            nombre='Aula Puntos',
            direccion='Calle Puntos 1',
            horario='Tarde',
        )
        self.user = user_model.objects.create_user(
            username='usuario_puntos',
            password='test1234',
            role='alumno',
            aula=self.aula,
        )
        self.client.force_login(self.user)

    def test_pagina_como_ganar_puntos_renderiza_listado(self):
        response = self.client.get(reverse('gamification:como_ganar_puntos'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Listado de formas de conseguir puntos')
        self.assertContains(response, 'Completar temas del curso')
        self.assertContains(response, 'Bug validado por admin')