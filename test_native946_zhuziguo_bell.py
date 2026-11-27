"""Mechanics integration on disposable encounters; no original dungeon claim."""
from copy import deepcopy
from unittest.mock import patch
import json
import unittest

from native946_login import MessageReader
from native946_wilderness import DATA,WildBattle
from native946_zhuziguo_bell import MECHANISM,defeated,next_round,revived
import test_native946_wilderness_party as wilderness_fixture
mid=wilderness_fixture.mid


class BellBattleTests(unittest.TestCase):
    setUp=wilderness_fixture.WildernessPartyTests.setUp
    call=wilderness_fixture.WildernessPartyTests.call
    team=wilderness_fixture.WildernessPartyTests.team
    buff=wilderness_fixture.WildernessPartyTests.buff

    def battle(self,pet=False):
        self.team()
        for game in self.games:
            self.buff(game)
            if pet:game.summons.claim_starter()
        zone=deepcopy(DATA['zones'][0]);zone['dungeon_mechanism']=MECHANISM
        battle=WildBattle(self.a,self.games,zone)
        for enemy in battle.enemies():
            enemy.update(hp=100000000,hpmax=100000000,attack=0,initiative=0)
        for game in self.games:self.svc.drain(game)
        return battle

    def activate(self,battle):
        battle.state['zhuziguo_bell']['energy']=15
        self.call(self.a,4891)
        self.assertEqual(battle.state['zhuziguo_bell']['active_round'],1)
        for game in self.games:self.svc.drain(game)

    def finish_input(self,battle):
        for _ in range(12):
            if battle.state['phase']!='input':return
            pending=next(((cid,battle.pending_actor(cid)) for cid in battle.games if battle.pending_actor(cid)),None)
            if pending is None:self.fail('No pending actor while battle is waiting for input')
            cid,fid=pending
            self.call(battle.games[cid],2182 if fid!=cid else 2178,1)
        self.fail('Input did not finish')

    def test_regular_encounter_cannot_enable_bell_by_request(self):
        self.buff(self.a)
        battle=WildBattle(self.a,[self.a],DATA['zones'][0]);before=deepcopy(battle.state)
        replies=self.call(self.a,4891)
        self.assertIn(98,[mid(p) for p in replies]);self.assertEqual(before,battle.state)

    def test_only_leader_can_toggle_and_payload_is_empty(self):
        battle=self.battle();battle.state['zhuziguo_bell']['energy']=15
        before=deepcopy(battle.state)
        for game,args in ((self.b,()),(self.a,(1,))):
            self.assertIn(98,[mid(p) for p in self.call(game,4891,*args)])
            self.assertEqual(before,battle.state)
        self.call(self.a,4891)
        self.assertEqual(battle.state['zhuziguo_bell']['energy'],5)
        self.assertIn(98,[mid(p) for p in self.call(self.a,4891)])
        self.assertEqual(battle.state['zhuziguo_bell']['energy'],5)

    def test_first_action_reopens_same_hero_then_pet_once(self):
        battle=self.battle(pet=True);self.activate(battle)
        cid=self.a.character['id'];pet=battle.current_pet(cid)
        deadline=battle.state['deadline']
        first=self.call(self.a,2178,1)
        self.assertEqual(battle.pending_actor(cid),cid)
        self.assertIn(3817,[mid(p) for p in first]);self.assertNotIn(2152,[mid(p) for p in first])
        input_packet=next(p for p in first if mid(p)==2151)
        self.assertEqual(MessageReader(input_packet[3:]).integer(),cid)
        second=self.call(self.a,2178,1)
        self.assertIn(4886,[mid(p) for p in second])
        self.assertEqual(battle.pending_actor(cid),pet['id'])
        self.assertEqual(battle.action_count(pet['id']),1)
        self.call(self.a,2182,1)
        self.assertIsNone(battle.pending_actor(cid))
        self.assertEqual(battle.state['deadline'],deadline)
        self.assertEqual(len(battle.actor_orders(cid)),2)
        self.assertEqual(json.loads(json.dumps(self.a.battle))['zhuziguo_bell']['energy'],5)

    def test_revert_removes_both_actions_and_pet_without_touching_peer(self):
        battle=self.battle(pet=True);self.activate(battle)
        cid,other=self.a.character['id'],self.b.character['id']
        self.call(self.a,2178,1);self.call(self.a,2178,1);self.call(self.a,2182,1)
        self.call(self.b,2178,1)
        peer=deepcopy(battle.actor_orders(other));deadline=battle.state['deadline']
        replies=self.call(self.a,3819)
        self.assertIn(2151,[mid(p) for p in replies])
        self.assertEqual(battle.actor_orders(cid),[])
        self.assertEqual(battle.actor_orders(battle.current_pet(cid)['id']),[])
        self.assertEqual(battle.actor_orders(other),peer)
        self.assertEqual(battle.state['deadline'],deadline)
        self.assertEqual(battle.state['zhuziguo_bell']['energy'],5)
        self.call(self.a,2178,1)
        self.assertFalse(battle.input_complete(cid))

    def test_medicine_slots_reserve_inventory_together_and_withdraw_releases_them(self):
        battle=self.battle(pet=True);self.activate(battle);cid=self.a.character['id']
        item=next(i for i in self.a.progression.snapshot()['items'] if i['type']==4000)
        with self.a.progression.transaction() as db:db.execute('UPDATE inventory SET amount=1 WHERE id=?',(item['id'],))
        battle.state['fighters'][cid]['hp']-=1000
        self.call(self.a,2185,8,item['id'],cid)
        replies=self.call(self.a,2185,8,item['id'],cid)
        self.assertIn(98,[mid(p) for p in replies]);self.assertEqual(len(battle.actor_orders(cid)),1)
        self.call(self.a,3819)
        replies=self.call(self.a,2183,8,item['id'],cid)
        self.assertNotIn(888,[mid(p) for p in replies])
        self.assertEqual(next(i['amount'] for i in self.a.progression.snapshot()['items'] if i['id']==item['id']),1)

    def test_both_actions_recheck_mana_when_resolving(self):
        from native946_role_battle import mana_cost,profile
        battle=self.battle();self.activate(battle);cid=self.a.character['id'];hero=battle.state['fighters'][cid]
        hero['skills']={12:1};hero['race']=1;hero['initiative']=100000
        cost=mana_cost(hero,profile(12));hero['mp']=cost
        target=battle.enemies()[0]['id']
        self.call(self.a,2185,9,12,target);self.call(self.a,2185,9,12,target)
        self.assertEqual(len(battle.actor_orders(cid)),2)
        self.finish_input(battle)
        self.assertEqual(battle.state['phase'],'drawing')
        self.assertEqual(hero['mp'],0)
        actor_packets=[p for p in self.svc.drain(self.a) if mid(p)==2157]
        casts=[]
        for p in actor_packets:
            reader=MessageReader(p[3:]);actor=reader.integer();reader.integer()
            for _ in range(reader.take(1)[0]):reader.integer()
            skill=reader.integer()
            if actor==cid and skill==12:casts.append(p)
        self.assertEqual(len(casts),1)

    def test_control_from_first_action_blocks_second_action(self):
        from native946_role_battle import Runtime
        battle=self.battle();self.activate(battle);cid=self.a.character['id']
        hero=battle.state['fighters'][cid];hero['initiative']=100000
        self.call(self.a,2177,3,0);self.call(self.a,2177,3,0)
        original=Runtime.attack;actions=[]
        def attack(runtime,actor,target,**kwargs):
            if actor['id']==cid:
                actions.append(actor['id']);actor['controls']['seal']=runtime.round
            return original(runtime,actor,target,**kwargs)
        with patch.object(Runtime,'attack',attack):self.finish_input(battle)
        self.assertEqual(actions,[cid])

    def test_timeout_repeats_one_order_and_storage_failure_rolls_back_second_action(self):
        battle=self.battle();self.activate(battle);cid=self.a.character['id']
        self.call(self.a,2178,1)
        before=deepcopy(battle.state)
        with patch.object(battle,'persist',side_effect=RuntimeError('fixture rollback')):
            with self.assertRaises(RuntimeError):battle.tick(battle.state['deadline']+1)
        self.assertEqual(battle.state,before)
        battle.tick(battle.state['deadline']+1)
        self.assertEqual(len(battle.actor_orders(cid)),2)
        self.assertEqual(battle.actor_orders(cid)[0],battle.actor_orders(cid)[1])
        self.assertEqual(battle.state['zhuziguo_bell']['energy'],5)

    def test_regeneration_and_verified_deaths_cap_without_duplicate_credit(self):
        battle=self.battle();bell=battle.state['zhuziguo_bell'];bell['energy']=19
        foe=battle.enemies()[0];foe.update(bell_reward=4,hp=0)
        self.assertTrue(defeated(battle.state,foe));self.assertEqual(bell['energy'],20)
        self.assertFalse(defeated(battle.state,foe))
        bell['energy']=5;revived(battle.state,foe);defeated(battle.state,foe)
        self.assertEqual(bell['energy'],9)
        battle.state['round']=2;next_round(battle.state)
        self.assertEqual(bell['energy'],11);next_round(battle.state)
        self.assertEqual(bell['energy'],11)

    def test_arming_during_drawing_can_cancel_before_next_round(self):
        battle=self.battle();self.finish_input(battle);bell=battle.state['zhuziguo_bell'];bell['energy']=15
        self.call(self.a,4891);self.assertTrue(bell['armed']);self.assertEqual(bell['energy'],15)
        with patch('native946_zhuziguo_bell.monotonic',return_value=bell['last_toggle']+4):self.call(self.a,4891)
        self.assertFalse(bell['armed']);self.assertEqual(bell['energy'],15)
        with patch('native946_zhuziguo_bell.monotonic',return_value=bell['last_toggle']+4):self.call(self.a,4891)
        for game in self.games:self.call(game,2184)
        self.assertEqual(battle.state['round'],2);self.assertEqual(bell['energy'],7)
        self.assertEqual(bell['active_round'],2)
        next_round(battle.state)
        self.assertEqual(bell['energy'],7);self.assertEqual(bell['active_round'],2)

    def test_two_medicines_resolve_independently_and_animation_ack_cannot_repeat_them(self):
        battle=self.battle();self.activate(battle);cid=self.a.character['id']
        hero=battle.state['fighters'][cid];hero['hp']-=1000;hero['initiative']=100000
        item=next(i for i in self.a.progression.snapshot()['items'] if i['type']==4000)
        self.call(self.a,2185,8,item['id'],cid);self.call(self.a,2185,8,item['id'],cid)
        self.finish_input(battle)
        amount=next(i['amount'] for i in self.a.progression.snapshot()['items'] if i['id']==item['id'])
        self.assertEqual(amount,item['amount']-2)
        self.call(self.a,2184);self.call(self.a,2184)
        self.assertEqual(next(i['amount'] for i in self.a.progression.snapshot()['items'] if i['id']==item['id']),amount)

    def test_two_summons_are_rejected_and_summon_then_dismiss_rechecks_current_pet(self):
        battle=self.battle();self.activate(battle);cid=self.a.character['id']
        sid=self.a.summons.claim_starter()
        self.call(self.a,2177,6,sid)
        replies=self.call(self.a,2177,6,sid)
        self.assertIn(98,[mid(p) for p in replies]);self.assertEqual(len(battle.actor_orders(cid)),1)
        replies=self.call(self.a,2178,7)
        self.assertNotIn(888,[mid(p) for p in replies])
        self.finish_input(battle)
        self.assertIsNone(battle.current_pet(cid))
        self.assertIn(sid,battle.state['used'][cid])

    def test_timeout_repeated_summon_does_not_resummon_or_reset_pet(self):
        battle=self.battle();self.activate(battle);cid=self.a.character['id']
        sid=self.a.summons.claim_starter();self.call(self.a,2177,6,sid)
        battle.tick(battle.state['deadline']+1)
        self.assertEqual(battle.current_pet(cid)['id'],sid)
        additions=[p for p in self.svc.drain(self.a) if mid(p)==2128]
        self.assertEqual(len(additions),1)

    def test_kill_energy_rolls_back_with_battle_and_retry_credits_once(self):
        battle=self.battle();self.activate(battle);cid=self.a.character['id']
        hero=battle.state['fighters'][cid];hero.update(initiative=100000,AttValue=10000)
        foe=battle.enemies()[0];foe.update(hp=1,defense=0,bell_reward=3,DodValue=0)
        self.call(self.a,2177,3,foe['id']);self.call(self.a,2177,3,foe['id'])
        self.call(self.b,2178,1);before=deepcopy(battle.state)
        with patch.object(battle,'persist',side_effect=RuntimeError('fixture rollback')):
            with self.assertRaises(RuntimeError):self.call(self.b,2178,1)
        self.assertEqual(battle.state,before)
        self.call(self.b,2178,1)
        self.assertEqual(battle.state['zhuziguo_bell']['energy'],8)
        self.assertEqual(battle.state['fighters'][foe['id']]['hp'],0)
        self.assertEqual(battle.state['zhuziguo_bell']['credited'],{foe['id']})


if __name__=='__main__':unittest.main()
