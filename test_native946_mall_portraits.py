from time import time
import unittest
from unittest.mock import patch
import test_native946_cosmetics as cosmetic_fixture
from test_native946_npc_services import mid
from native946_cosmetics import Cosmetics
from native946_mall_uses import DATA,features
from native946_mall_portraits import Portraits,current
from native946_fashion_shop import PANEL_PRICES
from native946_progression import Progression
from native946_login import MessageReader
from native946_codec import packet

class PortraitTests(unittest.TestCase):
    setUp=cosmetic_fixture.CosmeticTests.setUp
    send=cosmetic_fixture.CosmeticTests.send
    rows=cosmetic_fixture.CosmeticTests.rows

    def add(self,kind,n=1):
        with self.game.progression.transaction() as db:Cosmetics(self.game)._add(db,kind,n)
        return next(r for r in self.rows() if r['type']==kind)
    def active(self):
        with self.game.progression.transaction(write=False) as db:return current(db,self.game.progression.cid)
    def expiry(self,icon):
        with self.game.progression.transaction(write=False) as db:return features(db,self.game.progression.cid)['portrait:'+str(icon)]['expires']

    def offer(self,reply):
        sale=next(p for p in reply if mid(p)==3658)
        r=MessageReader(sale[3:]);r.string()
        for _ in range(6):r.integer()
        r.string();kind=r.integer();r.finish()
        return kind

    def buy(self,kind):
        self.assertEqual(mid(self.send(3663,kind,1)),3661)
        return self.confirm(kind)

    def confirm(self,kind):
        reply=self.send(3665,kind,1,0,0)
        self.assertIn(3676,[mid(p) for p in reply])
        return next(r for r in self.rows() if r['type']==kind)

    def flower_quote(self,reply):
        offer=next(p for p in reply if mid(p)==3661)
        r=MessageReader(offer[3:]);kind,amount=r.integer(),r.integer();r.string()
        r.integer();price=r.integer();r.integer();r.integer();r.finish()
        return kind,amount,price

    def panel(self, reply=None):
        if reply is None:
            reply=self.send(2598)
        if isinstance(reply,tuple):
            reply=next(p for p in reply if mid(p)==2596)
        self.assertEqual(mid(reply),2596)
        r=MessageReader(reply[3:])
        def read_array(read):
            return [read() for _ in range(r.take(1)[0])]
        decode=lambda: r.string().decode('gb18030')
        icons=read_array(r.integer)
        descriptions=read_array(decode)
        self.assertEqual((r.integer(),r.integer()),(46258,46257))
        frames=read_array(r.integer)
        frame_descriptions=read_array(decode)
        sale=read_array(r.integer)
        r.finish()
        self.assertEqual(len(icons),len(descriptions))
        self.assertEqual(len(frames),len(frame_descriptions))
        return dict(zip(icons,descriptions)),dict(zip(frames,frame_descriptions)),sale

    def test_panel_lists_available_and_owned_frames_and_correct_portrait_durations(self):
        self.assertEqual(self.game.progression.attribute_values()['TimeCredit'],0)
        preview,frames,sale=self.panel()
        self.assertEqual(len(preview),64)
        self.assertEqual(set(preview.values()),{''})
        self.assertEqual([icon for icon in preview if icon<9200],[9101,9102,9103,9104])
        self.assertEqual((frames,sale),({},sorted(PANEL_PRICES)))
        self.assertEqual(len(PANEL_PRICES),23)
        with self.game.progression.transaction() as db:
            cid=self.game.progression.cid
            db.executemany('INSERT INTO cosmetic_features VALUES(?,?,?,?)',[
                (cid,'portrait:9201',55832,0),(cid,'portrait_slot:9501',55836,0),
                (cid,'iconframe:1160',48278,0),(cid,'iconframe:1159',48277,int(time())+86400),
                (cid,'iconframe:1157',48256,1),
            ])
        icons,frames,sale=self.panel()
        self.assertEqual(icons,{**preview,9201:'永久有效'})
        self.assertEqual(set(frames),{1159,1160})
        self.assertRegex(frames[1159],r'^\d{4}-\d{2}-\d{2} ')
        self.assertEqual(frames[1160],'')
        self.assertNotIn(1160,sale)

    def test_classic_portrait_can_be_activated_from_the_preview_after_buying_flowers(self):
        icon=9101
        with self.game.progression.transaction() as db:
            db.execute('INSERT INTO mall_account_wallet(owner,yubo) VALUES(?,1800)',(self.game.progression.owner,))
        before=self.rows()
        self.assertEqual(self.flower_quote(self.send(2597,icon,46258)),(55516,1,300))
        self.assertEqual(self.flower_quote(self.send(2597,0,46258)),(55516,1,300))
        self.assertEqual(self.rows(),before)
        self.assertEqual(self.active(),0)
        box=self.confirm(55516)
        self.assertEqual(mid(self.send(2597,icon,46258)),98)
        self.send(807,0,box['id'])
        reply=self.send(2597,icon,46258)
        expiry=self.expiry(icon)
        self.assertRegex(self.panel(reply)[0][icon],r'^\d{4}-\d{2}-\d{2} ')
        self.assertLess(abs(expiry-int(time())-15*86400),3)
        self.send(2597,icon,46257)
        self.assertEqual(self.expiry(icon),expiry+180*86400)
        self.assertFalse(any(r['type'] in (46258,46257) for r in self.rows()))
        self.assertEqual(self.flower_quote(self.send(2597,0,46257)),(55515,1,1000))
        advanced=self.confirm(55515)
        self.send(807,0,advanced['id'])
        self.assertEqual(sum(r['amount'] for r in self.rows() if r['type']==46257),4)
        cold=Progression(self.path,'npc_owner',self.game.progression.cid)
        self.assertEqual(cold.attribute_values()['QIcon'],icon)

    def test_foreign_role_portrait_is_hidden_and_cannot_charge_even_if_previously_owned(self):
        self.add(46258)
        before=self.rows()
        with self.game.progression.transaction() as db:
            cid=self.game.progression.cid
            db.executemany('INSERT INTO cosmetic_features VALUES(?,?,?,0)',[
                (cid,'portrait:9001',46258),(cid,'portrait_current',9001),
            ])
            db.execute('INSERT INTO mall_account_wallet(owner,liuguang) VALUES(?,850)',(self.game.progression.owner,))
        self.assertNotIn(9001,self.panel()[0])
        self.assertEqual(self.active(),0)
        self.assertEqual(mid(self.send(2597,9001,46258)),98)
        self.assertEqual(mid(self.send(2599,9001,1047)),98)
        self.assertEqual(self.rows(),before)
        attrs=self.game.progression.attribute_values()
        self.assertEqual((attrs['QIcon'],attrs['IconFrame'],attrs['TimeCredit']),(0,0,850))
        with self.game.progression.transaction(write=False) as db:
            self.assertIn('portrait:9001',features(db,cid))

    def test_role_mapping_covers_every_base_role_and_each_native_expression_once(self):
        from native946_characters import ROLES
        from native946_mall_portraits import ROLE_PORTRAITS,PORTRAIT_ROLES
        self.assertEqual(set(ROLE_PORTRAITS),set(map(int,ROLES)))
        self.assertEqual(set(PORTRAIT_ROLES),set(range(9001,9121)))
        second=self.characters.create('npc_owner','苍风头像验证',713)
        from native946_codec import number
        from native946_game_login import NativeGameLogin,message_id
        from native946_sessions import LEASES
        port=547
        login=NativeGameLogin(port,None,number,None,self.characters)
        login.username='npc_owner'
        self.addCleanup(lambda:LEASES.release_port(self.path,port))
        login.receive(message_id(915)+number(second['id']))
        self.game=login.gameplay
        icons=list(self.panel()[0])
        self.assertEqual(icons[:4],[9117,9118,9119,9120])
        self.assertEqual(len(icons),64)

    def test_preview_routes_to_portrait_goods_without_charging_for_the_frame(self):
        with self.game.progression.transaction() as db:
            db.execute('INSERT INTO mall_account_wallet(owner,yubo,liuguang) VALUES(?,500,850)',(self.game.progression.owner,))
        self.assertEqual(self.offer(self.send(2599,9201,1047)),55832)
        self.assertEqual(self.game.progression.attribute_values()['TimeCredit'],850)
        item=self.buy(55832)
        self.assertEqual(mid(self.send(2599,9201,1047)),98)
        self.send(807,0,item['id'])
        self.send(2599,9201,1047)
        self.send(2599,9201,1047)
        cold=Progression(self.path,'npc_owner',self.game.progression.cid)
        state=cold.attribute_values()
        self.assertEqual((state['QIcon'],state['IconFrame'],state['TimeCredit']),(9201,1047,450))

    def test_purchase_and_portrait_update_are_atomic_and_replay_charges_once(self):
        self.assertEqual(mid(self.send(2599,0,1011)),98)
        with self.game.progression.transaction() as db:
            db.execute('INSERT INTO mall_account_wallet(owner,liuguang) VALUES(?,850)',(self.game.progression.owner,))
        self.assertEqual(self.game.progression.attribute_values()['TimeCredit'],850)
        for icon,frame in [(9999,1011),(0,1160),(0,1001)]:
            self.assertEqual(mid(self.send(2599,icon,frame)),98)
        self.assertEqual(mid(self.send(5032,1011)),98)
        self.assertEqual(self.game.progression.attribute_values()['TimeCredit'],850)
        row=self.add(55832)
        self.send(807,0,row['id'])
        reply=self.send(2599,9201,1011)
        self.assertIn(5031,[mid(p) for p in reply])
        self.assertEqual(self.panel(reply)[1],{1011:''})
        updates={}
        for p in reply:
            if mid(p)==695:
                r=MessageReader(p[3:]);key=r.integer();updates[key]=r.integer()
        self.assertEqual({key:updates[key] for key in (56,57,77)},{56:9201,57:1011,77:450})
        self.send(2599,9201,1011)
        cold=Progression(self.path,'npc_owner',self.game.progression.cid)
        values=cold.attribute_values()
        self.assertEqual((values['QIcon'],values['IconFrame'],values['TimeCredit']),(9201,1011,450))
        self.send(2599,self.game.character['role_id'],1011)
        self.assertEqual((self.active(),cold.attribute_values()['IconFrame']),(0,1011))
        self.send(2599,0,0)
        self.assertEqual(cold.attribute_values()['IconFrame'],0)

    def test_jing_requirement_unlocks_exact_frames_without_charge(self):
        from native946_role_systems import save
        with self.game.progression.transaction() as db:
            save(db,self.game.progression.cid,'jing',dict(level=2,exp=0))
        self.assertEqual(self.panel()[1],{1001:'',1002:''})
        self.assertEqual(mid(self.send(2599,0,1003)),98)
        self.send(2599,0,1002)
        values=self.game.progression.attribute_values()
        self.assertEqual((values['IconFrame'],values['TimeCredit']),(1002,0))
    def test_distinct_native_faces_and_reversed_pair_use_actual_resource_ids(self):
        base=self.game.character['role_id']
        for kind,icon in [(55832,9201),(55833,9202),(55917,9216),(55918,9215),(66307,9221)]:
            row=self.add(kind);self.assertNotEqual(mid(self.send(807,0,row['id'])),888)
            self.assertEqual(self.active(),icon)
            self.assertLess(abs(self.expiry(icon)-int(time())-15*86400),3)
            self.assertEqual(self.game.character['role_id'],base)
        cold=Progression(self.path,'npc_owner',self.game.progression.cid)
        self.assertEqual(cold.attribute_values()['QIcon'],9221)
        self.assertNotEqual(self.active(),4809)
    def test_duplicate_live_portrait_extends_from_the_existing_expiry(self):
        row=self.add(55832);self.send(807,0,row['id']);expiry=self.expiry(9201)
        row=self.add(65832)
        self.assertNotEqual(mid(self.send(807,0,row['id'])),888)
        self.assertFalse(any(r['id']==row['id'] for r in self.rows()))
        self.assertEqual(self.expiry(9201),expiry+15*86400)
        self.assertEqual(mid(self.send(807,0,row['id'])),98)
        self.assertEqual(self.expiry(9201),expiry+15*86400)
        cold=Progression(self.path,'npc_owner',self.game.progression.cid)
        with cold.transaction(write=False) as db:
            self.assertEqual(features(db,cold.cid)['portrait:9201']['expires'],expiry+15*86400)

    def test_permanent_license_keeps_the_item(self):
        row=self.add(55832);self.send(807,0,row['id'])
        with self.game.progression.transaction() as db:
            db.execute("UPDATE cosmetic_features SET expires=0 WHERE key='portrait:9201'")
        row=self.add(55832);before=self.rows()
        self.assertEqual(mid(self.send(807,0,row['id'])),98)
        self.assertEqual(self.rows(),before)
        self.assertEqual(self.expiry(9201),0)
    def test_slot_requires_unlock_then_native_15_or_180_day_material(self):
        flower=self.add(46258,2);before=self.rows()
        self.assertEqual(self.offer(self.send(2597,9501,46258)),55836)
        self.assertEqual(before,self.rows())
        token=self.add(55836);self.send(807,0,token['id'])
        self.assertEqual(self.active(),0);self.assertEqual(mid(self.send(2599,9501,0)),98)
        self.assertNotEqual(mid(self.send(2597,9501,46258)),888)
        self.assertEqual(self.active(),9501)
        self.assertEqual(sum(r['amount'] for r in self.rows() if r['type']==46258),1)
        self.assertLess(abs(self.expiry(9501)-int(time())-15*86400),3)
        advanced=self.add(46257);expiry=self.expiry(9501)
        self.assertNotEqual(mid(self.send(2597,9501,46257)),888)
        self.assertEqual(self.expiry(9501),expiry+180*86400)
        self.assertFalse(any(r['id']==advanced['id'] for r in self.rows()))
        with self.game.progression.transaction() as db:db.execute("UPDATE cosmetic_features SET expires=1 WHERE key='portrait:9501'")
        advanced=self.add(46257)
        self.assertNotEqual(mid(self.send(2597,9501,46257)),888)
        self.assertLess(abs(self.expiry(9501)-int(time())-180*86400),3)
        self.assertFalse(any(r['id']==advanced['id'] for r in self.rows()))
    def test_selection_default_expiry_tick_and_cold_state(self):
        row=self.add(55832);self.send(807,0,row['id'])
        self.assertEqual(mid(self.send(2599,9999,0)),98)
        self.send(2599,self.game.character['role_id'],0);self.assertEqual(self.active(),0)
        self.send(2599,9201,0);self.assertEqual(self.active(),9201)
        until=self.expiry(9201)
        with patch('native946_mall_portraits.time',return_value=until+1):
            self.assertEqual(self.active(),0)
            self.game.progression._cache_enabled=True;self.game.progression.snapshot()
            self.assertEqual(self.game.progression.attribute_values()['QIcon'],0)
            updates=self.game.tick();found=[]
            for p in updates:
                if mid(p)==695:
                    r=MessageReader(p[3:]);found.append((r.integer(),r.integer()))
            self.assertIn((56,0),found)
            self.assertEqual(self.offer(self.send(2599,9201,0)),55832)
    def test_locked_expired_or_wrong_item_cannot_write_a_portrait_license(self):
        row=self.add(55832)
        with self.game.progression.transaction() as db:db.execute('UPDATE cosmetic_expiry SET expires=1 WHERE item_id=?',(row['id'],))
        before=self.rows();self.assertEqual(mid(self.send(807,0,row['id'])),98)
        self.assertEqual(before,self.rows());self.assertEqual(self.active(),0)
        self.assertEqual(mid(self.send(2597,9501,55195)),98)
    def test_official_haichao_and_tide_slots_unlock_and_activate_exact_native_ids(self):
        for kind,icon in [(56203,9507),(56204,9508),(56205,9509),(56206,9510),
                          (66203,9507),(66204,9508),(66205,9509),(66206,9510)]:
            with self.subTest(kind=kind):
                with self.game.progression.transaction() as db:
                    db.execute("DELETE FROM cosmetic_features WHERE character_id=? AND key LIKE 'portrait%'",(self.game.progression.cid,))
                row=self.add(kind);self.assertNotEqual(mid(self.send(807,0,row['id'])),888)
                self.assertEqual(self.active(),0)
                flower=self.add(46258);self.assertNotEqual(mid(self.send(2597,icon,46258)),888)
                self.assertEqual(self.active(),icon)
                self.assertFalse(any(r['id']==flower['id'] for r in self.rows()))
                cold=Progression(self.path,'npc_owner',self.game.progression.cid)
                self.assertEqual(cold.attribute_values()['QIcon'],icon)
                other=self.add(kind+10000 if kind<60000 else kind-10000);before=self.rows()
                self.assertEqual(mid(self.send(807,0,other['id'])),98);self.assertEqual(before,self.rows())
    def test_identical_static_and_animated_faces_have_independent_licenses(self):
        for kind,icon,flower in [(56203,9507,46258),(56205,9509,46257)]:
            row=self.add(kind);self.send(807,0,row['id']);self.add(flower);self.send(2597,icon,flower)
        self.assertEqual(self.active(),9509)
        self.send(2599,9507,0);self.assertEqual(self.active(),9507)
        self.send(2599,9509,0);self.assertEqual(self.active(),9509)
        with patch('native946_mall_portraits.time',return_value=self.expiry(9507)+1):
            self.assertEqual(mid(self.send(2599,9507,0)),98)
            self.assertEqual(self.active(),9509)
    def test_native_bundle_amount_and_exact_number_ticket_identity(self):
        row=self.add(55515);self.send(807,0,row['id'])
        self.assertEqual(sum(r['amount'] for r in self.rows() if r['type']==46257),4)
        row=self.add(65218);self.send(807,0,row['id'])
        self.assertEqual(sum(r['amount'] for r in self.rows() if r['type']==45844),5)
        self.assertFalse(any(r['type']==55197 for r in self.rows()))
    def test_material_right_click_opens_native_selector_without_consumption(self):
        row=self.add(46257);before=self.rows();self.assertEqual(mid(self.send(807,0,row['id'])),2596)
        self.assertEqual(before,self.rows())

    def test_flower_activation_skips_locked_stack(self):
        token=self.add(55836)
        self.send(807,0,token['id'])
        flower=self.add(46258,20)
        with self.game.progression.transaction() as db:
            db.execute("INSERT INTO native_object_locks VALUES(?,'item',?,1,0)",
                       (self.game.progression.cid,flower['id']))
        reply=self.send(2597,9501,46258)
        self.assertIn(98,[mid(p) for p in reply])
        self.assertEqual(self.active(),0)
        self.add(46258)
        self.send(2597,9501,46258)
        self.assertEqual(self.active(),9501)
        self.assertEqual([(r['id'],r['amount']) for r in self.rows() if r['type']==46258],[(flower['id'],20)])
    def test_failure_after_license_write_rolls_back_license_and_item(self):
        row=self.add(55832);before=self.rows()
        with patch('native946_mall_uses.consume',side_effect=ValueError('fixture locked item')):
            self.assertEqual(mid(self.send(807,0,row['id'])),98)
        self.assertEqual(before,self.rows());self.assertEqual(self.active(),0)
        with self.game.progression.transaction(write=False) as db:self.assertNotIn('portrait:9201',features(db,self.game.progression.cid))
    def test_expiry_notification_retries_after_storage_busy(self):
        from native946_storage import StorageBusy
        row=self.add(55832);self.send(807,0,row['id']);until=self.expiry(9201)
        with patch('native946_mall_portraits.time',return_value=until+1):
            with patch.object(self.game.party_service,'team',side_effect=StorageBusy('fixture busy')):
                self.assertEqual(Portraits(self.game).tick(),())
            self.assertEqual(self.game._portrait_value,9201)
            self.assertIn(695,[mid(p) for p in Portraits(self.game).tick()])
            self.assertEqual(self.game._portrait_value,0)
    def test_expiry_does_not_reopen_a_previously_opened_native_panel(self):
        row=self.add(55832);self.send(807,0,row['id']);self.send(2598)
        with patch('native946_mall_portraits.time',return_value=self.expiry(9201)+1):
            updates=Portraits(self.game).tick()
        self.assertIn(695,[mid(p) for p in updates]);self.assertNotIn(2596,[mid(p) for p in updates])
    def test_chat_exports_current_portrait_and_returns_to_base_on_expiry(self):
        row=self.add(55832);self.send(807,0,row['id'])
        def head():
            raw=packet(112,['number','string','string','number'],[2,'portrait fixture','',0])
            reply=self.game.receive(112,MessageReader(raw[1:]));r=MessageReader(reply[1:])
            r.integer();r.integer()
            for _ in range(3):r.string()
            for _ in range(4):r.integer()
            r.take(1);r.integer();r.integer();r.take(2)
            return r.integer()
        self.assertEqual(head(),9201)
        with patch('native946_mall_portraits.time',return_value=self.expiry(9201)+1):self.assertEqual(head(),self.game.character['role_id'])

