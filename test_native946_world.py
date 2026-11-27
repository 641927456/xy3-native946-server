from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from struct import pack,unpack
from tempfile import TemporaryDirectory
import json,sqlite3,unittest
from unittest.mock import patch

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_codec import number,packet
from native946_game_login import NativeGameLogin,message_id
from native946_login import MessageReader
from native946_map import MapGrid,load_map
from native946_sessions import LEASES
from native946_storage import transaction
from native946_world import EVENT_TIMEZONE,PORTAL_APPROACH_CELLS,world


def mid(data):return int.from_bytes(data[1:3],'little') if data[0]==0 else data[0]


def scene_header(packet):
    reader = MessageReader(packet[3:])
    scene, x, y = unpack('<HHH', reader.take(6))
    name = reader.string().decode('gb18030')
    terrain, = unpack('<H', reader.take(2))
    reader.integer()
    reader.take(1)
    phase = reader.integer()
    reader.finish()
    return scene, x, y, name, terrain, phase


class CatalogTests(unittest.TestCase):
    def test_scene_day_effect_uses_resource_and_utc8_boundaries(self):
        catalog = world()
        for hour, minute, second, phase in (
            (5, 59, 59, 1), (6, 0, 0, 0), (17, 59, 59, 0), (18, 0, 0, 1),
        ):
            now = datetime(2026, 11, 27, hour, minute, second, tzinfo=EVENT_TIMEZONE)
            for scene in (2001, 2002, 2003, 2007, 2457, 2004, 2462):
                with self.subTest(scene=scene, now=now):
                    self.assertEqual(catalog.scene_day_effect(scene, now.astimezone(timezone.utc)), phase)
            # New 花果山 uses 2415. Several instances reuse a supported terrain.
            self.assertEqual(catalog.scene_day_effect(2011, now), 8)
            self.assertEqual(catalog.scene_day_effect(2287, now), 8)

    def test_all_enabled_scenes_are_reachable_through_validated_routes(self):
        w=world();graph=defaultdict(list)
        for row in w.data['portals']+w.data['npc_transports']:graph[row['from']].append(row['to'])
        reached={2001};todo=[2001]
        for sid in todo:
            for target in graph[sid]:
                if target not in reached:reached.add(target);todo.append(target)
        self.assertEqual(reached,set(w.scenes))
        for sid in [2001,2008,2009,2015,2041,2223,2457]:self.assertIn(sid,reached)

    def test_native_resource_alias_and_partial_indoor_cells(self):
        self.assertEqual(world().scene(2001)['map_id'],2443)
        grid=load_map(2034)
        self.assertEqual((grid.width,grid.height),(52,39))
        self.assertEqual(len(grid.cells),52*39)

    def test_native_cloud_floor_twelve_reads_inline_collision_data(self):
        path = Path(__file__).with_name('client-946') / 'map' / '2351.map'
        grid = MapGrid(path)
        self.assertEqual((grid.width, grid.height), (160, 97))
        self.assertEqual(len(grid.cells), 160 * 97)
        self.assertTrue(grid.walkable(20, 25))
        self.assertFalse(grid.walkable(5, 25))
        self.assertEqual(grid.route((20, 25), (21, 25)), [(20, 25), (21, 25)])

    def test_all_route_arrivals_are_legal_and_all_npcs_encode(self):
        w=world();w.warm()
        for row in w.data['portals']+w.data['npc_transports']:
            with self.subTest(route=row['id'],target=row['to']):self.assertTrue(load_map(row['to']).walkable(*row['arrival']))
        self.assertEqual(len(w._npc_packets),sum(len(rows) for rows in w.npcs.values()))

    def test_teleport_record_order_matches_original_client(self):
        data=packet(1593,[('array','tele_info')],[[{'x':17,'y':23,'aoi_x':2,'aoi_y':3}]])
        r=MessageReader(data[4:])
        self.assertEqual(data[3],1)
        self.assertEqual([r.integer() for _ in range(4)],[23,17,3,2]);r.finish()

    def test_arrivals_are_outside_other_physical_portal_triggers(self):
        w=world()
        for incoming in w.data['portals']+w.data['npc_transports']:
            for outgoing in w.portals[incoming['to']]:
                with self.subTest(incoming=incoming['id'],outgoing=outgoing['id']):
                    distance=max(abs(incoming['arrival'][i]-outgoing['source'][i]) for i in (0,1))
                    self.assertGreater(distance,outgoing['half_extent'])

    def test_native_approach_windows_do_not_overlap_different_entrances(self):
        for sid,rows in world().portals.items():
            for index,first in enumerate(rows):
                for second in rows[index+1:]:
                    with self.subTest(scene=sid,first=first['id'],second=second['id']):
                        distance=max(abs(first['source'][k]-second['source'][k]) for k in (0,1))
                        self.assertGreater(distance,max(first['half_extent'],PORTAL_APPROACH_CELLS)+
                                           max(second['half_extent'],PORTAL_APPROACH_CELLS))


class WorldGameplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'town.sqlite3'
        LocalAccounts(self.path).register({'username':'town_owner','password':'123456789'})
        self.characters=Characters(self.path)
        row=self.characters.create('town_owner','城镇测试',702)
        self.login=NativeGameLogin(321,None,number,None,self.characters);self.login.username='town_owner'
        self.addCleanup(lambda:LEASES.release_port(self.path,321))
        self.login.receive(message_id(915)+number(row['id']))
        self.game=self.login.gameplay

    def place(self,sid,point):
        c=self.game.character
        revision=self.characters.change_scene(c['owner'],c['id'],c['map_id'],sid,*point,c.get('position_revision',0))
        c.update(map_id=sid,x=point[0],y=point[1],position_revision=revision)
        self.game.grid=load_map(sid);self.game.npc_position=self.game.grid.nearby(300,210) if sid==2001 else None
        self.game._arrival_guard=None;self.game._last_transition=None;self.game._world_bucket=None
        self.game._world_visible=set()
        self.game.dialog_open=False;self.game.dialog_actions=[]

    def stored(self):return self.characters.list('town_owner')[0]

    def test_native_day_night_tick_does_not_reenter_scene_or_write_position(self):
        with patch('native946_world.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 27, 17, 59, 59, tzinfo=EVENT_TIMEZONE)
            self.assertEqual(scene_header(self.login.scene_packets()[0]), (2001, 300, 210, '长安', 2443, 0))
            before = self.stored()
            self.assertFalse(any(mid(p) == 452 for p in self.game.tick() or ()))

            clock.now.return_value = datetime(2026, 11, 27, 18, 0, tzinfo=EVENT_TIMEZONE)
            self.game._cinematic_pending = {'script': 1}
            self.assertFalse(any(mid(p) == 452 for p in self.game.tick() or ()))
            self.game._cinematic_pending = None
            updates = self.game.tick()
            self.assertNotIn(1597, [mid(p) for p in updates])
            reader = MessageReader(next(p for p in updates if mid(p) == 452)[3:])
            self.assertEqual(reader.integer(), 1)
            self.assertTrue(reader.string())
            reader.finish()
            self.assertFalse(any(mid(p) == 452 for p in self.game.tick() or ()))
            self.assertEqual(self.stored(), before)

            clock.now.return_value = datetime(2026, 11, 28, 6, 0, tzinfo=EVENT_TIMEZONE)
            # A normal scene restore (including battle exit) sends the new phase
            # itself, so the following timer must not repeat the update.
            self.assertEqual(scene_header(self.game.restore_scene()[0])[-1], 0)
            self.assertFalse(any(mid(p) == 452 for p in self.game.tick() or ()))

    def test_night_portal_keeps_native_scene_and_day_terrain_ids(self):
        route = next(row for row in world().data['portals'] if row['from'] == 2001 and row['to'] == 2002)
        self.place(2001, load_map(2001).nearest_walkable(*route['source']))
        with patch('native946_world.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 27, 18, 0, tzinfo=EVENT_TIMEZONE)
            replies = self.game.receive(1644, MessageReader(pack('<HHH', 2001, *route['source'])))
            header = scene_header(next(p for p in replies if mid(p) == 1597))
            self.assertEqual(header, (2002, *route['arrival'], '皇城', 2002, 1))
            self.assertEqual(self.stored()['map_id'], 2002)
            self.assertFalse(any(mid(p) == 452 for p in self.game.tick() or ()))

    def test_movement_on_a_night_only_cell_and_departure_at_dawn(self):
        ordinary, night_only = (172, 23), (173, 24)
        self.place(2002, ordinary)
        self.assertFalse(load_map(2002).walkable(*night_only))
        with patch('native946_world.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 27, 17, 59, tzinfo=EVENT_TIMEZONE)
            self.login.scene_packets()
            step = packet(1643, ['number', 'word', 'word', 'string'], [8, *ordinary, '0'])[3:]
            self.assertEqual(mid(self.game.receive(1643, MessageReader(step))), 1606)
            self.assertEqual((self.stored()['x'], self.stored()['y']), ordinary)

            clock.now.return_value = datetime(2026, 11, 27, 18, tzinfo=EVENT_TIMEZONE)
            self.assertEqual(mid(self.game.sync_scene_day_effect()[0]), 452)
            self.game.receive(1643, MessageReader(step))
            self.assertEqual((self.stored()['x'], self.stored()['y']), night_only)

            clock.now.return_value = datetime(2026, 11, 28, 6, tzinfo=EVENT_TIMEZONE)
            self.assertEqual(mid(self.game.sync_scene_day_effect()[0]), 452)
            back = packet(1643, ['number', 'word', 'word', 'string'], [8, *night_only, '4'])[3:]
            self.game.receive(1643, MessageReader(back))
            self.assertEqual((self.stored()['x'], self.stored()['y']), ordinary)

    def test_water_curtain_monkey_dialogue_enters_and_returns(self):
        self.place(2011,(11,53))
        before=self.game.progression.snapshot()
        menu=self.game.receive(1034,MessageReader(number(5718)))
        self.assertEqual(mid(menu),1025)
        reader=MessageReader(menu[3:])
        for _ in range(reader.take(1)[0]):
            reader.string()
            reader.integer()
        choices=[reader.string().decode('gb18030') for _ in range(reader.take(1)[0])]
        self.assertEqual(choices[0],'水帘洞')
        self.assertNotIn('原版活动说明与接入状态',choices)
        self.assertEqual(reader.integer(),5718)
        reader.integer()
        reader.finish()

        reply=self.game.receive(1032,MessageReader(b'\x01'+number(5718)))
        reader=MessageReader(next(p for p in reply if mid(p)==1597)[3:])
        self.assertEqual(unpack('<HHH',reader.take(6)),(2266,68,26))
        self.assertEqual(reader.string().decode('gb18030'),'水帘洞')
        self.assertEqual(unpack('<H',reader.take(2))[0],8056)
        self.assertEqual(self.stored()['map_id'],2266)
        self.assertIsNone(self.game.receive(1032,MessageReader(b'\x01'+number(5718))))
        self.assertIsNone(self.game.battle)

        self.assertEqual(mid(self.game.receive(1034,MessageReader(number(5703)))),1025)
        reply=self.game.receive(1032,MessageReader(b'\x01'+number(5703)))
        reader=MessageReader(next(p for p in reply if mid(p)==1597)[3:])
        self.assertEqual(unpack('<HHH',reader.take(6)),(2011,11,53))
        self.assertEqual(self.stored()['map_id'],2011)
        after=self.game.progression.snapshot()
        self.assertEqual(after.pop('position_revision'),before.pop('position_revision')+2)
        self.assertEqual(after,before)

    def test_water_curtain_floor_guards_offer_bidirectional_transport(self):
        from test_native946_npc_services import menu

        for start, point, forward, target, arrival, resource, label, reverse, reverse_label in (
            (2266, (10, 25), 5702, 2267, (70, 14), 8053, '灵泉洞', 5704, '水帘洞'),
            (2267, (5, 44), 5705, 2268, (56, 70), 8038, '云霄径', 5709, '灵泉洞'),
            (2268, (22, 12), 5707, 2269, (20, 56), 8035, '翠石峰', 5710, '云霄径'),
        ):
            with self.subTest(npc=forward):
                self.place(start, point)
                offered = self.game.receive(1034, MessageReader(number(forward)))
                self.assertEqual(menu(offered)[1:3], ([label], forward))
                replies = self.game.receive(1032, MessageReader(b'\x01' + number(forward)))
                reader = MessageReader(next(p for p in replies if mid(p) == 1597)[3:])
                self.assertEqual(unpack('<HHH', reader.take(6)), (target, *arrival))
                self.assertEqual(reader.string().decode('gb18030'), label)
                self.assertEqual(unpack('<H', reader.take(2))[0], resource)
                self.assertEqual(self.stored()['map_id'], target)
                self.assertIsNone(self.game.battle)

                offered = self.game.receive(1034, MessageReader(number(reverse)))
                self.assertEqual(menu(offered)[1:3], ([reverse_label], reverse))
                self.game.receive(1032, MessageReader(b'\x01' + number(reverse)))
                saved = self.stored()
                self.assertEqual((saved['map_id'], saved['x'], saved['y']), (start, *point))

    def test_login_sends_native_town_name_resource_and_portal_definitions(self):
        packets=self.login.world_packets(self.game.character)
        data=next(p for p in packets if mid(p)==1597)
        r=MessageReader(data[3:]);self.assertEqual(unpack('<HHH',r.take(6)),(2001,300,210))
        self.assertEqual(r.string().decode('gb18030'),'长安')
        self.assertEqual(unpack('<H',r.take(2))[0],2443)
        self.assertIn(1593,[mid(p) for p in packets])

    def test_every_physical_portal_commits_native_destination_and_scene_packet(self):
        for route in world().data['portals']:
            with self.subTest(route=route['id']):
                start=load_map(route['from']).nearest_walkable(*route['source'])
                self.place(route['from'],start)
                replies=self.game.receive(1644,MessageReader(pack('<HHH',route['from'],*route['source'])))
                self.assertIsInstance(replies,tuple)
                ids = [mid(p) for p in replies]
                if route['from'] == route['to']:
                    self.assertIn(1606, ids)
                    self.assertNotIn(1597, ids)
                    reader=MessageReader(replies[ids.index(1606)][3:])
                    self.assertEqual(reader.integer(),self.game.character['id'])
                    self.assertEqual(unpack('<HH',reader.take(4)),tuple(route['arrival']))
                    reader.finish()
                else:
                    self.assertIn(1597, ids)
                self.assertEqual(self.game.character['map_id'],route['to'])
                self.assertEqual([self.stored()['x'],self.stored()['y']],route['arrival'])

    def test_original_client_approach_requests_enter_expected_scenes(self):
        # Actual 946 requests from client-946/log/log.txt on 2026-11-26.
        # Five failed with the former center-only match; two already worked.
        samples=[(2001,(650,41),2002),(2001,(653,42),2002),
                 (2002,(10,306),2001),(2002,(7,305),2001),
                 (2001,(656,39),2002),(2001,(651,42),2002),
                 (2003,(18,188),2002)]
        for sid,point,target in samples:
            with self.subTest(scene=sid,point=point):
                self.assertTrue(load_map(sid).walkable(*point))
                self.place(sid,point)
                replies=self.game.receive(1644,MessageReader(pack('<HHH',sid,*point)))
                self.assertIsInstance(replies,tuple)
                self.assertIn(1597,[mid(p) for p in replies])
                self.assertEqual(self.game.character['map_id'],target)
                self.assertEqual(self.stored()['map_id'],target)

    def test_approach_does_not_allow_forged_or_remote_portal_requests(self):
        for actor,request in [((20,306),(10,306)),((6,306),(17,306)),
                              ((30,306),(6,306))]:
            with self.subTest(actor=actor,request=request):
                self.place(2002,actor);before=self.stored()
                reply=self.game.receive(1644,MessageReader(pack('<HHH',2002,*request)))
                self.assertEqual(mid(reply),98)
                self.assertEqual(self.stored(),before)

    def test_approach_still_rejects_battle_and_ignores_duplicate_requests(self):
        point=(10,306);self.place(2002,point);before=self.stored()
        request=pack('<HHH',2002,*point)
        self.game.battle={'phase':'input'}
        reply=self.game.receive(1644,MessageReader(request))
        self.assertEqual(mid(reply),98);self.assertEqual(self.stored(),before)
        self.game.battle=None
        self.assertIsInstance(self.game.receive(1644,MessageReader(request)),tuple)
        after=self.stored()
        self.assertIsNone(self.game.receive(1644,MessageReader(request)))
        self.assertEqual(self.stored(),after)

    def test_every_npc_transport_uses_original_npc_id_and_index(self):
        for route in world().data['npc_transports']:
            with self.subTest(route=route['id']):
                if route.get('eligibility'):
                    rule=route['eligibility'][0]
                    self.game.character.update(race=rule['race'] or 1,sex=rule['sex'])
                npc=world().npc(route['from'],route['npc_id'])
                start=load_map(route['from']).nearest_walkable(npc['x']//20,npc['y']//20)
                self.place(route['from'],start)
                replies=self.game.receive(1645,MessageReader(number(route['npc_id'])+number(route['trans_index'])))
                self.assertIsInstance(replies,tuple)
                self.assertEqual(self.game.character['map_id'],route['to'])
                self.assertEqual((self.stored()['x'],self.stored()['y']),tuple(route['arrival']))

    def test_remote_wrong_scene_and_unknown_portal_do_not_move_role(self):
        before=self.stored()
        route=world().portals[2001][0]
        for payload in [pack('<HHH',2001,*route['source']),pack('<HHH',2002,1,1),pack('<HHH',2001,1,1)]:
            reply=self.game.receive(1644,MessageReader(payload))
            self.assertEqual(mid(reply),98)
            self.assertEqual(self.stored(),before)

    def test_failure_after_scene_write_rolls_back_database_grid_and_visibility(self):
        route=world().portals[2001][0]
        self.place(2001,load_map(2001).nearest_walkable(*route['source']))
        before=self.stored();grid=self.game.grid;visible=set(self.game._world_visible)
        with patch.object(self.game,'travel_scene_packets',side_effect=ValueError('reject')):
            result=self.game.receive(1644,MessageReader(pack('<HHH',2001,*route['source'])))
        self.assertEqual(mid(result),98)
        self.assertEqual(self.stored(),before);self.assertIs(self.game.grid,grid)
        self.assertEqual(self.game._world_visible,visible)

    def test_duplicate_portal_and_stale_old_map_movement_are_not_replayed(self):
        route=world().portals[2001][0]
        self.place(2001,load_map(2001).nearest_walkable(*route['source']))
        request=pack('<HHH',2001,*route['source'])
        self.game.receive(1644,MessageReader(request));after=self.stored()
        self.assertIsNone(self.game.receive(1644,MessageReader(request)))
        self.assertEqual(self.stored(),after)
        payload=number(4)+pack('<HH',*route['source'])+b'\x01'
        self.assertEqual(mid(self.game.receive(1643,MessageReader(payload))),1606)
        self.assertEqual(self.stored(),after)

    def test_old_async_position_cannot_overwrite_a_returned_scene(self):
        self.characters.enable_deferred_positions();writer=self.characters.position_writer
        self.addCleanup(lambda:writer.close(1))
        c=self.game.character;old_revision=writer.submit(c['owner'],c['id'],2001,5,5)
        self.assertTrue(writer.flush(1))
        self.place(2002,load_map(2002).nearest_walkable(20,20))
        self.place(2001,(300,210))
        with transaction(self.path,write=True) as db:
            self.assertEqual(writer.apply(db,(c['owner'],c['id']),(2001,5,5,old_revision)),0)
        self.assertEqual((self.stored()['x'],self.stored()['y']),(300,210))

    def test_npc_transport_dialogue_and_battle_restriction(self):
        route=next(row for row in world().data['npc_transports'] if row['from']==2001)
        npc=world().npc(2001,route['npc_id'])
        self.place(2001,load_map(2001).nearest_walkable(npc['x']//20,npc['y']//20))
        menu=self.game.receive(1034,MessageReader(number(route['npc_id'])))
        self.assertEqual(mid(menu),1025)
        self.assertTrue(self.game.dialog_open)
        self.game.battle={'phase':'input'}
        before=self.stored()
        reject=self.game.receive(1645,MessageReader(number(route['npc_id'])+number(route['trans_index'])))
        self.assertEqual(mid(reject),98);self.assertEqual(self.stored(),before)
        self.game.battle=None
        reply=self.game.receive(1032,MessageReader(b'\x01'+number(route['npc_id'])))
        self.assertIsInstance(reply,tuple)
        self.assertEqual(self.game.character['map_id'],route['to'])


if __name__=='__main__':unittest.main()
