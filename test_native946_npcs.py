"""NPC presentation and transport authorization against original client tables."""
from pathlib import Path
from struct import unpack
from tempfile import TemporaryDirectory
import json,unittest

from local_accounts import LocalAccounts
from native946_characters import Characters,ROLES
from native946_codec import SCHEMAS,number
from native946_game_login import NativeGameLogin,message_id
from native946_login import MessageReader
from native946_map import load_map
from native946_sessions import LEASES
from native946_world import world


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
        self.assertEqual([a for a in self.game.dialog_actions if isinstance(a,int)],[13,14])
        self.assertTrue(all(isinstance(a,int) or a.get('kind') in ('story','campaign') for a in self.game.dialog_actions))
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
        # The active daily-task menu retains its purpose and rule entry;
        # the original-story catalog is appended after existing business.
        self.assertEqual(self.game.dialog_actions[0]['op'],'rules')
        self.assertTrue(any(isinstance(a,dict) and a.get('kind')=='story' for a in self.game.dialog_actions))
        self.assertTrue(self.game.dialog_open)
        self.game.character.update(x=0,y=0)
        data=self.game.receive(1034,MessageReader(number(2123)))
        r=MessageReader(data[3:]);r.take(1)
        self.assertEqual(r.string().decode('gb18030'),'请走近一些再交谈。')

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


if __name__=='__main__':unittest.main()
