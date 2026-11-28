import json
import unittest
from pathlib import Path

from unittest.mock import patch

from native946_innate_effects import ACTIVE, STATS, ALL_HANDLED, combat_skills, death_effect, prepare, stats
from test_native946_skillbooks import BasicBookTests


class AlwaysProc:
    def randrange(self, *args):
        return 0

    def randint(self, low, high):
        return low

    def random(self):
        return 0


def fighter(fid, camp, skills=()):
    return dict(id=fid, camp=camp, kind='pet', owner=400000, hp=10000, hpmax=10000, mp=1000, mpmax=1000,
                attack=100, defense=0, initiative=100, strength=100, mag=100, level=100,
                innate_skills=[dict(stunt_id=skill) for skill in skills])


class InnateEffectTests(unittest.TestCase):
    setUp = BasicBookTests.setUp
    send = BasicBookTests.send

    def test_every_innate_and_jing_skill_has_a_local_handler(self):
        innate = json.loads(Path('native946_guardian_innate_data.json').read_text('utf8'))['skills']
        rules = json.loads(Path('native946_guardian_rules_data.json').read_text('utf8'))
        jing = rules['verified_client_rules']['jing_skill_preview']
        self.assertFalse([skill for skill in innate if int(skill) not in ALL_HANDLED])
        self.assertFalse([skill for skill in jing if int(skill) not in ALL_HANDLED])
        skills = combat_skills({'innate_skills': [{'stunt_id': 4897}]})
        self.assertGreaterEqual(len(skills), 1)

    def test_fire_lance_hits_four_times_and_charges_mana(self):
        sid = self.s.acquire(3373, 'innate:fire-lance')
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET fight=0 WHERE character_id=?', (self.p.cid,))
            db.execute('UPDATE summons SET fight=1 WHERE id=?', (sid,))
        self.g.begin_battle()
        mana = self.g.battle['pet']['mp']
        before = self.g.battle['enemy_hp']
        self.send(2178, 1)
        replies = self.send(2183, 9, 4897, 900002)
        self.assertEqual(self.g.battle['pet']['mp'], mana - 100)
        self.assertLess(self.g.battle['enemy_hp'], before)
        self.assertIn(2157, [int.from_bytes(row[1:3], 'little') if row[0] == 0 else row[0] for row in replies])

    def test_tai_sui_lethal_damage_preserves_native_number_queue(self):
        from native946_innate_effects import tai_sui_values
        from native946_login import MessageReader
        from native946_role_battle import Runtime, nums

        caster = fighter(1, 4, (4906,))
        ratio, attached = tai_sui_values(caster)
        physical = 500 * ratio // 10000
        first_hit_hp = 1
        last_hit_hp = physical + attached * 2 + 1
        targets = {fid: fighter(fid, 3) for fid in (2, 3, 4)}
        for target, hp in zip(targets.values(), (first_hit_hp, last_hit_hp, 10000)):
            target.update(hp=hp, hpmax=hp, exit_on_defeat=True)
        runtime = Runtime({1: caster, **targets}, {}, 1, AlwaysProc(), {})
        with patch('native946_book_combat.roll', return_value=dict(miss=False, damage=500, effect=0)):
            runtime.guardian_cast(self.g, caster, 2, 4906)

        amounts = {fid: [] for fid in targets}
        for packet in runtime.out:
            if packet[:3] == nums(2164):
                reader = MessageReader(packet[3:])
                fid, amount = reader.integer(), reader.integer()
                amounts[fid].append(-amount)
        self.assertEqual(amounts, {
            2: [first_hit_hp],
            3: [physical, attached, attached, 1],
            4: [physical, attached, attached, attached],
        })
        for fid in (2, 3):
            self.assertTrue(targets[fid]['removed'])
            self.assertIn(nums(2142, fid, 2), runtime.out)
            self.assertNotIn(nums(2142, fid, 3), runtime.out)
        self.assertEqual([t['id'] for t in runtime.enemies(caster)], [4])

    def test_other_guardian_multihits_keep_numbers_through_lethal_hit(self):
        from native946_login import MessageReader
        from native946_role_battle import Runtime, nums

        def cast_with_hp(skill, hp):
            caster, target = fighter(1, 4, (skill,)), fighter(2, 3)
            if skill == 339:
                caster['innate_skills'] = []
                caster['learned_books'] = [dict(skill=339, level=30)]
            target.update(kind='enemy', hp=hp, hpmax=hp, exit_on_defeat=True)
            runtime = Runtime({1: caster, 2: target}, {}, 1, AlwaysProc(), {})
            runtime.guardian_cast(self.g, caster, 2, skill)
            amounts = []
            for packet in runtime.out:
                if packet[:3] == nums(2164):
                    reader = MessageReader(packet[3:])
                    if reader.integer() == 2:
                        amounts.append(-reader.integer())
            return amounts, target, runtime.out

        cases = ((317, 2), (339, 2), (346, 2), (4044, 2), (4539, 2),
                 (4743, 4), (4832, 2), (4897, 4), (4909, 2))
        with patch('native946_book_combat.roll', return_value=dict(miss=False, damage=500, effect=0)):
            for skill, count in cases:
                with self.subTest(skill=skill):
                    amounts, _, _ = cast_with_hp(skill, 1000000)
                    self.assertEqual(len(amounts), count)
                    for hp, expected in ((1, [1]), (sum(amounts), amounts)):
                        numbers, target, packets = cast_with_hp(skill, hp)
                        self.assertEqual(numbers, expected)
                        self.assertEqual(target['hp'], 0)
                        self.assertTrue(target['removed'])
                        self.assertEqual(packets.count(nums(2142, 2, 2)), 1)
                        self.assertNotIn(nums(2142, 2, 3), packets)

    def test_guardian_poison_follow_up_keeps_prior_hit_visible(self):
        from native946_role_battle import Runtime, nums

        caster, target = fighter(1, 4, (4868,)), fighter(2, 3)
        target.update(kind='enemy', hp=101, hpmax=101, exit_on_defeat=True)
        runtime = Runtime({1: caster, 2: target}, {}, 1, AlwaysProc(), {})
        runtime.group(caster, [target], 10)
        runtime.damage(caster, target, 100)
        runtime.end()
        self.assertEqual(target['hp'], 0)
        self.assertIn(nums(2164, 2, -100, 1, 0), runtime.out)
        self.assertIn(nums(2164, 2, -1, 1, 0), runtime.out)
        self.assertEqual(runtime.out.count(nums(2142, 2, 2)), 1)
        self.assertNotIn(nums(2142, 2, 3), runtime.out)

    def test_jade_rain_revives_and_heals_allies(self):
        sid = self.s.acquire(3372, 'innate:jade-rain')
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET fight=0 WHERE character_id=?', (self.p.cid,))
            db.execute('UPDATE summons SET fight=1 WHERE id=?', (sid,))
        self.g.begin_battle()
        self.g.battle['hp'] = 0
        self.send(2178, 1)
        replies = self.send(2183, 9, 4899, self.g.character['id'])
        self.assertGreater(self.g.battle['hp'], 0, (self.g.last_event, replies.decode('gb18030', errors='ignore') if isinstance(replies, bytes) else [row[:3] for row in replies]))

    def test_curse_of_grasp_deals_two_thunder_hits(self):
        sid = self.s.acquire(3375, 'innate:curse')
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET fight=0 WHERE character_id=?', (self.p.cid,))
            db.execute('UPDATE summons SET fight=1 WHERE id=?', (sid,))
        self.g.begin_battle()
        before = self.g.battle['enemy_hp']
        self.send(2178, 1)
        self.send(2183, 9, 4909, 900002)
        self.assertLess(self.g.battle['enemy_hp'], before)

    def test_every_active_skill_uses_the_real_native_request(self):
        original_active = self.s.active
        with patch.object(self.s, 'active', side_effect=lambda: self.g.battle['pet'] if self.g.battle and self.g.battle.get('pet') else original_active()):
            for skill, rule in ACTIVE.items():
                with self.subTest(skill=skill):
                    self.g.begin_battle()
                    battle = self.g.battle
                    battle.update(enemy_hp=100000000, enemy_hpmax=100000000, enemy_defense=0)
                    pet = battle['pet']
                    pet.update(innate_skills=[dict(stunt_id=skill)], mp=10000, mpmax=10000, attack=100, strength=100, mag=100)
                    target = self.p.cid if rule['element'] == 'support' else 900002
                    if skill == 4899:
                        from native946_role_battle import solo_fighters
                        battle['hp'] = 0
                        solo_fighters(self.g)[self.p.cid]['hp'] = 0
                    self.send(2178, 1)
                    self.send(2183, 9, skill, target)
                    self.assertEqual(self.g.battle['pet']['mp'], 10000 - rule['mana'], self.g.last_event)
                    self.assertEqual(self.g.last_event.get('action'), 'battle_turn')

    def test_every_innate_stat_modifier_changes_the_combat_snapshot(self):
        for skill, modifiers in STATS.items():
            with self.subTest(skill=skill):
                row = dict(innate_skills=[dict(stunt_id=skill)])
                stats(row)
                for key, amount in modifiers.items():
                    self.assertEqual(row[key], amount)

    def test_xianzhi_shields_use_each_recipient_cap_and_expire_separately(self):
        from native946_role_battle import Runtime

        caster = fighter(1, 3, (4806,))
        caster['innate_skills'][0].update(grade=180, fly=1)
        caster['unmodified_hpmax'] = 8000
        ally = fighter(2, 3)
        ally.update(hp=1000, hpmax=1000, role_absorb_hp={4656: 300})
        enemy = fighter(3, 4)
        runtime = Runtime({1: caster, 2: ally, 3: enemy}, {}, 5, AlwaysProc(), {})

        self.assertEqual(caster['role_absorb_hp'][4806], 7200)
        self.assertEqual(ally['role_absorb_hp'], {4656: 300, 4806: 1500})
        self.assertNotIn('role_absorb_hp', enemy)
        self.assertEqual(ally['role_all_reduction'], 150)
        runtime.round = 7
        runtime.begin_round()
        self.assertEqual(ally['role_all_reduction'], 0)
        self.assertEqual(ally['role_absorb_hp'][4806], 1500)
        runtime.round = 8
        runtime.begin_round()
        self.assertEqual(ally['role_absorb_hp'], {4656: 300})
        runtime.prepare(caster)
        self.assertNotIn(4806, ally['role_absorb_hp'])

    def test_duomu_native_misdirection_protects_the_caster_then_expires(self):
        from native946_innate_effects import active_rule, cast, nums
        from native946_role_battle import Runtime, profile

        caster = fighter(1, 3, (4804,))
        caster.update(mp=16000, mpmax=16000)
        ally = fighter(2, 3)
        target = fighter(3, 4)
        other = fighter(4, 4)
        runtime = Runtime({1: caster, 2: ally, 3: target, 4: other}, {}, 5, AlwaysProc(), {})

        self.assertTrue(cast(runtime, self.g, caster, 3, 4804))
        self.assertEqual(caster['mp'], 8000)
        self.assertIn(nums(2130, 3, 339, 0), runtime.out)
        self.assertIs(runtime.targets(target, profile(52), 2)[0][0], caster)
        runtime.attack(target, ally)
        self.assertIn(nums(2144, 1), runtime.out)
        self.assertEqual((caster['hp'], ally['hp']), (10000, 10000))
        self.assertEqual(runtime.shock(target, caster), 0)
        self.assertFalse(runtime.control(target, caster, 'seal'))
        self.assertEqual(runtime.damage(other, caster, 1000, 'physical'), 500)
        self.assertEqual(runtime.damage(other, caster, 1000, 'fire'), 1000)

        runtime.round = 6
        runtime.begin_round()
        self.assertIn(nums(2138, 3, 339), runtime.out)
        self.assertIsNone(runtime.misdirection_source(target))
        self.assertEqual(caster['role_physical_taken'], -500)
        with self.assertRaisesRegex(ValueError, '夺目尚在冷却'):
            active_rule(caster, 4804, round_number=6)
        runtime.round = 7
        runtime.begin_round()
        self.assertEqual(caster['role_physical_taken'], 0)
        self.assertEqual(active_rule(caster, 4804, round_number=7)['mana'], 8000)

    def test_duomu_is_available_from_the_native_guardian_learning_slot(self):
        from native946_guardian_skill_details import arguments

        sid = self.s.acquire(3363, 'innate:duomu')
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=100 WHERE id=?', (sid,))
        self.send(763, sid, 4804)
        pet = self.s.get(sid)
        entry = next(skill for skill in pet['innate_skills'] if skill['stunt_id'] == 4804)
        self.assertEqual(entry['position'], 3)
        self.assertEqual(arguments(pet, entry), (2, 50, 100))
        self.assertIn('法力：8000', [row['cost'] for row in combat_skills(pet)])

    def test_fire_lance_fixed_bonus_uses_attack_at_entry(self):
        from native946_innate_effects import physical
        from native946_role_battle import Runtime

        caster = fighter(1, 3, (4897,))
        target = fighter(2, 4)
        target['hp'] = 6000
        runtime = Runtime({1: caster, 2: target}, {}, 5, AlwaysProc(), {})
        runtime.buff(caster, 'attack', 400)
        with patch('native946_book_combat.roll', return_value=dict(miss=False, damage=100, effect=0)):
            physical(runtime, caster, target, 4897, lost_hp=True)
        self.assertEqual(target['hp'], 5400)

    def test_jing_cruelty_suppresses_the_ordinary_innate_bonus(self):
        pet = fighter(1, 3, (308, 4724))
        pet['innate_skills'][0].update(grade=180, fly=1)
        stats(pet)
        self.assertEqual(pet['CruelRate'], 400)
        self.assertEqual(pet['role_cruel_multiplier'], 400)
        self.assertNotIn('cruel_basis_points', pet)
        self.assertEqual([skill['stunt_id'] for skill in pet['innate_skills']], [308, 4724])

    def test_dragon_war_cannot_revive_from_poison_or_revive_ban(self):
        from native946_role_battle import Runtime

        for family, banned in (('poison', False), ('physical', True)):
            with self.subTest(family=family, banned=banned):
                pet = fighter(1, 3, (4619,))
                pet['no_revive_until'] = 5 if banned else 0
                foe = fighter(2, 4)
                runtime = Runtime({1: pet, 2: foe}, {}, 5, AlwaysProc(), {})
                runtime.damage(foe, pet, 100000, family)
                self.assertEqual(pet['hp'], 0)
                self.assertNotIn('role_dragon_revived', pet)

    def test_flash_checks_at_most_three_and_prefers_da_sha_si_fang(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from native946_role_battle import Runtime

        victim = fighter(1, 3)
        candidates = [fighter(i, 3, (4144, 4630)) for i in range(2, 6)]
        for candidate in candidates:
            candidate['innate_skills'][1].update(grade=180, fly=1)
        game = SimpleNamespace(summons=SimpleNamespace(snapshot=lambda: candidates))
        rng = Mock()
        rng.randrange.return_value = 2700
        runtime = Runtime({1: victim}, {victim['owner']: game}, 5, rng, {})
        runtime.flash_guardian(victim)
        self.assertEqual(rng.randrange.call_count, 3)
        self.assertFalse(victim.get('removed'))

    def test_cansheng_yijiu_shocks_three_enemies_and_revives_only_eligible_allies(self):
        from native946_codec import packet
        from native946_role_battle import Runtime

        for passive, revive_ids in (((), [12, 13, 14]), ((4762,), [12, 13, 14, 15])):
            with self.subTest(passive=passive):
                caster = fighter(1, 3, (4046, *passive))
                caster.update(mp=18000, mpmax=18000)
                enemies = {fid: fighter(fid, 4) for fid in range(2, 7)}
                allies = {fid: fighter(fid, 3) for fid in range(10, 17)}
                for ally in allies.values():
                    ally['hp'] = 0
                allies[10]['no_revive_until'] = 4
                allies[11]['removed'] = True
                allies[12]['hp_limit'] = 2000
                allies[16]['hp'] = 3000
                enemies[2]['ReMpAbsorb'] = 100
                runtime = Runtime({1: caster, **enemies, **allies}, {}, 1, AlwaysProc(), {})

                runtime.guardian_cast(self.g, caster, 6, 4046)
                self.assertEqual(caster['mp'], 10000)
                self.assertIn(packet(2157, ['number', 'number', ('array', 'number'), 'number'],
                                     [1, 6, [6, 2, 3], 4046]), runtime.out)
                self.assertEqual([(enemies[f]['hp'], enemies[f]['mp']) for f in (6, 2, 3, 4, 5)],
                                 [(8000, 800), (9000, 900), (8000, 800), (10000, 1000), (10000, 1000)])
                shock_packets = [i for i, reply in enumerate(runtime.out)
                                 if any(reply == packet(2164, ['number'] * 4, [f, -v, 1, 0])
                                        for f, v in ((6, 2000), (2, 1000), (3, 2000)))]
                revive_packets = [i for i, reply in enumerate(runtime.out)
                                  if any(reply == packet(2145, ['number'], [f]) for f in revive_ids)]
                self.assertEqual(len(shock_packets), 3)
                self.assertEqual(len(revive_packets), len(revive_ids))
                self.assertLess(max(shock_packets), min(revive_packets))
                self.assertEqual([fid for fid in range(10, 16) if allies[fid]['hp'] > 0], revive_ids)
                self.assertEqual([allies[fid]['hp'] for fid in revive_ids],
                                 [2000] + [2936] * (len(revive_ids) - 1))
                self.assertEqual(allies[16]['hp'], 3000)

    def test_cansheng_yijiu_cooldown_blocks_the_next_two_rounds(self):
        from native946_codec import packet
        from native946_guardian_magic import prepared
        from native946_role_battle import Runtime

        caster, enemy = fighter(1, 3, (4046,)), fighter(2, 4)
        caster.update(mp=18000, mpmax=18000)
        runtime = Runtime({1: caster, 2: enemy}, {}, 5, AlwaysProc(), {})
        runtime.guardian_cast(self.g, caster, 2, 4046)
        for turn, remaining in ((5, 2), (6, 1), (7, 0)):
            runtime.round = turn
            if turn != 5:
                caster['role_round'] = turn
                with self.assertRaisesRegex(ValueError, '冷却'):
                    prepared(caster, 4046)
                with self.assertRaisesRegex(ValueError, '冷却'):
                    runtime.guardian_cast(self.g, caster, 2, 4046)
                self.assertEqual(caster['mp'], 10000)
            runtime.end_round()
            self.assertIn(packet(2168, ['number', ('array', 'number'), ('array', 'number')],
                                 [1, [4046], [remaining]]), runtime.out)
        runtime.round = 8
        caster['role_round'] = 8
        runtime.guardian_cast(self.g, caster, 2, 4046)
        self.assertEqual(caster['mp'], 2000)

    def test_zhongling_yuxiu_applies_once_to_target_selection_and_spell_bonus(self):
        from native946_codec import packet
        from native946_innate_effects import cast
        from native946_role_battle import Runtime

        for skills, targets, damage, heal in (
                ((4539,), [6, 2, 3], 100, 100),
                ((4539, 4762), [6, 2, 3, 4], 120, 120)):
            with self.subTest(skills=skills):
                caster = fighter(1, 3, skills)
                enemies = {fid: fighter(fid, 4) for fid in range(2, 7)}
                runtime = Runtime({1: caster, **enemies}, {}, 1, AlwaysProc(), {})
                self.assertIn(f'最多作用{len(targets)}个目标', combat_skills(caster)[0]['des'])
                self.assertTrue(cast(runtime, self.g, caster, 6, 4539))
                self.assertIn(packet(2157, ['number', 'number', ('array', 'number'), 'number'],
                                     [1, 6, targets, 4539]), runtime.out)
                self.assertEqual(runtime.damage(caster, enemies[5], 100, 'water'), damage)
                self.assertEqual(runtime.damage(caster, enemies[5], 100, 'physical'), 100)
                caster['hp'] = 5000
                self.assertEqual(runtime.heal(caster, caster, 100), heal)

    def test_innate_book_attributes_and_training_levels_affect_real_stats(self):
        row = dict(innate_skills=[dict(stunt_id=300)], innate_training={300: dict(level=2, experience=0)})
        stats(row)
        self.assertGreater(row['hpmax'], 0)
        self.assertGreater(row['mpmax'], 0)
        self.assertGreater(row['attack'], 0)
        self.assertEqual(row['initiative'], 0)
        self.assertEqual(row['innate_skills'][0]['level'], 2)
        improved=dict(innate_skills=[dict(stunt_id=300)],innate_training={300:dict(level=4,experience=0)})
        stats(improved)
        self.assertEqual(improved['initiative'],1)

    def test_unyielding_blocks_lethal_damage_and_revival(self):
        from random import Random
        from native946_role_battle import Runtime
        attacker = dict(id=1, camp=3, kind='enemy', hp=100, hpmax=100, mp=0, mpmax=0, attack=1000,
                        defense=0, initiative=1, innate_skills=[])
        defender = dict(id=2, camp=4, kind='pet', owner=self.p.cid, hp=100, hpmax=100, mp=0, mpmax=0,
                        attack=1, defense=0, initiative=1, innate_skills=[dict(stunt_id=4736)])
        runtime = Runtime({1: attacker, 2: defender}, {}, 1, Random(1), {})
        runtime.end_round()
        self.assertFalse(defender.get('removed'))
        runtime.damage(attacker, defender, 1000)
        self.assertEqual(defender['hp'], 100)
        self.assertEqual(defender['role_unyielding_until'], 2)
        runtime.end_round()
        self.assertFalse(defender.get('removed'))
        runtime.round = 2
        runtime.damage(attacker, defender, 1000)
        self.assertEqual(defender['role_unyielding_until'], 2)
        runtime.end_round()
        self.assertTrue(defender['removed'])
        defender['hp'] = 0
        self.assertFalse(runtime.revive(attacker, defender, 100))

    def test_matching_ascended_stat_samples_do_not_change_untrained_skills(self):
        from native946_innate_training import schema

        sid = self.s.acquire(3307, 'innate:ascended-champion')
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET level=180,rebirth=2,ascension=1 WHERE id=?', (sid,))
        untrained = self.s.get(sid)
        with self.p.transaction() as db:
            schema(db)
            db.execute('INSERT INTO native_innate_training(summon_id,skill,level,rebirth,ascension) '
                       'VALUES(?,4140,180,2,1)', (sid,))
        trained = self.s.get(sid)
        self.assertEqual([trained[k] - untrained[k] for k in ('hpmax', 'mpmax', 'attack', 'initiative')],
                         [-215, -566, 40, -19])
        self.assertEqual((trained['PhyAbsorbDegree'], trained['ReJieti']), (116, 1240))
        self.assertNotIn('role_all_reduction', trained)
        for grade, fly in ((179, 1), (180, 0)):
            other = dict(innate_skills=[dict(stunt_id=4140, grade=grade, fly=fly)])
            stats(other)
            self.assertEqual(other['hpmax'], STATS[4140]['hpmax'])

    def test_ascended_peak_recovery_uses_own_hp_and_exact_proc_threshold(self):
        from native946_role_battle import Runtime

        for roll, expected in ((5964, 9800), (5965, 5000)):
            with self.subTest(roll=roll):
                attacker, defender = fighter(1, 3, (312,)), fighter(2, 4)
                attacker['innate_skills'][0].update(grade=180, fly=1)
                attacker['hp'] = 5000
                rng = AlwaysProc()
                rng.randrange = lambda stop: roll
                runtime = Runtime({1: attacker, 2: defender}, {}, 1, rng, {})
                runtime.damage(attacker, defender, 100, 'physical')
                self.assertEqual(attacker['hp'], expected)

    def test_legacy_ocean_pill_uses_water_resistance_and_reduces_hp_limit(self):
        from native946_role_battle import Runtime

        attacker, defender = fighter(1, 3), fighter(2, 4)
        attacker['neidan'] = [dict(kind=213, energy=1)]
        defender['ReWater'] = 100
        runtime = Runtime({1: attacker, 2: defender}, {}, 1, AlwaysProc(), {})
        with patch('native946_neidan_legacy.effect', return_value=dict(enabled=True, chance=100, damage=500)):
            runtime.pet_passives(attacker, defender)
        self.assertEqual(defender['role_last_damage_family'], 'water')
        self.assertEqual((defender['hp'], defender['hp_limit']), (9600, 9860))

    def test_elemental_pills_use_only_the_matching_resist_and_respect_spell_shields(self):
        from native946_role_battle import Runtime

        for pill, element, resist in ((211,'wind',37),(212,'thunder',81),(213,'water',103),(214,'fire',149)):
            with self.subTest(pill=pill):
                attacker,defender=fighter(1,3),fighter(2,4)
                attacker.update(hp=5000,neidan=[dict(kind=pill,energy=1)],new_neidan_power={282:1000})
                defender.update(ReWind=37,ReThunder=81,ReWater=103,ReFire=149)
                runtime=Runtime({1:attacker,2:defender},{},1,AlwaysProc(),{})
                defender['role_shields']['element']=1
                with patch('native946_neidan_legacy.effect',return_value=dict(enabled=True,chance=100,damage=500)):
                    runtime.pet_passives(attacker,defender)
                    self.assertEqual((defender['hp'],attacker['hp']),(10000,5000))
                    runtime.pet_passives(attacker,defender)
                hit=500-resist
                self.assertEqual(defender['role_last_damage_family'],element)
                self.assertEqual((defender['hp'],attacker['hp']),(10000-hit,5000+hit//10))

    def test_pill_water_bonuses_and_fire_critical_use_equipped_guardian_skills(self):
        from native946_role_battle import Runtime

        for pill,skills,protection,roll,expected in (
                (213,(4629,),0,0,10500), (213,(4629,4720),0,0,20500),
                (214,(4720,),0,0,500), (211,(4721,),0,0,500),
                (214,(4721,),0,299,620), (214,(4721,),1500,299,527),
                (214,(4721,),1500,300,500)):
            with self.subTest(pill=pill,skills=skills,roll=roll,protection=protection):
                attacker,defender=fighter(1,3,skills),fighter(2,4)
                attacker['neidan']=[dict(kind=pill,energy=1)]
                defender.update(hp=1_000_000,hpmax=1_000_000,new_neidan_power={280:protection})
                rng=AlwaysProc()
                rng.randrange=lambda stop:roll
                runtime=Runtime({1:attacker,2:defender},{},1,rng,{})
                with patch('native946_neidan_legacy.effect',return_value=dict(enabled=True,chance=100,damage=500)):
                    runtime.pet_passives(attacker,defender)
                self.assertEqual(1_000_000-defender['hp'],expected)

    def test_blood_flower_slow_expires_after_two_rounds_including_extra_target(self):
        from native946_guardian_skill_details import local_detail
        from native946_role_battle import Runtime

        for pill_chance, expected in ((0,3),(10000,4)):
            caster=fighter(1,3,(4615,))
            caster['new_neidan_power']={278:pill_chance}
            enemies=[fighter(i,4) for i in range(2,7)]
            for enemy in enemies:enemy.update(initiative=750,hp=100000,hpmax=100000)
            runtime=Runtime({f['id']:f for f in [caster,*enemies]},{},1,AlwaysProc(),{})
            runtime.guardian_cast(None,caster,2,4615)
            self.assertIn('持续2回合',local_detail(caster,4615))
            self.assertEqual([e['initiative'] for e in enemies],[550]*expected+[750]*(5-expected))
            runtime.round=2
            runtime.begin_round()
            self.assertEqual(sum(e['initiative']==550 for e in enemies),expected)
            runtime.round=3
            runtime.begin_round()
            self.assertTrue(all(e['initiative']==750 for e in enemies))

    def test_fire_critical_protection_keeps_the_normal_hit(self):
        from native946_guardian_magic import cast as book_cast
        from native946_role_battle import Runtime

        for skill in (214,338):
            for protected, reduction, expected in ((False,0,620 if skill==214 else 750),
                                                   (False,1500,527 if skill==214 else 637),
                                                   (True,1500,500)):
                attacker=fighter(1,3,(4721,) if skill==214 else ())
                target=fighter(2,4,(4746,) if protected else ())
                target['new_neidan_power']={280:reduction}
                rng=AlwaysProc()
                # Skip the separate 30% incoming damage reduction on 4746.
                rng.randrange=lambda scale: 305
                rt=Runtime({1:attacker,2:target},{},1,rng,{})
                if skill==214:
                    attacker['mag']=200 if protected else 100
                    rng.randrange=lambda scale: 305 if protected else 299
                    attacker['neidan']=[dict(kind=214,energy=1)]
                    with patch('native946_neidan_legacy.effect',return_value=dict(enabled=True,chance=100,damage=500)):
                        rt.pet_passives(attacker,target)
                else:
                    params=dict(targets=1, damage=500, mana=1, element='fire', proc_per_mille=900)
                    with patch('native946_guardian_magic.prepared',return_value=params):
                        rt.guardian_cast(None,attacker,2,338)
                        self.assertEqual(10000-target['hp'],expected)
                        target['hp']=10000
                        book_cast(None,attacker,[target],2,338,rng)
                self.assertEqual(10000-target['hp'],expected)

    def test_ordinary_fire_domain_checks_immunity_on_each_target(self):
        from native946_innate_effects import elemental
        from native946_role_battle import Runtime

        caster, vulnerable, immune = fighter(1,3,(4627,)), fighter(2,4), fighter(3,4,(4746,))
        rng = AlwaysProc()
        rng.randrange = lambda scale: 305
        rt = Runtime({actor['id']:actor for actor in (caster,vulnerable,immune)}, {}, 1, rng, {})
        with patch('native946_innate_effects.elemental_base', return_value=500):
            elemental(rt,caster,[vulnerable,immune],0,'fire')
        self.assertEqual((vulnerable['hp'],immune['hp']), (9250,9500))

    def test_jing_fire_tooltip_reports_the_bonus_used_in_combat(self):
        from native946_guardian_skill_details import apply_details
        for spirit,expected in ((0,'{25.0,20.0}'),(100,'{30.0,24.0}'),(2000,'{50.0,40.0}')):
            pet = fighter(1,3,(4721,))
            pet['mag'] = spirit
            self.assertEqual(apply_details(pet,[dict(stunt_id=4721)])[0]['desc'],expected)

    def test_physical_trigger_passives_execute_in_damage_pipeline(self):
        from native946_role_battle import Runtime
        checks = {
            310: lambda a, t: t['initiative'] < 100,
            311: lambda a, t: t['defense'] < 0,
            312: lambda a, t: a['hp'] > 5000,
            314: lambda a, t: t['ReSeal'] < 0,
            322: lambda a, t: t['role_medicine_percent'] < 0,
            4548: lambda a, t: a['defense'] > 0 and not t['role_buffs'],
            4559: lambda a, t: t['no_revive_until'] == 2,
            4635: lambda a, t: t['defense'] <= -693,
            4868: lambda a, t: bool(t.get('role_dots')),
        }
        for skill, check in checks.items():
            with self.subTest(skill=skill):
                attacker, defender = fighter(1, 3, (skill,)), fighter(2, 4)
                attacker['hp'] = 5000
                runtime = Runtime({1: attacker, 2: defender}, {}, 1, AlwaysProc(), {})
                if skill == 4548:
                    runtime.buff(defender, 'defense', 50, 3, 10)
                runtime.damage(attacker, defender, 100, 'physical')
                self.assertTrue(check(attacker, defender), (attacker, defender))

    def test_every_jing_absorption_heals_after_nonlethal_damage(self):
        from native946_role_battle import Runtime
        for skill, family in ((4714, 'shock'), (4715, 'thunder'), (4716, 'fire'), (4717, 'poison'),
                              (4718, 'water'), (4719, 'wind'), (4727, 'physical')):
            with self.subTest(skill=skill):
                attacker, defender = fighter(1, 3), fighter(2, 4, (skill,))
                defender['hp'] = 5000
                runtime = Runtime({1: attacker, 2: defender}, {}, 1, AlwaysProc(), {})
                runtime.damage(attacker, defender, 100, family)
                self.assertEqual(defender['hp'], 9965 if family=='water' else 10000)

    def test_start_and_death_skills_execute_with_real_fighters(self):
        from native946_role_battle import Runtime
        for skill in (334, 349, 4634):
            with self.subTest(skill=skill):
                victim = fighter(2, 4, (skill,))
                victim['hp'] = 0
                runtime = Runtime({2: victim}, {}, 1, AlwaysProc(), {})
                self.assertTrue(death_effect(runtime, fighter(1, 3), victim, skill))
                self.assertEqual(victim['hp'], victim['hpmax'])
                if skill == 349:
                    self.assertEqual(victim['role_shields']['endure'], 1)

    def test_flash_uses_the_reserve_guardian_and_rolls_only_once(self):
        from native946_gameplay import ENEMY_ID
        from native946_role_battle import Runtime, solo_fighters

        reserve_id = self.s.acquire(3356, 'flash:reserve')
        for skill, roll, replaced in ((4144, 2500, False), (4144, 2499, True),
                                      (4735, 9500, False), (4735, 9499, True)):
            with self.subTest(skill=skill, roll=roll):
                original = self.s.get(self.sid)
                self.s.set_vitals(self.sid, original['hpmax'], original['mpmax'])
                self.g.begin_battle()
                fighters = solo_fighters(self.g)
                victim = fighters[self.sid]
                victim['innate_skills'] = [dict(stunt_id=skill)]
                victim['hp'] = 0
                runtime = Runtime(fighters, {self.p.cid: self.g}, 1, AlwaysProc(), self.g.battle)
                with patch.object(runtime.rng, 'randrange', return_value=roll) as rolls:
                    runtime.kill(fighters[ENEMY_ID], victim)
                rolls.assert_called_once_with(10000)
                self.assertEqual(self.g.battle['pet']['id'], reserve_id if replaced else self.sid)
                self.assertEqual(reserve_id in self.g.battle['participants'], replaced)
                if replaced:
                    self.assertTrue(victim['removed'])
                    self.assertEqual(self.s.get(self.sid)['hp'], 0)

    def test_max_level_revival_and_cruel_probability_use_basis_points(self):
        from native946_book_combat import roll
        from native946_role_battle import Runtime

        attacker, victim = fighter(1, 3, (308,)), fighter(2, 4, (334,))
        for actor in (attacker, victim):
            actor['innate_skills'][0].update(grade=180, fly=1, feng=0)
        stats(attacker)
        self.assertEqual(attacker['cruel_basis_points'], 3151)
        rng = AlwaysProc()
        for rolled, expected in ((3151, 150), (3152, 100)):
            rng.randint = lambda low, high: rolled if high == 10000 else low
            self.assertEqual(roll(attacker, victim, rng)['damage'], expected)

        for rolled, family, hp in ((3540, 'fire', 6901), (3541, 'fire', 0), (0, 'poison', 0)):
            victim.update(hp=0, role_last_damage_family=family)
            rng.randrange = lambda high: rolled
            runtime = Runtime({1: attacker, 2: victim}, {}, 1, rng, {})
            self.assertEqual(death_effect(runtime, attacker, victim, 334), hp > 0)
            self.assertEqual(victim['hp'], hp)

    def test_peach_shield_shares_two_layers_and_does_not_refresh_each_round(self):
        from native946_role_battle import Runtime

        attacker, peach = fighter(1, 3), fighter(2, 4, (4765,))
        peach['innate_skills'][0].update(grade=180, fly=1, feng=0)
        stats(peach)
        self.assertEqual(peach['EquipElementResist'], 1500)
        fighters = {1: attacker, 2: peach}
        runtime = Runtime(fighters, {}, 1, AlwaysProc(), {})
        self.assertEqual(runtime.damage(attacker, peach, 2000, 'shock'), 2000)
        self.assertEqual(runtime.damage(attacker, peach, 2000, 'physical'), 0)
        self.assertEqual(runtime.damage(attacker, peach, 2000, 'water'), 0)
        self.assertEqual(peach['role_shields']['peach'], 0)
        runtime = Runtime(fighters, {}, 2, AlwaysProc(), {})
        self.assertEqual(runtime.damage(attacker, peach, 2000, 'fire'), 500)

    def test_multihit_support_and_damage_share_have_exact_effects(self):
        from native946_innate_effects import cast
        from native946_role_battle import Runtime
        for skill, family, count in ((4743, 'physical', 4), (4909, 'thunder', 2)):
            with self.subTest(skill=skill):
                attacker, defender = fighter(1, 4, (skill,)), fighter(2, 3)
                defender['hp'] = defender['hpmax'] = 1000000
                runtime = Runtime({1: attacker, 2: defender}, {}, 1, AlwaysProc(), {})
                hits = []
                original = runtime.damage
                def record(a, t, amount, damage_family='physical', **kwargs):
                    actual = original(a, t, amount, damage_family, **kwargs)
                    if actual:
                        hits.append(damage_family)
                    return actual
                runtime.damage = record
                self.assertTrue(cast(runtime, None, attacker, 2, skill))
                self.assertEqual(hits, [family] * count)
        attacker, ally, enemy = fighter(1, 4, (324,)), fighter(2, 4), fighter(3, 3)
        runtime = Runtime({1: attacker, 2: ally, 3: enemy}, {}, 1, AlwaysProc(), {})
        self.assertTrue(cast(runtime, None, attacker, 2, 324))
        runtime.damage(enemy, ally, 1000)
        self.assertEqual((attacker['hp'], ally['hp']), (9700, 9300))

    def test_jade_rain_heals_exactly_four_other_living_allies(self):
        from native946_innate_effects import cast
        from native946_role_battle import Runtime
        attacker = fighter(1, 4, (4899,))
        attacker['hp'] = 1000
        allies = [fighter(index, 4) for index in range(2, 8)]
        for ally in allies:
            ally['hp'] = 1000
        allies[0]['hp'] = 0
        fighters = {row['id']: row for row in [attacker, *allies]}
        runtime = Runtime(fighters, {}, 1, AlwaysProc(), {})
        self.assertTrue(cast(runtime, None, attacker, 2, 4899))
        self.assertGreater(allies[0]['hp'], 0)
        self.assertEqual(attacker['hp'], 4000)
        self.assertEqual([ally['hp'] for ally in allies[1:4]], [4000] * 3)
        self.assertEqual([ally['hp'] for ally in allies[4:]], [1000, 1000])

    def test_jing_physical_crit_combo_penetration_and_stealth_execute(self):
        from native946_book_combat import counter_ready, roll
        from native946_innate_effects import cast
        from native946_role_battle import Runtime
        for skill, expected in ((4724, 1900), (4725, 2800), (4731, 1150)):
            with self.subTest(skill=skill):
                attacker, defender = fighter(1, 4, (skill,)), fighter(2, 3)
                attacker['attack'] = 1000
                defender['defense'] = 1000 if skill == 4731 else 0
                stats(attacker)
                self.assertEqual(roll(attacker, defender, AlwaysProc())['damage'], expected)
        attacker, defender = fighter(1, 4, (4726,)), fighter(2, 3)
        stats(attacker)
        self.assertFalse(counter_ready(defender, 0, AlwaysProc(), attacker=attacker))
        attacker, defender = fighter(1, 4, (4728,)), fighter(2, 3)
        stats(attacker)
        runtime = Runtime({1: attacker, 2: defender}, {}, 1, AlwaysProc(), {})
        runtime.attack(attacker, defender)
        self.assertEqual(defender['hp'], 9800)
        attacker, defender = fighter(1, 4, (316, 4733)), fighter(2, 3)
        defender['invisible'] = True
        runtime = Runtime({1: attacker, 2: defender}, {}, 1, AlwaysProc(), {})
        self.assertIn(defender, runtime.enemies(attacker))
        self.assertTrue(cast(runtime, None, attacker, 2, 316))
        self.assertFalse(defender['invisible'])
        attacker, defender = fighter(1, 4, (4748,)), fighter(2, 3)
        runtime = Runtime({1: attacker, 2: defender}, {}, 1, AlwaysProc(), {})
        runtime.attack(attacker, defender)
        self.assertTrue(attacker['invisible'])

    def test_control_reflection_dodge_and_conditional_damage_execute(self):
        from native946_role_battle import Runtime
        attacker, defender = fighter(1, 3), fighter(2, 4, (4734,))
        runtime = Runtime({1: attacker, 2: defender}, {}, 1, AlwaysProc(), {})
        self.assertTrue(runtime.control(attacker, defender, 'seal', base=1000))
        self.assertIn('disorder', attacker['controls'])
        for skill, expected in ((4737, 150), (4745, 120), (4746, 50)):
            with self.subTest(skill=skill):
                attacker, defender = fighter(1, 3, (skill,) if skill != 4746 else ()), fighter(2, 4, (skill,) if skill == 4746 else ())
                runtime = Runtime({1: attacker, 2: defender}, {}, 1, AlwaysProc(), {})
                self.assertEqual(runtime.damage(attacker, defender, 100), expected)
        attacker, defender = fighter(1, 3), fighter(2, 4, (4797,))
        defender['strength'] = 200
        runtime = Runtime({1: attacker, 2: defender}, {}, 1, AlwaysProc(), {})
        self.assertEqual(runtime.damage(attacker, defender, 100, 'thunder'), 0)

    def test_entry_healing_and_active_cast_stacks_execute(self):
        from native946_innate_effects import cast
        from native946_role_battle import Runtime
        for skill, expected in ((4004, 20000), (4005, 25000), (4006, 30000)):
            with self.subTest(skill=skill):
                hero, pet = fighter(400000, 4), fighter(2, 4, (skill,))
                hero['kind'] = 'hero'
                runtime = Runtime({400000: hero, 2: pet}, {}, 1, AlwaysProc(), {})
                self.assertEqual(hero['hpmax'], expected)
        hero, pet = fighter(400000, 4), fighter(2, 4, (5019,))
        hero.update(kind='hero', hp=1000)
        hero.setdefault('controls', {})['seal'] = 3
        runtime = Runtime({400000: hero, 2: pet}, {}, 1, AlwaysProc(), {})
        self.assertEqual(hero['hp'], 6000)
        self.assertNotIn('seal', hero['controls'])
        attacker, defender = fighter(1, 4, (316, 4969, 4971)), fighter(2, 3)
        runtime = Runtime({1: attacker, 2: defender}, {}, 1, AlwaysProc(), {})
        self.assertTrue(cast(runtime, None, attacker, 2, 316))
        self.assertEqual(attacker['role_stacks']['4969'], 1)
        self.assertEqual(attacker['role_stacks']['4971'], 1)
        self.assertGreater(attacker['initiative'], 100)
        self.assertGreater(attacker['role_spell_stack'], 0)

    def test_jing_elemental_bonuses_change_real_damage(self):
        from native946_innate_effects import elemental
        from native946_role_battle import Runtime
        for skill, family in ((4720, 'water'), (4721, 'fire'), (4722, 'thunder'), (4723, 'wind'),
                              (4729, 'water'), (4732, 'fire')):
            with self.subTest(skill=skill):
                attacker, defender = fighter(1, 4, (skill,)), fighter(2, 3)
                runtime = Runtime({1: attacker, 2: defender}, {}, 1, AlwaysProc(), {})
                hits = []
                original = runtime.damage
                def record(a, t, amount, damage_family='physical', **kwargs):
                    hits.append(amount)
                    return original(a, t, amount, damage_family, **kwargs)
                runtime.damage = record
                elemental(runtime, attacker, [defender], skill, family)
                self.assertEqual(hits[0], {4720: 2100, 4721: 1488, 4722: 3000, 4723: 2700, 4729: 1200, 4732: 1200}[skill])
                if skill == 4720:
                    attacker['mag'] = 500
                    defender.update(hp=300000, hpmax=300000)
                    elemental(runtime, attacker, [defender], skill, family)
                    self.assertEqual(hits[-1], 1200 + 20000)
                if skill == 4723:
                    self.assertEqual(len(hits), 2)
                if skill == 4729:
                    self.assertLess(defender['ImmAbsorbDegree'], 0)

    def test_battle_start_shield_is_applied(self):
        sid = self.s.acquire(3364, 'innate:shield')
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET fight=0 WHERE character_id=?', (self.p.cid,))
            db.execute('UPDATE summons SET fight=1 WHERE id=?', (sid,))
        self.g.begin_battle()
        from native946_role_battle import Runtime, solo_fighters
        from random import Random
        runtime = Runtime(solo_fighters(self.g), {self.g.character['id']: self.g}, 1, Random(1), self.g.battle)
        self.assertEqual(runtime.fighters[sid].get('role_shields', {}).get('element'), 1)


if __name__ == '__main__':
    unittest.main()
