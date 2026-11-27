"""Native NPC daily chains: actual combat, durable rewards and hostile replay."""
from copy import deepcopy
from random import Random
from unittest.mock import patch
import json,unittest

from native946_codec import number,packet
from native946_login import MessageReader
from native946_map import load_map
from native946_npc_quests import Quests,QuestBattle,ACTIVITIES,GIVERS,reward,deliver,day_key,reference_roster
from native946_progression import Progression
from native946_world import world
import test_native946_wilderness_party as wild_fixtures
from test_native946_wilderness_party import mid
from test_native946_npc_services import menu


class NpcQuestTests(unittest.TestCase):
    setUp=wild_fixtures.WildernessPartyTests.setUp;call=wild_fixtures.WildernessPartyTests.call
    team=wild_fixtures.WildernessPartyTests.team;buff=wild_fixtures.WildernessPartyTests.buff;fight=wild_fixtures.WildernessPartyTests.fight

    def ready(self,team=False):
        if team:self.team()
        for g in self.games:
            self.buff(g)
            with g.progression.transaction() as db:db.execute('UPDATE cultivation_state SET ascension=1 WHERE character_id=?',(g.character['id'],))

    def position(self,g,scene,x,y):
        c=g.character;rev=self.characters.change_scene(c['owner'],c['id'],c['map_id'],scene,x,y,c.get('position_revision',0))
        c.update(map_id=scene,x=x,y=y,position_revision=rev);g.grid=load_map(scene)
        g._world_visible=set();g._world_bucket=None

    def visit(self,g,kind):
        scene,nid=ACTIVITIES[kind]['giver'];npc=world().npc(scene,nid)
        self.position(g,scene,*load_map(scene).nearest_walkable(npc['x']//20,npc['y']//20))
        return nid

    def accept(self,kind='ghost',team=False,branch=0):
        self.ready(team);Quests(self.a).accept(kind,branch)
        return Quests(self.a).active(kind)

    def start(self,row,team=False):
        for g in self.games if team else [self.a]:self.position(g,row['scene'],row['x'],row['y'])
        Quests(self.a).execute(row['npc'],dict(kind='npc_quest',op='fight',task=row['id']))
        return self.svc.battles[self.a.character['id']]

    def count(self,table):
        with self.a.progression.transaction(write=False) as db:return db.execute('SELECT count(*) FROM '+table).fetchone()[0]

    def test_shared_task_retreat_unpauses_each_peer_and_keeps_the_task(self):
        row = self.accept(team=True)
        battle = self.start(row, True)
        for game in self.games:self.svc.drain(game)
        replies = (self.call(self.a, 2178, 2), self.svc.drain(self.b))
        for game, messages in zip(self.games, replies):
            end = messages.index(packet(2146, [], []))
            self.assertEqual(messages[end - 1], packet(2152, ['number'], [game.character['id']]))
            self.assertNotIn(packet(2170, [], []), messages)
            self.assertIsNone(game.battle)
        self.assertEqual(battle.state['phase'], 'finished')
        self.assertEqual(self.count('native_npc_quest_rewards'), 0)
        self.assertEqual(Quests(self.a).active('ghost')['id'], row['id'])

    def test_giver_menu_accepts_from_original_npc_and_publishes_native_trace(self):
        self.ready();nid=self.visit(self.a,'ghost');reply=self.call(self.a,1034,nid)
        p=next(p for p in reply if mid(p)==1025);_,options,_,close=menu(p)
        self.assertEqual(close,1);self.assertIn('领取下一环',options)
        self.assertNotIn('领取下一环（参考规则）',options)
        choice=options.index('领取下一环')+1
        response=self.a.receive(1032,MessageReader(bytes([choice])+number(nid)))
        self.assertIn(1978,[mid(p) for p in response]);row=Quests(self.a).active('ghost')
        self.assertEqual(row['ring'],1);self.assertEqual(row['dynamic'],1)
        self.assertTrue(load_map(row['scene']).walkable(row['x'],row['y']))

    def test_same_kind_duplicate_accept_never_consumes_another_quota(self):
        row=self.accept()
        with self.assertRaises(ValueError):Quests(self.a).accept('ghost')
        self.assertEqual(self.count('native_npc_quests'),1)
        with self.a.progression.transaction(write=False) as db:self.assertEqual(db.execute('SELECT accepted FROM native_npc_activity_progress').fetchone()[0],1)

    def test_five_player_ghost_task_awards_the_captain_once(self):
        from time import time
        from local_accounts import LocalAccounts
        from native946_double_time import CAPTAIN_POINTS_PER_TASK, load
        from native946_gameplay import NativeGameplay

        self.team()
        accounts = LocalAccounts(self.path)
        for i in range(2, 5):
            owner = f'captain_fixture{i}'
            accounts.register(dict(username=owner,password='fixture-password'))
            role = self.characters.create(owner, '统帅样本'+str(i), 702)
            peer = NativeGameplay(role, self.characters, lambda: ())
            peer.source_port = 30000 + i
            self.games.append(peer)
            self.call(self.a, 2039, role['id'])
            self.call(peer, 2038, self.a.character['id'])
        self.assertEqual(len(self.svc.team(self.a.character['id'])['members']), 5)
        row = self.accept()
        battle = self.start(row, True)
        self.fight(battle)
        with self.a.progression.transaction() as db:
            battle.settle(db, True)
            points = [load(db, g.progression.cid, int(time()))['points'] for g in self.games]
        self.assertEqual(points, [CAPTAIN_POINTS_PER_TASK, 0, 0, 0, 0])

    def test_xianqi_reward_cannot_turn_into_a_same_named_consumable(self):
        from native946_equipment import DATA
        self.assertEqual(DATA['reference_items']['200189']['name'],'清心')
        self.assertEqual(DATA['reference_items']['200189']['kind'],'仙器')
        self.assertEqual(DATA['reward_map']['200189']['equipment'],200189)
        row=self.accept();forced=dict(experience=111,gold=22,guardian_experience=111,points=0,drops={200189:1},rare=False,victory=True)
        with patch('native946_npc_quests.reward',return_value=forced):self.fight(self.start(row))
        with self.a.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM native_npc_pending_items').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT reference_id FROM native_equipment').fetchone()[0],200189)
            self.assertEqual(db.execute('SELECT count(*) FROM inventory WHERE type=47640').fetchone()[0],0)

    def test_native_level_conditions_and_sanjie_team_requirement_are_enforced(self):
        with self.assertRaises(ValueError):Quests(self.a).accept('ghost')
        with self.assertRaises(ValueError):Quests(self.a).accept('king')
        with self.assertRaises(ValueError):Quests(self.a).accept('sanjie')
        self.assertEqual(self.count('native_npc_quests'),0)

    def test_target_is_private_and_remote_or_foreign_dialogue_cannot_start_combat(self):
        row=self.accept()
        with self.assertRaises(ValueError):Quests(self.a).execute(row['npc'],dict(kind='npc_quest',op='fight',task=row['id']))
        self.position(self.b,row['scene'],row['x'],row['y'])
        self.assertIsNone(Quests(self.b).look(row['npc']))
        self.assertEqual(self.count('native_npc_quest_rewards'),0)

    def test_expiry_and_cancel_preserve_daily_use_and_do_not_pay_rewards(self):
        row=self.accept();Quests(self.a).cancel(row['id']);self.assertIsNone(Quests(self.a).active('ghost'))
        with self.a.progression.transaction(write=False) as db:self.assertEqual(db.execute('SELECT accepted FROM native_npc_activity_progress').fetchone()[0],1)
        Quests(self.a).accept('ghost');second=Quests(self.a).active('ghost')
        with patch('native946_npc_quests.time',return_value=second['expires']+1):
            self.assertIsNone(Quests(self.a).active('ghost'))
            with self.assertRaises(ValueError):Quests(self.a).execute(second['npc'],dict(kind='npc_quest',op='fight',task=second['id']))
        self.assertEqual(self.count('native_npc_quest_rewards'),0)

    def test_native_task_panel_cancel_cannot_cancel_another_players_task(self):
        row=self.accept();self.call(self.b,1980,930000+row['id'])
        self.assertIsNotNone(Quests(self.a).active('ghost'))
        self.assertIn(1966,[mid(p) for p in self.call(self.a,1980,930000+row['id'])])
        self.assertIsNone(Quests(self.a).active('ghost'))

    def test_reference_roster_preserves_source_stats_and_party_counts(self):
        expected={
            'king': (20180,80000,1425),
            'heaven': (1,10000,1000),
            'sanjie': (11,15000,400),
        }
        for kind,(reference,hp,attack) in expected.items():
            for size,count in ((1,4),(3,4),(4,7),(5,8)):
                task=dict(kind=kind,token='reference-roster')
                roster=reference_roster(task,size)
                self.assertEqual(len(roster),count)
                self.assertEqual(roster,reference_roster(task,size))
                first=roster[0]['combat_stats']
                self.assertEqual((first['reference_monster'],first['hpmax'],first['attack']),
                                 (reference,hp,attack))
        for enemy in reference_roster(dict(kind='ghost',token='reference-roster'),1):
            stats=enemy['combat_stats']
            self.assertEqual(stats['hpmax'],2000)
            self.assertIn(stats['attack'],range(200,501))
            self.assertIn(stats['initiative'],range(50,151))

    def test_reference_element_vulnerability_is_used_as_a_signed_offset(self):
        from native946_role_battle import Runtime,profile
        actor=dict(level=40,mag=20,initiative=200,skills={53:1})
        enemy=dict(initiative=100,hp=1000,hpmax=1000)
        runtime=Runtime({}, {}, 1, Random(0), {})
        skill=profile(53)
        plain=runtime.base_damage(actor,enemy,skill)
        enemy['reference_element_resist']={'thunder':-500}
        self.assertEqual(runtime.base_damage(actor,enemy,skill),plain+500)

    def test_reference_enemy_spends_mana_on_a_native_spell(self):
        row=self.accept();battle=self.start(row)
        self.assertEqual(len(battle.enemies()),4)
        for enemy in battle.enemies():
            enemy.update(skills={53:1},enemy_spell_percent=100)
        self.call(self.a,2178,1)
        self.assertTrue(all(enemy.get('role_last_spell')==53 and enemy['mp']<enemy['mpmax']
                            for enemy in battle.enemies()))

    def test_each_activity_completes_real_combat_and_awards_once(self):
        self.ready(True)
        # Use a party equipped for a full reference ghost-king roster.
        for game in self.games:
            with game.progression.transaction() as db:
                db.execute('UPDATE progression SET cor=10000,strength=16000 WHERE character_id=?',
                           (game.character['id'],))
            stats=game.progression.snapshot()
            game.progression.set_vitals(stats['hpmax'],stats['mpmax'])
        for kind in GIVERS.values():
            Quests(self.a).accept(kind,0);row=Quests(self.a).active(kind);before={g.character['id']:g.progression.snapshot()['experience'] for g in self.games}
            battle=self.start(row,True);self.fight(battle)
            self.assertEqual(battle.state['phase'],'finished')
            for g in self.games:
                self.assertIsNone(Quests(g).active(kind));self.assertGreater(g.progression.snapshot()['experience'],before[g.character['id']])
            with self.a.progression.transaction() as db:battle.settle(db,True)
        self.assertEqual(self.count('native_npc_quest_rewards'),8)
        # Daily battles must never complete the separate tutorial quest.
        self.assertEqual(self.count('quest_battles'),0)

    def test_escape_and_captured_target_cannot_award_task_completion(self):
        row=self.accept();battle=self.start(row)
        enemy=battle.enemies()[0]
        reply=self.call(self.a,2177,5,enemy['id']);self.assertIn(98,[mid(p) for p in reply])
        self.call(self.a,2178,2)
        self.assertNotIn(5349,[mid(p) for p in self.svc.drain(self.a)])
        self.assertEqual(battle.state['phase'],'finished');self.assertIsNotNone(Quests(self.a).active('ghost'))
        self.assertEqual(self.count('native_npc_quest_rewards'),0)

    def test_failed_entry_does_not_publish_a_partial_battle_to_either_player(self):
        row=self.accept(team=True)
        for g in self.games:self.svc.drain(g)
        original=QuestBattle.entry
        def render(battle,game):
            if game is self.b:raise RuntimeError('fixture entry failure')
            return original(battle,game)
        with patch.object(QuestBattle,'entry',render):
            with self.assertRaises(RuntimeError):self.start(row,True)
        self.assertEqual(self.svc.battles,{})
        for g in self.games:self.assertIsNone(g.battle);self.assertEqual(self.svc.drain(g),())

    def test_constructor_failure_restores_registered_players_before_retry(self):
        row=self.accept(team=True);original=QuestBattle.__init__
        def construct(battle,*args):original(battle,*args);raise RuntimeError('fixture constructor failure')
        with patch.object(QuestBattle,'__init__',construct):
            with self.assertRaises(RuntimeError):self.start(row,True)
        self.assertEqual(self.svc.battles,{})
        for g in self.games:self.assertIsNone(g.battle)
        self.fight(self.start(row,True));self.assertEqual(self.count('native_npc_quest_rewards'),2)

    def test_failed_group_reward_rolls_back_every_wallet_receipt_and_task_state(self):
        row=self.accept(team=True);battle=self.start(row,True)
        before=[(g.progression.snapshot()['experience'],g.progression.snapshot()['gold']) for g in self.games]
        with patch('native946_npc_quests.deliver',side_effect=RuntimeError('fixture reward failure')):
            with self.assertRaises(RuntimeError):self.fight(battle)
        self.assertEqual(self.count('native_npc_quest_rewards'),0)
        self.assertEqual([(g.progression.snapshot()['experience'],g.progression.snapshot()['gold']) for g in self.games],before)
        for g in self.games:self.assertIsNotNone(Quests(g).active('ghost'))
        self.fight(battle);self.assertEqual(self.count('native_npc_quest_rewards'),2)

    def test_quest_ring_ten_settlement_advances_to_one_and_survives_cold_load(self):
        self.ready()
        with self.a.progression.transaction() as db:
            db.execute('INSERT INTO native_npc_activity_progress(character_id,kind,day,next_ring) VALUES(?,\'ghost\',?,10)',(self.a.character['id'],day_key()))
        Quests(self.a).accept('ghost');row=Quests(self.a).active('ghost');self.assertEqual(row['ring'],10)
        self.fight(self.start(row));p=Progression(self.path,self.a.character['owner'],self.a.character['id'])
        with p.transaction(write=False) as db:self.assertEqual(db.execute('SELECT next_ring FROM native_npc_activity_progress').fetchone()[0],1)

    def test_team_accept_failure_rolls_back_every_member_and_counter(self):
        self.ready(True)
        with self.b.progression.transaction() as db:
            db.execute('INSERT INTO native_npc_activity_progress(character_id,kind,day,accepted) VALUES(?,\'ghost\',?,200)',(self.b.character['id'],day_key()))
        with self.assertRaises(ValueError):Quests(self.a).accept('ghost')
        self.assertEqual(self.count('native_npc_quests'),0)
        with self.a.progression.transaction(write=False) as db:self.assertEqual(db.execute('SELECT count(*) FROM native_npc_activity_progress WHERE character_id=?',(self.a.character['id'],)).fetchone()[0],0)

    def test_full_bag_reward_is_durable_and_claim_replay_cannot_duplicate_it(self):
        row=self.accept();p=self.a.progression
        with p.transaction() as db:
            db.execute('DELETE FROM inventory WHERE character_id=?',(p.cid,))
            for slot in range(1,25):db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,8,?,1)',(p.cid,slot))
        forced=dict(experience=111,gold=22,guardian_experience=111,points=0,drops={2002:3},rare=False,victory=True)
        with patch('native946_npc_quests.reward',return_value=forced):self.fight(self.start(row))
        with p.transaction() as db:
            self.assertEqual(db.execute('SELECT amount FROM native_npc_pending_items').fetchone()[0],3)
            db.execute('DELETE FROM inventory WHERE character_id=? AND slot=1',(p.cid,))
            self.assertEqual(deliver(p,db),[(2002,3)]);self.assertEqual(deliver(p,db),[])
        self.assertEqual(sum(r['amount'] for r in p.snapshot()['items'] if r['type']==6110),3)

    def test_unmapped_reward_remains_an_exact_named_entitlement(self):
        row=self.accept();p=self.a.progression
        with p.transaction() as db:
            db.execute('INSERT INTO native_npc_pending_items(task_id,character_id,reference_type,amount) VALUES(?,?,999999,1)',(row['id'],p.cid))
            self.assertEqual(deliver(p,db),[]);self.assertIn('参考物品999999',Quests(self.a).pending_text(db))
            self.assertEqual(db.execute('SELECT amount FROM native_npc_pending_items').fetchone()[0],1)

    def test_reward_formulas_are_integer_exact_at_all_ten_rings(self):
        for ring in range(1,11):
            for level in (1,80,160,200):
                king=reward('king',level,ring,Random(7));self.assertEqual(king['experience'],level*level*ring//4)
                heaven=reward('heaven',level,ring,Random(7));self.assertEqual(heaven['experience'],level*level*(40+ring)//40)
                for branch,den in enumerate((40,35,30)):
                    value=reward('sanjie',level,ring,Random(7),branch);self.assertEqual(value['experience'],level*level*(den+ring)//den)
                    self.assertEqual(value['points'],(9,21,33)[branch])

    def test_noon_day_boundary_uses_hong_kong_time(self):
        self.assertEqual(day_key(0),day_key(4*3600-1))
        self.assertNotEqual(day_key(4*3600-1),day_key(4*3600))


if __name__=='__main__':unittest.main()
