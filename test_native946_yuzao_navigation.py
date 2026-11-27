"""Yuzao's native route must lead to reachable ground and the real NPC."""
from collections import deque
from pathlib import Path
from struct import pack
import sys,unittest

sys.path.insert(0,str(Path(__file__).parent/'analysis/town-portals-20261125'))
from client_archive import Archive
from build_world import plain
from literal_schema import replay
from native946_codec import number
from native946_login import MessageReader,string_field
from native946_map import DIRECTIONS,load_map
from native946_world import world
import test_native946_world as fixtures


def native_route():
    blob=Archive(Path(__file__).parent/'client-946/resource/configs.wdf').read('info/info_cross_scene_npc.lua')
    exports={};values=replay(blob,exports)
    return plain(values[0] if values else exports['info'])[212]


def ground_paths():
    grid=load_map(2012);npc=world().npc(2012,7365);goal=(npc['x']//20,npc['y']//20)
    parents={goal:None};queue=deque([goal])
    while queue:
        x,y=queue.popleft()
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
            point=(x+dx,y+dy)
            if point not in parents and grid.walkable(*point):parents[point]=(x,y);queue.append(point)
    paths=[]
    for route in world().data['portals']+world().data['npc_transports']:
        if route['to']!=2012:continue
        start=tuple(route['arrival']);path=[start]
        if start not in parents:raise AssertionError(('Incoming route cannot reach Yuzao',route['id']))
        while path[-1]!=goal:path.append(parents[path[-1]])
        paths.append((route['id'],path))
    return paths


class YuzaoDataTests(unittest.TestCase):
    def test_native_auto_route_points_to_the_visible_npc_on_ground(self):
        row=native_route();npc=world().npc(2012,7365)
        self.assertEqual((row['from'],row['npc_id'],row['trans_index'],row['to']),(2012,7365,1,2419))
        self.assertEqual(row['from_pos'],[npc['x'],npc['y']])
        self.assertTrue(load_map(2012).walkable(*(value//20 for value in row['from_pos'])))

    def test_server_route_and_client_route_use_the_same_source(self):
        row=native_route();route=next(r for r in world().data['npc_transports'] if r['id']==213)
        self.assertEqual(route['from_pos'],row['from_pos'])
        self.assertEqual(route['source'],[value//20 for value in row['from_pos']])

    def test_all_existing_arrivals_can_walk_to_yuzao(self):
        paths=ground_paths();self.assertEqual({rid for rid,path in paths},{229,5,27,215,20018})
        for rid,path in paths:
            with self.subTest(arrival=rid):
                self.assertTrue(all(load_map(2012).walkable(*point) for point in path))


class YuzaoWalkTests(unittest.TestCase):
    setUp=fixtures.WorldGameplayTests.setUp
    place=fixtures.WorldGameplayTests.place
    stored=fixtures.WorldGameplayTests.stored

    def test_native_movement_segments_reach_yuzao_and_transport_to_nanhai(self):
        self.characters.enable_deferred_positions()
        self.addCleanup(lambda:self.characters.position_writer.close(2))
        codes={delta:code for code,delta in DIRECTIONS.items()}
        for rid,path in ground_paths():
            with self.subTest(arrival=rid):
                self.place(2012,path[0])
                for start in range(0,len(path)-1,12):
                    part=path[start:start+13]
                    directions=bytes(codes[(b[0]-a[0],b[1]-a[1])] for a,b in zip(part,part[1:]))
                    self.game.receive(1643,MessageReader(number(4)+pack('<HH',*part[0])+string_field(directions)))
                    self.assertEqual((self.game.character['x'],self.game.character['y']),part[-1])
                    self.assertIsNone(self.game.battle)
                response=self.game.receive(1645,MessageReader(number(7365)+number(1)))
                self.assertIsInstance(response,tuple)
                self.assertEqual(self.stored()['map_id'],2419)
                self.assertEqual((self.stored()['x'],self.stored()['y']),(118,53))

    def test_old_blocked_target_does_not_enable_remote_teleport(self):
        point=load_map(2012).nearest_walkable(20,121)
        self.place(2012,point);before=self.stored()
        response=self.game.receive(1645,MessageReader(number(7365)+number(1)))
        self.assertEqual(fixtures.mid(response),98)
        self.assertEqual(self.stored(),before)


if __name__=='__main__':unittest.main()
