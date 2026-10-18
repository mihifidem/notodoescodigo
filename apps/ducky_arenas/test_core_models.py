from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.ducky_arenas.core.player_state import (
    ExplorationStateConflict,
    ExplorationStateRateLimited,
    persist_exploration_state,
    synchronize_era_progress,
)
from apps.ducky_arenas.models import (
    CharacterAsset,
    GameDestination,
    HistoricalEra,
    PlayerMapCheckpoint,
    PlayerEraProgress,
    Portal,
    Profession,
    ProfessionAssignment,
    ProfessionSelectionPolicy,
    UserProfileArenas,
    WorldMap,
)


class ProgressionTestProvider:
    def get_player_progress(self, user_id):
        user = get_user_model().objects.get(pk=user_id)
        return {
            'user_id': str(user.pk),
            'xp_total': user.puntos_totales,
            'attributes': None,
            'revision': user.puntos_totales,
            'updated_at': timezone.now(),
            'simulated': False,
        }


class CoreCatalogModelTests(TestCase):
    def test_migration_seeds_eras_and_maps_for_present_static_assets(self):
        self.assertEqual(HistoricalEra.objects.count(), 8)
        self.assertEqual(WorldMap.objects.count(), 9)
        pre_map = WorldMap.objects.get(era__code='PRE')

        self.assertEqual(pre_map.static_path, 'duckyarenas/DAR_mapas/DAR_PRE_mapa.PNG')
        self.assertEqual((pre_map.width, pre_map.height), (1672, 941))
        self.assertTrue(pre_map.is_available)
        self.assertIsNone(pre_map.spawn_x)
        self.assertIsNone(pre_map.spawn_y)
        self.assertFalse(HistoricalEra.objects.get(code='ACT').is_available)
        self.assertFalse(WorldMap.objects.filter(code='ACT').exists())
        self.assertEqual(
            set(WorldMap.objects.filter(era__isnull=True).values_list('code', flat=True)),
            {'DAR_000', 'DAR_001'},
        )
        for static_path in WorldMap.objects.values_list('static_path', flat=True):
            with self.subTest(static_path=static_path):
                self.assertIsNotNone(finders.find(static_path))

    def test_character_assets_reference_only_existing_portraits_and_sprites(self):
        self.assertEqual(CharacterAsset.objects.count(), 14)
        pre_male = CharacterAsset.objects.get(era__code='PRE', variant='BOY')
        self.assertEqual(
            pre_male.static_image_path,
            'duckyarenas/DAR_personajes/DAR_PRE1.png',
        )
        self.assertEqual(len(pre_male.movement_resources), 8)
        for asset in CharacterAsset.objects.all():
            with self.subTest(asset=asset):
                self.assertIsNotNone(finders.find(asset.static_image_path))
                for resource in asset.movement_resources:
                    self.assertIsNotNone(finders.find(resource))
        self.assertFalse(
            CharacterAsset.objects.filter(era__code='ACT').exists()
        )

    def test_profession_catalog_is_seeded_without_expanding_the_user_model(self):
        self.assertSetEqual(
            set(Profession.objects.values_list('code', flat=True)),
            {
                'developer',
                'cybersecurity',
                'systems-technician',
                'gamer',
                'data-ai',
                'marketing',
                'administration',
                'lawyer',
            },
        )
        self.assertEqual(
            tuple(get_user_model().DUCK_SPECIALTY_CHOICES),
            (
                ('developer', 'Developer'),
                ('systems', 'Sistemas'),
                ('cybersecurity', 'Ciberseguridad'),
                ('gamer', 'Gamer'),
                ('data_ia', 'Data IA'),
            ),
        )
        developer = Profession.objects.get(code='developer')
        self.assertEqual(developer.display_order, 1)
        self.assertEqual(
            developer.attribute_recommendations,
            {
                'attack': 'medium',
                'defense': 'medium',
                'speed': 'medium',
                'intelligence': 'high',
                'logic': 'very_high',
                'quack': 'medium',
            },
        )
        self.assertEqual(developer.icon_path, '')
        for profession in Profession.objects.all():
            with self.subTest(profession=profession.code):
                self.assertTrue(profession.description)
                self.assertSetEqual(
                    set(profession.attribute_recommendations),
                    set(Profession.ATTRIBUTE_CODES),
                )
                self.assertTrue(
                    set(profession.attribute_recommendations.values()).issubset(
                        set(Profession.ATTRIBUTE_PRIORITIES),
                    ),
                )

    def test_profession_recommendations_reject_unknown_attributes_and_priorities(self):
        profession = Profession(
            code='invalid-test',
            name='Invalid',
            attribute_recommendations={'strength': 'very_high'},
        )

        with self.assertRaises(ValidationError):
            profession.full_clean()

        profession.attribute_recommendations = {'attack': 'infinite'}
        with self.assertRaises(ValidationError):
            profession.full_clean()

    def test_profession_access_requirements_reject_unknown_or_invalid_values(self):
        profession = Profession(
            code='invalid-requirement-test',
            name='Invalid requirement',
            access_requirements={'admin': True},
        )

        with self.assertRaises(ValidationError):
            profession.full_clean()

        profession.access_requirements = {'min_xp': True}
        with self.assertRaises(ValidationError):
            profession.full_clean()

    def test_profession_assignment_is_one_to_one_and_does_not_store_attribute_values(self):
        user = get_user_model().objects.create_user(username='profession-owner')
        profile = UserProfileArenas.objects.create(user=user, nickname='Explorer')
        profession = Profession.objects.get(code='developer')
        assignment = ProfessionAssignment.objects.create(
            profile=profile,
            profession=profession,
        )

        self.assertEqual(assignment.status, ProfessionAssignment.Status.DRAFT)
        self.assertIsNotNone(assignment.operation_id)
        self.assertFalse(hasattr(assignment, 'attributes'))
        self.assertFalse(hasattr(assignment, 'distribution'))
        self.assertEqual(
            ProfessionSelectionPolicy.get_current().allow_reassignment,
            False,
        )
        with self.assertRaises(IntegrityError):
            ProfessionAssignment.objects.create(
                profile=profile,
                profession=profession,
            )

    def test_duckyclash_portal_is_known_but_inactive_until_geometry_is_configured(self):
        portal = Portal.objects.get(code='duckyclash')

        self.assertEqual(portal.destination.code, 'clash')
        self.assertEqual(portal.destination.entry_url_name, 'duckyclash:home')
        self.assertFalse(portal.is_active)
        self.assertIsNone(portal.x)
        self.assertIsNone(portal.y)
        self.assertIsNone(portal.area_width)
        self.assertIsNone(portal.area_height)

    def test_destination_catalog_only_marks_confirmed_route_available(self):
        destinations = {
            destination.code: destination
            for destination in GameDestination.objects.all()
        }

        self.assertEqual(destinations['clash'].status, GameDestination.Status.AVAILABLE)
        self.assertEqual(destinations['clash'].entry_url_name, 'duckyclash:home')
        for code in ('escape', 'quiz', 'training', 'bank', 'shop', 'shaman', 'quacksapp', 'dolmens'):
            with self.subTest(destination=code):
                self.assertEqual(
                    destinations[code].status,
                    GameDestination.Status.PENDING,
                )
                self.assertEqual(destinations[code].entry_url_name, '')

    def test_profile_state_extends_the_existing_one_to_one_profile(self):
        user = get_user_model().objects.create_user(username='profile-owner')
        profile = UserProfileArenas.objects.create(user=user, nickname='Explorer')
        profile.current_map = WorldMap.objects.get(era__code='PRE')
        profile.position_x = Decimal('836.00')
        profile.position_y = Decimal('733.98')
        profile.selected_character = CharacterAsset.objects.get(era__code='PRE', variant='BOY')
        profile.save(update_fields=['current_map', 'position_x', 'position_y', 'selected_character'])

        self.assertEqual(UserProfileArenas.objects.filter(user=user).count(), 1)
        self.assertEqual(user.user_profile_arenas.pk, profile.pk)
        self.assertEqual(profile.current_era.code, 'PRE')
        self.assertEqual(profile.selected_character.era.code, 'PRE')
        self.assertEqual(profile.exploration_version, 0)
        self.assertEqual(profile.exploration_preferences, {})

    def test_profile_validation_rejects_character_from_another_era(self):
        user = get_user_model().objects.create_user(username='mismatched-character')
        profile = UserProfileArenas(
            user=user,
            nickname='Explorer',
            current_map=WorldMap.objects.get(era__code='PRE'),
            selected_character=CharacterAsset.objects.get(era__code='GRE', variant='BOY'),
        )

        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_generic_map_has_a_safe_string_representation(self):
        generic_map = WorldMap.objects.get(code='DAR_000')

        self.assertEqual(str(generic_map), 'Mapa genérico: Mapa DAR 000')

    def test_era_progress_tracks_unlock_and_visit_without_xp_fields(self):
        user = get_user_model().objects.create_user(username='era-player')
        profile = UserProfileArenas.objects.create(user=user, nickname='Explorer')
        pre = HistoricalEra.objects.get(code='PRE')
        progress = PlayerEraProgress.objects.create(
            profile=profile,
            era=pre,
            unlocked_at='2026-01-01T00:00:00Z',
            first_visited_at='2026-01-01T00:00:00Z',
            last_visited_at='2026-01-02T00:00:00Z',
        )

        self.assertTrue(progress.is_unlocked)
        self.assertTrue(progress.is_visited)
        self.assertFalse(hasattr(progress, 'xp'))
        self.assertFalse(hasattr(progress, 'duckCoins'))

    def test_map_checkpoints_are_unique_per_profile_and_map(self):
        user = get_user_model().objects.create_user(username='map-checkpoint-owner')
        profile = UserProfileArenas.objects.create(user=user, nickname='Explorer')
        pre_map = WorldMap.objects.get(era__code='PRE')
        PlayerMapCheckpoint.objects.create(
            profile=profile,
            world_map=pre_map,
            position_x=400,
            position_y=500,
            facing_direction='left',
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            PlayerMapCheckpoint.objects.create(
                profile=profile,
                world_map=pre_map,
                position_x=420,
                position_y=520,
                facing_direction='right',
            )

    def test_historical_era_landscapes_reference_only_existing_assets(self):
        for era in HistoricalEra.objects.exclude(code='ACT'):
            with self.subTest(era=era.code):
                paths = era.resource_config['landscapes']
                self.assertEqual(len(paths), 3)
                self.assertEqual(paths, sorted(paths))
                for path in paths:
                    self.assertIsNotNone(finders.find(path))

    def test_server_progress_sync_is_idempotent_and_respects_era_availability(self):
        user = get_user_model().objects.create_user(username='progress-sync-player')
        profile = UserProfileArenas.objects.create(user=user, nickname='Explorer')

        synchronize_era_progress(profile, 1000, visited_era_code='GRE')
        first_unlocked_at = PlayerEraProgress.objects.get(
            profile=profile,
            era__code='PRE',
        ).unlocked_at
        synchronize_era_progress(profile, 1000, visited_era_code='GRE')

        self.assertEqual(
            set(
                PlayerEraProgress.objects.filter(profile=profile).values_list(
                    'era__code',
                    flat=True,
                )
            ),
            {'PRE', 'GRE'},
        )
        self.assertEqual(
            PlayerEraProgress.objects.get(profile=profile, era__code='PRE').unlocked_at,
            first_unlocked_at,
        )
        greek_progress = PlayerEraProgress.objects.get(profile=profile, era__code='GRE')
        self.assertTrue(greek_progress.is_unlocked)
        self.assertTrue(greek_progress.is_visited)

        with self.assertRaises(ValidationError):
            synchronize_era_progress(profile, 30000, visited_era_code='ACT')

    def test_database_rejects_partial_positions_and_unlocked_visits(self):
        user = get_user_model().objects.create_user(username='invalid-state-player')
        profile = UserProfileArenas.objects.create(user=user, nickname='Explorer')
        pre = HistoricalEra.objects.get(code='PRE')

        with self.assertRaises(IntegrityError), transaction.atomic():
            UserProfileArenas.objects.filter(pk=profile.pk).update(position_x=Decimal('0.2'))

        with self.assertRaises(IntegrityError), transaction.atomic():
            PlayerEraProgress.objects.create(
                profile=profile,
                era=pre,
                first_visited_at='2026-01-01T00:00:00Z',
                last_visited_at='2026-01-01T00:00:00Z',
            )

    def test_database_rejects_active_portals_without_full_geometry(self):
        world_map = WorldMap.objects.get(era__code='PRE')

        with self.assertRaises(IntegrityError), transaction.atomic():
            Portal.objects.create(
                world_map=world_map,
                code='invalid-active-portal',
                name='Portal incompleto',
                destination=GameDestination.objects.get(code='clash'),
                is_active=True,
            )

        configured = Portal.objects.create(
            world_map=world_map,
            code='configured-active-portal',
            name='Portal configurado',
            x=Decimal('500'),
            y=Decimal('400'),
            area_width=Decimal('100'),
            area_height=Decimal('100'),
            destination=GameDestination.objects.get(code='clash'),
            is_active=True,
        )
        self.assertTrue(configured.is_active)

    def test_portal_identifier_is_unique_per_map(self):
        generic_map = WorldMap.objects.get(code='DAR_000')
        Portal.objects.create(
            world_map=generic_map,
            code='duckyclash',
            name='Portal con el mismo código en otro mapa',
            destination=GameDestination.objects.get(code='clash'),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Portal.objects.create(
                world_map=generic_map,
                code='duckyclash',
                name='Portal duplicado en el mismo mapa',
                destination=GameDestination.objects.get(code='clash'),
            )


class ExplorationPersistenceTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username='persistent-explorer')
        self.profile = UserProfileArenas.objects.create(user=user, nickname='Explorer')
        self.world_map = WorldMap.objects.get(era__code='PRE')

    def test_persists_map_coordinate_checkpoint_and_increments_version(self):
        result = persist_exploration_state(
            self.profile,
            self.world_map,
            418,
            705.75,
            UserProfileArenas.ExplorationDirection.LEFT,
            expected_version=0,
        )

        self.assertEqual(result.current_map_id, self.world_map.pk)
        self.assertEqual(result.position_x, Decimal('418.00'))
        self.assertEqual(result.position_y, Decimal('705.75'))
        self.assertEqual(result.facing_direction, 'left')
        self.assertEqual(result.exploration_version, 1)
        self.assertIsNotNone(result.last_access)

    def test_stale_checkpoint_is_rejected_without_overwriting_newer_state(self):
        persist_exploration_state(
            self.profile,
            self.world_map,
            418,
            705.75,
            UserProfileArenas.ExplorationDirection.LEFT,
            expected_version=0,
        )

        with self.assertRaises(ExplorationStateConflict):
            persist_exploration_state(
                self.profile,
                self.world_map,
                800,
                800,
                UserProfileArenas.ExplorationDirection.RIGHT,
                expected_version=0,
            )

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.position_x, Decimal('418.00'))
        self.assertEqual(self.profile.exploration_version, 1)

    def test_invalid_position_direction_and_version_are_rejected(self):
        invalid = (
            (self.world_map.width + 1, 50, 'up', 0),
            (50, Decimal('NaN'), 'up', 0),
            (50, 50, 'diagonal', 0),
            (50, 50, 'up', True),
        )
        for x, y, direction, version in invalid:
            with self.subTest(x=x, y=y, direction=direction, version=version):
                with self.assertRaises(ValidationError):
                    persist_exploration_state(
                        self.profile,
                        self.world_map,
                        x,
                        y,
                        direction,
                        expected_version=version,
                    )

    def test_unchanged_checkpoint_does_not_write_or_increment_version(self):
        saved = persist_exploration_state(
            self.profile,
            self.world_map,
            418,
            705.75,
            UserProfileArenas.ExplorationDirection.LEFT,
            expected_version=0,
        )
        last_access = saved.last_access

        unchanged = persist_exploration_state(
            saved,
            self.world_map,
            418,
            705.75,
            UserProfileArenas.ExplorationDirection.LEFT,
            expected_version=1,
        )

        self.assertEqual(unchanged.exploration_version, 1)
        self.assertEqual(unchanged.last_access, last_access)

    def test_checkpoint_rate_limit_is_enforced_between_changed_saves(self):
        saved = persist_exploration_state(
            self.profile,
            self.world_map,
            418,
            705.75,
            UserProfileArenas.ExplorationDirection.LEFT,
            expected_version=0,
        )

        with self.assertRaises(ExplorationStateRateLimited):
            persist_exploration_state(
                saved,
                self.world_map,
                420,
                705.75,
                UserProfileArenas.ExplorationDirection.RIGHT,
                expected_version=1,
            )

    @override_settings(
        DUCKY_ARENAS_ECOMOTOR_PROGRESS_PROVIDER=(
            'apps.ducky_arenas.test_core_models.ProgressionTestProvider'
        ),
    )
    def test_periodic_save_cannot_switch_maps(self):
        greek_map = WorldMap.objects.get(era__code='GRE')
        self.profile.user.puntos_totales = 1000
        self.profile.user.save(update_fields=['puntos_totales'])
        persist_exploration_state(
            self.profile,
            self.world_map,
            100,
            100,
            UserProfileArenas.ExplorationDirection.DOWN,
            expected_version=0,
        )

        with self.assertRaisesMessage(ValidationError, 'transición autorizada'):
            persist_exploration_state(
                self.profile,
                greek_map,
                400,
                400,
                UserProfileArenas.ExplorationDirection.DOWN,
                expected_version=1,
            )
