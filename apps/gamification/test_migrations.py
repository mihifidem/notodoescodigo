from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LegacyBadgeMigrationTests(TransactionTestCase):
    def test_legacy_rows_receive_unique_codes_before_constraints_are_added(self):
        before_migration = [('gamification', '0005_alter_mision_puntos_recompensa')]
        after_migration = [
            ('gamification', '0006_insigniaauditevent_insigniaversion_insignia_category_and_more')
        ]
        executor = MigrationExecutor(connection)
        executor.migrate(before_migration)

        try:
            old_apps = executor.loader.project_state(before_migration).apps
            Insignia = old_apps.get_model('gamification', 'Insignia')
            InsigniaUsuario = old_apps.get_model('gamification', 'InsigniaUsuario')
            user = get_user_model().objects.create_user(username='legacy_badge_migration')

            badges = [
                Insignia.objects.create(
                    nombre=f'Insignia heredada {index}',
                    descripcion='Registro previo a la migración',
                    tipo='especial',
                    requisito_valor=0,
                    visible=True,
                )
                for index in range(2)
            ]
            awards = [
                InsigniaUsuario.objects.create(usuario_id=user.pk, insignia_id=badge.pk)
                for badge in badges
            ]

            executor = MigrationExecutor(connection)
            executor.migrate(after_migration)
            new_apps = executor.loader.project_state(after_migration).apps
            MigratedInsignia = new_apps.get_model('gamification', 'Insignia')
            MigratedAward = new_apps.get_model('gamification', 'InsigniaUsuario')

            codes = list(
                MigratedInsignia.objects.filter(pk__in=[badge.pk for badge in badges])
                .values_list('code', flat=True)
            )
            idempotency_keys = list(
                MigratedAward.objects.filter(pk__in=[award.pk for award in awards])
                .values_list('idempotency_key', flat=True)
            )
            self.assertEqual(len(set(codes)), 2)
            self.assertNotIn(None, codes)
            self.assertEqual(len(set(idempotency_keys)), 2)
            self.assertNotIn(None, idempotency_keys)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
