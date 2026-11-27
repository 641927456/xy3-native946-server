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
        before = hero['mp']
        runtime.cast(hero,skill,enemy['id'])
        self.assertLess(hero['mp'],before)
        self.assertEqual(hero['mp'],before-cost)
        self.assertIn(nums(2158,hero['id'],4,hero['mp']),runtime.out)
        self.assertNotIn(nums(2158,hero['id'],1,hero['hp']),runtime.out)
        self.assertFalse({2148,3458} & {actions.mid(p) for p in runtime.out})

    def test_demon_defense_and_attack_spells_apply_their_own_effects(self):
        runtime, fighters = self.runtime()
        hero = fighters[self.p.cid]
        hero.update(race=2, sex=2, skills={81: 1500, 91: 1500}, mp=100, mpmax=100)
        original_attack, original_defense = hero['attack'], hero['defense']

        self.assertTrue(all(profile(sid)['family'] == 'guard_buff' for sid in range(81, 86)))
        self.assertTrue(all(profile(sid)['family'] == 'attack_buff' for sid in range(91, 96)))
        validate_order(hero, 81, hero['id'], fighters)
        runtime.cast(hero, 81, hero['id'])
        self.assertEqual(hero['attack'], original_attack)
        self.assertGreater(hero['defense'], original_defense)
        self.assertEqual(hero.get('role_attack_buff', 0), 0)

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
        for sid in range(900010, 900015):
            fighters[sid] = dict(enemy, id=sid)

        for sid, samples in (
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
                    self.assertIn(f'最多目标：#G{expected}#n', description(sid, hero))

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