class PartyPortraitTests(unittest.TestCase):
    from test_native946_wilderness_party import WildernessPartyTests as _Base
    setUp=_Base.setUp
    call=_Base.call
    team=_Base.team
    def test_portrait_updates_team_wire_without_changing_model_icon(self):
        from native946_codec import SCHEMAS
        def records(payload):
            r=MessageReader(payload[3:]);r.integer();r.string();rows=[]
            for _ in range(r.take(1)[0]):
                row={}
                for name,typ in SCHEMAS['team_player_info']:
                    row[name]=r.integer() if typ=='number' else r.string() if typ=='string' else r.take(1)[0]
                rows.append(row)
            return rows
        self.team();self.svc.drain(self.a);self.svc.drain(self.b)
        with self.a.progression.transaction() as db:
            Cosmetics(self.a)._add(db,55832)
            item=db.execute('SELECT id FROM inventory WHERE character_id=? AND type=55832',(self.a.character['id'],)).fetchone()[0]
        self.call(self.a,807,0,item)
        view=next(p for p in self.svc.drain(self.b) if mid(p)==3228)
        row=next(r for r in records(view) if r['id']==self.a.character['id'])
        self.assertEqual(row['show_icon'],9201);self.assertEqual(row['icon'],self.a.character['role_id'])
        with self.a.progression.transaction(write=False) as db:until=features(db,self.a.character['id'])['portrait:9201']['expires']
        with patch('native946_mall_portraits.time',return_value=until+1):self.a.tick()
        view=next(p for p in self.svc.drain(self.b) if mid(p)==3228)
        row=next(r for r in records(view) if r['id']==self.a.character['id'])
        self.assertEqual(row['show_icon'],self.a.character['role_id'])

if __name__=='__main__':unittest.main()
