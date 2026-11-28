"""Actor lifecycle and recovery presentation must agree with battle state."""
from copy import deepcopy
from random import Random
from pathlib import Path
import ast
import unittest
from unittest.mock import patch
from native946_codec import number,packet
from native946_role_battle import InsufficientHeroMana,Runtime,description,mana_cost,nums,profile,solo_fighters,validate_order
from native946_gameplay import ENEMY_ID
from native946_login import MessageReader
import test_native946_battle_actions as actions


class BattlePresentationTests(unittest.TestCase):
    setUp=actions.BattleActionTests.setUp

    def runtime(self):
        self.g.begin_battle();fighters=solo_fighters(self.g)
        return Runtime(fighters,{self.p.cid:self.g},1,Random(1),self.g.battle),fighters

    def test_training_stick_slots_follow_the_saved_school_at_entry_and_death(self):
        from native946_role_systems import save

        for school, show_slots in ((0, False), (18, False), (19, True), (119, True),
                                   (219, True), (319, True), (719, True)):
            with self.subTest(school=school):
                with self.p.transaction() as db:
                    save(db, self.p.cid, 'school', {'school': school})
                entry = self.g.begin_battle()
                expected = [nums(4830, self.p.cid, 0, 2)] if show_slots else []
                self.assertEqual([p for p in entry if actions.mid(p) == 4830], expected)
                self.assertTrue(any(actions.mid(p) == 3458 for p in entry))

                fighters = solo_fighters(self.g)
                runtime = Runtime(fighters, {self.p.cid: self.g}, 1, Random(1), self.g.battle)
                hero = fighters[self.p.cid]
                hero.update(hp=0, role_stick=1)
                runtime.kill(fighters[ENEMY_ID], hero)
                self.assertEqual([p for p in runtime.out if actions.mid(p) == 4830], expected)
                self.g.battle = None

    def test_two_idle_training_rounds_do_not_refresh_power_or_hero_attributes(self):
        self.p.set_vitals(150,50)
        self.g.begin_battle()
        solo_fighters(self.g)[ENEMY_ID]['controls'] = {'seal':2}
        for round_number in (1,2):
            self.assertEqual(self.g.battle['round'],round_number)
            self.g.receive(2178,MessageReader(number(1)))
            replies = self.g.receive(2182,MessageReader(number(1)))
            self.assertEqual(actions.mid(replies[-1]),2139)
            self.assertFalse({694,695,696,2148,2158,3458} & {actions.mid(p) for p in replies})
            self.assertEqual((self.g.battle['hp'],self.g.battle['mp']),(150,50))
            self.g.receive(2184,MessageReader(b''))

    def test_battle_hit_and_cast_update_only_the_changed_power_fields(self):
        runtime,fighters = self.runtime()
        hero,enemy = fighters[self.p.cid],fighters[ENEMY_ID]
        runtime.damage(enemy,hero,5)
        self.assertEqual([p for p in runtime.out if actions.mid(p) in (2148,2158,3458)],
                         [nums(2158,hero['id'],1,hero['hp']),nums(2158,hero['id'],7,hero['role_wrath'])])

        runtime.out.clear()
        skill = 6100
        cost = mana_cost(hero,profile(skill))
        before, before_anger = hero['mp'], hero['role_anger']
        runtime.cast(hero,skill,enemy['id'])
        self.assertLess(hero['mp'],before)
        self.assertEqual(hero['mp'],before-cost)
        mana = nums(2158,hero['id'],4,hero['mp'])
        self.assertEqual(runtime.out.count(mana), 1)
        self.assertLess(runtime.out.index(mana),runtime.out.index(nums(2126,1)))
        self.assertGreater(hero['role_anger'], before_anger)
        self.assertEqual([p for p in runtime.out if p.startswith(nums(2158,hero['id'],8))],
                         [nums(2158,hero['id'],8,hero['role_anger'])])
        self.assertLess(runtime.out.index(nums(2126,2)),
                        runtime.out.index(nums(2158,hero['id'],8,hero['role_anger'])))
        self.assertNotIn(nums(2158,hero['id'],1,hero['hp']),runtime.out)
        self.assertFalse({2148,3458} & {actions.mid(p) for p in runtime.out})

    def test_wrath_starts_at_250_and_scales_with_actual_damage(self):
        entry = self.g.begin_battle()
        self.assertIn(nums(3458, self.p.cid, 250, 1000), entry)
        fighters = solo_fighters(self.g)
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        hero.update(hp=1000, hpmax=1000, hp_limit=1000)
        runtime = Runtime(fighters, {self.p.cid: self.g}, 1, Random(1), self.g.battle)

        runtime.damage(hero, enemy, 5)
        runtime.damage(hero, hero, 5)
        runtime.damage(enemy, hero, 0)
        self.assertEqual(hero['role_wrath'], 250)
        self.assertFalse(any(p.startswith(nums(2158, hero['id'], 7)) for p in runtime.out))

        runtime.defending.add(hero['id'])
        self.assertEqual(runtime.damage(enemy, hero, 80), 40)
        self.assertIn(nums(2158, hero['id'], 7, 290), runtime.out)
        self.assertEqual(runtime.damage(enemy, hero, 120, 'fire', primary=False), 120)
        self.assertEqual(hero['role_wrath'], 410)
        self.assertIn(nums(2158, hero['id'], 7, 410), runtime.out)

        hero['hpmax'] = 2000
        self.assertEqual(runtime.damage(enemy, hero, 120, 'fire'), 120)
        self.assertEqual(hero['role_wrath'], 470)
        runtime.out.clear()
        self.assertEqual(runtime.damage(enemy, hero, 1, 'fire'), 1)
        self.assertFalse(any(p.startswith(nums(2158, hero['id'], 7)) for p in runtime.out))

        hero['role_wrath'] = 999
        runtime.damage(enemy, hero, 5)
        self.assertEqual(hero['role_wrath'], 1000)
        self.assertIn(nums(2158, hero['id'], 7, 1000), runtime.out)
        self.g.battle = None
        self.assertIn(nums(3458, self.p.cid, 250, 1000), self.g.begin_battle())

    def test_wrath_includes_counterattacks_gushu_and_poison_ticks(self):
        runtime, fighters = self.runtime()
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        hero.update(hp=1000, hpmax=1000, hp_limit=1000)
        hero['role_dots'] = [dict(source=enemy['id'], skill=1, kind='poison', damage=25, until=2)]
        runtime.begin_round()
        self.assertEqual(hero['role_wrath'], 275)
        hero['role_gushu'] = dict(source=enemy['id'], cast_damage=40, attack_damage=20, until=2)
        runtime.action_gushu(hero, True)
        self.assertEqual(hero['role_wrath'], 315)
        with patch('native946_book_combat.roll', return_value={'miss': False, 'damage': 60, 'effect': 0}):
            runtime.attack(enemy, hero, active=False)
        self.assertEqual(hero['role_wrath'], 375)
        self.assertIn(nums(2158, hero['id'], 7, 375), runtime.out)

    def test_liuli_lamp_native_equip_cast_and_next_two_order_resolutions(self):
        from native946_role_systems import save
        from test_native946_guardian_passives import cast_ids
        from test_native946_skillbooks import decode_record

        cid = self.p.cid
        def call(mid,*values):
            return self.g.receive(mid, MessageReader(b''.join(number(value) for value in values)))

        with self.p.transaction() as db:
            iid = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,47800,1700,1,1)',
                             (cid,)).lastrowid
            save(db,cid,'artifacts',dict(item_progress={str(iid):dict(grade=50,generation=3)}))
        self.assertNotIn(4709, self.p.snapshot()['skills'])
        reply = call(313,iid)
        detail = next(p for p in reply if actions.mid(p)==3007)
        reader = MessageReader(detail[3:])
        self.assertEqual(reader.integer(), iid)
        info = decode_record(reader,'fabao_new_info')
        reader.finish()
        self.assertEqual((info['level'], info['grade']), (3, 50))
        self.assertIn('100点怨气',info['desc'])

        call(803,1700,1,34,4)
        self.assertEqual(self.p.snapshot()['skills'][4709], 50)
        entry = self.g.begin_battle()
        self.assertIn(self.p.combat_skills_packet(),entry)
        self.assertIn(dict(skill_id=4709,cost='无消耗',des=info['desc']),self.p.combat_skills())
        actors = solo_fighters(self.g)
        hero = actors[cid]
        actors[ENEMY_ID]['controls'] = {'seal': 6}
        hero['role_wrath'] = 0
        reply = call(2185,9,4709,ENEMY_ID)
        self.assertIn(98,[actions.mid(p) for p in reply])
        self.assertNotIn(cid,self.g.battle['orders'])
        call(2185,9,4709,cid)
        resolved = call(2182,1)
        self.assertIn((cid,cid,4709),cast_ids(resolved))
        self.assertEqual(solo_fighters(self.g)[cid]['role_wrath'], 0)
        for round_number, expected in ((2,100),(3,200),(4,200)):
            call(2184)
            self.assertEqual(self.g.battle['round'],round_number)
            before = solo_fighters(self.g)[cid]['role_wrath']
            call(2178,1)
            self.assertEqual(solo_fighters(self.g)[cid]['role_wrath'],before)
            resolved = call(2182,1)
            self.assertEqual(solo_fighters(self.g)[cid]['role_wrath'],expected)
            self.assertEqual(resolved.count(nums(2158,cid,7,expected)), int(round_number<4))
        self.g.finish_battle(False)
        call(803,34,4,1700,-1)
        self.assertNotIn(4709,self.p.snapshot()['skills'])
        self.g.begin_battle()
        reply = call(2185,9,4709,cid)
        self.assertIn(98,[actions.mid(p) for p in reply])
        self.assertNotIn(cid,self.g.battle['orders'])

    def test_max_rank_focus_cost_and_pvp_round_recovery(self):
        runtime, fighters = self.runtime()
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        skill = profile(6100)
        base = mana_cost(hero, skill)
        hero.update(mp=20, mpmax=1000, role_talents={'7033': 10})
        self.assertEqual(mana_cost(hero, skill), base * 80 // 100)
        self.assertEqual(validate_order(hero, 6100, enemy['id'], fighters)['mana'], base * 80 // 100)
        runtime.begin_round()
        self.assertEqual(hero['mp'], 20)
        enemy.update(owner=998, invisible=True)
        runtime.round += 1
        runtime.begin_round()
        runtime.begin_round()
        self.assertEqual(hero['mp'], 70)
        self.assertIn(nums(2158, hero['id'], 4, 70), runtime.out)

    def test_max_rank_shock_talents_use_current_hp_actual_damage_and_heal_cap(self):
        runtime, fighters = self.runtime()
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        enemy.update(hp=1000, hpmax=2000, hp_limit=2000, ReMpAbsorb=1000)
        hero['role_talents'] = {'7341': 10}
        self.assertEqual(runtime.shock(hero, enemy), 0)
        enemy.update(owner=998, role_all_reduction=700)
        self.assertEqual(runtime.shock(hero, enemy), 100)

        hero.update(hp=30, hpmax=300, hp_limit=300, role_talents={'7342': 10})
        enemy.update(hp=1000, ReMpAbsorb=0, role_all_reduction=0)
        self.assertEqual(runtime.shock(hero, enemy, primary=False), 200)
        self.assertEqual(hero['hp'], 30)
        enemy.update(hp=1000)
        self.assertEqual(runtime.shock(hero, enemy), 200)
        self.assertEqual(hero['hp'], 60)
        enemy.update(hp=4000, hpmax=4000, hp_limit=4000)
        self.assertEqual(runtime.shock(hero, enemy), 800)
        self.assertEqual(hero['hp'], 150)

    def test_max_rank_deep_sleep_checks_final_damage_against_target_max_hp(self):
        runtime, fighters = self.runtime()
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        hero['role_talents'] = {'7122': 10}
        enemy.update(owner=998, hp=2000, hpmax=2000, hp_limit=2000)
        self.assertTrue(runtime.control(hero, enemy, 'faint'))
        self.assertEqual(runtime.damage(hero, enemy, 199, 'fire'), 199)
        self.assertIn('faint', enemy['controls'])
        self.assertEqual(runtime.damage(hero, enemy, 200, 'fire'), 200)
        self.assertNotIn('faint', enemy['controls'])

    def test_max_rank_sleep_damage_modifiers_use_their_own_durations(self):
        for skill, key, amount, rounds in ((7123, 'role_damage_percent', -300, 2),
                                            (7124, 'role_taken_percent', 450, 1)):
            with self.subTest(skill=skill):
                runtime, fighters = self.runtime()
                hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
                hero['role_talents'] = {str(skill): 10}
                self.assertTrue(runtime.control(hero, enemy, 'faint', duration=4))
                self.assertEqual(enemy[key], amount)
                runtime.round += rounds
                runtime.begin_round()
                self.assertEqual(enemy[key], 0)
                self.g.battle = None

    def test_max_rank_battle_staff_capacity_has_no_probability_roll(self):
        from native946_role_battle import fighter_entry

        runtime, fighters = self.runtime()
        hero = fighters[self.p.cid]
        hero.update(role_school=719, role_talents={'7503': 10}, role_initialized=0)
        with patch.object(Random, 'randrange', side_effect=AssertionError('满级斗战应必定触发')):
            fighter_entry(hero, '斗战')
            self.assertEqual(hero['role_stick_max'], 3)
            hero['role_initialized'] = 0
            runtime.prepare(hero)
        self.assertEqual(hero['role_stick_max'], 3)

    def test_equipment_talents_calculate_each_native_attribute_separately(self):
        from native946_role_growth import boost_equipment

        for talent, expected_core, expected_direct in (
                (7010, [120, 120, 110, 110], [120, 120, 110, 110, 100]),
                (7013, [110, 110, 120, 115], [110, 110, 120, 115, 100])):
            with self.subTest(talent=talent):
                core = dict.fromkeys(('cor', 'mag', 'strength', 'speed'), 100)
                direct = dict.fromkeys(('hpmax', 'mpmax', 'attack', 'initiative', 'defense'), 100)
                with patch('native946_role_growth.talent_levels', return_value={talent: 10}):
                    boost_equipment(None, self.p.cid, core, direct, {})
                self.assertEqual(list(core.values()), expected_core)
                self.assertEqual(list(direct.values()), expected_direct)

    def test_xinyi_follow_up_uses_trained_skill_and_original_speed(self):
        from native946_skillbooks import SkillBooks
        from test_native946_guardian_passives import cast_ids

        with self.p.transaction() as db:
            db.execute('UPDATE summons SET type=3371,level=50 WHERE id=?', (self.sid,))
        self.g.receive(763, MessageReader(number(self.sid) + number(4749)))
        skill = next(row for row in SkillBooks(self.s).records(self.sid) if row['stunt_id'] == 4749)
        self.assertEqual(skill['desc'], '{5,900,250}')
        runtime, fighters = self.runtime()
        hero, pet, enemy = fighters[self.p.cid], fighters[self.sid], fighters[ENEMY_ID]
        enemy.update(owner=998, hp=10000, hpmax=10000, hp_limit=10000)
        speed = pet['role_initial_speed']
        self.assertGreater(speed, 0)
        pet['initiative'] += 500
        for offset in range(1, 6):
            victim = deepcopy(enemy)
            victim['id'] = enemy['id'] + offset
            fighters[victim['id']] = victim
            runtime.prepare(victim)
        pet.update(mp=1000, mpmax=1000)
        with patch('native946_book_combat.roll', return_value={'miss': True, 'damage': 0, 'effect': 0}):
            runtime.attack(pet, enemy)
            self.assertEqual(enemy['hp'], 10000 - speed * 9)
            self.assertEqual(sum(f['hp'] < 10000 for f in runtime.enemies(pet)), 5)
            self.assertEqual(hero['role_wrath'], 500)
            runtime.attack(pet, enemy)
            self.assertEqual(enemy['hp'], 10000 - speed * 9 - (speed * 9 // 2))
            self.assertEqual(hero['role_wrath'], 750)
            pet['controls']['disorder'] = runtime.round
            runtime.attack(pet, enemy)
            self.assertEqual(hero['role_wrath'], 750)
            pet['controls'].clear()
            runtime.attack(pet, enemy, active=False)
            self.assertEqual(hero['role_wrath'], 750)
        runtime.round += 1
        before = enemy['hp']
        pet['innate_skills'].append(dict(stunt_id=321))
        runtime.guardian_cast(self.g, pet, hero['id'], 321)
        self.assertEqual(before - enemy['hp'], speed * 9)
        self.assertEqual(hero['role_wrath'], 1000)
        self.assertEqual([skill for a, t, skill in cast_ids(runtime.out) if a == pet['id']].count(4749), 3)
        for bound in (500, 750, 1000):
            idx = runtime.out.index(nums(2158, hero['id'], 7, bound))
            self.assertEqual(runtime.out[idx-1], nums(2126, 2))

    def test_guardian_book_and_innate_costs_sync_before_the_cast_group(self):
        from native946_guardian_magic import prepared

        for skill, innate in ((338, False), (317, True), (321, True)):
            with self.subTest(skill=skill):
                runtime, fighters = self.runtime()
                guardian = fighters[self.sid]
                hero = fighters[self.p.cid]
                guardian.update(mp=1000, mpmax=1000, learned_books=[], innate_skills=[])
                if innate:
                    guardian['innate_skills'].append(dict(stunt_id=skill))
                else:
                    guardian['learned_books'].append(dict(skill=skill, level=20))
                restoring = skill == 321
                if restoring:
                    hero['mp'] = max(0, hero['mpmax'] - 100)
                cost = prepared(guardian, skill)['mana']
                transferred = min(100, hero['mpmax'] - hero['mp']) if restoring else 0
                runtime.guardian_cast(self.g, guardian, hero['id'] if restoring else ENEMY_ID, skill)
                self.assertEqual(guardian['mp'], 1000 - cost - transferred)
                sync = nums(2158, guardian['id'], 4, guardian['mp'])
                self.assertEqual(runtime.out.count(sync), 1)
                self.assertLess(runtime.out.index(sync), runtime.out.index(nums(2126, 1)))
                if restoring:
                    self.assertIn(nums(2164, hero['id'], transferred, 2, 0), runtime.out)
                else:
                    self.assertTrue(any(actions.mid(p) == 2164 for p in runtime.out))

    def test_target_mana_drain_stays_in_the_effect_group(self):
        runtime, fighters = self.runtime()
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        runtime.group(enemy, [hero], 12)
        lost = min(5, hero['mp'])
        runtime.mana(hero, -lost)
        runtime.end()
        self.assertGreater(lost, 0)
        self.assertEqual(runtime.out[-3:], [nums(2164, hero['id'], -lost, 2, 0),
                         nums(2158, hero['id'], 4, hero['mp']), nums(2126, 2)])

    def test_demon_defense_and_attack_spells_apply_their_own_effects(self):
        from native946_role_battle import value
        from native946_spell_preview import guard_human_resistance

        runtime, fighters = self.runtime()
        hero = fighters[self.p.cid]
        hero.update(race=2, sex=2, skills={81: 1500, 91: 1500}, mp=100, mpmax=100)
        original_attack, original_defense = hero['attack'], hero['defense']
        resistances={key:value(hero,key) for key in ('ReSeal','ReDisorder','ReFaint')}
        expected_resist=guard_human_resistance(hero,81)

        self.assertTrue(all(profile(sid)['family'] == 'guard_buff' for sid in range(81, 86)))
        self.assertTrue(all(profile(sid)['family'] == 'attack_buff' for sid in range(91, 96)))
        validate_order(hero, 81, hero['id'], fighters)
        runtime.cast(hero, 81, hero['id'])
        self.assertEqual(hero['attack'], original_attack)
        self.assertGreater(hero['defense'], original_defense)
        self.assertEqual(hero.get('role_attack_buff', 0), 0)
        self.assertIn(f'各提高#G{expected_resist}#n点',description(81,hero))
        for key,previous in resistances.items():
            self.assertEqual(value(hero,key),previous+expected_resist)

        buffed_defense = hero['defense']
        hero['sex'] = 1
        with self.assertRaisesRegex(ValueError, '不属于当前人物'):
            validate_order(hero, 81, hero['id'], fighters)
        validate_order(hero, 91, hero['id'], fighters)
        runtime.cast(hero, 91, hero['id'])
        self.assertGreater(hero['attack'], original_attack)
        self.assertEqual(hero['defense'], buffed_defense)

    def test_newbie_fixed_damage_hits_primary_and_four_visible_enemies(self):
        runtime, fighters = self.runtime()
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        skill = 4592
        hero.update(level=72, mp=419, mpmax=1000, mag=8000, attack=8000,
                    role_talents={'7232': 20}, XQAddHurtHpP=700, skills={})
        with self.assertRaisesRegex(ValueError, '尚未学会此技能'):
            validate_order(hero, skill, ENEMY_ID, fighters)
        hero['skills'][skill] = 9999
        with self.assertRaises(InsufficientHeroMana):
            validate_order(hero, skill, ENEMY_ID, fighters)
        self.assertFalse(runtime.cast(hero, skill, ENEMY_ID))
        self.assertEqual(hero['mp'], 419)
        self.assertEqual(runtime.out, [])
        hero['mp'] = 1000
        with self.assertRaisesRegex(ValueError, '请选择敌方目标'):
            validate_order(hero, skill, hero['id'], fighters)

        enemy.update(hp=3000, hpmax=3000, defense=9000, XQSubHurtHpP=500)
        for i, fid in enumerate(range(ENEMY_ID + 1, ENEMY_ID + 9)):
            fighters[fid] = dict(deepcopy(enemy), id=fid, hp=0 if i == 6 else 3000,
                                 invisible=i == 5, camp=hero['camp'] if i == 7 else enemy['camp'])
            runtime.prepare(fighters[fid])
        primary = ENEMY_ID + 5
        hit = [primary, ENEMY_ID, *range(ENEMY_ID + 1, ENEMY_ID + 4)]
        before = {fid: f['hp'] for fid, f in fighters.items()}
        runtime.defending.update(hit)
        self.assertEqual(validate_order(hero, skill, primary, fighters)['mana'], 420)
        runtime.cast(hero, skill, primary)
        self.assertEqual(hero['mp'], 580)
        self.assertEqual([p for p in runtime.out if actions.mid(p) == 2164],
                         [nums(2164, fid, -1937, 1, 0) for fid in hit])
        self.assertEqual({fid: before[fid] - f['hp'] for fid, f in fighters.items()},
                         {fid: 1937 if fid in hit else 0 for fid in fighters})

    def test_yuying_cast_modifies_the_absorption_used_by_damage_and_expires(self):
        from native946_role_battle import value

        runtime,fighters=self.runtime()
        hero,target=fighters[self.p.cid],fighters[ENEMY_ID]
        skill=6127
        hero.update(race=7,school=17,skills={skill:1},mp=200,mpmax=200)
        target.update(hp=5000,hpmax=5000,hp_limit=5000,PhyAbsorbDegree=800)
        defense=target['defense']
        with self.assertRaisesRegex(ValueError,'对应门派'):
            validate_order(hero,skill,ENEMY_ID,fighters)
        hero['school']=18
        validate_order(hero,skill,ENEMY_ID,fighters)
        self.assertEqual(runtime.damage(hero,target,1000),200)
        for _ in range(2):
            runtime.cast(hero,skill,ENEMY_ID)
            self.assertEqual(value(target,'PhyAbsorbDegree'),450)
            self.assertEqual(target['defense'],defense+700)
            self.assertEqual(target['role_heal_reduce'],250)
        self.assertEqual(runtime.damage(hero,target,1000),550)
        runtime.round=4
        runtime.begin_round()
        self.assertEqual(value(target,'PhyAbsorbDegree'),800)
        self.assertEqual(target['defense'],defense)
        self.assertEqual(target['role_heal_reduce'],0)
        tip=description(skill,hero)
        self.assertIn('本机结算预览',tip)
        self.assertIn('最多降低#G35#n个百分点',tip)

    def test_school_catalog_tooltips_use_available_values_or_explain_dynamic_effects(self):
        from native946_school import SCHOOL_SKILLS

        for sid in SCHOOL_SKILLS:
            with self.subTest(skill=sid):
                tip=description(sid,dict(level=71,attack=781,skills={sid:1},hpmax=400,mpmax=200,hp=200))
                self.assertTrue(tip.strip())
                self.assertNotIn('表示该数值待核实',tip)

    def test_xuelong_heal_respects_client_cap_after_advanced_modifiers(self):
        runtime,fighters=self.runtime()
        hero=fighters[self.p.cid]
        skill=6124
        hero.update(race=7,sex=1,school=15,skills={skill:1},mp=200,mpmax=200,
                    hp=100,hpmax=200000,hp_limit=200000,role_feng={skill:dict(power=100)})
        validate_order(hero,skill,hero['id'],fighters)
        runtime.cast(hero,skill,hero['id'])
        self.assertEqual(hero['hp'],20100)
        self.assertIn('单次回血最多#R20000#n',description(skill,hero))

    def test_newbie_spell_panel_and_battle_match_947_tianjie_samples(self):
        runtime, fighters = self.runtime()
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        state = self.p.snapshot()
        skill = 4592
        for grade, base, mana, damage in ((13, 381, 125, 533), (16, 432, 140, 604),
                                          (19, 483, 155, 676), (20, 500, 160, 700)):
            with self.subTest(grade=grade):
                state['level'] = grade
                text = description(skill, state)
                self.assertIn(f'#G{base}#n点固定气血伤害', text)
                self.assertIn(f'结算伤害：#G{damage}#n', text)
                self.assertEqual(mana_cost(state, profile(skill)), mana)
                self.assertEqual(self.p.skill_damage(skill, state), damage)
                hero.update(level=grade, mp=500, mpmax=500)
                enemy.update(hp=2000, hpmax=2000)
                runtime.out.clear()
                runtime.cast(hero, skill, ENEMY_ID)
                self.assertEqual(enemy['hp'], 2000 - damage)
                self.assertEqual(hero['mp'], 500 - mana)
                self.assertIn(nums(2164, ENEMY_ID, -damage, 1, 0), runtime.out)

    def test_verified_school_target_thresholds_are_shared_by_preview_and_cast(self):
        runtime, fighters = self.runtime()
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        for sid in range(900010, 900016):
            fighters[sid] = dict(enemy, id=sid)

        for sid, samples in (
            (35, ((999, 4), (1000, 5), (4999, 5), (5000, 6), (11000, 7))),
            (6102, ((2999, 3), (3000, 4), (10499, 4), (10500, 5))),
            (6104, ((0, 4), (2999, 4), (3000, 5))),
            (6109, ((999, 2), (1000, 3), (8999, 3), (9000, 4))),
            (6111, ((987, 3), (1000, 4), (4999, 4), (5000, 5))),
        ):
            for practice, expected in samples:
                with self.subTest(skill=sid, practice=practice):
                    hero['skills'] = {str(sid): practice}
                    selected, nominal = runtime.targets(hero, profile(sid), enemy['id'])
                    self.assertEqual(nominal, expected)
                    self.assertEqual(len(selected), expected)
                    self.assertEqual(selected[0]['id'], enemy['id'])
                    self.assertIn(f'{expected}人', description(sid, hero))

    def test_witch_preview_uses_the_cast_healing_cap_and_two_gushu_values(self):
        from native946_role_battle import gushu_damage

        runtime, fighters = self.runtime()
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        hero.update(level=71, race=7, sex=1, school=15, hp=5000, hpmax=100000, hp_limit=100000,
                    mp=5000, mpmax=5000, skills={6120:0}, XQAddCureHpP=1000)
        self.assertIn('回复队友#G19.26％#n的血量', description(6120, hero))
        runtime.cast(hero, 6120, hero['id'])
        self.assertEqual(hero['hp'], 25000)

        hero.update(school=0, sex=2, skills={6129:0})
        attack, cast = gushu_damage(hero, enemy, 6129)
        preview = description(6129, hero)
        for amount in gushu_damage(hero, {}, 6129):
            self.assertIn(f'#G{amount}#n点伤害', preview)
        runtime.cast(hero, 6129, enemy['id'])
        self.assertEqual((enemy['role_gushu']['attack_damage'], enemy['role_gushu']['cast_damage']),
                         (attack, cast))
        self.assertEqual(enemy['role_gushu']['until'], runtime.round + 1)

    def test_feixue_wuhen_uses_native_chance_targets_and_cooldown(self):
        runtime, fighters = self.runtime()
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        hero.update(mp=5000, mpmax=5000)
        rivals = []
        for actor_id in range(900010, 900014):
            rival = dict(enemy, id=actor_id, kind='hero', owner=actor_id, role_last_spell=41)
            fighters[actor_id] = rival
            rivals.append(rival)
        skill = 6139
        text = description(skill, hero)
        for expected in ('#G60.00％#n', '#G2#n个单位', '持续#G2#n回合', '需#Y3#n回合'):
            self.assertIn(expected, text)
        self.assertEqual(runtime.targets(hero, profile(skill), rivals[0]['id']), (rivals[:2], 2))
        with patch.object(runtime.rng, 'randrange', side_effect=(599, 600)):
            runtime.cast(hero, skill, rivals[0]['id'])
        self.assertEqual(rivals[0]['role_forbidden'], {'41': 3})
        self.assertNotIn('41', rivals[1].get('role_forbidden', {}))
        self.assertNotIn('41', enemy.get('role_forbidden', {}))
        self.assertEqual(hero['role_cooldowns']['skill:6139'], 4)
        mana = hero['mp']
        runtime.round = 3
        runtime.cast(hero, skill, rivals[0]['id'])
        self.assertEqual(hero['mp'], mana)
        hero['role_talents'] = {'7423': 10}
        self.assertIn('#G3#n个单位', description(skill, hero))
        self.assertEqual(runtime.targets(hero, profile(skill), rivals[0]['id']), (rivals[:3], 3))
        runtime.round = 4
        with patch.object(runtime.rng, 'randrange', return_value=999):
            runtime.cast(hero, skill, rivals[0]['id'])
        self.assertLess(hero['mp'], mana)
        self.assertEqual(hero['role_cooldowns']['skill:6139'], 7)

    def test_gushu_rounds_each_action_damage_and_skips_cleanse(self):
        from native946_role_battle import gushu_damage

        source = {'level': 1, 'mag': 221, 'skills': {}}
        self.assertEqual(gushu_damage(source, {'XQSubHurtHpP': 125}, 6129), (481, 687))
        source['role_talents'] = {'7421': 10}
        self.assertEqual(gushu_damage(source, {}, 6129), (715, 1021))
        self.assertEqual(gushu_damage(source, {}, 6129, pvp=True), (632, 902))

        runtime, fighters = self.runtime()
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        hero.update(hp=100, hpmax=100, mp=500, mpmax=500)
        effect = dict(source=enemy['id'], attack_damage=50, cast_damage=90, until=2)
        hero['role_gushu'] = effect.copy()
        with patch.object(runtime.rng, 'randrange', return_value=0):
            runtime.cast(hero, 6107, hero['id'])
        self.assertEqual(hero['hp'], 100)
        self.assertNotIn('role_gushu', hero)
        hero['role_gushu'] = effect.copy()
        runtime.cast(hero, 6038, hero['id'])
        self.assertEqual(hero['hp'], 10)
        self.assertEqual(hero['role_last_spell'], 6038)

    def test_published_max_rank_witch_talents_use_their_cast_conditions(self):
        runtime, fighters = self.runtime()
        hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
        hero.update(hp=100, hpmax=1000, hp_limit=1000, mp=5000, mpmax=5000,
                    race=7, school=17, AddXiaorongRate=20, role_talents={'7414': 10})
        with patch.object(runtime.rng, 'randrange', return_value=0):
            runtime.cast(hero, 6107, hero['id'])
        self.assertEqual(hero['hp'], 700)
        hero.update(AddXiaorongRate=300, role_talents={'7412': 10, '7431': 10})
        with patch.object(runtime.rng, 'randrange', return_value=999):
            runtime.cast(hero, 6038, hero['id'])
        self.assertEqual(hero['role_element_power'], 240)
        self.assertTrue(hero['role_reveal'])

        hero['role_talents'] = {'7411': 10}
        runtime.cast(hero, 6140, hero['id'])
        self.assertEqual(hero['role_anti_books'], 300)
        self.assertEqual(hero['role_reveal'], 200)
        hero['role_talents'] = {'7432': 10}
        with patch.object(runtime.rng, 'randrange', return_value=999):
            runtime.cast(hero, 6120, hero['id'])
        self.assertEqual(hero['role_shields']['endure'], 1)

        hero['role_talents'] = {'7422': 10}
        with patch.object(runtime.rng, 'randrange', return_value=0):
            runtime.cast(hero, 6129, enemy['id'])
        self.assertEqual(enemy['role_element_taken'], 150)
        self.assertFalse(enemy.get('role_spell_taken'))
        self.assertEqual(next(b['until'] for b in enemy['role_buffs'] if b['source'] == 7422), 2)

    def test_defence_applies_to_each_physical_hit_and_water_limit_uses_final_damage(self):
        runtime,fighters=self.runtime()
        hero,enemy=fighters[self.p.cid],fighters[ENEMY_ID]
        hero.update(hp=1000,hpmax=1000,role_all_reduction=200)
        runtime.defending.add(hero['id'])

        for family in ('physical','wuli','pierce'):
            self.assertEqual(runtime.damage(enemy,hero,100,family),40)
        self.assertEqual(runtime.out.count(nums(2141,hero['id'])),3)
        self.assertEqual(runtime.damage(enemy,hero,100,'fire'),80)
        self.assertNotIn('hp_limit',hero)
        self.assertEqual(runtime.damage(enemy,hero,100,'water'),80)
        self.assertEqual(hero['hp_limit'],972)
        self.assertIn(nums(2158,hero['id'],2,972),runtime.out)
        self.assertEqual(self.g.battle_vitals_packet(hero),
                         nums(2148,hero['id'],hero['hp'],972,1000,hero['mp'],hero['mpmax'],hero['mpmax'],0,100,0))
        hero['role_shields']['element']=1
        self.assertEqual(runtime.damage(enemy,hero,100,'water'),0)
        self.assertEqual(hero['hp_limit'],972)

    def test_expired_hp_limit_buff_updates_affected_fighter_only(self):
        runtime,fighters = self.runtime()
        pet = fighters[self.sid]
        original_max = pet['hpmax']
        runtime.buff(pet,'hpmax',50,duration=1)
        pet['hp'] = pet['hpmax']
        subsequent = Runtime(fighters,{self.p.cid:self.g},2,Random(2),self.g.battle)
        subsequent.begin_round()
        self.assertEqual((pet['hp'],pet['hpmax']),(original_max,original_max))
        self.assertEqual([p for p in subsequent.out if actions.mid(p) in (2148,2158,3458)],
                         [nums(2158,pet['id'],3,original_max),nums(2158,pet['id'],2,original_max),
                          nums(2158,pet['id'],1,original_max)])

    def test_original_practice_scale_is_safe_in_battle_and_skill_preview(self):
        runtime,fighters = self.runtime()
        actor,target = fighters[self.p.cid],fighters[ENEMY_ID]
        snapshot = self.p.snapshot()
        for skill in (41,6100):
            values = []
            for practice in (5,25000):
                actor['skills'] = {skill:practice}
                values.append((runtime.base_damage(actor,target,profile(skill)),
                               self.p.skill_damage(skill,dict(snapshot,skills={skill:practice}))))
            with self.subTest(skill=skill):
                if skill == 41:
                    self.assertEqual(values[0],values[1])
                else:
                    self.assertGreater(values[1][0],values[0][0])
                    self.assertGreater(values[1][1],values[0][1])

    def test_reflection_death_follows_attacker_return_but_target_death_stays_at_impact(self):
        runtime,fighters=self.runtime();actor=fighters[self.sid];target=fighters[ENEMY_ID]
        actor.update(hp=5,attack=37,AttValue=1000,innate_skills=[],learned_books=[])
        target.update(hp=1000,hpmax=1000,role_reflect=1000)
        runtime.attack(actor,target)
        close=packet(2126,['number'],[2]);death=packet(2142,['number','number'],[actor['id'],2])
        self.assertEqual(actor['hp'],0);self.assertEqual(runtime.out.count(death),1)
        self.assertGreater(runtime.out.index(death),runtime.out.index(close))
        runtime.out=[];actor['hp']=100;target.update(hp=1,role_reflect=0)
        runtime.attack(actor,target)
        self.assertLess(runtime.out.index(packet(2142,['number','number'],[target['id'],3])),runtime.out.index(close))

    def test_repeat_and_secondary_hero_hits_preserve_the_native_number_queue(self):
        cases = ((36, [100, 100, 100], 2), (1702, [100, 100], 2),
                 (4552, [120, 120], 2), (6105, [100, 100], 2),
                 (6016, [100, 70], 2), (6100, [100], 3))
        for skill, amounts, death_mode in cases:
            for hp, expected in ((1, [1]), (sum(amounts), amounts)):
                with self.subTest(skill=skill, hp=hp):
                    runtime, fighters = self.runtime()
                    hero, enemy = fighters[self.p.cid], fighters[ENEMY_ID]
                    hero.update(mp=10000, role_anger=10, role_wrath=1000, role_talents={})
                    enemy.update(hp=hp, hpmax=hp, defense=0)
                    with patch.object(runtime, 'base_damage', return_value=100), \
                         patch.object(runtime.rng, 'randrange', return_value=0):
                        runtime.cast(hero, skill, ENEMY_ID)
                    self.assertEqual([p for p in runtime.out if p.startswith(nums(2164, ENEMY_ID))],
                                     [nums(2164, ENEMY_ID, -amount, 1, 0) for amount in expected])
                    self.assertEqual(enemy['hp'], 0)
                    self.assertTrue(enemy['removed'])
                    self.assertIn(nums(2142, ENEMY_ID, death_mode), runtime.out)
                    self.g.battle = None

    def test_same_action_revival_cancels_the_deferred_corpse(self):
        runtime,fighters=self.runtime();actor=fighters[self.p.cid];enemy=fighters[ENEMY_ID]
        runtime.group(actor,[enemy],10);actor['hp']=0;runtime.kill(enemy,actor)
        runtime.revive(enemy,actor,20);runtime.end()
        self.assertNotIn(packet(2142,['number','number'],[actor['id'],2]),runtime.out)
        self.assertEqual(actor['hp'],20)

    def test_reflected_lethal_damage_cancels_combo_and_remaining_counter_attacks(self):
        runtime,fighters=self.runtime();actor=fighters[self.sid];target=fighters[ENEMY_ID]
        actor.update(hp=5,attack=37,AttValue=1000,innate_skills=[],learned_books=[],RoleLineupCombo=1000)
        target.update(hp=1000,hpmax=1000,defense=0,role_reflect=1000)
        runtime.attack(actor,target)
        self.assertEqual((actor['hp'],target['hp']),(0,963))
        actor.update(hp=5,RoleLineupCombo=0,neidan=[dict(kind=233)]);target['hp']=1000
        with patch('native946_neidan_legacy.effect',return_value=dict(enabled=True,chance=100,counter_count=2)):
            runtime.pet_counter(actor,target,{})
        self.assertEqual((actor['hp'],target['hp']),(0,963))

    def test_revival_has_a_native_recovery_number_and_respects_the_hp_cap(self):
        runtime,fighters=self.runtime();actor=fighters[self.p.cid];target=fighters[self.sid];target['hp']=0
        runtime.revive(actor,target,target['hpmax']+100)
        self.assertIn(packet(2145,['number'],[target['id']]),runtime.out)
        self.assertIn(packet(2164,['number']*4,[target['id'],target['hpmax'],1,0]),runtime.out)

    def test_medicine_recovery_number_matches_actual_recovery_and_single_consumption(self):
        for kind,attr in [(4000,'hp'),(4001,'mp')]:
            runtime,fighters=self.runtime();actor=fighters[self.p.cid];actor[attr]=1
            item=next(i for i in self.p.snapshot()['items'] if i['type']==kind)
            before=item['amount']
            with self.p.transaction() as db:runtime.item(self.g,actor,dict(item_id=item['id'],target=actor['id']),db)
            actual=actor[attr]-1
            self.assertGreater(actual,0)
            self.assertIn(packet(2164,['number']*4,[actor['id'],actual,1 if attr=='hp' else 2,0]),runtime.out)
            self.assertTrue(any(p[:3]==packet(2165,[],[]) for p in runtime.out))
            self.assertEqual(next(i for i in self.p.snapshot()['items'] if i['id']==item['id'])['amount'],before-1)
            self.g.battle=None

    def test_mana_numbers_use_the_capped_actual_change_in_both_directions(self):
        runtime,fighters=self.runtime();actor=fighters[self.p.cid]
        actor['mp']=actor['mpmax']-2
        self.assertEqual(runtime.mana(actor,100),2)
        self.assertIn(packet(2164,['number']*4,[actor['id'],2,2,0]),runtime.out)
        runtime.out=[];self.assertEqual(runtime.mana(actor,-actor['mpmax']-100),-actor['mpmax'])
        self.assertIn(packet(2164,['number']*4,[actor['id'],-actor['mpmax'],2,0]),runtime.out)
        runtime.out=[];self.assertEqual(runtime.mana(actor,-1),0)
        self.assertFalse(any(p[:3]==packet(2164,[],[]) for p in runtime.out))

    def test_shared_escape_while_waiting_for_input_uses_force_end(self):
        from native946_wilderness import WildBattle,DATA
        battle=WildBattle(self.g,[self.g],DATA['zones'][0]);self.g.party_service.drain(self.g)
        battle.finish(False);messages=self.g.party_service.drain(self.g)
        self.assertIn(packet(2146,[],[]),messages);self.assertNotIn(packet(2170,[],[]),messages)
        self.assertIsNone(self.g.battle)

    def test_completed_animation_exit_also_uses_the_self_starting_native_end(self):
        self.g.begin_battle();messages=self.g.finish_battle(False,completed=True)
        self.assertIn(packet(2146,[],[]),messages);self.assertNotIn(packet(2170,[],[]),messages)

    def test_number_packet_emitters_use_native_number_types_instead_of_second_deltas(self):
        def valid(node):
            return (isinstance(node,ast.Constant) and node.value in (1,2,3,7,8) or
                    isinstance(node,ast.IfExp) and valid(node.body) and valid(node.orelse))
        checked=0
        for name in ['native946_role_battle.py','native946_gameplay.py','native946_guardian_magic.py']:
            tree=ast.parse(Path(__file__).with_name(name).read_text('utf8'))
            for node in ast.walk(tree):
                if not isinstance(node,ast.Call) or not node.args or not isinstance(node.args[0],ast.Constant) or node.args[0].value!=2164:continue
                if isinstance(node.func,ast.Name) and node.func.id in ('nums','numbers'):kind=node.args[3]
                elif isinstance(node.func,ast.Name) and node.func.id=='packet':kind=node.args[2].elts[2]
                else:continue
                self.assertTrue(valid(kind),f'{name}:{node.lineno}: native field 3 is the number type')
                checked+=1
        self.assertGreaterEqual(checked,9)


if __name__=='__main__':unittest.main()
