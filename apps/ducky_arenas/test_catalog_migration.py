from importlib import import_module

from django.db import migrations
from django.test import SimpleTestCase


class CoreCatalogMigrationTests(SimpleTestCase):
    def test_slug_is_indexed_only_after_catalog_population(self):
        migration = import_module(
            'apps.ducky_arenas.migrations.0009_core_profile_assets_and_catalog'
        ).Migration
        slug_operations = [
            (index, operation) for index, operation in enumerate(migration.operations)
            if isinstance(operation, (migrations.AddField, migrations.AlterField))
            and operation.model_name == 'historicalera' and operation.name == 'slug'
        ]
        self.assertEqual(len(slug_operations), 2)
        add_index, add = slug_operations[0]
        alter_index, alter = slug_operations[1]
        self.assertFalse(add.field.db_index)
        self.assertFalse(add.field.unique)
        self.assertTrue(add.field.null)
        self.assertTrue(alter.field.unique)
        self.assertTrue(alter.field.db_index)
        self.assertFalse(alter.field.null)
        populate_index = next(
            index for index, operation in enumerate(migration.operations)
            if isinstance(operation, migrations.RunPython)
            and operation.code.__name__ == 'populate_core_catalog'
        )
        self.assertLess(add_index, populate_index)
        self.assertLess(populate_index, alter_index)
