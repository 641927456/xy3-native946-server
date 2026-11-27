"""NPC presentation and transport authorization against original client tables."""
from datetime import date, datetime, time
from pathlib import Path
from struct import unpack
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json,unittest

from local_accounts import LocalAccounts
from native946_characters import Characters,ROLES
from native946_codec import SCHEMAS,number,packet
from native946_game_login import NativeGameLogin,message_id
from native946_login import MessageReader
from native946_map import load_map
from native946_sessions import LEASES
from native946_world import EVENT_TIMEZONE, world


ROOT=Path(__file__).resolve().parent
ORIGINAL=json.loads((ROOT/'analysis/npc-transport-audit-20261125/native-tables.json').read_text(encoding='utf8'))


def read_npc(payload):
    reader=MessageReader(payload[3:]);row={}
    for name,kind in SCHEMAS['oci_npc']:
        if kind=='string':value=reader.string().decode('gb18030')
        elif kind=='number':value=reader.integer()
        elif kind=='byte':value=reader.take(1)[0]
        elif kind=='word':value=unpack('<H',reader.take(2))[0]
        else:raise AssertionError('Unexpected NPC schema field')
        row[name]=value
    reader.finish();return row


class NpcEventScheduleTests(unittest.TestCase):
    def test_event_npc_days_match_native_calendar_for_the_full_replayed_range(self):
        calendar = json.loads((ROOT/'native946_calendar_data.json').read_text('utf8'))['days']
        catalog = world()
        entries = {
            '御前文举': (2001, 4699, time(10)),
            '璇玑幻域': (2001, 4760, time(10)),
            '联盟大战': (2006, 2524, time(20, 45)),
            '天地劫2': (2011, 5873, time(19)),
        }
        with patch('native946_world.datetime') as clock:
            for value, rows in calendar.items():
                names = {row['name'].strip() for row in rows}
                for name, (sid, nid, hour) in entries.items():
                    clock.now.return_value = datetime.combine(date.fromisoformat(value), hour, EVENT_TIMEZONE)
                    with self.subTest(date=value, activity=name):
                        self.assertEqual(catalog.event_npc_is_open(sid, nid), name in names)

    def test_all_exam_stages_and_resident_entries_use_distinct_policies(self):
        catalog = world()
        exam_groups = {2001: range(4695, 4700), 2002: (4700, 4701, 4702), 2027: (4692, 4693, 4694)}
        with patch('native946_world.datetime') as clock:
            for hour, minute, active in ((9, 59, False), (10, 0, True), (19, 59, True), (20, 0, False), (21, 0, False)):
                clock.now.return_value = datetime(2026, 11, 14, hour, minute, tzinfo=EVENT_TIMEZONE)
                for sid, population in exam_groups.items():
                    for nid in population:
                        self.assertIsNotNone(catalog.npc(sid, nid))
                        self.assertEqual(catalog.event_npc_is_open(sid, nid), active)
                for sid, nid in ((2001, 4002), (2001, 4706), (2003, 2157), (2003, 2158),
                                 (2026, 1952), (2026, 1953), (2223, 4765), (2334, 6200)):
                    self.assertIsNotNone(catalog.npc(sid, nid))
                    self.assertTrue(catalog.event_npc_is_open(sid, nid))

    def test_earth_ordeal_weekday_and_weekend_windows_have_separate_boundaries(self):
        catalog = world()
        with patch('native946_world.datetime') as clock:
            for day, hour, minute, active in ((7, 13, 59, False), (7, 14, 0, True), (7, 20, 0, False),
                                                (10, 18, 59, False), (10, 19, 0, True), (10, 23, 55, False)):
                clock.now.return_value = datetime(2026, 11, day, hour, minute, tzinfo=EVENT_TIMEZONE)
                self.assertEqual(catalog.event_npc_is_open(2011, 5873), active)


