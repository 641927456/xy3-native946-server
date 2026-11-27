"""Transport retries and relocation keep native client/server frames aligned."""
from struct import pack
from unittest.mock import patch
import unittest

from local_accounts import LocalAccounts
from native946_characters import scene_actor_packet
from native946_codec import number
from native946_game_login import NativeGameLogin
from native946_gameplay import NativeGameplay
from native946_login import MessageReader,string_field
from native946_map import load_map
from native946_party import numbers
from native946_storage import StorageBusy
from native946_world import world
import test_native946_world as town_fixtures
import test_native946_wilderness_party as party_fixtures


def mid(data):return int.from_bytes(data[1:3],'little') if data[0]==0 else data[0]


class TransportRetryTests(unittest.TestCase):
    setUp=town_fixtures.WorldGameplayTests.setUp
    place=town_fixtures.WorldGameplayTests.place
    stored=town_fixtures.WorldGameplayTests.stored

    def prepare(self,rid):
        route=next(r for r in world().data['npc_transports'] if r['id']==rid)
        npc=world().npc(route['from'],route['npc_id'])
        self.place(route['from'],load_map(route['from']).nearest_walkable(npc['x']//20,npc['y']//20))
        return route,number(route['npc_id'])+number(route['trans_index'])

    def test_solo_same_map_teleport_moves_native_actor_and_refreshes_nearby_npcs(self):
        route,request=self.prepare(36)
        self.game.sync_world_npcs(force=True)
        old_visible=set(self.game._world_visible)
        reply=self.game.receive(1645,MessageReader(request))
        self.assertEqual([mid(p) for p in reply[:2]],[1183,1606])
        jump=MessageReader(reply[1][3:])
        self.assertEqual(jump.integer(),self.game.character['id'])
        self.assertEqual(jump.take(4),pack('<HH',*route['arrival']))
        jump.finish()
        self.assertTrue({1597,1593,695,789,1978,1206}.isdisjoint(map(mid,reply)))
        new_visible=self.game._world_visible
        removed={MessageReader(p[3:]).integer() for p in reply if mid(p)==1604}
        self.assertTrue(old_visible-new_visible)
        self.assertTrue(new_visible-old_visible)
        self.assertTrue((old_visible-new_visible)<=removed)
        self.assertEqual((self.stored()['x'],self.stored()['y']),tuple(route['arrival']))

    def test_cross_map_teleport_rebuilds_scene_without_full_player_sync(self):
        route,request=self.prepare(37)
        reply=self.game.receive(1645,MessageReader(request))
        ids=[mid(p) for p in reply]
        self.assertEqual(ids[:4],[1183,1597,1590,1585])
        self.assertIn(1593,ids)
        self.assertIn(1621,ids)
        self.assertTrue({1606,695,789,1978,1206}.isdisjoint(ids))
        self.assertEqual((self.stored()['map_id'],self.stored()['x'],self.stored()['y']),
                         (route['to'],*route['arrival']))

    def test_duplicate_npc_transport_is_silent_for_same_and_different_scenes(self):
        self.characters.enable_deferred_positions()
        self.addCleanup(lambda:self.characters.position_writer.close(2))
        for rid in (40,77):
            with self.subTest(route=rid):
                route,request=self.prepare(rid)
                self.assertIsInstance(self.game.receive(1645,MessageReader(request)),tuple)
                saved=self.stored()
                with patch.object(self.game,'change_scene',wraps=self.game.change_scene) as change:
                    self.assertIsNone(self.game.receive(1645,MessageReader(request)))
                    change.assert_not_called()
                self.assertEqual(self.stored(),saved)

    def test_physical_retry_with_position_writer_does_not_reserve_a_revision(self):
        self.characters.enable_deferred_positions()
        self.addCleanup(lambda:self.characters.position_writer.close(2))
        route=world().portals[2001][0]
        self.place(2001,load_map(2001).nearest_walkable(*route['source']))
        request=pack('<HHH',2001,*route['source'])
        self.assertIsInstance(self.game.receive(1644,MessageReader(request)),tuple)
        saved=self.stored()
        self.assertIsNone(self.game.receive(1644,MessageReader(request)))
        self.assertEqual(self.stored(),saved)
        self.assertEqual(mid(self.game.receive(1644,MessageReader(request+b'\0'))),98)
        self.assertEqual(self.stored(),saved)

    def test_native_auto_trace_retry_after_menu_transport_is_silent(self):
        route,request=self.prepare(40)
        self.game.receive(1034,MessageReader(number(route['npc_id'])))
        self.assertIsInstance(self.game.receive(1032,MessageReader(b'\x01'+number(route['npc_id']))),tuple)
        saved=self.stored()
        self.assertIsNone(self.game.receive(1645,MessageReader(request)))
        self.assertEqual(self.stored(),saved)

    def test_different_npc_index_still_validates_location_and_keeps_save(self):
        route,request=self.prepare(40)
        self.game.receive(1645,MessageReader(request));saved=self.stored()
        reject=self.game.receive(1645,MessageReader(number(route['npc_id'])+number(2)))
        self.assertEqual(mid(reject),98);self.assertEqual(self.stored(),saved)

    def test_movement_ends_retry_window_and_old_npc_is_no_longer_nearby(self):
        route,request=self.prepare(40)
        self.game.receive(1645,MessageReader(request))
        c=self.game.character
        self.game.receive(1643,MessageReader(number(4)+pack('<HH',c['x'],c['y'])+string_field(b'')))
        self.assertIsNone(self.game._last_transition)
        saved=self.stored()
        self.assertEqual(mid(self.game.receive(1645,MessageReader(request))),98)
        self.assertEqual(self.stored(),saved)

    def test_native_route_sources_far_from_displayed_npcs_are_accepted(self):
        for rid in (146,152,227,245):
            with self.subTest(route=rid):
                route=next(r for r in world().data['npc_transports'] if r['id']==rid)
                point=load_map(route['from']).nearest_walkable(*route['source'])
                self.place(route['from'],point)
                reply=self.game.receive(1645,MessageReader(number(route['npc_id'])+number(route['trans_index'])))
                self.assertIsInstance(reply,tuple)
                self.assertEqual(self.stored()['map_id'],route['to'])
                self.assertEqual((self.stored()['x'],self.stored()['y']),tuple(route['arrival']))

    def test_npc_route_source_does_not_enable_remote_or_unknown_routes(self):
        route=next(r for r in world().data['npc_transports'] if r['id']==245)
        point=load_map(route['from']).nearest_walkable(route['source'][0]+5,route['source'][1])
        self.place(route['from'],point);saved=self.stored()
        for index in (route['trans_index'],999):
            reply=self.game.receive(1645,MessageReader(number(route['npc_id'])+number(index)))
            self.assertEqual(mid(reply),98)
            self.assertEqual(self.stored(),saved)


class PartyRelocationTests(unittest.TestCase):
    setUp=party_fixtures.WildernessPartyTests.setUp
    call=party_fixtures.WildernessPartyTests.call
    team=party_fixtures.WildernessPartyTests.team

    def place(self,game,sid,point):
        c=game.character
        revision=self.characters.change_scene(c['owner'],c['id'],c['map_id'],sid,*point,c.get('position_revision',0))
        c.update(map_id=sid,x=point[0],y=point[1],position_revision=revision)
        game.grid=load_map(sid);game._arrival_guard=None;game._last_transition=None
        game._world_visible=set();game._world_bucket=None

    def prepare(self,rid=40):
        self.team()
        route=next(r for r in world().data['npc_transports'] if r['id']==rid)
        npc=world().npc(route['from'],route['npc_id'])
        point=load_map(route['from']).nearest_walkable(npc['x']//20,npc['y']//20)
        for game in (self.a,self.b):self.place(game,route['from'],point);self.svc.drain(game)
        return route,point

    def transport(self,route):
        return self.a.receive(1645,MessageReader(number(route['npc_id'])+number(route['trans_index'])))

    def peer(self,index,sid,point):
        owner='observer'+str(index)
        LocalAccounts(self.path).register(dict(username=owner,password='isolated-fixture-password'))
        c=self.characters.create(owner,'旁观'+str(index),702)
        game=NativeGameplay(c,self.characters,lambda:())
        self.games.append(game);self.place(game,sid,point)
        return game

    def test_same_map_npc_warp_refreshes_followers_instead_of_walking_them(self):
        route,point=self.prepare()
        observer = self.peer(1, route['to'], tuple(route['arrival']))
        self.svc.reconnect_packets(self.a)
        self.assertIn(1590, [mid(p) for p in self.svc.drain(observer)])
        with patch.object(self.b,'restore_scene',return_value=()) as refresh:
            self.transport(route)
            self.assertEqual(refresh.call_count,1)
        replies=self.svc.drain(self.b)
        self.assertNotIn(1608,[mid(p) for p in replies])
        self.assertTrue({1606,695,789,1978,1206}.isdisjoint(map(mid,replies)))
        self.assertEqual((self.b.character['x'],self.b.character['y']),tuple(route['arrival']))
        saved=self.characters.list(self.b.character['owner'])[0]
        self.assertEqual((saved['map_id'],saved['x'],saved['y']),(route['to'],*route['arrival']))
        observed = self.svc.drain(observer)
        remove_index = observed.index(numbers(1604, self.a.character['id']))
        self.assertEqual(observed[remove_index + 1], scene_actor_packet(self.a.character))

    def test_normal_walking_does_not_reenter_follower_scene(self):
        self.team()
        x,y=self.a.character['x'],self.a.character['y']
        target=load_map(2001).nearest_walkable(x+1,y)
        self.svc.drain(self.b)
        with patch.object(self.b,'restore_scene',return_value=()) as refresh:
            self.a.receive(1643,MessageReader(number(4)+pack('<HH',*target)+string_field(b'')))
            refresh.assert_not_called()
        self.assertIn(1608,[mid(p) for p in self.svc.drain(self.b)])

    def test_busy_follow_retry_remembers_that_the_leader_teleported(self):
        route,point=self.prepare()
        original=self.svc.follow_team;calls=[]
        def follow(team,*,relocate=False):
            calls.append(relocate)
            if len(calls)==1:raise StorageBusy('fixture busy')
            return original(team,relocate=relocate)
        with patch.object(self.svc,'follow_team',side_effect=follow):
            self.transport(route)
            self.a._last_transition=None;self.a._arrival_guard=None
            with patch.object(self.b,'restore_scene',return_value=()) as refresh:
                self.svc.tick(self.a)
                self.assertEqual(refresh.call_count,1)
        self.assertEqual(calls,[True,True])
        self.assertFalse(self.svc.pending_follow)
        self.assertEqual((self.b.character['x'],self.b.character['y']),tuple(route['arrival']))

    def test_warp_removes_old_observer_entities_and_creates_new_ones(self):
        route,point=self.prepare(77)
        old_observer=self.peer(1,route['from'],point)
        new_observer=self.peer(2,route['to'],tuple(route['arrival']))
        far_point = load_map(route['from']).nearest_walkable(point[0], point[1] + 128)
        distant = self.peer(3, route['from'], far_point)
        for game in (self.a, self.b):
            self.svc.reconnect_packets(game)
        self.assertEqual(sum(mid(p)==1590 for p in self.svc.drain(distant)), 2)
        for game in self.games:self.svc.drain(game)
        self.transport(route)
        departed=self.svc.drain(old_observer);arrived=self.svc.drain(new_observer)
        removed=[MessageReader(p[3:]).integer() for p in departed if mid(p)==1604]
        self.assertCountEqual(removed,[self.a.character['id'],self.b.character['id']])
        remote_removed = [MessageReader(p[3:]).integer() for p in self.svc.drain(distant) if mid(p) == 1604]
        self.assertCountEqual(remote_removed, removed)
        self.assertEqual(sum(mid(p)==1590 for p in arrived),2)
        self.assertNotIn(1608,[mid(p) for p in departed+arrived])

    def bind_scene_headers(self, *games):
        for game in games:
            login = NativeGameLogin(None, None, number, None)
            login.current_character = game.character
            game.restore_scene = login.scene_packets

    def test_team_members_are_created_after_the_scene_header_on_both_clients(self):
        route, _ = self.prepare(77)
        self.bind_scene_headers(self.a, self.b)
        old_actor = self.peer(1, route['from'], tuple(route['source']))
        old_actor_packet = scene_actor_packet(old_actor.character)
        self.svc.enqueue(self.a, old_actor_packet)
        self.b.character['appearance'] = {'mount_shape': 2510}

        leader_replies = self.transport(route)
        follower_replies = self.svc.drain(self.b)
        self.assertLess(leader_replies.index(old_actor_packet), [mid(p) for p in leader_replies].index(1597))
        for replies, observer, other in ((leader_replies, self.a, self.b), (follower_replies, self.b, self.a)):
            with self.subTest(observer=observer.character['id']):
                ids = [mid(p) for p in replies]
                actor_packet = scene_actor_packet(other.character)
                self.assertEqual(replies.count(actor_packet), 1)
                self.assertLess(ids.index(1597), ids.index(1585))
                self.assertLess(ids.index(1585), replies.index(actor_packet))
                self.assertEqual((other.character['map_id'], other.character['x'], other.character['y']),
                                 (route['to'], *route['arrival']))

    def test_movement_retry_cannot_turn_a_pending_warp_into_a_long_walk(self):
        route,point=self.prepare()
        with patch.object(self.svc,'follow_team',side_effect=StorageBusy('fixture busy')):
            self.transport(route)
        c=self.a.character
        with patch.object(self.b,'restore_scene',return_value=()) as refresh:
            target=load_map(c['map_id']).nearest_walkable(c['x']+1,c['y'])
            self.a.receive(1643,MessageReader(number(4)+pack('<HH',*target)+string_field(b'')))
            self.assertEqual(refresh.call_count,1)
        self.assertFalse(self.svc.pending_follow)
        self.assertFalse(self.svc.pending_follow_relocations)
        self.assertEqual((self.b.character['x'],self.b.character['y']),target)

    def test_all_followers_have_arrived_before_any_scene_is_serialized(self):
        route,point=self.prepare()
        third=self.peer(3,route['from'],point)
        self.call(self.a,2039,third.character['id'])
        self.call(third,2038,self.a.character['id'])
        seen=[];original=self.b.scene_objects
        def scene():
            seen.append((third.character['x'],third.character['y']))
            return original()
        with patch.object(self.b,'scene_objects',side_effect=scene):self.transport(route)
        self.assertEqual(seen,[tuple(route['arrival'])])


if __name__=='__main__':unittest.main()
