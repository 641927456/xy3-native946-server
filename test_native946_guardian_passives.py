"""Fixed official effects, one-use state, shared rollback and native ordering."""
from copy import deepcopy
from random import Random
from unittest.mock import patch
import unittest

from native946_codec import packet
from native946_login import MessageReader
from native946_guardian_passives import death_effects
from native946_gameplay import ENEMY_ID
from native946_wilderness import WildBattle,DATA
import test_native946_battle_actions as actions


def pet(skill):
    return dict(id=100001,camp=4,hp=0,hpmax=100,mp=40,attack=27,
                innate_skills=[dict(stunt_id=skill)])


def cast_ids(replies):
    found=[]
    for reply in replies:
        if reply[:3]!=packet(2157,[],[]):continue
        reader=MessageReader(reply[3:]);actor=reader.integer();target=reader.integer()
        count=reader.take(1)[0]
        for _ in range(count):reader.integer()
        skill=reader.integer();reader.finish();found.append((actor,target,skill))
    return found


class FixedDeathEffectTests(unittest.TestCase):
    def test_retaliation_is_one_ordinary_attack_and_never_charges_mana(self):
        actor=pet(4922);target=dict(id=200001,camp=3,hp=100,defense=5,mp=19)
        events=death_effects(actor,target,[actor,target],Random(1))
        self.assertEqual((events[0]['skill'],events[0]['damage'],target['hp']),(4922,22,78))
        self.assertEqual((actor['mp'],target['mp']),(40,19))
        self.assertEqual(death_effects(actor,target,[actor,target],Random(2)),[])
        self.assertEqual(target['hp'],78)
        # A new battle receives an actor without the old one-use marker.
        self.assertEqual(len(death_effects(pet(4922),target,[],Random(3))),1)

    def test_healing_uses_unique_lowest_absolute_hp_and_caps_at_maximum(self):
        actor=pet(4923)
        targets=[dict(id=1,camp=4,hp=8,hpmax=1000),dict(id=2,camp=4,hp=9,hpmax=10),
                 dict(id=3,camp=3,hp=1,hpmax=500)]
        event,=death_effects(actor,None,targets,Random(1))
        self.assertEqual((event['target']['id'],event['damage'],targets[0]['hp']),(1,-992,1000))
        self.assertEqual(targets[1]['hp'],9)
        self.assertEqual(death_effects(actor,None,targets,Random(1)),[])
        self.assertEqual(actor['mp'],40)

    def test_alive_or_recalled_guardians_never_trigger(self):
        for key,value in (('hp',1),('removed',True)):
            actor=pet(4922);actor[key]=value
            target=dict(id=9,hp=20,defense=0)
            self.assertEqual(death_effects(actor,target,[],Random(1)),[])
            self.assertNotIn('innate_death_used',actor)

    def test_ties_dead_targets_and_healing_restrictions_use_local_rules(self):
        actor=pet(4923);targets=[dict(id=2,camp=4,hp=5,hpmax=100),dict(id=1,camp=4,hp=5,hpmax=100)]
        event,=death_effects(actor,None,targets,Random(1))
        self.assertEqual((event['target']['id'],targets[1]['hp'],targets[0]['hp']),(1,100,5))
        actor=pet(4923);targets=[dict(id=1,camp=4,hp=0,hpmax=100),dict(id=2,camp=4,hp=5,hpmax=100)]
        event,=death_effects(actor,None,targets,Random(1))
        self.assertEqual((event['target']['id'],targets[0]['hp'],targets[1]['hp']),(2,0,100))
        actor=pet(4923);targets=[dict(id=1,camp=4,hp=5,hpmax=100,role_heal_reduce=500)]
        event,=death_effects(actor,None,targets,Random(1))
        self.assertEqual((event['damage'],targets[0]['hp']),(-47,52))

    def test_indirect_death_never_invents_a_retaliation_target(self):
        actor=pet(4922)
        self.assertEqual(death_effects(actor,None,[],Random(1)),[])
        self.assertNotIn('innate_pending',actor)

    def test_connected_passives_cannot_be_cast_as_active_skills(self):
        from native946_guardian_magic import prepared
        for skill in (4922,4923):
            with self.assertRaisesRegex(ValueError,'自动触发'):
                prepared(pet(skill),skill)


