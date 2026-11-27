"""Cosmetic ownership, atomic delivery, native refresh and untrusted cart input."""
import json,sys,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).with_name('.test-deps')))
from hypothesis import given,strategies as st,settings
from native946_cosmetics import Cosmetics,DATA,PAGES,account_wallet_schema,appearance,numeric_table
from native946_game_login import NativeGameLogin,message_id
from native946_sessions import LEASES
from native946_codec import number,SCHEMAS
from native946_login import MessageReader,string_field
from native946_progression import Progression,lua_data
import test_native946_npcs as fixtures
from test_native946_npc_services import mid,menu

class CosmeticTests(unittest.TestCase):
    setUp=fixtures.NpcGameplayTests.setUp
    approach=fixtures.NpcGameplayTests.approach
    def send(self,kind,*args):return self.game.receive(kind,MessageReader(b''.join(number(a) for a in args)))
    def choose(self,choice):return self.game.receive(1032,MessageReader(bytes([choice])+number(5657)))
    def visit(self):self.approach(2001,5657);return self.send(1034,5657)
    def kit(self):self.visit();return self.choose(7)
    def cs(self):return Cosmetics(self.game)
    def rows(self):
        with self.game.progression.transaction(write=False) as db:return [dict(r) for r in db.execute('SELECT * FROM inventory WHERE character_id=? ORDER BY id',(self.game.progression.cid,))]
    def balance(self):
        with self.game.progression.transaction(write=False) as db:return self.cs().wallet(db)[0]
    def buy(self,kind=56006,amount=1):
        self.send(3663,kind,amount);return self.send(3665,kind,amount,0,0)
    def colors(self):
        self.cs().refresh();return self.game.character['appearance']
    def sibling(self):
        owner = self.game.progression.owner
        row = self.characters.create(owner, '商城同账号', 113)
        port = 554
        login = NativeGameLogin(port, None, number, None, self.characters)
        login.username = owner
        self.addCleanup(LEASES.release_port, self.path, port)
        login.receive(message_id(915) + number(row['id']))
        return login.gameplay

    def test_legacy_balances_merge_once_and_keep_character_preferences(self):
        other = self.sibling().progression
        p = self.game.progression
        with p.transaction() as db:
            db.execute('DROP TABLE mall_account_wallet')
            db.executemany('INSERT INTO cosmetic_wallet VALUES(?,?,?,?,?)',
                           [(p.cid, 200, 3, 0, 1), (other.cid, 300, 4, 1, 0)])
            db.execute("INSERT INTO accounts(username,salt,password_hash) VALUES('separate',X'01',X'01')")
            stranger = db.execute(
                'INSERT INTO characters(owner,name,role_id,race,sex,created_at,map_id,x,y) '
                "VALUES('separate','商城另一账号',702,1,2,0,2001,1,1)").lastrowid
            db.execute('INSERT INTO cosmetic_wallet VALUES(?,50,6,0,0)', (stranger,))
        Progression(self.path, p.owner, p.cid)
        with p.transaction() as db:
            account_wallet_schema(db)
            self.assertEqual(Cosmetics(self.game).wallet(db), (500, 7))
            self.assertEqual(tuple(db.execute(
                'SELECT yubo,coupon,test_yubo_claimed FROM mall_account_wallet WHERE owner=?',
                (p.owner,)).fetchone()), (500, 7, 1))
            self.assertEqual(tuple(db.execute(
                "SELECT yubo,coupon FROM mall_account_wallet WHERE owner='separate'").fetchone()), (50, 6))
            self.assertEqual([tuple(r) for r in db.execute(
                'SELECT yubo,coupon,test_claimed,mail FROM cosmetic_wallet WHERE character_id IN (?,?) ORDER BY character_id',
                (p.cid, other.cid))], [(0, 0, 0, 1), (0, 0, 1, 0)])

    def test_credits_and_concurrent_native_checkout_share_the_account(self):
        other = self.sibling()
        p = self.game.progression
        kind = 56006
        cost = DATA['goods'][str(kind)]['yubo']
        with p.transaction() as db:
            item = Cosmetics(self.game)._add(db, 45850, backpack=True)
        # The item continues to belong to this role. Redeeming it updates the shared wallet.
        self.send(807, 0, item)
        with p.transaction() as db:
            db.execute('UPDATE mall_account_wallet SET coupon=? WHERE owner=?', (cost, p.owner))
        request = lambda game, mid, *args: game.receive(mid, MessageReader(b''.join(number(arg) for arg in args)))
        for game in (self.game, other):
            reply = request(game, 3667)
            balance = next(r for r in reply if mid(r) == 813)
            self.assertEqual(MessageReader(balance[3:]).integer(), cost)
            request(game, 3663, kind, 1)
        ready = Barrier(2)
        def checkout(game):
            ready.wait(timeout=3)
            result = request(game, 3665, kind, 1, 1, 0)
            return mid(result) if isinstance(result, bytes) else 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(checkout, (self.game, other)), [0, 98])
        with p.transaction(write=False) as db:
            self.assertEqual(Cosmetics(self.game).wallet(db), (0, 0))
            self.assertEqual(db.execute('SELECT count(*) FROM cosmetic_orders').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM inventory WHERE type=?', (kind,)).fetchone()[0], 1)
        with other.progression.transaction(write=False) as db:
            self.assertEqual(Cosmetics(other).wallet(db), (0, 0))

    def test_native_mall_open_and_closet_inventory_pages(self):
        self.kit();reply=next(p for p in self.send(3667) if mid(p)==3658);self.assertEqual(mid(reply),3658)
        r=MessageReader(reply[3:]);text=r.string().decode('gb18030');self.assertIn('[56006]',text)
        self.assertEqual([r.integer() for _ in range(6)],[0,100000,0,0,0,0]);r.string();r.integer();r.finish()
        self.buy();self.assertTrue(any(r['type']==56006 and r['page'] in PAGES for r in self.rows()))
    def test_quote_cannot_be_forged_or_confirmed_twice(self):
        self.kit();before=self.balance();self.assertEqual(mid(self.send(3665,56006,1,0,0)),98)
        self.send(3663,56006,1);self.assertEqual(mid(self.send(3665,56006,2,0,0)),98)
        self.assertEqual(self.balance(),before)
        self.buy();after=self.balance();self.assertEqual(mid(self.send(3665,56006,1,0,0)),98)
        self.assertEqual(self.balance(),after);self.assertEqual(before-after,DATA['goods']['56006']['yubo'])
    def test_zero_negative_huge_and_unknown_goods_never_change_money(self):
        self.kit();before=self.rows()
        for kind,amount in [(56006,0),(56006,-1),(56006,25),(999999,1)]:self.assertEqual(mid(self.send(3663,kind,amount)),98)
        self.assertEqual(self.rows(),before);self.assertEqual(self.balance(),100000)
    def test_failed_delivery_rolls_back_all_items_and_payment(self):
        self.kit();self.send(3663,56006,2);before=self.rows();old=self.cs()._add;calls=[]
        def fail(service,db,kind,*a,**kw):
            calls.append(kind)
            if len(calls)==2:raise ValueError('fixture storage full')
            return old(db,kind,*a,**kw)
        with patch.object(Cosmetics,'_add',fail):self.assertEqual(mid(self.send(3665,56006,2,0,0)),98)
        self.assertEqual(self.rows(),before);self.assertEqual(self.balance(),100000)
        self.assertIsNotNone(self.game.cosmetic_quote)
    def test_wardrobe_equips_swaps_unequips_and_cold_reloads(self):
        self.kit();self.buy();item=next(r for r in self.rows() if r['type']==56006)
        result=self.send(807,0,item['id']);self.assertIn(2501,[mid(p) for p in result]);self.assertNotIn(4822,[mid(p) for p in result]);self.assertGreater(self.colors()['suit_id'],0)
        cold=Progression(self.path,'npc_owner',self.game.progression.cid)
        with cold.transaction(write=False) as db:self.assertEqual(appearance(db,cold.cid)['suit_id'],self.colors()['suit_id'])
        result=self.send(807,0,item['id']);self.assertNotIn(4822,[mid(p) for p in result]);self.assertEqual(self.colors()['suit_id'],0)
        self.assertIn(next(r for r in self.rows() if r['id']==item['id'])['page'],PAGES)
    def test_wardrobe_does_not_open_skill_panel_or_change_active_skin(self):
        self.kit();skin=next(r for r in self.rows() if r['type']==56313)
        self.assertIn(4822,[mid(p) for p in self.send(807,0,skin['id'])])
        before=self.cs().skin_packet()
        self.buy();self.buy(56005)
        clothes=[r for r in self.rows() if r['type'] in (56006,56005)]
        for item in (*clothes,clothes[-1]):
            result=self.send(807,0,item['id'])
            self.assertIn(2501,[mid(p) for p in result])
            self.assertNotIn(4822,[mid(p) for p in result])
            self.assertEqual(self.cs().skin_packet(),before)
        self.assertEqual(self.send(4824),before)
    def test_wrong_sex_and_expired_wardrobe_items_cannot_equip(self):
        self.kit();self.buy(56002);item=next(r for r in self.rows() if r['type']==56002)
        self.assertEqual(mid(self.send(807,0,item['id'])),98)
        self.buy();item=next(r for r in self.rows() if r['type']==56006)
        with self.game.progression.transaction() as db:db.execute('UPDATE cosmetic_expiry SET expires=1 WHERE item_id=?',(item['id'],))
        self.assertEqual(mid(self.send(807,0,item['id'])),98);self.assertEqual(self.colors()['suit_id'],0)
    def test_inventory_movement_cannot_equip_arbitrary_items_or_wrong_slot(self):
        self.kit();self.buy();item=next(r for r in self.rows() if r['type']==56006)
        self.assertEqual(mid(self.send(803,item['page'],item['slot'],29,10)),98)
        medicine=next(r for r in self.rows() if r['type']==4000)
        self.assertEqual(mid(self.send(803,8,medicine['slot'],25,1)),98)
    def test_forged_item_owner_does_not_consume_or_equip(self):
        self.kit();before=self.rows();self.assertEqual(mid(self.send(807,0,999999)),98);self.assertEqual(self.rows(),before)
    def test_test_kit_is_one_time_and_full_backpack_rolls_back_currency(self):
        self.kit();before=self.rows();self.visit();self.assertEqual(mid(self.choose(7)),98)
        self.assertEqual(self.rows(),before);self.assertEqual(self.balance(),100000)
    def dye(self,colors,session=None,trailing=b''):
        d=self.game.cosmetic_dye
        return self.game.receive(1033,MessageReader(bytes([3])+b''.join(string_field(str(v)) for v in colors)+number(session or d['session'])+trailing))
    def test_native_dye_validates_session_palette_and_trailing_fields(self):
        self.visit();self.choose(4);self.assertEqual(mid(self.dye([1,1,0],session=1)),98)
        self.assertEqual(mid(self.dye([255,255,255])),98);self.assertEqual(self.colors()['color1'],0)
        role=DATA['parts']['702'];valid=[role['color'+str(i)][0] for i in (1,2,3)]
        self.assertEqual(mid(self.dye(valid,trailing=b'x')),98);self.assertEqual(self.colors()['color1'],0)
        reply=self.dye(valid);self.assertIn(1620,[mid(p) for p in reply]);self.assertEqual([self.colors()['color'+str(i)] for i in (1,2,3)],valid)
        self.assertEqual(mid(self.dye(valid)),98) if self.game.cosmetic_dye else None
    def test_weapon_only_native_role_models_and_battle_field_positions(self):
        reply,wiwc=self.native_weapon();self.assertIn(1620,[mid(p) for p in reply])
        model=wiwc//10;self.assertEqual(self.colors()['weapon_idx'],0)
        with self.game.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT weapon_idx FROM cosmetic_state WHERE character_id=?',(self.game.progression.cid,)).fetchone()[0],model)
        p=self.game.fighter(self.game.character['id'],702,'测试',4,1);r=MessageReader(p[3:])
        for i in range(7):r.string() if i==3 else r.integer()
        self.assertEqual(r.take(3),bytes([0,0,0]))
    def native_weapon(self):
        from native946_mall_uses import DATA as USE_DATA
        self.kit();self.buy(47398);self.visit();self.assertIn(3472,[mid(p) for p in self.choose(3)])
        wiwc=next(v['wiwc'] for v in USE_DATA['weapon']['702'] if v['wiwc']//10)
        self.send(3476,0,wiwc);self.send(3475,0)
        return self.send(3474,0,1),wiwc
    def test_native_skill_item_activation_toggle_expiry_and_action_override(self):
        self.kit();item=next(r for r in self.rows() if r['type']==56313)
        activation=self.send(807,0,item['id']);self.assertIn(4822,[mid(p) for p in activation]);self.assertFalse(any(r['id']==item['id'] for r in self.rows()))
        self.assertEqual(mid(self.send(4824)),4822)
        action=self.game.native_action(self.game.character['id'],900002,36,10,100)
        skin=next(p for p in action if mid(p)==4825);r=MessageReader(skin[3:]);self.assertEqual((r.integer(),r.integer()),(36,56313));r.finish()
        self.assertEqual(mid(self.game.receive(4823,MessageReader(number(36)+number(1)+b'\0'))),4822)
        r=MessageReader(self.cs().actor_skin(36)[3:]);self.assertEqual((r.integer(),r.integer()),(36,0))
        with self.game.progression.transaction() as db:db.execute('UPDATE cosmetic_skins SET expires=1 WHERE character_id=?',(self.game.progression.cid,))
        self.assertEqual(mid(self.game.receive(4823,MessageReader(number(36)+number(1)+b'\1'))),98)
    def test_skin_needs_owned_license_and_invalid_bool_never_changes_state(self):
        self.assertEqual(mid(self.game.receive(4823,MessageReader(number(36)+number(1)+b'\1'))),98)
        self.assertEqual(mid(self.game.receive(4823,MessageReader(number(36)+number(1)+b'\2'))),98)
    def test_cart_uses_native_six_field_rows_and_selected_index_list(self):
        self.kit();reply=self.send(3668,56006);r=MessageReader(reply[3:]);text=r.string().decode('gb18030');self.assertEqual(numeric_table(text)[1],{1:56006,2:1,3:500,4:500,5:1,6:{}})
        self.send(3668,56005)
        payload=string_field('{2}')+number(0)+number(0)
        self.assertEqual(mid(self.game.receive(3669,MessageReader(payload))),3659)
        self.game.receive(3671,MessageReader(payload));types=[r['type'] for r in self.rows()]
        self.assertIn(56005,types);self.assertNotIn(56006,types);self.assertEqual(self.balance(),99500)
    def test_native_schema_order_comes_from_original_registry(self):
        self.assertEqual(SCHEMAS['skill_skin_info'],[['skin_id','number'],['skin_active','byte'],['skin_time','number'],['skill_id','number']])
    def test_edited_cart_quantities_and_duplicate_indexes(self):
        self.kit();self.send(3668,56006)
        payload=string_field('{{1,3}}')+number(0)+number(0)
        self.assertEqual(mid(self.game.receive(3669,MessageReader(payload))),3659)
        self.game.receive(3671,MessageReader(payload))
        self.assertEqual(sum(r['amount'] for r in self.rows() if r['type']==56006),3);self.assertEqual(self.balance(),98500)
        self.send(3668,56006)
        self.assertEqual(mid(self.game.receive(3669,MessageReader(string_field('{{1,1},{1,1}}')+number(0)+number(0)))),98)
    def test_unpriced_native_skin_sells_for_user_default_100(self):
        self.kit();before=sum(r['amount'] for r in self.rows() if r['type']==56313)
        self.buy(56313,1);self.assertEqual(self.balance(),99900)
        self.assertEqual(sum(r['amount'] for r in self.rows() if r['type']==56313),before+1)
    def test_native_full_suit_delivers_all_defined_parts_once(self):
        self.kit();self.buy(56008);types=[r['type'] for r in self.rows()]
        self.assertTrue(set(DATA['sets']['56008'])<=set(types));self.assertEqual(self.balance(),98500)
    def test_full_catalog_prices_and_client_categories_match(self):
        root=Path(__file__).parent/'analysis/cosmetics-20261126'
        audit=json.loads((root/'full-native-mall-audit.json').read_text('utf8'))
        patches=json.loads((root/'catalog-patches.json').read_text('utf8'))
        from native946_fashion_shop import DATA as SHOP
        sys.path[:0]=[str(Path(__file__).parent/'analysis/town-portals-20261125'),str(Path(__file__).parent/'analysis/native-npc-completion-20261126')]
        from native946_battle_ui import archive_rows
        from native946_client_stage import CONFIG_ARCHIVE, prepare_config
        from client_archive import resource_hash
        from literal_schema import replay
        from audit_native_rules import plain
        blob = prepare_config(CONFIG_ARCHIVE.read_bytes())
        locations = {key: (offset, size) for key, offset, size, _ in archive_rows(blob)[2]}

        def catalog(name):
            offset, size = locations[resource_hash('info/info_' + name + '.lua')]
            data = blob[offset:offset + size]
            mask = data[0] ^ 27
            return replay(bytes(value ^ mask for value in data))[0]

        client={str(row['type']):row for name in patches for row in plain(catalog(name))['info']}
        staged={str(row['type']):row for table in patches.values() for row in table['info']}
        original=[dict(entry.items()) for _,entry in catalog('single_day_2014').items()]
        archived={str(int(row['id'])):row for row in original}
        from native946_cosmetics import SALE_ALIASES, SALE_GOODS
        from native946_mall_mounts import DYE_MATERIAL
        self.assertEqual(set(client) | {str(DYE_MATERIAL)},set(SALE_GOODS))
        for kind in client:
            row = SALE_GOODS[kind]
            self.assertEqual(row['yubo'],client[kind]['yubo'])
            if row['price_source']=='native_yubo_single_day_2014':
                self.assertEqual(row['yubo'],staged[kind]['yubo'])
                self.assertEqual(row['yubo'],archived[kind]['price'])
                self.assertEqual(DATA['goods'][kind]['name'],archived[kind]['name'])
            self.assertEqual(row['name'],client[kind]['name'])
            self.assertEqual(row['page_type'],client[kind]['page_type'])
            if row['price_source']=='user_default_100':self.assertEqual(row['yubo'],100)
        additions=set(SHOP['goods'])-set(audit['sources'])
        represented=set(client)|set(map(str,SALE_ALIASES))
        missing=set(audit['missing_current_item_definitions'])
        self.assertEqual(audit['total']+len(additions),len(represented))
        self.assertEqual(set(audit['sources'])|additions,represented|missing)
        self.assertFalse(missing&represented)
    def test_purchase_materials_stacks_with_native_cap(self):
        self.kit();self.buy(47398,12)
        amounts=[r['amount'] for r in self.rows() if r['type']==47398]
        self.assertEqual(amounts,[10,2]);self.assertEqual(self.balance(),98800)
    def test_insufficient_balance_and_full_backpack_never_deduct(self):
        self.assertEqual(mid(self.buy()),98)
        self.kit()
        with self.game.progression.transaction() as db:
            occupied={r['slot'] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8',(self.game.progression.cid,))}
            for slot in set(range(1,25))-occupied:db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,8,?,1)',(self.game.progression.cid,slot))
        before=self.balance();self.assertEqual(mid(self.buy(56313)),98);self.assertEqual(self.balance(),before)
    def test_nearby_players_receive_model_refresh_without_scene_restart(self):
        from native946_gameplay import NativeGameplay
        self.visit()
        # A selected appearance is visible to nearby players only while an
        # actual weapon occupies the native weapon slot.
        with self.game.progression.transaction() as db:
            db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,5597,1,4,1)',(self.game.progression.cid,))
        self.cs().visual_packets()
        row=self.characters.create('npc_owner','附近观测',702);row.update(x=226,y=52)
        viewer=NativeGameplay(row,self.characters,lambda:())
        reply,wiwc=self.native_weapon()
        received=self.game.party_service.drain(viewer)
        self.assertEqual([mid(p) for p in received],[1620,2501,1621,1621,1621,1621,1621,1621,1621,1622])
        self.assertEqual(MessageReader(received[0][3:]).integer(),self.game.character['id'])
        # Every participant sees the same hero weapon in a shared battle.
        p=viewer.fighter(self.game.character['id'],702,'其他队员',4,1);r=MessageReader(p[3:])
        for i in range(7):r.string() if i==3 else r.integer()
        self.assertEqual(r.take(1)[0],wiwc//10)

class NumericTableProperties(unittest.TestCase):
    @settings(max_examples=150,deadline=None)
    @given(st.lists(st.integers(min_value=0,max_value=65535),max_size=24))
    def test_native_array_roundtrip(self,values):self.assertEqual(numeric_table(lua_data(values)),dict(enumerate(values,1)))
    @settings(max_examples=150,deadline=None)
    @given(st.dictionaries(st.integers(min_value=1,max_value=65535),st.integers(min_value=0,max_value=999999),max_size=24))
    def test_numeric_map_roundtrip(self,value):self.assertEqual(numeric_table(lua_data(value)),value)
    def test_calls_names_strings_duplicates_depth_and_trailing_input_rejected(self):
        for text in ['os.execute(1)','{1};return 2','{[1]=1,[1]=2}','{[0]=1}','{"1"}','{true}','{{{{1}}}}','{1}x','{1..2}','{nan}']:
            with self.subTest(text=text),self.assertRaises(ValueError):numeric_table(text)
if __name__=='__main__':unittest.main()
