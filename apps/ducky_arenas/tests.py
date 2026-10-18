from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.ducky_arenas.config import ARENA_REWARD_CONFIG, DUCKY_TYPES, MAX_LEVEL
from apps.ducky_arenas.models import ArenaItem, ArenaRewardTransaction, UserArenaInventory, UserProfileArenas
from apps.ducky_arenas.services import (
    calculate_effective_stats,
    calculate_power,
    get_level_for_xp,
    get_level_progress,
    grant_arena_rewards,
    update_match_stats,
)


class DuckyArenasOnboardingAccessTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(username='duck_user', password='test1234')

    def test_usuario_sin_perfil_va_a_onboarding(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('ducky_arenas:home'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('ducky_arenas:onboarding'))

    def test_usuario_con_perfil_incompleto_va_a_onboarding(self):
        UserProfileArenas.objects.create(user=self.user, nickname='Ducky', avatarType='PREHISTORIC', onboardingCompleted=False)
        self.client.force_login(self.user)

        response = self.client.get(reverse('ducky_arenas:home'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('ducky_arenas:onboarding'))

    def test_usuario_con_onboarding_completo_puede_acceder(self):
        UserProfileArenas.objects.create(
            user=self.user,
            nickname='Ducky',
            avatarType='GAMER',
            onboardingCompleted=True,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('ducky_arenas:home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("ducky_arenas:home")}" class="nav-link py-2 rounded sidebar-link ducky-arenas-link active"')
        self.assertNotContains(response, 'data-bs-target="#duckyArenasCollapse"')

    def test_lobby_muestra_ducky_y_las_cuatro_arenas_sin_modificar_perfil(self):
        profile = UserProfileArenas.objects.create(
            user=self.user, nickname='NeonDucky', avatarType='DEVELOPER',
            onboardingCompleted=True, xp=150, rating=1240, duckCoins=37,
            stats=DUCKY_TYPES['DEVELOPER']['baseStats'],
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('ducky_arenas:home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'NeonDucky')
        self.assertContains(response, 'DA_DEV_avatar')
        self.assertContains(response, 'img/pato.')
        self.assertContains(response, 'Tu Ducky de cuerpo entero · Developer')
        self.assertContains(response, '1240')
        self.assertContains(response, 'Experiencia · 150 XP')
        self.assertContains(response, f'value="{get_level_progress(150)["progress_percent"]}" max="100"')
        self.assertEqual(response.context['avatar_config'], DUCKY_TYPES['DEVELOPER'])
        self.assertContains(response, 'name="arena"', count=4)
        for arena in ('quiz', 'battle', 'challenge', 'escape'):
            self.assertContains(response, f'data-url="{reverse(f"ducky_arenas:{arena}")}"')
            self.assertContains(response, f'href="{reverse(f"ducky_arenas:{arena}")}"')
        for destination in ('profile', 'inventory'):
            self.assertContains(response, f'href="{reverse(f"ducky_arenas:{destination}")}"')
        self.assertContains(response, 'data-arena-play')
        self.assertContains(response, '<noscript>')
        self.assertEqual(
            [(stat['code'], stat['value']) for stat in response.context['characteristics']],
            [('ATK', 8), ('DEF', 7), ('LOG', 14), ('SPE', 9), ('INT', 10)],
        )
        self.assertContains(response, 'title="Inteligencia">INT</abbr>')
        profile.refresh_from_db()
        self.assertEqual(profile.avatarType, 'DEVELOPER')
        self.assertEqual(profile.xp, 150)
        self.assertEqual(profile.duckCoins, 37)

    def test_caracteristicas_conservan_los_bonos_de_energia_como_inteligencia(self):
        profile = UserProfileArenas.objects.create(
            user=self.user, nickname='BonusDucky', avatarType='DEVELOPER',
            onboardingCompleted=True, stats=DUCKY_TYPES['DEVELOPER']['baseStats'],
            equipped={'device': 'lobby_device'},
        )
        ArenaItem.objects.create(
            code='lobby_device', name='Dispositivo', slot='DEVICE',
            avatarType='DEVELOPER', statBonuses={'energy': 3, 'attack': 2},
        )
        self.client.force_login(self.user)
        for destination in ('home', 'profile'):
            with self.subTest(destination=destination):
                response = self.client.get(reverse(f'ducky_arenas:{destination}'))
                stats = {stat['code']: stat['value'] for stat in response.context['characteristics']}
                self.assertEqual(stats['INT'], 13)
                self.assertEqual(stats['ATK'], 10)
                self.assertContains(response, 'title="Inteligencia">INT</abbr>')
        profile.refresh_from_db()
        self.assertEqual(profile.stats['energy'], 10)

    def test_usuario_que_ya_termino_no_vuelve_a_ver_onboarding(self):
        UserProfileArenas.objects.create(
            user=self.user,
            nickname='Ducky',
            avatarType='SYSTEMS',
            onboardingCompleted=True,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('ducky_arenas:onboarding'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('ducky_arenas:home'))

    def test_menu_sidebar_abre_el_lobby_del_ducky_creado(self):
        UserProfileArenas.objects.create(
            user=self.user, nickname='SidebarDucky', avatarType='GAMER',
            onboardingCompleted=True,
        )
        self.client.force_login(self.user)
        profile_page = self.client.get(reverse('ducky_arenas:profile'))
        self.assertContains(
            profile_page,
            f'href="{reverse("ducky_arenas:home")}" class="nav-link py-2 rounded sidebar-link ducky-arenas-link active"',
        )
        lobby = self.client.get(reverse('ducky_arenas:home'), follow=True)
        self.assertEqual(lobby.redirect_chain, [])
        self.assertTemplateUsed(lobby, 'ducky_arenas/home.html')
        self.assertContains(lobby, 'data-ducky-lobby')
        self.assertContains(lobby, 'SidebarDucky')

    def test_escape_usa_escenario_y_sprite_con_teclas_de_cursor(self):
        UserProfileArenas.objects.create(
            user=self.user,
            nickname='Ducky',
            avatarType='GAMER',
            onboardingCompleted=True,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('ducky_arenas:escape'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'escenario')
        self.assertContains(response, 'griego')
        self.assertContains(response, 'ArrowLeft')
        self.assertContains(response, 'ArrowRight')


class DuckyArenasInventoryAndProfileTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(username='duck_inventory', password='test1234')
        self.profile = UserProfileArenas.objects.create(
            user=self.user,
            nickname='Inventario',
            avatarType='CYBERSECURITY',
            onboardingCompleted=True,
            stats=DUCKY_TYPES['CYBERSECURITY']['baseStats'],
            equipped={'head': None, 'face': None, 'torso': None, 'protection': None, 'legs': None, 'feet': None, 'back': None, 'neck': None, 'device': None, 'artifact': None},
        )
        self.item = ArenaItem.objects.create(
            code='cyber_hoodie_01',
            name='Cyber Hoodie',
            description='Chaqueta técnica',
            slot='TORSO',
            rarity='COMMON',
            avatarType='CYBERSECURITY',
            statBonuses={'attack': 2, 'defense': 3, 'speed': 1, 'logic': 0, 'energy': 2},
            isActive=True,
        )
        UserArenaInventory.objects.create(profile=self.profile, item=self.item, quantity=1)

    def test_usuario_sin_onboarding_no_entra_a_profile(self):
        self.user.user_profile_arenas.delete()
        self.client.force_login(self.user)

        response = self.client.get(reverse('ducky_arenas:profile'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('ducky_arenas:onboarding'))

    def test_usuario_completo_puede_entrar_a_profile(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('ducky_arenas:profile'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Inventario')

    def test_inventario_solo_muestra_items_del_usuario(self):
        other_user = self.user_model.objects.create_user(username='other_duck', password='test1234')
        other_profile = UserProfileArenas.objects.create(
            user=other_user,
            nickname='Otro',
            avatarType='GAMER',
            onboardingCompleted=True,
            stats=DUCKY_TYPES['GAMER']['baseStats'],
        )
        other_item = ArenaItem.objects.create(
            code='gamer_hat_01',
            name='Gamer Hat',
            description='Sombrero de gamer',
            slot='HEAD',
            rarity='RARE',
            avatarType='GAMER',
            statBonuses={'attack': 0, 'defense': 0, 'speed': 2, 'logic': 2, 'energy': 2},
            isActive=True,
        )
        UserArenaInventory.objects.create(profile=other_profile, item=other_item, quantity=1)
        self.client.force_login(self.user)

        response = self.client.get(reverse('ducky_arenas:inventory'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.item.name)
        self.assertNotContains(response, other_item.name)

    def test_usuario_puede_equipar_item_que_posee(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('ducky_arenas:inventory_equip'), {'item_code': self.item.code})

        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.equipped['TORSO'.lower()] if False else self.profile.equipped.get('torso'), self.item.code)

    def test_usuario_no_puede_equipar_item_que_no_posee(self):
        item = ArenaItem.objects.create(
            code='cyber_laptop_01',
            name='Laptop',
            description='Equipo',
            slot='DEVICE',
            rarity='EPIC',
            avatarType='CYBERSECURITY',
            statBonuses={'attack': 1, 'defense': 0, 'speed': 1, 'logic': 5, 'energy': 2},
            isActive=True,
        )
        self.client.force_login(self.user)

        response = self.client.post(reverse('ducky_arenas:inventory_equip'), {'item_code': item.code})

        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.equipped.get('device'))

    def test_usuario_no_puede_equipar_item_incompatible_con_avatar(self):
        item = ArenaItem.objects.create(
            code='roman_helmet_01',
            name='Roman Helmet',
            description='Casco romano',
            slot='HEAD',
            rarity='RARE',
            avatarType='ROMAN',
            statBonuses={'attack': 0, 'defense': 2, 'speed': 1, 'logic': 0, 'energy': 0},
            isActive=True,
        )
        UserArenaInventory.objects.create(profile=self.profile, item=item, quantity=1)
        self.client.force_login(self.user)

        response = self.client.post(reverse('ducky_arenas:inventory_equip'), {'item_code': item.code})

        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.equipped.get('head'))

    def test_equipar_item_coloca_en_slot_correcto(self):
        self.client.force_login(self.user)
        self.client.post(reverse('ducky_arenas:inventory_equip'), {'item_code': self.item.code})

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.equipped['torso'], self.item.code)

    def test_equipar_segundo_item_del_mismo_slot_reemplaza_al_anterior(self):
        other_item = ArenaItem.objects.create(
            code='cyber_armor_01',
            name='Cyber Armor',
            description='Armadura',
            slot='TORSO',
            rarity='RARE',
            avatarType='CYBERSECURITY',
            statBonuses={'attack': 1, 'defense': 4, 'speed': 1, 'logic': 0, 'energy': 0},
            isActive=True,
        )
        UserArenaInventory.objects.create(profile=self.profile, item=other_item, quantity=1)
        self.client.force_login(self.user)

        self.client.post(reverse('ducky_arenas:inventory_equip'), {'item_code': other_item.code})

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.equipped['torso'], other_item.code)
        self.assertEqual(UserArenaInventory.objects.filter(profile=self.profile, item=self.item).count(), 1)

    def test_desequipar_pone_slot_a_null(self):
        self.client.force_login(self.user)
        self.client.post(reverse('ducky_arenas:inventory_equip'), {'item_code': self.item.code})
        response = self.client.post(reverse('ducky_arenas:inventory_unequip'), {'slot': 'TORSO'})

        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.equipped.get('torso'))

    def test_calculate_effective_stats_suma_bonuses(self):
        self.profile.stats = {'attack': 10, 'defense': 12, 'speed': 14, 'logic': 18, 'energy': 12}
        self.profile.equipped = {'torso': self.item.code, 'head': None, 'face': None, 'protection': None, 'legs': None, 'feet': None, 'back': None, 'neck': None, 'device': None, 'artifact': None}
        self.profile.save(update_fields=['stats', 'equipped'])

        self.assertEqual(calculate_effective_stats(self.profile), {'attack': 12, 'defense': 15, 'speed': 15, 'logic': 18, 'energy': 14})

    def test_calculate_power_usa_effective_stats(self):
        self.profile.stats = {'attack': 10, 'defense': 12, 'speed': 14, 'logic': 18, 'energy': 12}
        self.profile.equipped = {'torso': self.item.code, 'head': None, 'face': None, 'protection': None, 'legs': None, 'feet': None, 'back': None, 'neck': None, 'device': None, 'artifact': None}
        self.profile.save(update_fields=['stats', 'equipped'])

        self.assertEqual(calculate_power(self.profile), 74)

    def test_no_se_puede_modificar_otro_profile_manipulando_post(self):
        other_user = self.user_model.objects.create_user(username='random_user', password='test1234')
        other_profile = UserProfileArenas.objects.create(
            user=other_user,
            nickname='Otro',
            avatarType='SYSTEMS',
            onboardingCompleted=True,
            stats=DUCKY_TYPES['SYSTEMS']['baseStats'],
        )
        other_item = ArenaItem.objects.create(
            code='sys_vest_01',
            name='System Vest',
            description='Chaleco',
            slot='TORSO',
            rarity='EPIC',
            avatarType='SYSTEMS',
            statBonuses={'attack': 1, 'defense': 2, 'speed': 0, 'logic': 1, 'energy': 1},
            isActive=True,
        )
        UserArenaInventory.objects.create(profile=other_profile, item=other_item, quantity=1)
        self.client.force_login(self.user)

        response = self.client.post(reverse('ducky_arenas:inventory_equip'), {'item_code': other_item.code})

        self.assertEqual(response.status_code, 302)
        other_profile.refresh_from_db()
        self.assertNotIn('torso', other_profile.equipped or {})

    def test_seed_command_es_idempotente(self):
        call_command('seed_ducky_items')
        first_count = ArenaItem.objects.count()
        call_command('seed_ducky_items')
        second_count = ArenaItem.objects.count()
        self.assertEqual(first_count, second_count)

    def test_user_inventory_impide_duplicados_profile_item(self):
        with self.assertRaises(Exception):
            UserArenaInventory.objects.create(profile=self.profile, item=self.item, quantity=1)


class DuckyArenasProgressionRewardTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(username='reward_user', password='test1234')
        self.profile = UserProfileArenas.objects.create(
            user=self.user,
            nickname='Progressor',
            avatarType='GAMER',
            onboardingCompleted=True,
            xp=0,
            level=1,
            duckCoins=0,
            stats=DUCKY_TYPES['GAMER']['baseStats'],
            equipped={'head': None, 'face': None, 'torso': None, 'protection': None, 'legs': None, 'feet': None, 'back': None, 'neck': None, 'device': None, 'artifact': None},
        )

    def test_conceder_xp_incrementa_profile_xp(self):
        result = grant_arena_rewards(self.profile, 'QUIZ', 'COMPLETED', 'MEDIUM', 800, 'reward-001')

        self.assertGreater(result['xp_awarded'], 0)
        self.profile.refresh_from_db()
        self.assertGreater(self.profile.xp, 0)

    def test_xp_suficiente_produce_level_up(self):
        self.profile.xp = 100
        self.profile.level = 1
        self.profile.save(update_fields=['xp', 'level'])

        result = grant_arena_rewards(self.profile, 'QUIZ', 'COMPLETED', 'MEDIUM', 800, 'reward-002')

        self.assertTrue(result['level_up'])
        self.assertGreater(result['new_level'], result['previous_level'])

    def test_puede_subir_varios_niveles_de_una_vez(self):
        self.profile.xp = 4200
        self.profile.level = 1
        self.profile.save(update_fields=['xp', 'level'])

        result = grant_arena_rewards(self.profile, 'QUIZ', 'WIN', 'HARD', 1500, 'reward-003')

        self.assertGreater(result['levels_gained'], 1)
        self.assertLessEqual(result['new_level'], MAX_LEVEL)

    def test_level_no_supera_max_level(self):
        self.profile.xp = 90000
        self.profile.level = MAX_LEVEL
        self.profile.save(update_fields=['xp', 'level'])

        result = grant_arena_rewards(self.profile, 'BATTLE', 'WIN', 'HARD', 2000, 'reward-004')

        self.assertEqual(result['new_level'], MAX_LEVEL)
        self.assertLessEqual(self.profile.level, MAX_LEVEL)

    def test_duck_coins_se_incrementan(self):
        result = grant_arena_rewards(self.profile, 'BATTLE', 'WIN', 'HARD', 2000, 'reward-005')

        self.assertGreater(result['duck_coins_awarded'], 0)
        self.profile.refresh_from_db()
        self.assertGreater(self.profile.duckCoins, 0)

    def test_reward_puede_conceder_item_y_crear_inventory(self):
        item = ArenaItem.objects.create(
            code='reward_lantern_01',
            name='Lantern',
            description='Luz de arena',
            slot='DEVICE',
            rarity='RARE',
            avatarType='GAMER',
            statBonuses={'speed': 1, 'logic': 1},
            isActive=True,
        )
        reward_context = {'item_code': item.code}

        result = grant_arena_rewards(self.profile, 'ESCAPE', 'COMPLETED', 'HARD', 1200, 'reward-006', reward_context)

        self.assertIn(item.code, result['items_awarded'])
        self.assertTrue(UserArenaInventory.objects.filter(profile=self.profile, item=item).exists())

    def test_item_repetido_aumenta_quantity(self):
        item = ArenaItem.objects.create(
            code='reward_bag_01',
            name='Reward Bag',
            description='Bolsa de recompensa',
            slot='BACK',
            rarity='COMMON',
            avatarType='GAMER',
            statBonuses={'energy': 2},
            isActive=True,
        )
        UserArenaInventory.objects.create(profile=self.profile, item=item, quantity=1)

        grant_arena_rewards(self.profile, 'CHALLENGE', 'COMPLETED', 'MEDIUM', 500, 'reward-007', {'item_code': item.code})

        inventory = UserArenaInventory.objects.get(profile=self.profile, item=item)
        self.assertEqual(inventory.quantity, 2)

    def test_source_id_es_idempotente(self):
        first = grant_arena_rewards(self.profile, 'QUIZ', 'COMPLETED', 'MEDIUM', 500, 'repeat-001')
        second = grant_arena_rewards(self.profile, 'QUIZ', 'COMPLETED', 'MEDIUM', 500, 'repeat-001')

        self.assertEqual(first['xp_awarded'], second['xp_awarded'])
        self.assertEqual(ArenaRewardTransaction.objects.filter(profile=self.profile, arenaType='QUIZ', sourceId='repeat-001').count(), 1)

    def test_mismo_source_id_no_duplica_coins_ni_items(self):
        item = ArenaItem.objects.create(
            code='repeat_coin_item_01',
            name='Repeat Coin Item',
            description='No duplicado',
            slot='NECK',
            rarity='COMMON',
            avatarType='GAMER',
            statBonuses={'attack': 1},
            isActive=True,
        )
        before_xp = self.profile.xp
        before_coins = self.profile.duckCoins

        grant_arena_rewards(self.profile, 'QUIZ', 'WIN', 'MEDIUM', 700, 'repeat-002', {'item_code': item.code})
        grant_arena_rewards(self.profile, 'QUIZ', 'WIN', 'MEDIUM', 700, 'repeat-002', {'item_code': item.code})

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.xp, before_xp + ARENA_REWARD_CONFIG['QUIZ']['completion_xp'] + 25 + 7)
        self.assertEqual(self.profile.duckCoins, before_coins + ARENA_REWARD_CONFIG['QUIZ']['completion_coins'] + 8)
        self.assertEqual(UserArenaInventory.objects.filter(profile=self.profile, item=item).count(), 1)

    def test_win_actualiza_wins_y_streak(self):
        update_match_stats(self.profile, 'WIN')

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.wins, 1)
        self.assertEqual(self.profile.gamesPlayed, 1)
        self.assertEqual(self.profile.currentWinStreak, 1)
        self.assertEqual(self.profile.bestWinStreak, 1)

    def test_loss_reinicia_streak(self):
        self.profile.currentWinStreak = 3
        self.profile.bestWinStreak = 3
        self.profile.save(update_fields=['currentWinStreak', 'bestWinStreak'])

        update_match_stats(self.profile, 'LOSS')

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.losses, 1)
        self.assertEqual(self.profile.currentWinStreak, 0)

    def test_get_level_progress_devuelve_porcentaje(self):
        self.profile.xp = 350
        self.profile.save(update_fields=['xp'])

        progress = get_level_progress(self.profile.xp)

        self.assertIn('current_level', progress)
        self.assertIn('next_level', progress)
        self.assertIn('progress_percent', progress)
        self.assertGreaterEqual(progress['progress_percent'], 0)

    def test_rewards_view_requires_login(self):
        response = self.client.get('/ducky-arenas/rewards/')
        self.assertEqual(response.status_code, 302)


class DuckyArenasOnboardingCompleteTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(username='duck_complete', password='test1234')

    def test_completar_onboarding_crea_perfil_con_valores_servidor(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('ducky_arenas:onboarding_complete'),
            {'nickname': '  DuckyM   ', 'avatarType': 'DEVELOPER', 'gender': 'BOY'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        profile = UserProfileArenas.objects.get(user=self.user)
        self.assertEqual(profile.nickname, 'DuckyM')
        self.assertEqual(profile.avatarType, 'DEVELOPER')
        self.assertEqual(profile.gender, 'BOY')
        self.assertTrue(profile.onboardingCompleted)
        self.assertIsNotNone(profile.onboardingCompletedAt)
        self.assertEqual(profile.level, 1)
        self.assertEqual(profile.xp, 0)
        self.assertEqual(profile.prestigeLevel, 0)
        self.assertEqual(profile.evolutionStage, 'BASE')
        self.assertEqual(profile.rank, 'UNRANKED')
        self.assertEqual(profile.stats, DUCKY_TYPES['DEVELOPER']['baseStats'])
        self.assertEqual(profile.equipped, {'head': None, 'face': None, 'torso': None, 'protection': None, 'legs': None, 'feet': None, 'back': None, 'neck': None, 'device': None, 'artifact': None})
        self.assertEqual(response.json()['redirectUrl'], reverse('ducky_arenas:home'))
        lobby = self.client.get(response.json()['redirectUrl'])
        self.assertContains(lobby, 'data-ducky-lobby')
        self.assertContains(lobby, 'DuckyM')
        self.assertContains(lobby, 'title="Inteligencia">INT</abbr>')

    def test_genero_invalido_rechaza_validacion(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('ducky_arenas:onboarding_complete'),
            {'nickname': 'Ducky', 'avatarType': 'GAMER', 'gender': 'INVALID'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('gender', response.json()['errors'])

    def test_doble_envio_no_crea_perfiles_duplicados(self):
        self.client.force_login(self.user)

        first = self.client.post(
            reverse('ducky_arenas:onboarding_complete'),
            {'nickname': 'DuckyOne', 'avatarType': 'GAMER'},
            content_type='application/json',
        )
        second = self.client.post(
            reverse('ducky_arenas:onboarding_complete'),
            {'nickname': 'DuckyTwo', 'avatarType': 'ROMAN'},
            content_type='application/json',
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(UserProfileArenas.objects.filter(user=self.user).count(), 1)
        self.assertEqual(UserProfileArenas.objects.get(user=self.user).nickname, 'DuckyTwo')

    def test_un_usuario_no_puede_editar_el_perfil_de_otro(self):
        other_user = self.user_model.objects.create_user(username='other_duck', password='test1234')
        other_profile = UserProfileArenas.objects.create(
            user=other_user,
            nickname='Otro',
            avatarType='PREHISTORIC',
            onboardingCompleted=False,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('ducky_arenas:onboarding_complete'),
            {'nickname': 'Intruso', 'avatarType': 'SYSTEMS'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        other_profile.refresh_from_db()
        self.assertEqual(other_profile.nickname, 'Otro')

    def test_no_se_puede_manipular_stats_xp_dinero_rank_desde_body(self):
        self.client.force_login(self.user)

        payload = {
            'nickname': 'Ducky',
            'avatarType': 'CYBERSECURITY',
            'level': 99,
            'xp': 999,
            'stats': {'attack': 9000},
            'duckCoins': 250,
            'arenaTokens': 100,
            'rating': 6000,
            'rank': 'LEGENDARY',
            'equipped': {'head': 'sword'},
        }

        response = self.client.post(reverse('ducky_arenas:onboarding_complete'), payload, content_type='application/json')

        self.assertEqual(response.status_code, 200)
        profile = UserProfileArenas.objects.get(user=self.user)
        self.assertEqual(profile.level, 1)
        self.assertEqual(profile.xp, 0)
        self.assertEqual(profile.stats, DUCKY_TYPES['CYBERSECURITY']['baseStats'])
        self.assertEqual(profile.duckCoins, 0)
        self.assertEqual(profile.arenaTokens, 0)
        self.assertEqual(profile.rating, 1000)
        self.assertEqual(profile.rank, 'UNRANKED')
        self.assertEqual(profile.equipped, {'head': None, 'face': None, 'torso': None, 'protection': None, 'legs': None, 'feet': None, 'back': None, 'neck': None, 'device': None, 'artifact': None})

    def test_nickname_invalido_rechaza_validacion(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('ducky_arenas:onboarding_complete'),
            {'nickname': '  ', 'avatarType': 'GAMER'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('nickname', response.json()['errors'])

    def test_avatar_type_no_valido_rechaza_validacion(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('ducky_arenas:onboarding_complete'),
            {'nickname': 'Ducky', 'avatarType': 'INVALID'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('avatarType', response.json()['errors'])

    def test_api_profile_me_indica_requiere_onboarding(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('ducky_arenas:profile_me'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['profile'])
        self.assertTrue(response.json()['requiresOnboarding'])