class BattleDeathEffectTests(unittest.TestCase):
    setUp=actions.BattleActionTests.setUp

    def species(self,kind):
        with self.p.transaction() as db:
            db.execute('UPDATE summons SET type=?,hp=1,cor=1 WHERE id=?',(kind,self.sid))

    def training(self,kind):
        self.species(kind);self.g.begin_battle();b=self.g.battle
        b.update(round=2,hp=20,enemy_hp=10000)
        from native946_role_battle import solo_fighters
        fighters=solo_fighters(self.g);fighters[self.p.cid]['hp']=20
        fighters[ENEMY_ID].update(hp=10000,hpmax=10000)
        b['orders']={self.p.cid:dict(defend=True,skill=10,damage=0,mana=0),
                     self.sid:dict(defend=True,skill=10,damage=0,mana=0)}
        return b,self.g.resolve_round()

    def test_training_retaliation_precedes_native_death_and_persists_zero_hp(self):
        b,replies=self.training(3390)
        self.assertEqual(b['enemy_hp'],10000-b['pet']['attack'])
        self.assertIn((self.sid,ENEMY_ID,4922),cast_ids(replies))
        attack=next(i for i,p in enumerate(replies) if p[:3]==packet(2157,[],[]) and p==packet(2157,['number','number',('array','number'),'number'],[self.sid,ENEMY_ID,[ENEMY_ID],4922]))
        death=packet(2142,['number','number'],[self.sid,2])
        self.assertEqual(replies.count(death),1);self.assertGreater(replies.index(death),attack)
        active=False;passive_finished=False
        for index,reply in enumerate(replies):
            if reply[:3]==packet(2126,[],[]):
                active=MessageReader(reply[3:]).integer()==1
                if not active and index>attack:passive_finished=True
            if reply==death:
                self.assertFalse(active,'source death must follow native return movement')
                self.assertTrue(passive_finished)
        self.assertEqual(self.s.get(self.sid)['hp'],0)
        self.assertEqual(self.s.get(self.sid)['mp'],b['pet']['mp'])

    def test_defend_orders_never_create_an_actor_group_without_an_actor(self):
        _,replies=self.training(3390)
        inside=False;has_actor=False
        for reply in replies:
            if reply[:3]==packet(2126,[],[]):
                operation=MessageReader(reply[3:]).integer()
                if operation==1:
                    self.assertFalse(inside);inside=True;has_actor=False
                else:
                    self.assertTrue(inside);self.assertTrue(has_actor);inside=False
            elif reply[:3]==packet(2157,[],[]):has_actor=True
        self.assertFalse(inside)

    def test_training_healing_reaches_the_owned_character_and_keeps_mana(self):
        b,replies=self.training(3391)
        self.assertEqual(b['hp'],self.p.snapshot()['hpmax'])
        self.assertIn((self.sid,self.p.cid,4923),cast_ids(replies))
        self.assertEqual(self.p.snapshot()['hp'],b['hp'])
        self.assertEqual(self.s.get(self.sid)['mp'],b['pet']['mp'])

    def test_background_input_draw_acks_cannot_complete_a_combat_round(self):
        b,_=self.training(3390)
        b.update(guardian_acceptance=4922,_guardian_input_draw_acks=2)
        before=b['round']
        actions.BattleActionTests.call(self,2184);actions.BattleActionTests.call(self,2184)
        self.assertEqual((b['round'],b['phase'],b['_guardian_input_draw_acks']),(before,'drawing',0))
        actions.BattleActionTests.call(self,2184)
        self.assertEqual((b['round'],b['phase']),(before+1,'input'))

    def shared(self,kind):
        self.species(kind);battle=WildBattle(self.g,[self.g],DATA['zones'][0])
        battle.state['round']=2
        actor=battle.current_pet(self.p.cid);hero=battle.state['fighters'][self.p.cid]
        actor.update(hp=1,initiative=0);hero.update(hp=20,initiative=0)
        for enemy in battle.enemies():enemy.update(hp=10000,hpmax=10000,attack=10,defense=0,initiative=1000)
        for enemy in battle.enemies()[1:]:enemy['removed']=True
        battle.state['orders']={self.sid:dict(kind='defend'),self.p.cid:dict(kind='defend')}
        return battle

    def test_shared_battle_uses_the_same_retaliation_and_healing_events(self):
        battle=self.shared(3390);actor=battle.current_pet(self.p.cid)
        total=sum(e['hp'] for e in battle.enemies());battle.resolve()
        self.assertEqual(sum(e['hp'] for e in battle.enemies()),total-actor['attack'])
        self.assertEqual(actor['innate_death_used'],[4922])
        self.assertEqual(self.s.get(self.sid)['hp'],0)

    def test_shared_healing_is_applied_before_settlement(self):
        battle=self.shared(3391);battle.resolve()
        self.assertEqual(self.p.snapshot()['hp'],self.p.snapshot()['hpmax'])
        self.assertEqual(battle.current_pet(self.p.cid)['innate_death_used'],[4923])

    def test_failed_shared_commit_restores_effect_marker_health_and_rng(self):
        battle=self.shared(3390);before=deepcopy(battle.state);rng=battle.rng.getstate()
        with patch.object(battle,'persist',side_effect=RuntimeError('fixture persist failure')):
            with self.assertRaises(RuntimeError):battle.resolve()
        self.assertEqual(battle.state,before);self.assertEqual(battle.rng.getstate(),rng)
        battle.resolve()
        self.assertEqual(battle.current_pet(self.p.cid)['innate_death_used'],[4922])


if __name__=='__main__':unittest.main()
