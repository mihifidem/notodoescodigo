import json
import math
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ducky_arenas.models import (
    CharacterAsset,
    GameDestination,
    HistoricalEra,
    Portal,
    PlayerMapCheckpoint,
    PlayerEraProgress,
    Profession,
    ProfessionAssignment,
    UserProfileArenas,
    WorldMap,
)

from .ecomotor_progress import (
    EcoMotorProgressUnavailable,
    get_progress_snapshot,
)
from .player_state import (
    acknowledge_evolution_notice,
    pending_evolution_notices,
    persist_exploration_state,
    synchronize_era_progress,
    transition_exploration_map,
)
from .progression import era_for_xp, get_era, unlocked_eras_for_user
from .worlds import MAP_WIDTH, world_config


class TestProgressionProvider:
    """Test-only bridge for progression fixtures; not a production source."""

    def get_player_progress(self, user_id):
        user = get_user_model().objects.get(pk=user_id)
        return {
            'user_id': str(user.pk),
            'xp_total': user.puntos_totales,
            'attributes': {
                'attack': 8,
                'defense': 7,
                'speed': 9,
                'intelligence': 6,
                'logic': 10,
                'quack': 5,
            },
            'revision': user.puntos_totales,
            'updated_at': timezone.now(),
            'simulated': False,
        }


class FixtureProgressionProvider:
    def get_player_progress(self, user_id):
        from django.conf import settings

        return settings.DUCKY_ARENAS_ECOMOTOR_PROGRESS_FIXTURE


class CoreProgressionTests(TestCase):
    def test_eras_use_configured_historical_era_requirements(self):
        self.assertEqual(era_for_xp(0).code, 'PRE')
        self.assertEqual(era_for_xp(1000).code, 'GRE')
        self.assertEqual(era_for_xp(29999).code, 'SXX')
        self.assertEqual(era_for_xp(30000).code, 'ACT')

        HistoricalEra.objects.filter(code='GRE').update(required_xp=1250)
        self.assertEqual(era_for_xp(1000).code, 'PRE')
        self.assertEqual(era_for_xp(1250).code, 'GRE')

    def test_invalid_xp_and_unknown_eras_are_rejected(self):
        for xp in (-1, 1.5, True):
            with self.subTest(xp=xp), self.assertRaises(ValidationError):
                era_for_xp(xp)
        for era_code in ('unknown', 5):
            with self.subTest(era_code=era_code), self.assertRaises(ValidationError):
                get_era(era_code)