class NpcCatalogTests(unittest.TestCase):
    def test_all_published_npc_appearances_match_original_client_data(self):
        w=world()
        for sid,rows in w.npcs.items():
            for nid in rows:
                native=ORIGINAL['waijing_npc'][str(sid)][str(nid)]
                wire=read_npc(w.npc_packet(sid,nid))
                with self.subTest(scene=sid,npc=nid):
                    self.assertEqual((wire['id'],wire['type'],wire['x'],wire['y']),
                                     (nid,native['shape'],native['x'],native['y']))
                    self.assertEqual(wire['name'],native.get('name',''))
                    self.assertEqual(wire['title'],native.get('title',''))
                    self.assertEqual(wire['is_flying'],native.get('is_aviate',0))
                    self.assertEqual(wire['fly'],0)
                    self.assertEqual([wire[k] for k in ['color1','color2','color3']],
                                     [native.get(k,0) for k in ['color1','color2','color3']])
                    self.assertEqual(wire['weapon_idx'],native.get('weapon_index',0))
                    self.assertEqual(wire['weapon_color'],native.get('weapon_color',0))

    def test_hidden_unmapped_and_off_map_npcs_are_not_published(self):
        w=world()
        self.assertIsNone(w.npc(2001,4716))
        self.assertIsNone(w.npc(2255,5606))
        self.assertIsNone(w.npc(2429,7503))
        self.assertNotIn(2242,w.npcs)
        self.assertNotIn(2276,w.npcs)
        for sid,rows in w.npcs.items():
            grid=load_map(sid)
            for row in rows.values():
                self.assertTrue(0<=row['x']<grid.width*20 and 0<=row['y']<grid.height*20)

    def test_disconnected_npc_catalog_does_not_invent_public_routes(self):
        w=world()
        self.assertEqual(w.npc(2044,2711)['name'],'玄叶')
        self.assertNotIn(2044,w.scenes)
        self.assertFalse(any(r['to']==2044 for r in w.data['portals']+w.data['npc_transports']))

    def test_nearby_query_finds_each_original_npc_at_its_location(self):
        for sid,rows in world().npcs.items():
            for nid,row in rows.items():
                self.assertIn(nid,world().nearby(sid,row['x']//20,row['y']//20))

    def test_native_categories_and_descriptions_are_preserved(self):
        self.assertEqual(world().npc(2009,2123)['desc'],'抓鬼任务领取人')
        self.assertEqual(world().npc(2009,2123)['npc_type'],3)
        self.assertEqual(world().npc(2001,1001)['head_ani'],4)

    def test_school_menu_authorization_matches_native_table_for_every_role(self):
        w=world()
        for role in ROLES.values():
            expected={row['trans_index'] for row in ORIGINAL['cross_scene_npc_school']
                      if row['race'] in (0,role['race']) and row['sex']==role['sex']}
            actual={row['trans_index'] for row in w.available_transports(2001,1139,role)}
            self.assertEqual(actual,expected)
        self.assertEqual(w.available_transports(2001,1139,None),[])


class NpcGameplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'npcs.sqlite3'
        LocalAccounts(self.path).register({'username':'npc_owner','password':'fixture-only-password'})
        self.characters=Characters(self.path);row=self.characters.create('npc_owner','交互测试',702)
        self.login=NativeGameLogin(543,None,number,None,self.characters);self.login.username='npc_owner'
        self.addCleanup(lambda:LEASES.release_port(self.path,543))
        self.login.receive(message_id(915)+number(row['id']));self.game=self.login.gameplay

    def approach(self,sid,nid):
        c=self.game.character;row=world().npc(sid,nid)
        x,y=load_map(sid).nearest_walkable(row['x']//20,row['y']//20)
        rev=self.characters.change_scene(c['owner'],c['id'],c['map_id'],sid,x,y,c.get('position_revision',0))
        c.update(map_id=sid,x=x,y=y,position_revision=rev)
        self.game.grid=load_map(sid);self.game._world_visible=set();self.game._world_bucket=None

    def test_changan_sanjie_npcs_send_retail_facing_title_style_and_positions(self):
        self.approach(2001,4102)
        rows = [read_npc(payload) for payload in self.game.scene_objects()
                if payload.startswith(message_id(1589))]
        npcs = {row['id']:row for row in rows}
        expected = {
            4102: (12129, 1145),  # 宋寒
            4101: (12205, 1188),  # 吕毅
            5500: (12289, 1230),  # 吕风
        }
        for nid, position in expected.items():
            with self.subTest(npc=nid):
                row = npcs[nid]
                self.assertEqual((row['x'], row['y']), position)
                self.assertEqual((row['dir'], row['class_type'], row['title_style']), (1, 1, 7))
        self.assertEqual((npcs[4198]['dir'], npcs[4198]['title_style']), (4, 0))

    def test_npc_facing_reload_refreshes_only_changed_npcs_in_place(self):
        self.approach(2001,4102)
        self.game.scene_objects()
        original_visible = self.game._world_visible.copy()
        original_position = {key:self.game.character[key] for key in ('map_id', 'x', 'y')}
        # Simulate a client that received the old facing before directions were tracked.
        self.game._world_bucket = self.game._world_bucket[:4]
        updates = self.game.sync_static_npcs()
        affected = (4101, 4102, 5500)
        self.assertEqual(updates[:3], tuple(message_id(1604)+number(nid) for nid in affected))
        self.assertEqual([(read_npc(p)['id'], read_npc(p)['dir']) for p in updates[3:]],
                         [(nid, 1) for nid in affected])
        self.assertEqual({key:self.game.character[key] for key in original_position}, original_position)
        self.assertEqual(self.game._world_visible, original_visible)
        self.assertEqual(self.game.sync_static_npcs(), ())

    def test_direct_school_rpc_rejects_forged_race_and_sex_without_changing_save(self):
        self.approach(2001,1139);before=self.characters.list('npc_owner')[0]
        # 火薇 is a female 巫族. Human male and the male-only shared route
        # must both be rejected even when the menu is bypassed.
        for index in [1,17]:
            reply=self.game.receive(1645,MessageReader(number(1139)+number(index)))
            self.assertEqual(reply[0] or int.from_bytes(reply[1:3],'little'),98)
            self.assertEqual(self.characters.list('npc_owner')[0],before)

    def test_school_menu_only_lists_native_allowed_destinations(self):
        self.approach(2001,1139)
        self.game.receive(1034,MessageReader(number(1139)))
        self.assertEqual(self.game.dialog_actions,[13,14])
        result=self.game.receive(1032,MessageReader(b'\x01'+number(1139)))
        self.assertIsInstance(result,tuple)
        self.assertEqual(self.game.character['map_id'],2360)

    def test_npc_dialog_uses_native_description_and_obeys_interaction_distance(self):
        self.approach(2009,2123)
        data=self.game.receive(1034,MessageReader(number(2123)))
        r=MessageReader(data[3:]);self.assertEqual(r.take(1),b'\x01')
        text=r.string().decode('gb18030')
        self.assertIn('抓鬼任务',text)
        self.assertIn('每10环一轮',text)
        self.assertNotIn('参考规则',text)
        self.assertEqual(self.game.dialog_actions[0]['op'],'rules')
        self.assertFalse(any(a.get('kind') in ('story','campaign') for a in self.game.dialog_actions))
        self.assertTrue(self.game.dialog_open)
        self.game.character.update(x=0,y=0)
        data=self.game.receive(1034,MessageReader(number(2123)))
        r=MessageReader(data[3:]);r.take(1)
        self.assertEqual(r.string().decode('gb18030'),'请走近一些再交谈。')

    def test_double_time_npc_opens_original_menu_with_its_portrait(self):
        nid = 1077
        self.approach(2001, nid)
        self.game.dialog_actions = [dict(kind='campaign', op='hub')]
        self.game.menu('这里是长安，路上小心。', ['新手与主线剧情进度'], nid)

        reply = self.game.receive(1034, MessageReader(number(nid)))
        self.assertTrue(reply.startswith(message_id(1025)))
        reader = MessageReader(reply[3:])
        self.assertEqual(reader.take(1), b'\x01')
        self.assertEqual(reader.string().decode('gb18030'), '#R双倍时间#n可以使你在练功区和许多任务中获得双倍人物和守护经验。')
        self.assertEqual(reader.integer(), nid)
        self.assertEqual(reader.take(1), b'\x07')
        self.assertEqual([reader.string().decode('gb18030') for _ in range(7)], [
            '我来领取双倍时间', '我来查看双倍时间', '冻结双倍时间(10两)', '冻结守护/内丹催长丹效果',
            '取消冻结守护/内丹催长丹效果', '我要用统帅进行封赏', '您忙，我随便看看',
        ])
        self.assertEqual((reader.integer(), reader.integer()), (nid, 1))
        reader.finish()
        self.assertEqual(len(self.game.dialog_actions), 7)

    def double_time_choice(self, choice):
        self.game.receive(1034, MessageReader(number(1077)))
        return self.game.receive(1032, MessageReader(bytes([choice]) + number(1077)))

    def test_arena_scarecrow_enters_damage_test_and_awards_no_progress(self):
        nid = 7631
        self.approach(2001, nid)
        g = self.game
        actors = [read_npc(p) for p in g.sync_static_npcs(force=True) if p.startswith(message_id(1589))]
        npc, = [row for row in actors if row['id'] == nid]
        self.assertEqual((npc['name'], npc['title'], npc['type']), ('擂台稻草人', '伤害测试', 2007))

        look = message_id(1034) + number(nid)
        start = message_id(1032) + b'\x01' + number(nid)
        self.assertIsNone(self.login.receive(start))
        reply = self.login.receive(look)
        self.assertTrue(reply.startswith(message_id(1025)))
        self.assertIn('测试伤害'.encode('gb18030'), reply)
        self.assertTrue(self.login.receive(message_id(1032) + b'\x02' + number(nid)).startswith(message_id(1183)))

        self.login.receive(look)
        before = g.progression.snapshot()
        entry = self.login.receive(start)
        battle = g._wild_battle
        enemies = battle.enemies()
        self.assertEqual(len(enemies), 8)
        self.assertTrue(all((e['type'], e['level'], e['hp'], e['defense']) == (2007, 150, 10_000_000, 0)
                            for e in enemies))
        self.assertTrue(any(p.startswith(message_id(2171)) for p in entry))
        self.assertEqual(sum(p.startswith(message_id(2147)) for p in entry), 9)
        self.login.receive(start)
        self.assertIs(g._wild_battle, battle)

        battle.rng.seed(17)
        round_packets = self.login.receive(message_id(2176) + number(enemies[0]['id']))
        damage = 10_000_000 - enemies[0]['hp']
        self.assertGreater(damage, 0)
        self.assertIn(packet(2164, ['number'] * 4, [enemies[0]['id'], -damage, 1, 0]), round_packets)
        self.assertTrue(any(p.startswith(message_id(2167)) for p in round_packets))
        self.login.receive(message_id(2184))
        self.assertEqual(battle.state['round'], 2)

        # End the test through the same last-hit and playback path as a full victory.
        for e in enemies[1:]:
            e['hp'] = 0
        enemies[0]['hp'] = 1
        battle.rng.seed(17)
        self.login.receive(message_id(2176) + number(enemies[0]['id']))
        self.assertTrue(battle.state['victory'])
        self.login.receive(message_id(2184))
        self.assertIsNone(g.battle)
        after = g.progression.snapshot()
        for key in ('experience', 'gold', 'skills', 'quest', 'items'):
            self.assertEqual(after[key], before[key], key)
        self.login.receive(look)
        self.login.receive(start)
        self.assertIsNot(g._wild_battle, battle)
        exit_packets = self.login.receive(message_id(2178) + number(2))
        self.assertIn(message_id(2146), exit_packets)
        self.assertIsNone(g.battle)

    def test_double_time_claims_stack_and_freeze_can_only_charge_once(self):
        from native946_double_time import HOUR, load

        self.approach(2001, 1077)
        p = self.game.progression
        close = packet(1183, ['string'], ['dlg_npc_chat'])
        now = 1_800_000_000
        with p.transaction() as db:
            db.execute('UPDATE characters SET gold=100 WHERE id=?', (p.cid,))
        with patch('native946_double_time.time', return_value=now) as clock:
            for _ in range(2):
                self.assertEqual(self.double_time_choice(1)[0], close)
                self.assertIsNone(self.game.receive(1032, MessageReader(b'\x01' + number(1077))))
            with p.transaction(write=False) as db:
                value = load(db, p.cid, now)
            self.assertEqual((value['expires'], value['used']), (now + HOUR * 2, HOUR * 2))
            self.assertIn('生效双倍：2小时00分00秒'.encode('gb18030'), self.double_time_choice(2))
            clock.return_value += 10
            self.assertEqual(self.double_time_choice(3)[0], close)
            self.assertIsNone(self.game.receive(1032, MessageReader(b'\x03' + number(1077))))
            clock.return_value += 900
            self.assertEqual(self.double_time_choice(1)[0], close)
            with p.transaction(write=False) as db:
                value = load(db, p.cid, clock.return_value)
                gold = db.execute('SELECT gold FROM characters WHERE id=?', (p.cid,)).fetchone()[0]
            self.assertEqual((value['frozen'], value['expires'], value['used'], gold),
                             (0, now + HOUR * 2 + 900, HOUR * 2, 90))
            self.assertEqual(self.double_time_choice(7), close)

    def test_guardian_medicine_freeze_resume_and_clear(self):
        from native946_double_time import experience_rates, load
        from native946_inventory_features import give

        self.approach(2001, 1077)
        p = self.game.progression
        now = 1_800_000_000
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=185 WHERE id=?', (p.cid,))
            db.execute('UPDATE cultivation_state SET sanctification=1 WHERE character_id=?', (p.cid,))
            give(db, p, 6205)
            give(db, p, 6206)
            items = dict(db.execute('SELECT type,id FROM inventory WHERE type IN (6205,6206) AND character_id=?', (p.cid,)))
        with patch('native946_double_time.time', return_value=now) as clock:
            self.game.receive(807, MessageReader(number(p.cid) + number(items[6205])))
            with p.transaction(write=False) as db:
                self.assertEqual(experience_rates(db,p.cid,now), (20,1,2))
            clock.return_value += 50
            self.double_time_choice(4)
            clock.return_value += 500
            with p.transaction(write=False) as db:
                self.assertEqual(experience_rates(db,p.cid,clock.return_value), (100,1,1))
                self.assertEqual(load(db,p.cid,clock.return_value)['medicine_frozen'], 7150)
            self.double_time_choice(5)
            with p.transaction(write=False) as db:
                self.assertEqual(experience_rates(db,p.cid,clock.return_value), (20,1,2))
            self.game.receive(807, MessageReader(number(p.cid) + number(items[6206])))
            with p.transaction(write=False) as db:
                self.assertEqual(experience_rates(db,p.cid,clock.return_value), (100,1,1))
                self.assertEqual(db.execute('SELECT count(*) FROM inventory WHERE type IN (6205,6206) AND character_id=?', (p.cid,)).fetchone()[0], 0)

    def test_captain_draw_requires_confirmation_and_cannot_be_replayed(self):
        from native946_double_time import award_captain, load

        self.approach(2001, 1077)
        p = self.game.progression
        now = 1_800_000_000
        with p.transaction() as db:
            award_captain(db, p.cid, now)
        with patch('native946_double_time.time', return_value=now), patch('native946_double_time.choice', return_value=46988):
            panel = self.double_time_choice(6)
            self.assertIn('统帅值：250'.encode('gb18030'), panel)
            callback = self.game.dialog_npc
            self.assertNotEqual(callback, 1077)
            self.assertIsNone(self.game.receive(1032, MessageReader(b'\x06' + number(1077))))
            self.game.receive(1032, MessageReader(b'\x01' + number(callback)))
            self.assertIsNone(self.game.receive(1032, MessageReader(b'\x01' + number(callback))))
            with p.transaction(write=False) as db:
                result = load(db, p.cid, now)
                quantity = db.execute('SELECT sum(amount) FROM inventory WHERE type=46988 AND character_id=?', (p.cid,)).fetchone()[0]
            self.assertEqual((result['points'], result['draws'], quantity), (0, 1, 1))

    def test_npc_visibility_changes_without_restarting_scene_or_echoing_movement(self):
        self.approach(2001,1001)
        first=self.game.scene_objects();old=set(self.game._world_visible)
        self.assertIn(1001,old)
        self.approach(2001,1139)
        self.game._world_visible=old
        updates=self.game.sync_world_npcs()
        kinds=[int.from_bytes(p[1:3],'little') for p in updates]
        self.assertIn(1604,kinds);self.assertIn(1589,kinds)
        self.assertNotIn(1597,kinds);self.assertNotIn(1608,kinds)
        self.assertIn(1139,self.game._world_visible)

    def test_exam_npc_appears_and_disappears_while_the_player_stands_still(self):
        from native946_npc_services import NpcServices

        examiner, coachman = 4699, 4002
        self.approach(2001, examiner)
        position = dict(self.game.character)
        saved = self.characters.list('npc_owner')
        with patch('native946_world.datetime') as clock:
            clock.now.return_value = datetime(2026, 11, 14, 9, 59, tzinfo=EVENT_TIMEZONE)
            self.game.scene_objects()
            self.assertIn(coachman, self.game._world_visible)
            self.assertNotIn(examiner, self.game._world_visible)
            self.assertIsNone(self.game.receive(1034, MessageReader(number(examiner))))
            with self.assertRaisesRegex(ValueError, '当前未开放'):
                NpcServices(self.game).validate(examiner)

            clock.now.return_value = datetime(2026, 11, 14, 10, 0, tzinfo=EVENT_TIMEZONE)
            opened = self.game.tick()
            self.assertIn(world().npc_packet(2001, examiner), opened)
            self.assertNotIn(world().npc_packet(2001, coachman), opened)
            self.assertEqual(NpcServices(self.game).validate(examiner)['npc_id'], examiner)
            self.assertIsNone(self.game.receive(1034, MessageReader(number(examiner))))
            # Exercise cleanup when a dialogue is still on screen at the closing boundary.
            self.game.menu('', npc_id=examiner)

            clock.now.return_value = datetime(2026, 11, 14, 20, 0, tzinfo=EVENT_TIMEZONE)
            closed = self.game.tick()
            self.assertIn(message_id(1604)+number(examiner), closed)
            self.assertIn(message_id(1183), [p[:3] for p in closed])
            self.assertTrue(all(p[:3] not in (message_id(1597), message_id(1608)) for p in (*opened, *closed)))
            self.assertFalse(self.game.dialog_open)
            self.assertIsNone(self.game.dialog_npc)
            self.assertIn(coachman, self.game._world_visible)
            self.assertNotIn(examiner, self.game._world_visible)
            self.assertIsNone(self.game.receive(1034, MessageReader(number(examiner))))
            self.assertNotIn(message_id(1604)+number(examiner), self.game.tick() or ())
        self.assertEqual(self.game.character, position)
        self.assertEqual(self.characters.list('npc_owner'), saved)

    def test_activity_navigation_already_opened_before_close_is_rechecked(self):
        from native946_activities import Activities, CALLBACK

        guide = Activities(self.game)
        with patch('native946_world.datetime') as clock:
            clock.now.return_value = datetime(2026, 11, 28, 10, 0, tzinfo=EVENT_TIMEZONE)
            guide.detail(28)
            prior = next(action for action in self.game.dialog_actions if action.get('op') == 'navigate')
            result = guide.execute(CALLBACK, prior)
            self.assertEqual(result[0][:3], message_id(1183))

            clock.now.return_value = datetime(2026, 11, 28, 23, 55, tzinfo=EVENT_TIMEZONE)
            with self.assertRaisesRegex(ValueError, '当前未开放'):
                guide.execute(CALLBACK, prior)
            guide.detail(28)
            self.assertFalse(any(action.get('op') == 'navigate' for action in self.game.dialog_actions))

    def test_other_timer_contention_keeps_star_spawn_and_expiry_packets(self):
        from native946_gameplay import numbers
        from native946_star_challenge import RULE, spawn
        from native946_storage import StorageBusy

        hour = 497000
        star = spawn(hour)
        self.game.character.update(map_id=star['scene'], x=star['x'], y=star['y'])
        with patch('native946_star_challenge.time') as clock, patch(
                'native946_afei_expansion.AfeiWorld.tick', side_effect=StorageBusy('fixture busy')):
            clock.return_value = hour*3600
            started = self.game.tick() or ()
            self.assertIn(star['npc'], [read_npc(p)['id'] for p in started if p[:3] == message_id(1589)])
            self.assertEqual(self.game._star_visible, star['npc'])

            clock.return_value += RULE['window_minutes']*60
            self.game._star_poll_at = 0
            removed = numbers(1604, star['npc'])
            self.assertIn(removed, self.game.tick() or ())
            self.assertIsNone(self.game._star_visible)
            self.assertNotIn(removed, self.game.tick() or ())


if __name__=='__main__':unittest.main()
