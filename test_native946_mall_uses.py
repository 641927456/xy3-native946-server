"""Real dispatcher coverage for consumables and native NPC panel handshakes."""
from time import time
import unittest
from unittest.mock import patch
from test_native946_cosmetics import CosmeticTests
from test_native946_npc_services import mid
from native946_mall_uses import DATA,features
from native946_cosmetics import DATA as CATALOG,Cosmetics,sale_type
from native946_login import MessageReader

class MallUseTests(CosmeticTests):
    # unittest inheritance intentionally reruns the existing ownership tests
    # against the new routes as well as the extra use-flow cases below.
    def add(self,kind,n=1):
        with self.game.progression.transaction() as db:Cosmetics(self.game)._add(db,kind,n)
        return next(r for r in self.rows() if r['type']==kind)
    def saved(self):
        with self.game.progression.transaction(write=False) as db:return features(db,self.game.progression.cid)
    def test_npc_opens_actual_native_panels(self):
        self.visit();reply=self.choose(2)
        opening=next(p for p in reply if mid(p)==3993)
        self.assertEqual(MessageReader(opening[3:]).string().decode(),'dlg_fashion_closet')
        self.assertNotIn(3993,[mid(p) for p in self.send(837)])
        self.visit();self.assertIn(3472,[mid(p) for p in self.choose(3)])
        self.visit();self.assertIn(3308,[mid(p) for p in self.choose(8)])
        self.visit();self.assertIn(4822,[mid(p) for p in self.choose(5)])
    def test_large_bundle_splits_to_native_stack_limits(self):
        item=self.add(55235);self.send(807,0,item['id'])
        rows=[r for r in self.rows() if r['type']==45851]
        self.assertEqual(sum(r['amount'] for r in rows),999)
        self.assertTrue(all(r['amount']<=min(500,CATALOG['items']['45851']['max_stack']) for r in rows))
        self.assertFalse(any(r['id']==item['id'] for r in self.rows()))
    def test_bundle_failure_restores_container_and_every_output(self):
        item=self.add(55413);before=self.rows();old=Cosmetics._add;calls=[]
        def fail(cs,db,kind,*args,**kwargs):
            calls.append(kind)
            if len(calls)==3:raise ValueError('fixture full')
            return old(cs,db,kind,*args,**kwargs)
        with patch.object(Cosmetics,'_add',fail):self.assertEqual(mid(self.send(807,0,item['id'])),98)
        self.assertEqual(before,self.rows())
    def test_wedding_bundles_deliver_native_counts_duration_and_nested_box(self):
        from collections import Counter
        self.kit()
        wedding_bundles = {
            55146: 0, 65146: 0, 55167: 180, 65167: 180, 55418: 0, 65418: 0,
        }
        for kind, valid_days in wedding_bundles.items():
            with self.subTest(kind=kind, valid_days=valid_days):
                with self.game.progression.transaction() as db:
                    db.execute('DELETE FROM inventory WHERE character_id=?',(self.game.progression.cid,))
                before_purchase = self.balance()
                self.assertNotEqual(mid(self.buy(kind)), 888)
                self.assertEqual(self.balance(), before_purchase - CATALOG['goods'][str(kind)]['yubo'])
                item = next(row for row in self.rows() if row['type'] == sale_type(kind))
                with self.game.progression.transaction(write=False) as db:
                    expires = db.execute('SELECT expires FROM cosmetic_expiry WHERE item_id=?',
                                         (item['id'],)).fetchone()[0]
                if valid_days:
                    self.assertLess(abs(expires - int(time()) - valid_days * 86400), 3)
                else:
                    self.assertEqual(expires, 0)
                self.assertNotEqual(mid(self.send(807,0,item['id'])),888)
                actual=Counter()
                for row in self.rows():actual[row['type']]+=row['amount']
                expected={45715:1,65147:2,55136:2,45718:1} if kind in (55146,65146,55167,65167) else {45715:1,65147:1,55136:1,46153:1,3309:1}
                self.assertEqual(dict(actual),expected)
                with self.game.progression.transaction(write=False) as db:
                    for row in db.execute('SELECT i.type,e.expires FROM inventory i JOIN cosmetic_expiry e ON e.item_id=i.id WHERE i.type IN (65147,55136)'):
                        if row['type']==65147:self.assertLess(abs(row['expires']-int(time())-30*86400),3)
                        else:self.assertEqual(row['expires'],0)
                if 46153 in actual:
                    box=next(r for r in self.rows() if r['type']==46153)
                    self.assertNotEqual(mid(self.send(807,0,box['id'])),888)
                    self.assertEqual(sum(r['amount'] for r in self.rows() if r['type']==55415),1)
                # A verified container does not bypass unavailable wedding play.
                candy=next(r for r in self.rows() if r['type']==45715)
                before=self.rows();self.assertEqual(mid(self.send(807,0,candy['id'])),98)
                self.assertEqual(before,self.rows())
    def test_wedding_bundle_full_bag_rolls_back_wardrobe_and_container(self):
        for kind in (55146, 65146, 55418):
            with self.subTest(kind=kind):
                with self.game.progression.transaction() as db:
                    db.execute('DELETE FROM inventory WHERE character_id=?', (self.game.progression.cid,))
                item=self.add(kind)
                with self.game.progression.transaction() as db:
                    occupied={r['slot'] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8',(self.game.progression.cid,))}
                    for slot in set(range(1,25))-occupied:
                        db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,8,?,1)',(self.game.progression.cid,slot))
                before=self.rows();self.assertEqual(mid(self.send(807,0,item['id'])),98)
                self.assertEqual(before,self.rows())
    def add_child(self, shape):
        import json
        from native946_role_society import baby_defaults

        p = self.game.progression
        with p.transaction() as db:
            value = dict(baby_defaults(p.cid), type=shape, icon=shape)
            return db.execute('INSERT INTO native_babies(character_id,value) VALUES(?,?)',
                              (p.cid, json.dumps(value))).lastrowid

    def test_child_fashion_binding_and_both_native_views(self):
        from native946_child_fashion import worn_model
        from native946_codec import number
        from native946_cosmetics import sale_type
        from native946_login import string_field
        from native946_lua_values import table
        from native946_progression import Progression
        from native946_role_society import baby

        boy, girl, second_boy, second_girl = (self.add_child(shape) for shape in (401, 402, 403, 405))
        self.kit()
        rules = ((56537, boy, girl, 4899), (56538, girl, boy, 4900),
                 (66537, second_boy, second_girl, 4899), (66538, second_girl, second_boy, 4900))
        other_child = {boy: second_boy, second_boy: boy, girl: second_girl, second_girl: girl}
        bound = {}
        for kind, child, wrong_sex, model in rules:
            with self.subTest(kind=kind):
                if sale_type(kind) == kind:
                    self.assertNotEqual(mid(self.buy(kind)), 888)
                    item = next(row for row in self.rows() if row['type'] == kind)
                else:
                    # An older bound variant can still exist in inventory
                    # after its sale has been merged with the kept product.
                    item = self.add(kind)
                item_id = item['id']
                select = self.send(807, 0, item_id)
                self.assertEqual(mid(select), 5260)
                reader = MessageReader(select[3:])
                self.assertEqual(reader.integer(), item_id)
                offered = {r['SId'] for r in table(reader.string().decode('gb18030')).values()}
                self.assertIn(child, offered)
                self.assertNotIn(wrong_sex, offered)
                self.assertEqual(reader.integer(), kind)
                reader.finish()
                self.assertEqual(next(row for row in self.rows() if row['id'] == item_id), item)
                self.assertEqual(mid(self.send(5261, item_id, wrong_sex, kind)), 98)
                self.assertEqual(mid(self.send(5261, item_id, 999999, kind)), 98)
                self.assertEqual(mid(self.send(5261, item_id, child, 56006)), 98)
                self.assertEqual(next(row for row in self.rows() if row['id'] == item_id), item)
                result = self.send(5261, item_id, child, kind)
                self.assertIn(5265, [mid(p) for p in result])
                row = next(row for row in self.rows() if row['id'] == item_id)
                self.assertIn(row['page'], range(25,29))
                self.assertEqual(mid(self.send(5261, item_id, child, kind)), 98)
                self.assertEqual(mid(self.send(803, row['page'], row['slot'], 8, 24)), 98)
                owner_entry = next(p for p in self.send(837) if mid(p) == 5265)
                reader = MessageReader(owner_entry[3:])
                wardrobes = {r['Id']:r['Fashions'] for r in table(reader.string().decode('gb18030')).values()}
                self.assertEqual(wardrobes[child][str(item_id)]['ItemType'], kind)
                self.assertEqual(wardrobes[child][str(item_id)]['IsWearing'], 1)
                self.assertNotIn(str(item_id), wardrobes[wrong_sex])
                reply = self.game.receive(3076, MessageReader(number(child) + bytes([11])))
                panel = next(p for p in reply if mid(p) == 5262)
                r = MessageReader(panel[3:])
                self.assertEqual(r.integer(), child)
                self.assertEqual(r.string(), str(child).encode())
                r.string()
                self.assertEqual(r.string(), str(item_id).encode())
                self.assertIn(str(item_id), table(r.string().decode('gb18030')))
                r.string()
                r.integer()
                self.assertEqual(r.integer(), model)
                r.finish()
                self.assertEqual(mid(self.game.receive(5263, MessageReader(
                    number(other_child[child]) + string_field(str(item_id)) + number(1)))), 98)
                removed = self.send(5264, child, 0)
                self.assertIn(5262, [mid(p) for p in removed])
                p = self.game.progression
                with p.transaction(write=False) as db:
                    self.assertEqual(worn_model(db, p.cid, baby(db, p.cid, child)), 0)
                self.assertIn(5265, [mid(p) for p in self.game.receive(5263, MessageReader(
                    number(child) + string_field(str(item_id)) + number(1)))])
                bound[child] = model
        listing = next(p for p in self.send(3083, 0) if mid(p) == 3054)
        reader = MessageReader(listing[3:])
        icons = [reader.integer() for _ in range(reader.take(1)[0])]
        ids = [reader.integer() for _ in range(reader.take(1)[0])]
        reader.integer()
        reader.integer()
        flags = [reader.integer() for _ in range(reader.take(1)[0])]
        reader.finish()
        self.assertEqual(icons, [401, 402, 403, 405])
        self.assertEqual(dict(zip(ids, flags)), {child: 1 for child in bound})
        self.assertIn(3048, [mid(p) for p in self.send(3087, boy, 1)])
        self.assertIn(3048, [mid(p) for p in self.send(3087, boy, 3)])
        cold = Progression(self.path, self.game.progression.owner, self.game.progression.cid)
        with cold.transaction(write=False) as db:
            self.assertEqual(baby(db, cold.cid, boy)['status'], 0)
            for child, model in bound.items():
                self.assertEqual(worn_model(db, cold.cid, baby(db, cold.cid, child)), model)
            score = db.execute('SELECT sum(score) FROM native_fashion_used WHERE character_id=? '
                               'AND type IN (56537,56538)', (cold.cid,)).fetchone()[0]
            self.assertEqual(score, 80)

    def test_child_fashion_full_wardrobe_rolls_back_binding(self):
        from native946_cosmetics import PAGES
        from native946_role_society import baby

        child = self.add_child(401)
        item = self.add(56537)
        p = self.game.progression
        with p.transaction() as db:
            db.executemany('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,?,?,1)',
                           ((p.cid, page, slot) for page in PAGES for slot in range(1, 25)))
        before = self.rows()
        self.assertEqual(mid(self.send(5261, item['id'], child, 56537)), 98)
        self.assertEqual(before, self.rows())
        with p.transaction(write=False) as db:
            self.assertFalse(baby(db, p.cid, child).get('fashion_items'))
    def test_virtual_product_recipe_does_not_authorize_unknown_inventory_item(self):
        with self.game.progression.transaction() as db:
            row=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,100001,8,24,1)',(self.game.progression.cid,))
            item_id=row.lastrowid
        before=self.rows();self.assertEqual(mid(self.send(807,0,item_id)),98)
        self.assertEqual(before,self.rows())
    def test_emoji_native_list_and_duplicate_permanent_preserved(self):
        item=self.add(55170);reply=self.send(807,0,item['id']);self.assertIn(1327,[mid(p) for p in reply])
        self.assertIn('emote:1',self.saved())
        item=self.add(55170);self.assertEqual(mid(self.send(807,0,item['id'])),98)
        self.assertTrue(any(r['id']==item['id'] for r in self.rows()))
    def test_mount_owned_ride_unride_expiry_and_cold_state(self):
        self.assertEqual(mid(self.send(2300,19)),98)
        item=self.add(55143);self.send(807,0,item['id']);self.assertIn('mount:19',self.saved())
        self.assertEqual(mid(self.send(2302)),2265)
        reply=self.send(2300,19);self.assertIn(1592,[mid(p) for p in reply]);self.assertEqual(self.colors()['mount_shape'],4544)
        self.send(2289);self.assertNotIn('mount_shape',self.colors())
        self.send(2300,19)
        with self.game.progression.transaction() as db:db.execute("UPDATE cosmetic_features SET expires=1 WHERE key='mount:19'")
        self.assertEqual(mid(self.send(2300,19)),98)
        self.assertNotIn('mount_shape',self.colors())
        regular=MessageReader(self.send(2302)[3:])
        self.assertEqual((regular.integer(),regular.take(1),regular.integer()),(0,b'\x00',0))
        short=MessageReader(self.send(2296)[3:])
        self.assertEqual((short.take(1),short.integer()),(b'\x00',0));short.finish()
    def test_namecard_activation_switch_ownership_and_expiry(self):
        item=self.add(56029);reply=self.send(807,0,item['id'])
        self.assertIn(4473,[mid(p) for p in reply]);self.assertIn('namecard:1',self.saved())
        self.assertEqual(self.saved()['namecard_current']['value'],1)
        self.send(4475,0);self.assertEqual(self.saved()['namecard_current']['value'],0)
        self.assertEqual(mid(self.send(4475,999)),98)
        self.send(4475,1);self.assertEqual(self.saved()['namecard_current']['value'],1)
        with self.game.progression.transaction() as db:db.execute("UPDATE cosmetic_features SET expires=1 WHERE key='namecard:1'")
        self.assertEqual(mid(self.send(4475,1)),98)
        reply=self.send(4476);self.assertEqual(mid(reply),4473)
        r=MessageReader(reply[3:]);r.integer()
        for _ in range(3):r.string()
        for _ in range(4):r.integer()
        r.string();data=r.string().decode();selected=r.integer();r.finish()
        self.assertEqual(selected,0);self.assertNotIn('[1]=1',data)
    def test_point_uses_change_native_mall_point(self):
        item=self.add(55635);reply=self.send(807,0,item['id'])
        self.assertEqual(self.game.progression.attribute_values()['TimeCredit'],800)
        points=next(p for p in reply if mid(p)==815)
        r=MessageReader(points[3:]);self.assertEqual(r.integer(),800);r.finish()
    def test_totem_uses_native_left_side_and_expires_without_affecting_other_cosmetics(self):
        item=self.add(55040);self.send(807,0,item['id'])
        self.assertEqual((self.colors()['left_totem'],self.colors()['right_totem']),(1,0))
        self.assertLess(abs(self.saved()['totem:1']['expires']-int(time())-15*86400),3)
        item=self.add(55040);before=self.rows();self.assertEqual(mid(self.send(807,0,item['id'])),98);self.assertEqual(before,self.rows())
        with self.game.progression.transaction() as db:db.execute("UPDATE cosmetic_features SET expires=1 WHERE key='totem:1'")
        self.assertEqual(self.colors()['left_totem'],0)
    def test_carving_preset_uses_native_cost_and_rejects_replay(self):
        from native946_mall_carving import stored
        self.add(55174,3);item=next(i for i in self.rows() if i['type']==55174)
        gear=next(i for i in self.rows() if i['type']==5001)
        self.assertEqual(mid(self.send(807,0,item['id'])),222)
        self.assertEqual(mid(self.send(280,gear['id'],gear['type'],1)),223)
        self.send(238,gear['id'],1,3)
        self.assertFalse(any(i['type']==55174 for i in self.rows()))
        with self.game.progression.transaction(write=False) as db:data=stored(db,self.game.progression.cid,gear['id'])
        self.assertEqual(data['ctype'],1);self.assertEqual(data['cname'],self.game.character['name'])
        self.assertEqual(mid(self.send(238,gear['id'],1,3)),98)
    def test_carving_custom_validation_and_missing_material_keep_equipment(self):
        from native946_codec import packet
        from native946_mall_carving import stored
        item=self.add(55174);gear=next(i for i in self.rows() if i['type']==5001)
        self.send(807,0,item['id']);self.send(280,gear['id'],gear['type'],1)
        before=self.rows()
        def send_text(text):
            raw=packet(239,['number','string'],[gear['id'],text])
            return self.game.receive(239,MessageReader(raw[1:]))
        self.assertEqual(mid(send_text('x'*41)),98);self.assertEqual(before,self.rows())
        self.assertEqual(mid(self.send(238,gear['id'],-1,3)),98);self.assertEqual(before,self.rows())
        send_text('native inscription')
        with self.game.progression.transaction(write=False) as db:data=stored(db,self.game.progression.cid,gear['id'])
        self.assertEqual(data['content'],'native inscription');self.assertEqual(data['ctype'],0)
    def test_native_advanced_auto_and_unlimited_time_extend_separately(self):
        item=self.add(55577);reply=self.send(807,0,item['id']);self.assertIn(2756,[mid(p) for p in reply])
        advanced=self.saved()['auto_advanced']['expires'];self.assertLess(abs(advanced-int(time())-7*86400),3)
        item=self.add(56660);self.send(807,0,item['id']);self.assertEqual(self.saved()['auto_advanced']['expires'],advanced+86400)
        item=self.add(70476);self.send(807,0,item['id']);self.assertLess(abs(self.saved()['auto_infinite']['expires']-int(time())-7*86400),3)
        reply=self.send(2757);self.assertEqual(mid(reply),2756)
        r=MessageReader(reply[3:]);self.assertEqual(r.integer(),8)
        with self.game.progression.transaction() as db:db.execute("UPDATE cosmetic_features SET expires=1 WHERE key IN ('auto_advanced','auto_infinite')")
        reply=self.send(2757);self.assertEqual(MessageReader(reply[3:]).integer(),0)
    def test_checkout_voucher_threshold_quantity_and_native_seven_day_expiry(self):
        self.kit();ticket=self.add(48112,3)
        with self.game.progression.transaction(write=False) as db:
            expiry=db.execute('SELECT expires FROM cosmetic_expiry WHERE item_id=?',(ticket['id'],)).fetchone()[0]
        self.assertLess(abs(expiry-int(time())-7*86400),3)
        before=self.balance();self.send(3663,56006,3);self.send(3665,56006,3,0,48112)
        self.assertEqual(before-self.balance(),CATALOG['goods']['56006']['yubo']*3-300)
        self.assertFalse(any(i['type']==48112 for i in self.rows()))
    def test_expired_or_ineligible_voucher_never_changes_payment_or_items(self):
        self.kit();ticket=self.add(48112)
        with self.game.progression.transaction() as db:db.execute('UPDATE cosmetic_expiry SET expires=1 WHERE item_id=?',(ticket['id'],))
        before=self.rows();balance=self.balance();self.send(3663,56006,1)
        self.assertEqual(mid(self.send(3665,56006,1,0,48112)),98)
        self.assertEqual(before,self.rows());self.assertEqual(balance,self.balance())
        with self.game.progression.transaction() as db:db.execute('UPDATE cosmetic_expiry SET expires=0 WHERE item_id=?',(ticket['id'],))
        self.send(3663,47103,4);self.assertEqual(mid(self.send(3665,47103,4,0,48112)),98)
        self.assertEqual(balance,self.balance())
    def test_temporary_bag_expiry_allows_withdraw_and_blocks_deposit_or_swap(self):
        item=self.add(55358);self.send(807,0,item['id'])
        self.assertLess(abs(self.saved()['bag_temp']['expires']-int(time())-7*86400),3)
        medicine=next(i for i in self.rows() if i['type']==4000)
        self.game.progression.move_item(medicine['id'],15,1)
        with self.game.progression.transaction() as db:db.execute("UPDATE cosmetic_features SET expires=1 WHERE key='bag_temp'")
        other=next(i for i in self.rows() if i['page']==8)
        with self.assertRaises(ValueError):self.game.progression.move_item(other['id'],15,2)
        with self.assertRaises(ValueError):self.game.progression.move_item(medicine['id'],8,other['slot'])
        empty=next(s for s in range(1,25) if not any(i['page']==8 and i['slot']==s for i in self.rows()))
        self.game.progression.move_item(medicine['id'],8,empty)
        self.assertEqual(next(i for i in self.rows() if i['id']==medicine['id'])['page'],8)
    def test_permanent_expansion_native_confirm_caps_and_replay(self):
        self.assertEqual(mid(self.send(4042,1,0,0)),98)
        for n in range(3):
            item=self.add(55655);self.assertEqual(mid(self.send(4041,1,n+1,0)),4040)
            self.send(4042,1,n+1,0);self.assertEqual(self.saved()['bag_item']['value'],n+1)
            self.assertEqual(mid(self.send(4042,1,n+1,0)),98)
        item=self.add(55655);before=self.rows();self.assertEqual(mid(self.send(4041,1,4,0)),98);self.assertEqual(before,self.rows())
        self.send(4041,3,0,0);self.send(4042,3,0,0);self.assertEqual(self.game.summons.carry_limit(),7)
    def test_permanent_temp_bag_duplicate_is_not_consumed(self):
        item=self.add(55360);self.send(807,0,item['id']);self.assertEqual(self.saved()['bag_temp']['expires'],0)
        item=self.add(55359);before=self.rows();self.assertEqual(mid(self.send(807,0,item['id'])),98);self.assertEqual(before,self.rows())
    def test_unimplemented_activity_rules_keep_tokens(self):
        for kind in (45877,55039,70486,55617):
            item=self.add(kind);before=self.rows()
            self.assertEqual(mid(self.send(807,0,item['id'])),98);self.assertEqual(before,self.rows())

    def test_number_voucher_tongbao_is_trial_only_and_consumed_once(self):
        item = self.add(45844)
        item_id = item['id']
        progression = self.game.progression
        progression.item_packets()
        balance = progression.snapshot()['cultivation_state']['tongbao']
        self.assertEqual(mid(self.send(807, 0, item_id)), 98)
        self.assertIn(item, self.rows())
        self.assertEqual(progression.snapshot()['cultivation_state']['tongbao'], balance)

        with patch.object(self.game.characters, 'trial_server', True):
            with progression.transaction() as db:
                db.execute("INSERT INTO native_object_locks(character_id,kind,object_id,flag) VALUES(?,'item',?,1)",
                           (progression.cid, item_id))
            self.assertEqual(mid(self.send(807, 0, item_id)), 98)
            self.assertEqual(progression.snapshot()['cultivation_state']['tongbao'], balance)
            with progression.transaction() as db:
                db.execute("DELETE FROM native_object_locks WHERE kind='item' AND object_id=?", (item_id,))
            replies = self.send(807, 0, item_id)
            self.assertIn(780, [mid(reply) for reply in replies])
            self.assertFalse(any(row['id'] == item_id for row in self.rows()))
            self.assertEqual(mid(self.send(807, 0, item_id)), 98)
            self.assertEqual(progression.snapshot()['cultivation_state']['tongbao'], balance + 100000)
            ordinary_number_voucher = self.add(55197)
            self.assertEqual(mid(self.send(807, 0, ordinary_number_voucher['id'])), 98)
            self.assertIn(ordinary_number_voucher, self.rows())
            self.assertEqual(progression.snapshot()['cultivation_state']['tongbao'], balance + 100000)

    def test_guardian_reset_uses_owned_persisted_state(self):
        from native946_mall_guardians import GuardianAppearance
        sid=self.game.summons.claim_starter()
        with self.game.progression.transaction() as db:GuardianAppearance(self.game)._save(db,sid,color1=1,color2=3,halo=28)
        item=self.add(55320);reply=self.send(807,sid,item['id'])
        self.assertIn(3970,[mid(p) for p in reply])
        self.assertEqual([self.game.summons.get(sid)['color'+str(i)] for i in (1,2,3)],[0,0,0])
        self.assertEqual(self.game.summons.get(sid)['halo'],28)
        item=self.add(55321);self.send(807,sid,item['id']);self.assertEqual(self.game.summons.get(sid)['halo'],0)
    def test_guardian_appearance_consumes_once_and_heals_on_either_outcome(self):
        sid=self.game.summons.claim_starter()
        for kind,roll,key in ((55068,99,'color1'),(55070,99,'color1'),(55265,0,'halo'),(55305,99,'foot_halo')):
            with self.subTest(kind=kind):
                with self.game.progression.transaction() as db:
                    db.execute('UPDATE summons SET hp=1,mp=1,honesty=1 WHERE id=?',(sid,))
                item=self.add(kind)
                with patch('native946_mall_guardians.randbelow',return_value=roll), patch('native946_mall_guardians.choice',side_effect=lambda pool:pool[-1]):
                    reply=self.send(807,sid,item['id'])
                self.assertIn(3970,[mid(p) for p in reply])
                pet=self.game.summons.get(sid)
                self.assertEqual((pet['hp'],pet['mp'],pet['honesty']),(pet['hpmax'],pet['mpmax'],100))
                if kind==55068:self.assertEqual(pet[key],0)
                else:self.assertNotEqual(pet[key],0)
                self.assertFalse(any(r['id']==item['id'] for r in self.rows()))
                self.assertEqual(mid(self.send(807,sid,item['id'])),98)
    def test_guardian_owned_halo_toggle_rejects_unowned_and_foreign(self):
        from native946_mall_guardians import GuardianAppearance
        from native946_lua_values import table

        sid=self.game.summons.claim_starter()
        from native946_summons import STARTER_TYPE
        other=self.game.summons.acquire(STARTER_TYPE,'wardrobe_fixture')
        with self.game.progression.transaction() as db:GuardianAppearance(self.game)._save(db,sid,halo=28,items='{"halo":{"55265":28,"55263":21}}')
        panel=self.send(3969,0)
        reader=MessageReader(panel[3:])
        self.assertEqual(reader.integer(),0)
        decoded=table(reader.string().decode('gb18030'))
        reader.finish()
        self.assertEqual(set(decoded),{sid, other})
        self.assertEqual(decoded[sid]['Halo']['CurItem'],55265)
        self.assertEqual(decoded[other]['Halo']['CurItem'],0)
        self.send(3971,2,sid,0);self.assertEqual(self.game.summons.get(sid)['halo'],0)
        self.send(3971,2,sid,0);self.assertEqual(self.game.summons.get(sid)['halo'],0)
        self.send(3971,2,sid,55265);self.assertEqual(self.game.summons.get(sid)['halo'],28)
        self.assertEqual(mid(self.send(3971,2,sid,55264)),98)
        self.assertEqual(mid(self.send(3971,2,999999,55265)),98)
    def test_guardian_native_preview_and_sku_specific_eligibility(self):
        sid=self.game.summons.claim_starter()
        with self.game.progression.transaction() as db:
            db.execute('UPDATE summons SET type=? WHERE id=?', (3399, sid))
        for kind in (55071,65071):
            with self.subTest(kind=kind):
                item=self.add(kind)
                before=self.rows()
                self.assertEqual(mid(self.send(807,0,item['id'])),3970)
                if kind==55071:
                    for result in (self.send(807,sid,item['id']),self.send(3971,4,sid,kind)):
                        self.assertEqual(mid(result),98)
                        reader=MessageReader(result[1:])
                        self.assertIn('不适用',reader.string().decode('gb18030'))
                        self.assertEqual(reader.string(),b'')
                        self.assertEqual((reader.integer(),reader.integer()),(1,0))
                        reader.finish()
                    self.assertEqual(self.rows(),before)
                else:
                    with patch('native946_mall_guardians.randbelow',return_value=0):
                        self.assertIn(3970,[mid(p) for p in self.send(3971,4,sid,kind)])
                    self.assertNotIn(item,self.rows())

    def test_local_fudou_wallet_and_native_checkout(self):
        from native946_mall_checkout import fudou_eligible
        from native946_cosmetics import SALE_GOODS
        kind=next(int(k) for k in SALE_GOODS if fudou_eligible(int(k)))
        price=SALE_GOODS[str(kind)]['yubo']
        bean=self.add(45850,2)
        self.send(807,0,bean['id'])
        self.assertEqual(next(r for r in self.rows() if r['id']==bean['id'])['amount'],1)
        cs=Cosmetics(self.game)
        p=self.game.progression
        with p.transaction() as db:
            self.assertEqual(cs.wallet(db)[1],1)
            db.execute('UPDATE mall_account_wallet SET coupon=?,yubo=1000 WHERE owner=?',(price,p.owner))
        self.send(3663,55219,1)
        self.assertEqual(mid(self.send(3665,55219,1,1,0)),98)
        self.send(3663,kind,1)
        result=self.send(3665,kind,1,1,0)
        self.assertIn(813,[mid(r) for r in result])
        self.assertTrue(any(r['type']==kind for r in self.rows()))
        with p.transaction(write=False) as db:self.assertEqual(cs.wallet(db),(1000,0))
        self.assertEqual(mid(self.send(3665,kind,1,1,0)),98)

    def test_local_anniversary_box_checks_key_and_delivers_original_fixed_rewards(self):
        from native946_mall_event_boxes import ANNIVERSARY_REWARDS
        chest=self.add(46577)
        self.assertEqual(mid(self.send(807,0,chest['id'])),98)
        self.assertIn(chest,self.rows())
        self.add(65613)
        with patch('native946_mall_event_boxes.randbelow',return_value=4):
            self.send(807,0,chest['id'])
        counts={kind:sum(row['amount'] for row in self.rows() if row['type']==kind)
                for kind in (*ANNIVERSARY_REWARDS,6183,46577,65613)}
        for kind,(count,*_) in ANNIVERSARY_REWARDS.items():
            self.assertEqual(counts[kind],count)
        self.assertEqual((counts[6183],counts[46577],counts[65613]),(1,0,0))
        registration=next(r for r in self.rows() if r['type']==46578)
        self.assertEqual(mid(self.send(807,0,registration['id'])),98)
        self.assertIn(registration,self.rows())

    def test_local_thanksgiving_bag_preserves_fixed_reward_and_roll_count(self):
        for kind in (55519,65519):
            box=self.add(kind)
            with patch('native946_mall_uses.secrets.choice',side_effect=lambda pool:pool[2]) as draw:
                self.send(807,0,box['id'])
                self.assertEqual(draw.call_count,2)
        self.assertEqual(sum(r['amount'] for r in self.rows() if r['type']==55174),2)
        self.assertEqual(sum(r['amount'] for r in self.rows() if r['type']==55248),4)

    def test_local_rare_box_favors_known_native_rewards_and_guardian_exp_pill(self):
        for box,reward,roll in ((70632,46525,0),(80632,47576,999)):
            item=self.add(box)
            with patch('native946_mall_event_boxes.randbelow',return_value=roll):
                self.send(807,0,item['id'])
            self.assertFalse(any(r['id']==item['id'] for r in self.rows()))
            self.assertTrue(any(r['type']==reward for r in self.rows()))
        sid=self.game.summons.claim_starter()
        p=self.game.progression
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=2 WHERE id=?',(p.cid,))
        item=self.add(47100)
        self.send(807,sid,item['id'])
        pet=self.game.summons.get(sid)
        self.assertEqual((pet['level'],pet['experience']),(12,197360))
        self.assertNotIn(item,self.rows())

    def test_century_guardian_experience_levels_and_refreshes_the_native_panel(self):
        from native946_codec import packet
        from native946_guardian_lottery import EXPERIENCE_ITEM

        sid=self.game.summons.claim_starter()
        p=self.game.progression
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=2 WHERE id=?',(p.cid,))
            page,slot=p.free_bag_slot(p._snapshot(db))
            item=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
                            (p.cid,EXPERIENCE_ITEM,page,slot)).lastrowid
        reply=self.send(807,sid,item)
        pet=self.game.summons.get(sid)
        self.assertEqual((pet['level'],pet['experience'],pet['points']),(12,499997360,48))
        self.assertIn(packet(706,['number','summon'],[sid,self.game.summons.info_record(pet)]),reply)
        self.assertNotIn(item,{row['id'] for row in self.rows()})
        self.assertEqual(mid(self.send(807,sid,item)),98)
        self.assertEqual(self.game.summons.get(sid),pet)

    def test_local_beast_box_persists_charges_and_grants_real_equipment(self):
        from native946_mall_event_boxes import BEAST_BOX_WEIGHTS, remaining_uses
        from native946_equipment import records
        p=self.game.progression
        item=self.add(80483)
        rolls=[sum(w for _,w in BEAST_BOX_WEIGHTS[:2]),sum(w for _,w in BEAST_BOX_WEIGHTS)-1]
        with patch('native946_mall_event_boxes.randbelow',side_effect=rolls):
            for _ in rolls:self.send(807,0,item['id'])
        with p.transaction(write=False) as db:
            self.assertEqual(remaining_uses(db,item),18)
            gear=records(db,p.cid)
            self.assertEqual(len(gear),1)
            self.assertEqual(next(iter(gear.values()))['grade'],1)
        self.assertEqual(sum(r['amount'] for r in self.rows() if r['type']==47100),60)
        self.assertTrue(any(r['type']==46980 for r in self.rows()))
        single=self.add(70482)
        with patch('native946_mall_event_boxes.randbelow',return_value=0):
            self.send(807,0,single['id'])
        self.assertNotIn(single,self.rows())
        self.assertTrue(any(r['type']==46525 for r in self.rows()))

    def test_guardian_panel_consumes_only_available_unlocked_reset(self):
        from native946_mall_guardians import GuardianAppearance

        sid=self.game.summons.claim_starter()
        p=self.game.progression
        bag=self.add(55358)
        self.send(807,0,bag['id'])
        available=self.add(55320)
        p.move_item(available['id'],15,1)
        with p.transaction() as db:
            GuardianAppearance(self.game)._save(db,sid,color1=1)
            Cosmetics(self.game)._add(db,55320)
            locked=db.execute('SELECT id FROM inventory WHERE character_id=? AND type=55320 AND page=8',(p.cid,)).fetchone()[0]
            db.execute("INSERT INTO native_object_locks(character_id,kind,object_id,flag) VALUES(?,'item',?,1)",(p.cid,locked))
        reply=self.send(3971,4,sid,55320)
        self.assertIn(3970,[mid(result) for result in reply])
        self.assertEqual({r['id'] for r in self.rows() if r['type']==55320},{locked})
        self.assertEqual(self.game.summons.get(sid)['color1'],0)
    def test_weapon_requires_session_material_and_one_confirmation(self):
        self.visit();self.choose(3);pool=DATA['weapon']['702'];wi=pool[-1]['wiwc']
        self.assertEqual(mid(self.send(3476,0,99999)),98)
        self.send(3476,0,wi);self.assertEqual(mid(self.send(3475,0)),98)
        material=self.add(47398);self.send(3475,0);before=self.rows()
        self.assertEqual(mid(self.send(3475,0)),98);self.assertEqual(before,self.rows())
        self.send(3474,0,1);self.assertEqual(self.colors()['weapon_idx'],0)
        with self.game.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT weapon_idx FROM cosmetic_state WHERE character_id=?',(self.game.progression.cid,)).fetchone()[0],wi//10)
        self.assertEqual(mid(self.send(3474,0,1)),98)
    def test_weapon_color_native_result_does_not_accept_invalid_or_replay(self):
        self.visit();self.choose(8);material=self.add(47103)
        self.assertEqual(mid(self.send(3309,0,99)),98)
        self.send(3309,0,2);self.assertFalse(any(r['id']==material['id'] for r in self.rows()))
        self.assertEqual(mid(self.send(3309,0,3)),98)
        self.send(3310,0,1);self.assertEqual(self.colors()['weapon_color'],0)
        with self.game.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT weapon_color FROM cosmetic_state WHERE character_id=?',(self.game.progression.cid,)).fetchone()[0],2)
    def test_halo_refinement_opens_without_consuming_ding(self):
        from native946_codec import packet

        item = self.add(46782)
        before = self.rows()
        self.assertEqual(self.send(807, 0, item['id']), packet(2980, ['number'], [1]))
        self.assertEqual(self.rows(), before)
    def test_random_bag_keeps_guarantee_and_commits_draws_once(self):
        item=self.add(55230);rule=DATA['rules']['55230']
        with patch('native946_mall_uses.secrets.choice',side_effect=[rule['candidates'][0],rule['candidates'][-1]]):self.send(807,0,item['id'])
        rows=self.rows()
        for kind in [55331,55218,55142]:self.assertEqual(sum(r['amount'] for r in rows if r['type']==kind),1)
        self.assertEqual(CATALOG['items']['55331']['days'],15)
        with self.game.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cosmetic_use_receipts').fetchone()[0],1)
        self.assertEqual(mid(self.send(807,0,item['id'])),98)
        self.assertEqual(rows,self.rows())
    def test_random_bag_full_storage_does_not_draw_or_consume(self):
        item=self.add(55230)
        with self.game.progression.transaction() as db:
            occupied={r['slot'] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8',(self.game.progression.cid,))}
            for slot in set(range(1,25))-occupied:db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,8,?,1)',(self.game.progression.cid,slot))
        before=self.rows()
        with patch('native946_mall_uses.secrets.choice') as draw:
            self.assertEqual(mid(self.send(807,0,item['id'])),98);draw.assert_not_called()
        self.assertEqual(before,self.rows())
    def test_random_bag_delivery_failure_rolls_back_reward_and_receipt(self):
        item=self.add(55230);before=self.rows();old=Cosmetics._add;calls=[]
        def fail(cs,db,kind,*args,**kwargs):
            calls.append(kind)
            if len(calls)==2:raise ValueError('fixture write failure')
            return old(cs,db,kind,*args,**kwargs)
        with patch.object(Cosmetics,'_add',fail):self.assertEqual(mid(self.send(807,0,item['id'])),98)
        self.assertEqual(before,self.rows())
        with self.game.progression.transaction(write=False) as db:self.assertEqual(db.execute('SELECT count(*) FROM cosmetic_use_receipts').fetchone()[0],0)
    def test_active_duration_does_not_use_purchase_validity(self):
        item=self.add(55331)
        with self.game.progression.transaction(write=False) as db:expiry=db.execute('SELECT expires FROM cosmetic_expiry WHERE item_id=?',(item['id'],)).fetchone()[0]
        self.assertLess(abs(expiry-int(time())-15*86400),3)
    def test_chat_uses_owned_lingxizhi_and_restores_on_unequip(self):
        from native946_codec import packet
        item=self.add(55150);self.send(807,0,item['id'])
        def say():
            request=packet(112,['number','string','string','number'],[2,'灵犀纸测试','',0])
            reply=self.game.receive(112,MessageReader(request[1:]));self.assertEqual(mid(reply),95)
            r=MessageReader(reply[1:]);r.integer();r.integer()
            for _ in range(3):r.string()
            r.integer();r.integer();return r.integer()
        self.assertEqual(say(),1)
        self.send(807,0,item['id']);self.assertEqual(say(),0)
    def test_enter_effect_is_queued_with_native_item_type(self):
        item=self.add(56459);self.send(807,0,item['id'])
        effects=Cosmetics(self.game).enter_effects();self.assertEqual(mid(effects[0]),3832)
        r=MessageReader(effects[0][3:]);self.assertEqual(r.integer(),9);self.assertEqual(r.take(1),b'\x01')
        # pair_string_number native order: key string then value number.
        self.assertEqual(r.string().decode(),str(self.game.character['id']));self.assertEqual(r.integer(),56459);r.finish()

if __name__=='__main__':unittest.main()
