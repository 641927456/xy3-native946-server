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
        npc = world().npc(scene, nid)
        self.position(self.a, scene, *load_map(scene).nearest_walkable(npc['x'] // 20, npc['y'] // 20))
        reply = self.call(self.a, 1034, nid)
        return menu(next(p for p in reply if mid(p) == 1025))[1]

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

    def test_exchange_redeems_existing_points_once_and_creates_real_equipment(self):
        nid = 4101
        self.set_points(2000)
        choices = self.open_npc(2001, 4102)
        self.assertIn(2966, [mid(p) for p in self.choose(4102, choices.index('前往吕毅兑换三界令') + 1)])
        options = self.open_npc(2001, nid)
        slot = next(i for i, label in enumerate(options, 1) if label.startswith('国寺法带'))
        self.assertTrue(any(mid(p) == 786 for p in self.choose(nid, slot)))
        self.choose(nid, slot)
        self.assertEqual(self.points(), 100)
        state = self.a.progression.snapshot()
        item = next(i for i in state['items'] if i['type'] == 11685)
        self.assertEqual(state['equipment'][item['id']]['properties'], {'防御': 178, '敏捷': 7})
        self.assertEqual(state['equipment'][item['id']]['slot'], 6)

    def test_native_dialogue_exchanges_and_digs_super_map(self):
        from native946_inventory_features import give
        from native946_ordinary_treasure import ADVANCED, SUPER

        scene,nid = ADVANCED['giver']
        p = self.a.progression
        with p.transaction() as db:
            give(db,p,ADVANCED['item'],SUPER['advanced_cost'])
        options = self.open_npc(scene,nid)
        self.choose(nid,options.index('兑换超级藏宝图')+1)
        state = p.snapshot()
        self.assertFalse(any(i['type']==ADVANCED['item'] for i in state['items']))
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
        for scene,nid,line in CONTACTS:
            options = self.open_npc(scene,nid)
            self.choose(nid,options.index(line+'（本服）')+1)
        with p.transaction(write=False) as db:
            self.assertIn('mount:'+str(RULE['mount_index']),features(db,p.cid))
        trace,_ = p.task_records(p.snapshot())
        self.assertNotIn(MOUNT_QID,{row['id'] for row in trace})
        scene,nid,_ = CONTACTS[0]
        self.assertNotIn(CONTACTS[0][2]+'（本服）',self.open_npc(scene,nid))

    def test_exchange_full_bag_fails_without_spending_points(self):
        self.set_points(2000)
        self.open_npc(2001, 4101)
        with self.a.progression.transaction() as db:
            cid = self.a.character['id']
            for slot in range(1, 25):
                db.execute('INSERT OR IGNORE INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,8,?,1)',
                           (cid, slot))
        self.assertIn(98, [mid(p) for p in self.choose(4101, 2)])
        self.assertEqual(self.points(), 2000)
        self.assertNotIn(11685, [i['type'] for i in self.a.progression.snapshot()['items']])

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