@override_settings(
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'ducky-arenas-progression-tests',
        },
    },
    DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER=(
        'apps.ducky_arenas.core.tests.TestProgressionProvider'
    ),
)
class ExplorationStateTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username='world_explorer',
            password='test-password',
        )
        UserProfileArenas.objects.create(
            user=self.user,
            nickname='Explorer',
            onboardingCompleted=True,
        )
        self.client.force_login(self.user)

    def test_state_api_persists_map_coordinates_and_returns_them(self):
        url = reverse('ducky_arenas:exploration_state')
        response = self.client.post(
            url,
            data=json.dumps({'era': 'pre', 'x': 836, 'y': 470.5, 'version': 0}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['exploration'], {
            'era': 'PRE',
            'x': 836.0,
            'y': 470.5,
            'direction': 'down',
            'version': 1,
        })

        response = self.client.get(url)
        self.assertEqual(response.json()['exploration']['x'], 836.0)

    def test_locked_era_and_out_of_bounds_positions_are_rejected(self):
        url = reverse('ducky_arenas:exploration_state')
        locked = self.client.post(
            url,
            data=json.dumps({
                'era': 'gre',
                'x': 10,
                'y': 10,
                'version': 0,
                'xp_total': 30000,
                'attributes': {'attack': 999999},
            }),
            content_type='application/json',
        )
        outside = self.client.post(
            url,
            data=json.dumps({'era': 'pre', 'x': MAP_WIDTH + 1, 'y': 10, 'version': 0}),
            content_type='application/json',
        )
        malformed = self.client.post(
            url,
            data=json.dumps({'era': 'pre', 'x': math.nan, 'y': 10, 'version': 0}),
            content_type='application/json',
        )
        oversized = self.client.post(
            url,
            data='{"era":"pre","x":' + str(10 ** 1000) + ',"y":10,"version":0}',
            content_type='application/json',
        )
        excessive_digits = self.client.post(
            url,
            data='{"era":"pre","x":' + ('9' * 5000) + ',"y":10,"version":0}',
            content_type='application/json',
        )
        self.assertEqual(
            (
                locked.status_code,
                outside.status_code,
                malformed.status_code,
                oversized.status_code,
                excessive_digits.status_code,
            ),
            (400, 400, 400, 400, 400),
        )

    def test_checkpoint_requires_version_and_rejects_collision_zones(self):
        url = reverse('ducky_arenas:exploration_state')
        missing_version = self.client.post(
            url,
            data=json.dumps({'era': 'pre', 'x': 150, 'y': 250}),
            content_type='application/json',
        )
        self.assertEqual(missing_version.status_code, 400)

        WorldMap.objects.filter(era__code='PRE').update(
            collision_config=[{
                'type': 'rect',
                'mode': 'obstacle',
                'x': 100,
                'y': 200,
                'width': 100,
                'height': 100,
            }],
        )
        blocked = self.client.post(
            url,
            data=json.dumps({'era': 'pre', 'x': 150, 'y': 250, 'version': 0}),
            content_type='application/json',
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIn('no transitable', blocked.json()['error'])

    def test_exploration_api_requires_authentication(self):
        self.client.logout()
        response = self.client.get(reverse('ducky_arenas:exploration_state'))
        self.assertEqual(response.status_code, 401)

    def test_api_restores_saved_position_without_a_session_checkpoint(self):
        url = reverse('ducky_arenas:exploration_state')
        saved = self.client.post(
            url,
            data=json.dumps({
                'era': 'pre',
                'x': 836,
                'y': 470.5,
                'direction': 'left',
                'version': 0,
            }),
            content_type='application/json',
        )
        self.assertEqual(saved.status_code, 200)

        restored = self.client.get(url)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(
            restored.json()['exploration'],
            {
                'era': 'PRE',
                'x': 836.0,
                'y': 470.5,
                'direction': 'left',
                'version': 1,
            },
        )

    def test_launching_duckyclash_saves_checkpoint_and_uses_named_route(self):
        Portal.objects.filter(code='duckyclash').update(
            x=100,
            y=200,
            area_width=500,
            area_height=300,
            destination=GameDestination.objects.get(code='clash'),
            access_requirements={},
            is_active=True,
        )
        response = self.client.post(
            reverse('ducky_arenas:portal_launch', kwargs={'portal_id': 'duckyclash'}),
            data=json.dumps({
                'era': 'pre',
                'x': 200,
                'y': 300,
                'version': 0,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        redirect = urlsplit(response.json()['redirectUrl'])
        self.assertEqual(redirect.path, reverse('duckyclash:home'))
        self.assertEqual(
            parse_qs(redirect.query)['return_url'],
            [reverse('ducky_arenas:portal_return')],
        )
        self.assertEqual(
            response.json()['returnUrl'],
            reverse('ducky_arenas:portal_return'),
        )
        profile = UserProfileArenas.objects.get(user=self.user)
        self.assertEqual(profile.position_x, 200)
        self.assertEqual(profile.position_y, 300)
        self.assertEqual(profile.exploration_version, 1)

    def test_portal_launch_requires_configured_active_portal(self):
        response = self.client.post(
            reverse('ducky_arenas:portal_launch', kwargs={'portal_id': 'duckyclash'}),
            data=json.dumps({'era': 'pre', 'x': 200, 'y': 300, 'version': 0}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('no está disponible', response.json()['error'])

    def test_portal_launch_requires_player_to_be_inside_interaction_area(self):
        Portal.objects.filter(code='duckyclash').update(
            x=800,
            y=600,
            area_width=100,
            area_height=100,
            destination=GameDestination.objects.get(code='clash'),
            access_requirements={},
            is_active=True,
        )
        response = self.client.post(
            reverse('ducky_arenas:portal_launch', kwargs={'portal_id': 'duckyclash'}),
            data=json.dumps({'era': 'pre', 'x': 200, 'y': 300, 'version': 0}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Acércate al portal', response.json()['error'])

    def test_portal_launch_checks_server_side_point_requirements(self):
        Portal.objects.filter(code='duckyclash').update(
            x=100,
            y=200,
            area_width=500,
            area_height=300,
            destination=GameDestination.objects.get(code='clash'),
            access_requirements={'min_total_points': 1},
            is_active=True,
        )

        response = self.client.post(
            reverse('ducky_arenas:portal_launch', kwargs={'portal_id': 'duckyclash'}),
            data=json.dumps({'era': 'pre', 'x': 200, 'y': 300, 'version': 0}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('requisitos', response.json()['error'])
        self.assertEqual(
            UserProfileArenas.objects.get(user=self.user).exploration_version,
            0,
        )

    def test_unavailable_destination_does_not_update_checkpoint(self):
        Portal.objects.filter(code='duckyclash').update(
            x=100,
            y=200,
            area_width=500,
            area_height=300,
            destination=GameDestination.objects.get(code='escape'),
            is_active=True,
        )

        response = self.client.post(
            reverse('ducky_arenas:portal_launch', kwargs={'portal_id': 'duckyclash'}),
            data=json.dumps({'era': 'pre', 'x': 200, 'y': 300, 'version': 0}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            UserProfileArenas.objects.get(user=self.user).exploration_version,
            0,
        )

    def test_portal_return_uses_authenticated_server_checkpoint(self):
        profile = UserProfileArenas.objects.get(user=self.user)
        profile.current_map = WorldMap.objects.get(era__code='PRE')
        profile.position_x = 668.8
        profile.position_y = 564.6
        profile.save(update_fields=['current_map', 'position_x', 'position_y'])
        self.user.puntos_totales = 1000
        self.user.save(update_fields=['puntos_totales'])

        response = self.client.get(reverse('ducky_arenas:portal_return'))

        self.assertRedirects(
            response,
            reverse('ducky_arenas:world_era', kwargs={'era': 'pre'}),
        )
        self.assertTrue(
            PlayerEraProgress.objects.filter(
                profile=profile,
                era__code='GRE',
                unlocked_at__isnull=False,
            ).exists(),
        )

    def test_state_api_rejects_stale_exploration_version(self):
        url = reverse('ducky_arenas:exploration_state')
        first = self.client.post(
            url,
            data=json.dumps({'era': 'pre', 'x': 200, 'y': 300, 'version': 0}),
            content_type='application/json',
        )
        stale = self.client.post(
            url,
            data=json.dumps({'era': 'pre', 'x': 220, 'y': 300, 'version': 0}),
            content_type='application/json',
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['exploration']['version'], 1)
        self.assertEqual(stale.status_code, 409)

    def test_world_uses_real_static_map_and_restores_persisted_checkpoint(self):
        profile = UserProfileArenas.objects.get(user=self.user)
        profile.current_map = WorldMap.objects.get(era__code='PRE')
        profile.position_x = 418
        profile.position_y = 470.5
        profile.exploration_version = 1
        profile.save(update_fields=[
            'current_map',
            'position_x',
            'position_y',
            'exploration_version',
        ])
        session = self.client.session
        session['ducky_arenas_exploration'] = {
            'era': 'PRE',
            'x': 0.9,
            'y': 0.1,
        }
        session.save()

        response = self.client.get(reverse('ducky_arenas:world'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['world_config']['player']['x'], 418)
        self.assertEqual(response.context['world_config']['player']['y'], 470.5)
        self.assertEqual(
            response.context['world_config']['map']['url'],
            '/static/duckyarenas/DAR_mapas/DAR_PRE_mapa.PNG',
        )
        self.assertContains(response, 'DAR_PRE_sprite01')
        self.assertContains(response, 'cdn.jsdelivr.net/npm/phaser@3.90.0')
        self.assertContains(response, 'XP oficial de EcoMotor')
        self.assertContains(response, 'Quack')

    def test_map_selection_requires_authorized_transition(self):
        self.user.puntos_totales = 1000
        self.user.save(update_fields=['puntos_totales'])
        response = self.client.post(
            reverse('ducky_arenas:map_transition', kwargs={'era': 'gre'}),
            data=json.dumps({
                'era': 'pre',
                'x': 836,
                'y': 733.98,
                'direction': 'right',
                'version': 0,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['exploration']['era'], 'GRE')
        self.assertFalse(response.json()['restoredCheckpoint'])
        self.assertIn('checkpoint=spawn', response.json()['redirectUrl'])
        profile = UserProfileArenas.objects.get(user=self.user)
        self.assertEqual(profile.current_map.era.code, 'GRE')
        self.assertEqual(profile.exploration_version, 1)

    def test_map_transition_rejects_locked_era_and_stale_version(self):
        url = reverse('ducky_arenas:map_transition', kwargs={'era': 'gre'})
        locked = self.client.post(
            url,
            data=json.dumps({'era': 'pre', 'x': 836, 'y': 734, 'version': 0}),
            content_type='application/json',
        )
        self.assertEqual(locked.status_code, 400)

        self.user.puntos_totales = 1000
        self.user.save(update_fields=['puntos_totales'])
        stale = self.client.post(
            url,
            data=json.dumps({'era': 'pre', 'x': 836, 'y': 734, 'version': 1}),
            content_type='application/json',
        )
        self.assertEqual(stale.status_code, 409)

    def test_map_transitions_save_and_restore_a_checkpoint_per_map(self):
        profile = self.user.user_profile_arenas
        pre_map = WorldMap.objects.get(era__code='PRE')
        greek_map = WorldMap.objects.get(era__code='GRE')
        persist_exploration_state(
            profile,
            pre_map,
            420,
            610,
            'right',
            0,
            allow_rapid=True,
        )
        synchronize_era_progress(profile, 1000)
        PlayerMapCheckpoint.objects.create(
            profile=profile,
            world_map=greek_map,
            position_x=300,
            position_y=250,
            facing_direction='left',
        )

        first_transition = transition_exploration_map(
            profile,
            greek_map,
            'PRE',
            420,
            610,
            'right',
            1,
        )
        self.assertTrue(first_transition.restored_checkpoint)

        profile.refresh_from_db()
        self.assertEqual(profile.current_map_id, greek_map.pk)
        self.assertEqual((profile.position_x, profile.position_y), (300, 250))
        self.assertEqual(
            PlayerMapCheckpoint.objects.get(profile=profile, world_map=pre_map).position_x,
            420,
        )

        return_transition = transition_exploration_map(
            profile,
            pre_map,
            'GRE',
            300,
            250,
            'left',
            2,
        )
        self.assertTrue(return_transition.restored_checkpoint)

        profile.refresh_from_db()
        self.assertEqual(profile.current_map_id, pre_map.pk)
        self.assertEqual((profile.position_x, profile.position_y), (420, 610))

    def test_unavailable_actuality_is_recorded_as_unlocked_but_not_visit_ready(self):
        profile = self.user.user_profile_arenas
        synchronize_era_progress(profile, 30000)

        actuality = PlayerEraProgress.objects.get(profile=profile, era__code='ACT')
        self.assertTrue(actuality.is_unlocked)
        self.assertIsNone(actuality.first_visited_at)
        with self.assertRaises(ValidationError):
            synchronize_era_progress(profile, 30000, visited_era_code='ACT')

    def test_evolution_notice_acknowledgement_is_idempotent(self):
        profile = self.user.user_profile_arenas
        synchronize_era_progress(profile, 1000)

        self.assertEqual(
            [progress.era.code for progress in pending_evolution_notices(profile)],
            ['GRE'],
        )
        self.assertTrue(acknowledge_evolution_notice(profile, 'GRE'))
        self.assertFalse(acknowledge_evolution_notice(profile, 'GRE'))
        self.assertFalse(pending_evolution_notices(profile).exists())

    def test_evolution_notice_api_requires_authentication_and_confirms_unlock(self):
        profile = self.user.user_profile_arenas
        synchronize_era_progress(profile, 1000)
        url = reverse('ducky_arenas:evolution_notice_ack')

        response = self.client.post(
            url,
            data=json.dumps({'era_code': 'GRE'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['acknowledged'])
        self.assertFalse(pending_evolution_notices(profile).exists())

        self.client.logout()
        unauthenticated = self.client.post(
            url,
            data=json.dumps({'era_code': 'GRE'}),
            content_type='application/json',
        )
        self.assertEqual(unauthenticated.status_code, 401)

    def test_world_timeline_includes_actuality_without_inventing_a_map(self):
        self.user.puntos_totales = 30000
        self.user.save(update_fields=['puntos_totales'])

        response = self.client.get(reverse('ducky_arenas:world'))

        self.assertEqual(response.status_code, 200)
        actuality = next(
            era for era in response.context['era_navigation']
            if era['code'] == 'ACT'
        )
        self.assertTrue(actuality['unlocked'])
        self.assertFalse(actuality['map_available'])
        self.assertIsNone(actuality['transition_url'])
        self.assertEqual(len(response.context['evolution_notices']), 7)
        self.assertContains(response, 'Nueva era desbloqueada')

    def test_confirmed_era_unlock_survives_lower_current_xp(self):
        profile = UserProfileArenas.objects.get(user=self.user)
        gre = HistoricalEra.objects.get(code='GRE')
        PlayerEraProgress.objects.create(
            profile=profile,
            era=gre,
            unlocked_at=timezone.now(),
        )

        synchronize_era_progress(profile, 0, visited_era_code='GRE')

        self.assertIsNotNone(
            PlayerEraProgress.objects.get(profile=profile, era=gre).last_visited_at,
        )

    def test_world_remains_available_but_does_not_unlock_eras_when_ecomotor_is_unavailable(self):
        cache.clear()
        with override_settings(DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER=''):
            response = self.client.get(reverse('ducky_arenas:world'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'no se conceden accesos nuevos')
        self.assertEqual(response.context['unlocked_eras'], {'PRE'})

    def test_locked_map_redirects_to_current_unlocked_era(self):
        response = self.client.get(reverse(
            'ducky_arenas:world_era',
            kwargs={'era': 'gre'},
        ))
        self.assertRedirects(
            response,
            reverse('ducky_arenas:world_era', kwargs={'era': 'pre'}),
        )

    def test_unconfirmed_actuality_map_is_not_enabled(self):
        self.user.puntos_totales = 30000
        self.user.duck_specialty = 'developer'
        self.user.save(update_fields=['puntos_totales', 'duck_specialty'])

        response = self.client.get(
            reverse('ducky_arenas:world_era', kwargs={'era': 'act'}),
        )

        self.assertRedirects(
            response,
            reverse('ducky_arenas:world_era', kwargs={'era': 'pre'}),
        )

    def test_duckyclash_specialty_route_redirects_to_core_profession_view(self):
        self.user.puntos_totales = 30000
        self.user.save(update_fields=['puntos_totales'])

        response = self.client.get(reverse('duckyclash:specialty'))

        self.assertRedirects(response, reverse('ducky_arenas:profession'))

    def test_profession_selection_creates_a_draft_without_changing_clash_specialty(self):
        self.user.puntos_totales = 30000
        self.user.save(update_fields=['puntos_totales'])
        profile = UserProfileArenas.objects.get(user=self.user)
        profession = Profession.objects.get(code='developer')

        response = self.client.post(
            reverse('ducky_arenas:profession'),
            data={'profession': profession.pk},
        )

        self.assertRedirects(
            response,
            reverse('ducky_arenas:world_era', kwargs={'era': 'act'}),
            fetch_redirect_response=False,
        )
        assignment = ProfessionAssignment.objects.get(profile=profile)
        self.assertEqual(assignment.profession, profession)
        self.assertEqual(assignment.status, ProfessionAssignment.Status.DRAFT)
        self.assertTrue(assignment.operation_id)
        self.user.refresh_from_db()
        self.assertEqual(self.user.duck_specialty, '')
        profile.refresh_from_db()
        self.assertEqual(profile.stats, {})

    def test_profession_catalog_is_database_driven_and_describes_attribute_priorities(self):
        self.user.puntos_totales = 30000
        self.user.save(update_fields=['puntos_totales'])

        response = self.client.get(reverse('ducky_arenas:profession'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['professions']), 8)
        self.assertFalse(response.context['allocation_available'])
        self.assertContains(response, 'Lógica')
        self.assertContains(response, 'Muy alta')
        self.assertContains(response, 'EcoMotor')
        self.assertNotContains(response, 'name="attack"')

    def test_profession_selection_requires_actuality_to_be_unlocked(self):
        response = self.client.post(
            reverse('ducky_arenas:profession'),
            data={'profession': Profession.objects.get(code='developer').pk},
        )

        self.assertRedirects(
            response,
            reverse('ducky_arenas:world'),
            fetch_redirect_response=False,
        )
        self.assertFalse(
            ProfessionAssignment.objects.filter(
                profile__user=self.user,
            ).exists(),
        )

    def test_profession_requirements_are_checked_server_side(self):
        self.user.puntos_totales = 30000
        self.user.save(update_fields=['puntos_totales'])
        profession = Profession.objects.get(code='developer')
        profession.access_requirements = {'min_xp': 40000}
        profession.save(update_fields=['access_requirements'])

        response = self.client.post(
            reverse('ducky_arenas:profession'),
            data={'profession': profession.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'requiere 40000 XP oficiales')
        self.assertFalse(
            ProfessionAssignment.objects.filter(profile__user=self.user).exists(),
        )

    def test_duplicate_profession_submission_is_idempotent(self):
        self.user.puntos_totales = 30000
        self.user.save(update_fields=['puntos_totales'])
        profession = Profession.objects.get(code='developer')
        url = reverse('ducky_arenas:profession')

        self.client.post(url, data={'profession': profession.pk})
        assignment = ProfessionAssignment.objects.get(profile__user=self.user)
        operation_id = assignment.operation_id

        self.client.post(url, data={'profession': profession.pk})

        assignment.refresh_from_db()
        self.assertEqual(assignment.operation_id, operation_id)
        self.assertEqual(ProfessionAssignment.objects.filter(profile__user=self.user).count(), 1)

    def test_player_profile_reads_official_attributes_without_overwriting_arena_stats(self):
        self.user.puntos_totales = 30000
        self.user.save(update_fields=['puntos_totales'])
        profile = UserProfileArenas.objects.get(user=self.user)
        profile.stats = {'attack': 999}
        profile.save(update_fields=['stats'])
        ProfessionAssignment.objects.create(
            profile=profile,
            profession=Profession.objects.get(code='developer'),
        )

        response = self.client.get(reverse('ducky_arenas:profile'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['official_xp'], 30000)
        self.assertTrue(response.context['official_progress_available'])
        self.assertEqual(
            {item['code']: item['value'] for item in response.context['official_characteristics']},
            {'ATK': 8, 'DEF': 7, 'SPE': 9, 'INT': 6, 'LOG': 10, 'QUA': 5},
        )
        self.assertEqual(response.context['effective_stats']['attack'], 999)
        self.assertEqual(
            response.context['profession_assignment'].profession.code,
            'developer',
        )
        self.assertContains(response, 'No se ha confirmado ni se han aplicado atributos')

    def test_player_profile_marks_official_attributes_unavailable_without_ecomotor(self):
        cache.clear()
        with override_settings(DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER=''):
            response = self.client.get(reverse('ducky_arenas:profile'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['official_progress_available'])
        self.assertTrue(
            all(item['value'] == '—' for item in response.context['official_characteristics']),
        )

    def test_selected_profession_cannot_be_changed_through_post(self):
        self.user.puntos_totales = 30000
        self.user.save(update_fields=['puntos_totales'])
        profile = UserProfileArenas.objects.get(user=self.user)
        profession = Profession.objects.get(code='systems-technician')
        assignment = ProfessionAssignment.objects.create(
            profile=profile,
            profession=profession,
            status=ProfessionAssignment.Status.CONFIRMED,
            confirmed_at=timezone.now(),
        )

        response = self.client.post(
            reverse('ducky_arenas:profession'),
            data={'profession': Profession.objects.get(code='developer').pk},
        )

        self.assertRedirects(
            response,
            reverse('ducky_arenas:world'),
            fetch_redirect_response=False,
        )
        assignment.refresh_from_db()
        self.assertEqual(assignment.profession, profession)

    def test_current_resources_resolve_through_django_static_finders(self):
        paths = []
        paths.extend(
            WorldMap.objects.filter(is_available=True).values_list('static_path', flat=True)
        )
        for character in CharacterAsset.objects.filter(is_available=True):
            if character.static_image_path:
                paths.append(character.static_image_path)
            paths.extend(character.movement_resources)

        for path in paths:
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path), f'Missing static resource: {path}')


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class WorldAssetConfigurationTests(TestCase):
    def test_pre_map_uses_directional_images_without_claiming_walk_animation(self):
        profile = UserProfileArenas(gender=UserProfileArenas.Gender.BOY)
        config = world_config(get_era('PRE'), profile)

        self.assertEqual(config['map']['url'], '/static/duckyarenas/DAR_mapas/DAR_PRE_mapa.PNG')
        self.assertEqual(config['player']['character']['kind'], 'directional-sprites')
        self.assertEqual(config['player']['character']['walk_cycle_available'], False)
        self.assertEqual(
            set(config['player']['character']['directions']),
            {'up', 'down', 'left', 'right'},
        )
        self.assertTrue(config['player']['portrait_url'])
        self.assertEqual(len(config['landscapes']), 3)
        for landscape_url in config['landscapes']:
            with self.subTest(landscape_url=landscape_url):
                self.assertIsNotNone(finders.find(landscape_url.removeprefix('/static/')))

    def test_missing_act_character_is_explicitly_unavailable(self):
        profile = UserProfileArenas(gender=UserProfileArenas.Gender.BOY)
        with self.assertRaisesMessage(ValidationError, 'El mapa de esa época no está disponible.'):
            world_config(get_era('ACT'), profile)


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'ducky-arenas-adapter-tests',
        },
    },
    DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER=(
        'apps.ducky_arenas.core.tests.FixtureProgressionProvider'
    ),
)
class EcoMotorProgressAdapterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username='progress_reader',
            password='test-password',
        )
        UserProfileArenas.objects.create(
            user=self.user,
            nickname='Progress reader',
            onboardingCompleted=True,
        )

    def _fixture(self, *, user_id=None, xp_total=2500, revision=1):
        return {
            'user_id': str(user_id if user_id is not None else self.user.pk),
            'xp_total': xp_total,
            'attributes': None,
            'revision': revision,
            'updated_at': timezone.now(),
            'simulated': False,
        }

    def test_rejects_a_response_for_a_different_user(self):
        with override_settings(
            DUCKY_ARENAS_ECOMOTOR_PROGRESS_FIXTURE=self._fixture(user_id='someone-else'),
        ):
            with self.assertRaises(EcoMotorProgressUnavailable):
                get_progress_snapshot(self.user)

    def test_older_revision_cannot_replace_a_newer_cached_snapshot(self):
        with override_settings(
            DUCKY_ARENAS_ECOMOTOR_PROGRESS_FIXTURE=self._fixture(xp_total=2500, revision=5),
        ):
            current = get_progress_snapshot(self.user)
        with override_settings(
            DUCKY_ARENAS_ECOMOTOR_PROGRESS_FIXTURE=self._fixture(xp_total=1000, revision=4),
        ):
            stale = get_progress_snapshot(self.user, force_refresh=True)

        self.assertEqual(current.revision, 5)
        self.assertEqual(stale.revision, 5)
        self.assertEqual(stale.xp_total, 2500)

    def test_development_simulator_is_rejected_outside_debug(self):
        with override_settings(
            DEBUG=False,
            DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER=(
                'apps.ducky_arenas.core.ecomotor_progress.DevelopmentProgressionSimulator'
            ),
            DUCKY_ARENAS_ECOMOTOR_SIMULATED_PROGRESS={},
        ):
            with self.assertRaises(EcoMotorProgressUnavailable):
                get_progress_snapshot(self.user, force_refresh=True)

    def test_missing_provider_does_not_promote_local_user_points_to_xp(self):
        self.user.puntos_totales = 30000
        self.user.save(update_fields=['puntos_totales'])
        PlayerEraProgress.objects.create(
            profile=self.user.user_profile_arenas,
            era=HistoricalEra.objects.get(code='PRE'),
            unlocked_at=timezone.now(),
        )

        with override_settings(DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER=''):
            self.assertEqual(
                [era.code for era in unlocked_eras_for_user(self.user)],
                ['PRE'],
            )
