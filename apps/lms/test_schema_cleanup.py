from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase
from django.db.migrations.loader import MigrationLoader


cleanup = import_module(
    'apps.lms.migrations.0004_section_allow_self_enrollment_section_capacity_and_more'
).cleanup_partial_lms_0004_schema


class PartialSchemaCleanupTests(SimpleTestCase):
    def test_non_sqlite_backends_do_not_query_or_drop_tables(self):
        for vendor in ('postgresql', 'mysql', 'oracle'):
            with self.subTest(vendor=vendor):
                connection = MagicMock(vendor=vendor)
                cleanup(None, SimpleNamespace(connection=connection))
                connection.cursor.assert_not_called()

    def test_sqlite_retains_cleanup_of_existing_partial_tables_only(self):
        connection = MagicMock(vendor='sqlite')
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [
            ('lms_coursesettingslms',), None, ('lms_cohort',),
        ]
        cleanup(None, SimpleNamespace(connection=connection))
        drops = [
            call.args[0] for call in cursor.execute.call_args_list
            if call.args[0].startswith('DROP TABLE')
        ]
        self.assertEqual(drops, [
            'DROP TABLE IF EXISTS "lms_coursesettingslms"',
            'DROP TABLE IF EXISTS "lms_cohort"',
        ])

    def test_sqlite_without_partial_tables_does_not_drop_anything(self):
        connection = MagicMock(vendor='sqlite')
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        cleanup(None, SimpleNamespace(connection=connection))
        self.assertEqual(cursor.execute.call_count, 3)
        self.assertFalse(any(
            call.args[0].startswith('DROP TABLE')
            for call in cursor.execute.call_args_list
        ))


class MembershipConstraintMigrationTests(SimpleTestCase):
    def test_migration_graph_defines_membership_constraints_only_once(self):
        loader = MigrationLoader(None)
        target = ('lms', '0003_membership_constraints_and_profiles')
        state = loader.project_state([target])
        constraints = state.models['lms', 'coursemembership'].options['constraints']
        for name in ('lms_member_valid_role', 'lms_member_valid_status'):
            with self.subTest(name=name):
                self.assertEqual(sum(item.name == name for item in constraints), 1)
        migration = loader.get_migration(*target)
        self.assertIn(
            ('lms', '0003_alter_section_options_alter_userprofilelms_options_and_more'),
            migration.dependencies,
        )
        self.assertEqual(len(migration.operations), 1)
        self.assertEqual(migration.operations[0].code.__name__, 'backfill_profiles')

    def test_leaf_state_does_not_duplicate_membership_constraints(self):
        loader = MigrationLoader(None)
        state = loader.project_state()
        constraints = state.models['lms', 'coursemembership'].options['constraints']
        names = [constraint.name for constraint in constraints]
        self.assertEqual(len(names), len(set(names)))
