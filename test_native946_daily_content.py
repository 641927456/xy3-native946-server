"""Daily content through normal 946 dialogue, combat and inventory messages."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_gameplay import NativeGameplay

from native946_codec import number
from native946_login import MessageReader
from native946_npc_quests import Quests
from native946_ordinary_treasure import GIVER, ITEM, destination
from native946_star_challenge import RULE as STAR, StarChallenge, spawn
from native946_world import world
from native946_map import load_map
from test_native946_npc_services import menu, mid


class DailyContentTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        database = Path(temp.name) / 'daily.sqlite3'
        accounts = LocalAccounts(database)
        self.characters = Characters(database)
        self.games = []
        for index in range(2):
            owner = f'daily{index}'
            accounts.register(dict(username=owner, password='fixture-only'))
            character = self.characters.create(owner, f'日常测试{index}', 702)
            self.games.append(NativeGameplay(character, self.characters, lambda: ()))
        self.a, self.b = self.games
        self.svc = self.a.party_service

    def call(self, game, mid, *values):
        reply = game.receive(mid, MessageReader(b''.join(number(value) for value in values)))
        return reply if isinstance(reply, tuple) else (reply,) if reply else ()

    def buff(self, game):
        p = game.progression
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=180 WHERE id=?', (p.cid,))
            db.execute('UPDATE progression SET cor=1000,strength=1000 WHERE character_id=?', (p.cid,))
        game.character['level'] = 180
        state = p.snapshot()
        p.set_vitals(state['hpmax'], state['mpmax'])

    def fight(self, battle):
        for _ in range(12):
            if battle.state['phase'] == 'finished':
                return
            for g in battle.games.values():
                if battle.state['phase'] == 'input':
                    self.call(g, 2177, 3, 0)
            if battle.state['phase'] == 'drawing':
                for g in battle.games.values():
                    self.call(g, 2184)
        self.fail('Battle did not finish')

    def team(self):
        lead = self.a.character['id']
        self.call(self.a, 2042, lead)
        self.call(self.a, 2039, self.b.character['id'])
        self.call(self.b, 2038, lead)
        self.assertEqual(len(self.svc.team(lead)['members']), 2)

    def position(self, game, scene, x, y):
        character = game.character
        revision = self.characters.change_scene(character['owner'], character['id'], character['map_id'],
                                                scene, x, y, character.get('position_revision', 0))
        character.update(map_id=scene, x=x, y=y, position_revision=revision)
        game.grid = load_map(scene)
        game._world_visible = set()
        game._world_bucket = None

    def open_npc(self, scene, nid):
        reply = self.talk(scene,nid)
        return menu(next(p for p in reply if mid(p) == 1025))[1]

    def talk(self, scene, nid):
        npc = world().npc(scene, nid)
        self.position(self.a, scene, *load_map(scene).nearest_walkable(npc['x'] // 20, npc['y'] // 20))
        return self.call(self.a, 1034, nid)

    def choose(self, nid, choice):
        response = self.a.receive(1032, MessageReader(bytes([choice]) + number(nid)))
        return response if isinstance(response, tuple) else (response,) if response else ()

    def set_points(self, balance):
        with self.a.progression.transaction() as db:
            db.execute('INSERT OR REPLACE INTO native_npc_quest_wallet VALUES(?,?)',
                       (self.a.character['id'], balance))

    def points(self):
        with self.a.progression.transaction(write=False) as db:
            return db.execute('SELECT sanjie FROM native_npc_quest_wallet WHERE character_id=?',
                              (self.a.character['id'],)).fetchone()[0]

    def accept_sanjie(self, choice):
        self.team()
        for game in self.games:self.buff(game)
        self.assertEqual(self.open_npc(2001,4102), [
            '好吧，我愿意接受大唐的任务', '了解如何做任务', '查询队员完成三界令任务情况',
            '我要雇佣侠客帮我完成任务', '算了，让我再考虑一下',
        ])
        c=self.a.character
        self.position(self.b,2001,c['x'],c['y'])
        choices=menu(next(p for p in self.choose(4102,1) if mid(p)==1025))[1]
        self.assertEqual(len(choices),8)
        self.choose(4102,choice)

    def test_sanjie_colleague_roster_and_shared_team_continuation(self):
        from native946_npc_quests import SANJIE_OPPONENTS

        self.accept_sanjie(1)
        for stage,npc in enumerate((4103,4108,4111,2150,2156),1):
            leader,follower=[Quests(game).active('sanjie') for game in self.games]
            self.assertEqual((leader['id'],leader['ring'],leader['npc']),
                             (follower['id'],stage,npc))
            self.position(self.b,leader['scene'],leader['x'],leader['y'])
            self.assertEqual(self.open_npc(leader['scene'],npc),
                             ['任务： [御前比武]','<我找你有其他事>','离开'])
            self.choose(npc,1)
            battle=self.a._wild_battle
            self.assertIs(battle,self.b._wild_battle)
            self.assertEqual([(e['name'],e['type']) for e in battle.enemies()],
                             [(e['name'],e['shape']) for e in SANJIE_OPPONENTS[str(npc)]['party']])
            for enemy in battle.enemies():enemy.update(hp=1,hpmax=1,initiative=-10)
            self.fight(battle)
            self.assertEqual(self.points(),4 if stage==5 else 0)
            self.svc.drain(self.a)
            self.assertEqual([mid(p) for p in self.choose(npc,1)], [1183])
        self.assertTrue(all(Quests(game).active('sanjie') is None for game in self.games))

    def test_sanjie_kit_is_owned_per_member_and_rolls_back_failed_entry(self):
        from native946_npc_quests import QuestBattle

        self.accept_sanjie(3)
        task=Quests(self.a).active('sanjie')
        self.position(self.b,task['scene'],task['x'],task['y'])
        def quantities():
            return [sum(i['amount'] for i in game.progression.snapshot()['items']
                        if i['type']==6901 and i['page']==8) for game in self.games]
        self.assertEqual(quantities(),[4,4])
        self.assertEqual(self.open_npc(task['scene'],4121)[0],'使用驱邪水（任务： [长安驱邪]）')
        with patch.object(QuestBattle,'entry',side_effect=ValueError('测试开战失败')):
            self.assertIn(98,[mid(p) for p in self.choose(4121,1)])
        self.assertEqual(quantities(),[4,4])
        self.assertTrue(all(game.battle is None for game in self.games))
        self.open_npc(task['scene'],4121)
        self.choose(4121,1)
        self.assertIs(self.a._wild_battle,self.b._wild_battle)
        self.assertEqual(quantities(),[3,3])

    def test_sanjie_exchange_uses_configured_offers_and_debits_once(self):
        from native946_daily_content import EXCHANGE

        self.set_points(2000)
        choices = self.open_npc(2001, 4102)
        choices = menu(next(p for p in self.choose(4102, choices.index('了解如何做任务') + 1) if mid(p)==1025))[1]
        self.assertIn(2966, [mid(p) for p in self.choose(4102, choices.index('前往吕毅兑换三界令') + 1)])
        for scene,npc in ((2001,4101),(2006,4220),(2001,5500)):
            self.assertEqual(self.open_npc(scene,npc)[-1],'离开')
            offered=[action['item'] for action in self.a.dialog_actions if action['kind']=='sanjie_exchange']
            self.assertTrue(offered)
            self.assertTrue(set(offered)<=set(EXCHANGE['npcs'][f'{scene}:{npc}']))
        choices=self.open_npc(2001,4101)
        choice=next(i for i,label in enumerate(choices,1) if label.startswith('三千年蟠桃'))
        self.assertIn(786,[mid(p) for p in self.choose(4101,choice)])
        self.assertEqual(self.choose(4101,choice),())
        self.assertEqual(self.points(),1967)
        self.assertTrue(any(i['type']==6029 for i in self.a.progression.snapshot()['items']))

    def test_unverified_exchange_is_closed_and_owned_super_map_still_digs(self):
        from native946_inventory_features import give
        from native946_ordinary_treasure import ADVANCED, SUPER

        scene,nid = ADVANCED['giver']
        p = self.a.progression
        with p.transaction() as db:
            give(db,p,ADVANCED['item'],SUPER['advanced_cost'])
            give(db,p,SUPER['item'])
        self.assertEqual([mid(p) for p in self.talk(scene,nid)],[1183,100])
        self.assertEqual(self.choose(nid,2),())
        state = p.snapshot()
        self.assertEqual(sum(i['amount'] for i in state['items'] if i['type']==ADVANCED['item']),SUPER['advanced_cost'])
        iid = next(i['id'] for i in state['items'] if i['type']==SUPER['item'])
        self.position(self.a,*destination(iid))
        result = self.call(self.a,807,p.cid,iid)
        after = p.snapshot()
        self.assertFalse(any(i['id']==iid for i in after['items']))
        self.assertEqual(after['gold'],state['gold']+SUPER['cash'])
        self.assertTrue(any(i['type'] in SUPER['drops'] for i in after['items']))
        self.assertIn(786,[mid(packet) for packet in result])

    def test_local_mount_route_grants_native_mount_once(self):
        from native946_local_guides import CONTACTS, MOUNT_QID, RULE
        from native946_mall_uses import features

        self.buff(self.a)
        p = self.a.progression
        reply = self.call(self.a, 1985)
        journal = MessageReader(next(packet for packet in reply if mid(packet) == 1973)[3:])
        self.assertIn(f'[{MOUNT_QID}]={{["n"]="汗血宝马",', journal.string().decode('gb18030'))
        journal.finish()
        tracker = next(packet for packet in reply if mid(packet) == 1978)
        self.assertIn('#G【坐骑】#Y汗血宝马'.encode('gb18030'), tracker)
        for scene,nid,line in CONTACTS:
            options = self.open_npc(scene,nid)
            self.choose(nid,options.index(line+'（本服）')+1)
        with p.transaction(write=False) as db:
            self.assertIn('mount:'+str(RULE['mount_index']),features(db,p.cid))
        trace,_ = p.task_records(p.snapshot())
        self.assertNotIn(MOUNT_QID,{row['id'] for row in trace})
        scene,nid,_ = CONTACTS[0]
        self.assertNotIn(CONTACTS[0][2]+'（本服）',self.open_npc(scene,nid))

    def test_stale_exchange_menu_cannot_spend_points(self):
        self.set_points(2000)
        self.talk(2006,4220)
        self.a.dialog_actions = [dict(kind='sanjie_exchange',item=11685)]
        self.a.menu('旧兑换对话',['旧商品'],4220)
        self.assertIn(98,[mid(p) for p in self.choose(4220,1)])
        self.assertEqual(self.points(), 2000)
        self.assertNotIn(11685, [i['type'] for i in self.a.progression.snapshot()['items']])

    def test_sanjie_exchange_rolls_back_for_full_equipment_bag(self):
        from native946_daily_content import EXCHANGE

        actor=self.characters.create('daily0','装备测试',113)
        self.a=NativeGameplay(actor,self.characters,lambda:())
        self.set_points(2000)
        self.open_npc(2001,4101)
        choice=next(i for i,a in enumerate(self.a.dialog_actions,1) if
                    a['kind']=='sanjie_exchange' and EXCHANGE['goods'][str(a['item'])].get('equipment'))
        item=self.a.dialog_actions[choice-1]['item']
        with patch.object(self.a.progression,'free_bag_slot',side_effect=ValueError('背包已满')):
            self.assertIn(98,[mid(p) for p in self.choose(4101,choice)])
        self.assertEqual(self.points(),2000)
        self.open_npc(2001,4101)
        self.assertIn(786,[mid(p) for p in self.choose(4101,choice)])
        self.assertEqual(self.points(),100)
        with self.a.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT grade FROM native_equipment e JOIN inventory i ON i.id=e.item_id '
                                        'WHERE e.character_id=? AND i.type=?',(self.a.progression.cid,item)).fetchone()[0],11)

    def test_lotus_eight_rings_advance_and_charge_at_each_acceptance(self):
        p=self.a.progression
        self.buff(self.a)
        self.set_points(80)
        with p.transaction() as db:
            db.execute('INSERT INTO cultivation_state(character_id,rebirth) VALUES(?,2) '
                       'ON CONFLICT(character_id) DO UPDATE SET rebirth=2',(p.cid,))
        expected=(4551,4561,4561,4581,4559,4552,4576,4564)
        for ring,nid in enumerate(expected,1):
            self.open_npc(2221,4544)
            self.choose(4544,1)
            task=Quests(self.a).active('lotus')
            self.assertEqual((task['ring'],task['npc']),(ring,nid))
            choice=self.open_npc(task['scene'],nid)[0]
            self.assertEqual(choice,'把话带到（完成本环）' if ring<=4 else '挑战任务目标')
            self.choose(nid,1)
            if ring>4:
                battle=self.a._wild_battle
                for enemy in battle.enemies():enemy.update(hp=1,hpmax=1,initiative=-10)
                self.fight(battle)
            self.assertIsNone(Quests(self.a).active('lotus'))
            self.assertEqual(self.points(),80-ring*10+(12 if ring==8 else 0))

    def test_lotus_paid_help_requires_funds_and_keeps_the_original_task(self):
        p=self.a.progression
        self.buff(self.a)
        self.set_points(10)
        with p.transaction() as db:
            db.execute('INSERT INTO cultivation_state(character_id,rebirth) VALUES(?,2) '
                       'ON CONFLICT(character_id) DO UPDATE SET rebirth=2',(p.cid,))
            db.execute('UPDATE characters SET gold=4999 WHERE id=?',(p.cid,))
        self.open_npc(2221,4544)
        self.choose(4544,1)
        task=Quests(self.a).active('lotus')
        self.assertEqual(self.open_npc(2221,4544)[1],'寻求帮助')
        self.assertIn(98,[mid(reply) for reply in self.choose(4544,2)])
        self.assertEqual(p.snapshot()['gold'],4999)
        self.assertIsNone(Quests(self.a).active('lotus_help'))
        with p.transaction() as db:
            db.execute('UPDATE characters SET gold=5000 WHERE id=?',(p.cid,))
        self.open_npc(2221,4544)
        self.assertIn(1183,[mid(reply) for reply in self.choose(4544,2)])
        self.assertEqual(p.snapshot()['gold'],0)
        choices=self.open_npc(2018,3093)
        self.assertIn('前往领取天劫',choices)
        self.assertIn('寻求帮助',choices)
        reply=next(packet for packet in self.choose(3093,choices.index('寻求帮助')+1) if mid(packet)==1025)
        self.assertEqual(menu(reply)[1][0],'挑战任务目标')
        self.choose(3093,1)
        battle=self.a._wild_battle
        for enemy in battle.enemies():enemy.update(hp=1,hpmax=1,initiative=-10)
        self.fight(battle)
        self.assertIsNone(Quests(self.a).active('lotus_help'))
        self.assertEqual(Quests(self.a).active('lotus')['id'],task['id'])

    def test_sanjie_scene_override_restores_original_npc(self):
        from test_native946_npcs import read_npc

        self.accept_sanjie(7)
        task=Quests(self.a).active('sanjie')
        self.position(self.a,task['scene'],task['x'],task['y'])
        def find(replies):
            return [npc for p in replies if mid(p)==1589 and
                    (npc:=read_npc(p))['id']==6025][-1]
        shown=find(self.a.sync_world_npcs(force=True))
        self.assertEqual((shown['name'],shown['type']),('洞渊蛟',2030))
        self.assertEqual(Quests(self.a).sanjie_scene_appearance(),())
        Quests(self.a).cancel(task['id'])
        restored=find(Quests(self.a).sanjie_scene_appearance())
        self.assertEqual((restored['name'],restored['type']),('修罗木阵',4300))

    def test_gm_refresh_is_per_character_and_retries_serialization(self):
        from native946_gm_bridge import notify, poll

        for game in self.games:notify(self.characters.database,game.progression.cid)
        with patch.object(self.a,'sync_progression',side_effect=ValueError('fixture')):
            self.assertEqual(poll(self.a,10),())
        self.assertIn(98,[mid(p) for p in poll(self.b,10)])
        self.assertIn(98,[mid(p) for p in poll(self.a,11)])
        for game in self.games:self.assertEqual(poll(game,12),())

    def prepare_trial_team(self,point):
        LocalAccounts(self.characters.database).register(dict(username='daily2',password='fixture-only'))
        actor=self.characters.create('daily2','日常测试2',702)
        c=NativeGameplay(actor,self.characters,lambda:())
        self.games.append(c)
        for game in self.games:self.position(game,2001,*point)
        self.team()
        self.call(self.a,2039,actor['id'])
        self.call(c,2038,self.a.character['id'])
        for game in self.games:
            self.buff(game)
            with game.progression.transaction() as db:
                db.execute('INSERT INTO cultivation_state(character_id,rebirth) VALUES(?,1) '
                           'ON CONFLICT(character_id) DO UPDATE SET rebirth=1',(game.progression.cid,))

    def test_relic_requires_three_eligible_members_and_settles_shared_battle(self):
        from time import time
        from native946_double_time import load, save
        from native946_field_trials import _progress

        self.open_npc(2001,6859)
        self.choose(6859,1)
        self.assertIn(98,[mid(p) for p in self.choose(6859,1)])
        self.assertEqual(_progress(self.a)['runs'],{})
        self.prepare_trial_team((627,31))
        self.open_npc(2001,6859)
        self.choose(6859,1)
        replies=self.choose(6859,1)
        self.assertEqual(self.a.character['map_id'],2384,
                         [p.decode('gb18030','replace') for p in replies if mid(p)==98])
        runs=[_progress(game)['runs']['relic'] for game in self.games]
        self.assertEqual(len({run['id'] for run in runs}),1)
        self.assertTrue(all(game.character['map_id']==2384 for game in self.games))
        with self.a.progression.transaction() as db:
            now=int(time())
            double=load(db,self.a.progression.cid,now)
            double['expires']=now+3600
            save(db,self.a.progression.cid,double)
        self.open_npc(2384,6809)
        self.choose(6809,1)
        battle=self.a._wild_battle
        self.assertEqual(len(battle.games),3)
        for enemy in battle.enemies():enemy.update(hp=1,hpmax=1,initiative=-10)
        self.fight(battle)
        for game in self.games:
            self.assertEqual(_progress(game)['relic_stars'],1)
            self.assertEqual(_progress(game)['relic_high'],29)
        awards=battle.state['rewards']
        xp=[awards[g.progression.cid]['experience'] for g in self.games]
        self.assertEqual(xp[0],2*xp[1])
        for game in self.games:
            self.position(game,2001,627,31)
            self.svc.drain(game)
        self.assertEqual(self.open_npc(2001,6859)[2],'查看队员完成次数')
        reply=next(p for p in self.choose(6859,3) if mid(p)==1025)
        for game in self.games:self.assertIn(game.character['name'].encode('gb18030'),reply)

    def test_visitor_team_clears_mobs_and_completes_all_three_scenes(self):
        from native946_field_trials import VISITOR_STAGES, _mobs, _progress

        self.prepare_trial_team((573,67))
        self.open_npc(2001,7510)
        self.choose(7510,1)
        self.choose(7510,1)
        for index,stage in enumerate(VISITOR_STAGES):
            scene,nid=stage['scene'],stage['npc']
            mobs=_mobs(self.a)[1]
            self.assertEqual(len(mobs),5)
            self.assertTrue(all(_mobs(game)[1]==mobs for game in self.games))
            for mob in mobs:
                for game in self.games:self.position(game,scene,mob['x'],mob['y'])
                self.assertIn(1025,[mid(p) for p in self.call(self.a,1034,mob['id'])])
                self.choose(mob['id'],1)
                battle=self.a._wild_battle
                for enemy in battle.enemies():enemy.update(hp=1,hpmax=1,initiative=-10)
                self.fight(battle)
            for game in self.games:
                self.assertEqual(_mobs(game)[1],[])
                self.position(game,scene,*stage['cell'])
                self.svc.drain(game)
            self.assertEqual(self.open_npc(scene,nid)[0],'迎战')
            self.choose(nid,1)
            battle=self.a._wild_battle
            for enemy in battle.enemies():enemy.update(hp=1,hpmax=1,initiative=-10)
            self.fight(battle)
            expected=VISITOR_STAGES[min(index+1,len(VISITOR_STAGES)-1)]['scene']
            for game in self.games:
                self.assertEqual(game.character['map_id'],expected)
                self.assertEqual(_progress(game)['runs']['visitor']['stage'],index+1)
                self.assertEqual(_progress(game)['visitor_high'],9-index)

    def test_star_round_snapshot_and_global_single_settlement(self):
        now = 1_800_000_000
        target = spawn(now // 3600)
        self.team()
        for game in self.games:
            self.buff(game)
            self.position(game, target['scene'], target['x'], target['y'])
        with patch('native946_star_challenge.time', return_value=now):
            self.assertIn(1589, [mid(p) for p in StarChallenge(self.a).scene_packets(force=True)])
            self.assertIn(1025, [mid(p) for p in self.call(self.a, 1034, target['npc'])])
            reply = self.choose(target['npc'], 1)
            self.assertIn(2171, [mid(p) for p in reply])
            battle = self.svc.battles[self.a.character['id']]
            self.assertEqual(len(battle.enemies()), len(STAR['roster']))
            self.assertTrue(all(isinstance(skill, int) for enemy in battle.enemies() for skill in enemy['skills']))
            before = [g.progression.snapshot()['gold'] for g in self.games]
            self.fight(battle)
            self.assertIsNone(StarChallenge(self.a).available())
            self.assertIn(98, [mid(p) for p in self.call(self.a, 1034, target['npc'])])
        for game, old in zip(self.games, before):
            self.assertEqual(game.progression.snapshot()['gold'], old + STAR['cash'])
        with self.a.progression.transaction() as db:
            battle.settle(db, True)
            self.assertEqual(db.execute('SELECT count(*) FROM native_encounter_rewards WHERE battle_id=?',
                                        (target['key'],)).fetchone()[0], 2)
        with patch('native946_star_challenge.time', return_value=now + 901):
            self.assertIsNone(StarChallenge(self.a).available())
        with patch('native946_star_challenge.time', return_value=now + 3600):
            self.assertNotEqual(StarChallenge(self.a).available()['npc'], target['npc'])

    def test_50_level_treasure_giver_and_owned_map_is_consumed_only_at_target(self):
        scene, nid = GIVER
        choices = self.open_npc(scene, nid)
        self.assertIn(98, [mid(p) for p in self.choose(nid, choices.index('领取挖宝任务') + 1)])
        self.buff(self.a)
        choices = self.open_npc(scene, nid)
        self.assertIn(1978, [mid(p) for p in self.choose(nid, choices.index('领取挖宝任务') + 1)])
        quest = Quests(self.a).active('treasure')
        self.assertEqual(quest['expires'] - quest['created'], 1800)
        self.position(self.a, quest['scene'], quest['x'], quest['y'])
        self.call(self.a, 1034, quest['npc'])
        self.assertIn(2171, [mid(p) for p in self.choose(quest['npc'], 1)])
        self.fight(self.svc.battles[self.a.character['id']])
        state = self.a.progression.snapshot()
        item = next(i for i in state['items'] if i['type'] == ITEM)
        iid = item['id']
        description = MessageReader(self.a.progression.item_description(iid)[3:])
        description.integer()
        self.assertIn('宝藏地点', description.string().decode('gb18030'))
        self.open_npc(scene, nid)
        self.call(self.a, 807, self.a.character['id'], iid)
        self.assertIn(iid, [i['id'] for i in self.a.progression.snapshot()['items']])
        self.position(self.a, *destination(iid))
        gold = self.a.progression.snapshot()['gold']
        self.assertIn(780, [mid(p) for p in self.call(self.a, 807, self.a.character['id'], iid)])
        self.call(self.a, 807, self.a.character['id'], iid)
        self.assertEqual(self.a.progression.snapshot()['gold'], gold + 500)
        self.assertNotIn(iid, [i['id'] for i in self.a.progression.snapshot()['items']])
