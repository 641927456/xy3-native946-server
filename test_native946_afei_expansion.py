"""946 dialogue, battle, item and persistence checks for the Afei features."""
from datetime import datetime
import unittest
from unittest.mock import patch

from native946_afei_expansion import AfeiWorld, AutoGhostBattle, CALENDAR_EVENTS, EVENTS, HK, STAR_EVENTS, ghost_tick, spawn, window
from native946_afei_society import CHAMBER, DIVORCE_CASH, MARRY_CASH
from native946_inventory_features import give
from native946_login import MessageReader
from native946_codec import number
from native946_combat_control import blocked
from native946_map import load_map
from native946_npc_quests import ACTIVITIES
from native946_ordinary_treasure import ADVANCED, ITEM, destination
from native946_world import world
import test_native946_daily_content as base
from test_native946_npc_services import menu, mid


class AfeiExpansionTests(unittest.TestCase):
    setUp = base.DailyContentTests.setUp
    call = base.DailyContentTests.call
    buff = base.DailyContentTests.buff
    fight = base.DailyContentTests.fight
    position = base.DailyContentTests.position
    team = base.DailyContentTests.team

    def near(self, game, scene, nid):
        npc = world().npc(scene, nid)
        self.position(game, scene, *load_map(scene).nearest_walkable(npc['x']//20, npc['y']//20))

    def choose(self, game, nid, option):
        reply = game.receive(1032, MessageReader(bytes([option]) + number(nid)))
        return reply if isinstance(reply, tuple) else (reply,) if reply else ()

    def label(self, game, nid, prefix, *, look=True, replies=()):
        replies = self.call(game, 1034, nid) if look else replies
        options = menu(next(p for p in replies if mid(p) == 1025))[1]
        return next(i for i, s in enumerate(options, 1) if s.startswith(prefix))

    def gold(self, game):
        with game.progression.transaction(write=False) as db:
            return db.execute('SELECT gold FROM characters WHERE id=?', (game.progression.cid,)).fetchone()[0]

    def items(self, game, kind):
        with game.progression.transaction(write=False) as db:
            return [tuple(row) for row in db.execute('SELECT id,amount FROM inventory WHERE character_id=? AND type=? ORDER BY id',
                                                     (game.progression.cid, kind))]

    def test_all_activity_sites_and_windows_use_reachable_946_tiles(self):
        self.assertEqual(len(CALENDAR_EVENTS), 14)
        self.assertEqual(len(STAR_EVENTS), 69)
        self.assertEqual({int(EVENTS[i]['name'].split('级')[0]) for i in STAR_EVENTS}, set(range(1, 17)))
        for event_id, event in enumerate(EVENTS):
            with self.subTest(event=event['name']):
                for rule in event['timing']:
                    for day in rule['days']:
                        start = int(datetime(2026, 11, 23+day, rule['hours'][0], rule['minutes'][0], tzinfo=HK).timestamp())
                        self.assertEqual(window(event, start), start)
                        if day == rule['days'][0]:
                            for scene in event['scenes']:
                                spot = spawn(event_id, scene, start)
                                self.assertTrue(load_map(scene).walkable(spot['x'], spot['y']))

    def test_high_star_uses_afei_spawn_slot_for_settlement(self):
        index = next(i for i in STAR_EVENTS if EVENTS[i]['name'] == '13级地勇星')
        event = EVENTS[index]
        start = int(datetime(2026, 11, 27, 10, 0, tzinfo=HK).timestamp())
        scene = event['scenes'][0]
        spot = spawn(index, scene, start)
        self.buff(self.a)
        with self.a.progression.transaction() as db:
            db.execute('UPDATE progression SET cor=30000,strength=5000,speed=3000 WHERE character_id=?',
                       (self.a.progression.cid,))
        state = self.a.progression.snapshot()
        self.a.progression.set_vitals(state['hpmax'], state['mpmax'])
        self.position(self.a, scene, spot['x'], spot['y'])
        cash = self.gold(self.a)
        with patch('native946_afei_expansion.time', return_value=start+1):
            self.choose(self.a, spot['npc'], self.label(self.a, spot['npc'], '开始挑战'))
            battle = self.svc.battles[self.a.progression.cid]
            self.assertEqual(event['reference_encounter'], 100072)
            self.assertEqual(len(battle.enemies()), len(event['roster']))
            cid = self.a.progression.cid
            for _ in range(45):
                if battle.state['phase'] == 'finished':
                    break
                if battle.state['phase'] == 'input':
                    if cid not in battle.required():
                        battle.tick(battle.state['deadline'] + 1)
                    elif blocked(battle.state['fighters'][cid], battle.state['round']):
                        self.call(self.a, 2178, 1)
                    else:
                        self.call(self.a, 2177, 3, 0)
                if battle.state['phase'] == 'drawing':
                    self.call(self.a, 2184)
        self.assertEqual(self.gold(self.a)-cash, 200*event['star_slot'])

    def test_world_boss_dialog_settlement_persists_and_cannot_replay(self):
        event_id = next(i for i, e in enumerate(EVENTS) if e['reference_encounter'] == 150001)
        event = EVENTS[event_id]
        scene = event['scenes'][0]
        start = int(datetime(2026, 11, 27, 10, 15, tzinfo=HK).timestamp())
        spot = spawn(event_id, scene, start)
        self.buff(self.a)
        self.position(self.a, scene, spot['x'], spot['y'])
        self.position(self.b, scene, spot['x'], spot['y'])
        gold = self.gold(self.a)
        with patch('native946_afei_expansion.time', return_value=start+1):
            self.assertIn(1589, [mid(p) for p in AfeiWorld(self.a).scene_packets(force=True)])
            option = self.label(self.a, spot['npc'], '开始挑战')
            self.choose(self.a, spot['npc'], option)
            battle = self.svc.battles[self.a.progression.cid]
            other = self.label(self.b, spot['npc'], '开始挑战')
            self.choose(self.b, spot['npc'], other)
            self.assertNotIn(self.b.progression.cid, self.svc.battles)
            self.fight(battle)
            self.choose(self.a, spot['npc'], option)
            self.assertFalse(any(s['key'] == spot['key'] for s in AfeiWorld(self.b).spots(scene)))
        self.assertEqual(self.gold(self.a), gold+event['cash'])
        with self.a.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM native_encounter_rewards WHERE battle_id=?', (spot['key'],)).fetchone()[0], 1)

    def test_cave_claim_is_private_to_character(self):
        index = next(i for i, event in enumerate(EVENTS) if event.get('mode') == 'personal_daily')
        scene = EVENTS[index]['scenes'][0]
        left = AfeiWorld(self.a).spots(scene)[0]
        right = AfeiWorld(self.b).spots(scene)[0]
        self.assertEqual(left['npc'], right['npc'])
        self.assertNotEqual(left['key'], right['key'])
        self.buff(self.a)
        self.position(self.a, scene, left['x'], left['y'])
        self.choose(self.a, left['npc'], self.label(self.a, left['npc'], '开始挑战'))
        self.fight(self.svc.battles[self.a.progression.cid])
        self.assertEqual(AfeiWorld(self.a).spots(scene), [])
        self.assertEqual(AfeiWorld(self.b).spots(scene)[0]['key'], right['key'])

    def test_advanced_map_exchange_hints_and_digs_one_native_stone(self):
        scene, nid = ADVANCED['giver']
        self.near(self.a, scene, nid)
        with self.a.progression.transaction() as db:
            for _ in range(ADVANCED['ordinary_cost']):
                give(db, self.a.progression, ITEM)
        choice = self.label(self.a, nid, '兑换高级藏宝图')
        self.choose(self.a, nid, choice)
        self.choose(self.a, nid, choice)
        iid, amount = self.items(self.a, ADVANCED['item'])[0]
        self.assertEqual((self.items(self.a, ITEM), amount), ([], 1))
        self.call(self.a, 807, self.a.progression.cid, iid)
        self.assertEqual(self.items(self.a, ADVANCED['item']), [(iid, 1)])
        self.position(self.a, *destination(iid))
        before = sum(len(self.items(self.a, kind)) for kind in ADVANCED['drops'])
        self.call(self.a, 807, self.a.progression.cid, iid)
        self.call(self.a, 807, self.a.progression.cid, iid)
        self.assertEqual(self.items(self.a, ADVANCED['item']), [])
        self.assertEqual(sum(len(self.items(self.a, kind)) for kind in ADVANCED['drops']), before+1)

    def test_auto_ghost_progresses_native_daily_counter_and_stops(self):
        scene, nid = ACTIVITIES['ghost']['giver']
        self.buff(self.a)
        self.near(self.a, scene, nid)
        with patch('native946_afei_expansion.monotonic', return_value=1000) as clock:
            choice = self.label(self.a, nid, '开启1小时自动抓鬼')
            self.choose(self.a, nid, choice)
            clock.return_value += 5
            ghost_tick(self.a)
            battle = self.svc.battles[self.a.progression.cid]
            self.assertIsInstance(battle, AutoGhostBattle)
            self.fight(battle)
            self.assertGreater(self.a._auto_ghost_until, clock.return_value)
            clock.return_value += 5
            ghost_tick(self.a)
            battle = self.svc.battles[self.a.progression.cid]
            self.assertEqual(battle.ring, 2)
            battle.finish(False)
            self.assertEqual(self.a._auto_ghost_until, 0)
        with self.a.progression.transaction(write=False) as db:
            row = db.execute("SELECT accepted,completed,next_ring FROM native_npc_activity_progress WHERE kind='ghost'").fetchone()
        self.assertEqual(tuple(row), (1, 1, 2))

    def test_shop_stock_and_proceeds_move_once(self):
        scene, nid = CHAMBER
        self.near(self.a, scene, nid)
        self.near(self.b, scene, nid)
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=1000')
            give(db, self.a.progression, 6106, 2)
        self.choose(self.a, nid, self.label(self.a, nid, '开设物品商铺'))
        self.assertEqual(self.gold(self.a), 1000)
        self.buff(self.a)
        self.choose(self.a, nid, self.label(self.a, nid, '开设物品商铺'))
        bag = self.choose(self.a, nid, self.label(self.a, nid, '选择背包物品'))
        bag_option = next(i for i, action in enumerate(self.a.dialog_actions, 1)
                          if action.get('item') == self.items(self.a, 6106)[0][0])
        self.assertIn(1025, [mid(p) for p in bag])
        pricing = self.choose(self.a, nid, bag_option)
        self.choose(self.a, nid, self.label(self.a, nid, '100现金', look=False, replies=pricing))
        self.assertEqual(self.items(self.a, 6106), [])
        offers = self.choose(self.b, nid, self.label(self.b, nid, '查看在售物品'))
        self.assertIn(1025, [mid(p) for p in offers])
        self.choose(self.b, nid, 1)
        self.choose(self.b, nid, 1)
        self.assertEqual(self.items(self.b, 6106)[0][1], 2)
        self.assertEqual(self.gold(self.b), 900)
        self.choose(self.a, nid, self.label(self.a, nid, '提取商铺收入'))
        self.assertEqual(self.gold(self.a), 1099)

    def test_marriage_both_confirm_and_offline_divorce_clears_both(self):
        scene, nid = 2081, 6753
        self.near(self.a, scene, nid)
        self.near(self.b, scene, nid)
        with self.a.progression.transaction() as db:
            self.b.character['sex'] = 3-self.a.character['sex']
            db.execute('UPDATE characters SET sex=? WHERE id=?', (self.b.character['sex'], self.b.progression.cid))
            db.execute('UPDATE characters SET gold=? WHERE id=?', (3_000_000,self.a.progression.cid))
        self.team()
        self.choose(self.a, nid, self.label(self.a, nid, '发出结婚请求'))
        self.assertEqual(self.gold(self.a), 3_000_000)
        self.choose(self.b, nid, self.label(self.b, nid, '确认对方请求'))
        self.assertEqual(self.gold(self.a), 3_000_000-MARRY_CASH)
        with self.a.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM native_afei_marriage').fetchone()[0], 2)
        self.svc.detach(self.b.progression.cid)
        nid = 6755
        self.near(self.a, scene, nid)
        confirmation = self.choose(self.a, nid, self.label(self.a, nid, '申请强制离婚'))
        option = self.label(self.a, nid, '确认强制离婚', look=False, replies=confirmation)
        self.choose(self.a, nid, option)
        self.choose(self.a, nid, option)
        self.assertEqual(self.gold(self.a), 3_000_000-MARRY_CASH-DIVORCE_CASH)
        with self.a.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM native_afei_marriage').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM native_hero_titles WHERE name LIKE '%的夫君' OR name LIKE '%的娘子'").fetchone()[0], 0)
