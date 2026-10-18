from django.db import migrations, models
import django.db.models.deletion


def populate_core_catalog(apps, schema_editor):
    HistoricalEra = apps.get_model('ducky_arenas', 'HistoricalEra')
    WorldMap = apps.get_model('ducky_arenas', 'WorldMap')
    CharacterAsset = apps.get_model('ducky_arenas', 'CharacterAsset')
    database = schema_editor.connection.alias

    slugs = {
        'PRE': 'prehistoria',
        'GRE': 'grecia',
        'ROM': 'roma',
        'MED': 'edad-media',
        'REN': 'renacimiento',
        'REV': 'revolucion-industrial',
        'SXX': 'siglo-xx',
        'ACT': 'actualidad',
    }
    for code, slug in slugs.items():
        HistoricalEra.objects.using(database).filter(code=code).update(slug=slug)
    HistoricalEra.objects.using(database).filter(code='ACT').update(is_available=False)

    generic_maps = (
        ('DAR_000', 'Mapa DAR 000', 'duckyarenas/DAR_mapas/DAR_000_mapa.PNG'),
        ('DAR_001', 'Mapa DAR 001', 'duckyarenas/DAR_mapas/DAR_001_mapa.PNG'),
    )
    for code, name, static_path in generic_maps:
        WorldMap.objects.using(database).get_or_create(
            code=code,
            defaults={
                'era_id': None,
                'name': name,
                'static_path': static_path,
                'width': 1672,
                'height': 941,
                'is_available': True,
                'collision_config': [],
            },
        )

    character_assets = {
        'PRE': ('DAR_PRE1.jpg', 'DAR_PRE2.jpg'),
        'GRE': ('DAR_GRE1.jpg', 'DAR_GRE2.png'),
        'ROM': ('DAR_ROM1.jpg', 'DAR_ROM2.jpg'),
        'MED': ('DAR_MED1.png', 'DAR_MED2.png'),
        'REN': ('DAR_REN1.png', 'DAR_REN2.png'),
        'REV': ('DAR_REV1.jpg', 'DAR_REV2.png'),
        'SXX': ('DAR_SXX1.png', 'DAR_SXX2.png'),
    }
    for era_code, image_names in character_assets.items():
        era = HistoricalEra.objects.using(database).get(code=era_code)
        for variant, image_name in zip(('BOY', 'GIRL'), image_names):
            CharacterAsset.objects.using(database).get_or_create(
                era_id=era.pk,
                variant=variant,
                defaults={
                    'name': f'{era.name} {"chico" if variant == "BOY" else "chica"}',
                    'static_image_path': f'duckyarenas/DAR_personajes/{image_name}',
                    'movement_resources': (
                        [
                            f'duckyarenas/DAR_sprites/DAR_PRE_sprite{frame:02}.png'
                            for frame in range(1, 9)
                        ]
                        if era_code == 'PRE' and variant == 'BOY'
                        else []
                    ),
                    'is_available': True,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ('ducky_arenas', '0008_historicalera_playereraprogress_portal_profession_and_more'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='userprofilearenas',
            name='arena_profile_position_pair',
        ),
        migrations.RemoveConstraint(
            model_name='userprofilearenas',
            name='arena_profile_x_bounds',
        ),
        migrations.RemoveConstraint(
            model_name='userprofilearenas',
            name='arena_profile_y_bounds',
        ),
        migrations.RenameField(
            model_name='userprofilearenas',
            old_name='exploration_direction',
            new_name='facing_direction',
        ),
        migrations.RenameField(
            model_name='userprofilearenas',
            old_name='exploration_last_activity',
            new_name='last_access',
        ),
        migrations.RenameField(
            model_name='userprofilearenas',
            old_name='exploration_x',
            new_name='position_x',
        ),
        migrations.RenameField(
            model_name='userprofilearenas',
            old_name='exploration_y',
            new_name='position_y',
        ),
        migrations.AddConstraint(
            model_name='userprofilearenas',
            constraint=models.CheckConstraint(
                check=models.Q(
                    models.Q(('position_x__isnull', True), ('position_y__isnull', True)),
                    models.Q(('position_x__isnull', False), ('position_y__isnull', False)),
                    _connector='OR',
                ),
                name='arena_profile_position_pair',
            ),
        ),
        migrations.AddConstraint(
            model_name='userprofilearenas',
            constraint=models.CheckConstraint(
                check=models.Q(
                    ('position_x__isnull', True),
                    models.Q(('position_x__gte', 0), ('position_x__lte', 1)),
                    _connector='OR',
                ),
                name='arena_profile_x_bounds',
            ),
        ),
        migrations.AddConstraint(
            model_name='userprofilearenas',
            constraint=models.CheckConstraint(
                check=models.Q(
                    ('position_y__isnull', True),
                    models.Q(('position_y__gte', 0), ('position_y__lte', 1)),
                    _connector='OR',
                ),
                name='arena_profile_y_bounds',
            ),
        ),
        migrations.AddField(
            model_name='historicalera',
            name='slug',
            field=models.SlugField(blank=True, max_length=48, null=True),
        ),
        migrations.AddField(
            model_name='historicalera',
            name='description',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='historicalera',
            name='resource_config',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RenameField(
            model_name='historicalera',
            old_name='is_active',
            new_name='is_available',
        ),
        migrations.AlterField(
            model_name='worldmap',
            name='era',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='maps',
                to='ducky_arenas.historicalera',
            ),
        ),
        migrations.AlterField(
            model_name='worldmap',
            name='code',
            field=models.CharField(max_length=24, unique=True),
        ),
        migrations.RemoveConstraint(
            model_name='worldmap',
            name='arena_map_era_code_uniq',
        ),
        migrations.AddField(
            model_name='portal',
            name='display_order',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.RemoveConstraint(
            model_name='portal',
            name='arena_portal_map_code_uniq',
        ),
        migrations.AlterField(
            model_name='portal',
            name='code',
            field=models.CharField(max_length=64, unique=True),
        ),
        migrations.AddField(
            model_name='profession',
            name='access_requirements',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name='CharacterAsset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('variant', models.CharField(choices=[('BOY', 'Chico'), ('GIRL', 'Chica')], max_length=8)),
                ('name', models.CharField(max_length=100)),
                ('static_image_path', models.CharField(blank=True, default='', max_length=255)),
                ('movement_resources', models.JSONField(blank=True, default=list)),
                ('is_available', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('era', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='characters',
                    to='ducky_arenas.historicalera',
                )),
            ],
            options={
                'verbose_name': 'Recurso de personaje',
                'verbose_name_plural': 'Recursos de personajes',
                'ordering': ['era__order', 'variant'],
            },
        ),
        migrations.AddField(
            model_name='userprofilearenas',
            name='selected_character',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='selected_by_profiles',
                to='ducky_arenas.characterasset',
            ),
        ),
        migrations.AddIndex(
            model_name='characterasset',
            index=models.Index(fields=['era', 'is_available'], name='arena_char_era_available'),
        ),
        migrations.AddConstraint(
            model_name='characterasset',
            constraint=models.UniqueConstraint(fields=('era', 'variant'), name='arena_character_era_variant_uniq'),
        ),
        migrations.RunPython(populate_core_catalog, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='historicalera',
            name='slug',
            field=models.SlugField(max_length=48, unique=True),
        ),
    ]
