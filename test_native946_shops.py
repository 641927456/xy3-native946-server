"""Transactional purchases and native catalog contracts using disposable saves."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from threading import Barrier
import json,unittest
from unittest.mock import patch
from native946_codec import number
from native946_login import MessageReader, string_field
from native946_shops import Shops,DATA,BASE,TZ,cycle,liuguang_balance,migrate_liuguang,on_shelf,period
from native946_npc_services import NpcServices,service_kind
from native946_world import world
import test_native946_npcs as fixtures
from test_native946_npc_services import mid,menu


class ShopTests(unittest.TestCase):
    setUp=fixtures.NpcGameplayTests.setUp
    approach=fixtures.NpcGameplayTests.approach

    def send(self,kind,*args):return self.game.receive(kind,MessageReader(b''.join(number(a) for a in args)))
    def choose(self,nid,choice):return self.game.receive(1032,MessageReader(bytes([choice])+number(nid)))
    def open(self,scene,nid,op):
        self.approach(scene,nid);self.send(1034,nid)
        choice=next(i for i,a in enumerate(self.game.dialog_actions,1) if isinstance(a,dict) and a.get('op')==op)
        return self.choose(nid,choice)
    def state(self):
        with self.game.progression.transaction(write=False) as db:
            return dict(gold=db.execute('SELECT gold FROM characters WHERE id=?',(self.game.progression.cid,)).fetchone()[0],
                items=[tuple(r) for r in db.execute('SELECT * FROM inventory ORDER BY id')],
                orders=[tuple(r) for r in db.execute('SELECT * FROM native_shop_orders ORDER BY id')])
    def enable_special(self):
        self.addCleanup(patch.stopall)
        patch.dict(DATA['server'],opened_at=1).start()
        with self.game.progression.transaction() as db:
            cid=self.game.progression.cid
            db.execute('UPDATE characters SET level=140 WHERE id=?',(cid,))
            db.execute('UPDATE cultivation_state SET rebirth=2,ascension=1 WHERE character_id=?',(cid,))
            db.execute('INSERT INTO mall_account_wallet(owner,yubo,coupon,liuguang) VALUES(?,100000,100000,100000) '
                       'ON CONFLICT(owner) DO UPDATE SET yubo=excluded.yubo,coupon=excluded.coupon,liuguang=excluded.liuguang',
                       (self.game.progression.owner,))
        self.open(2001,5657,'specmall')

    def test_all_twenty_medicines_have_native_metadata_and_can_be_bought(self):
        with self.game.progression.transaction() as db:db.execute('UPDATE characters SET gold=100000 WHERE id=?',(self.game.progression.cid,))
        reply=self.open(2034,1271,'open');p=next(p for p in reply if mid(p)==1697)
        r=MessageReader(p[3:]);self.assertEqual((r.integer(),r.integer()),(1271,1))
        rows=[(r.integer(),r.integer()) for _ in range(r.take(1)[0])];r.finish()
        self.assertEqual({k for price,k in rows},set(range(4000,4020)))
        for price,k in rows:self.assertNotEqual(mid(self.send(1716,1271,k,2)[-1]),888)
        state=self.state();self.assertEqual(state['gold'],100000-2*sum(price for price,k in rows))
        self.assertEqual(len(state['orders']),20)
        from native946_progression import Progression
        self.assertEqual(Progression(self.path,'npc_owner',self.game.progression.cid).snapshot()['gold'],state['gold'])

    def test_medicine_does_not_merge_into_locked_or_expired_stack(self):
        self.open(2034,1271,'open')
        with self.game.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=100 WHERE id=?',(self.game.progression.cid,))
            iid=db.execute('SELECT id FROM inventory WHERE character_id=? AND type=4000',(self.game.progression.cid,)).fetchone()[0]
            db.execute("INSERT INTO native_object_locks VALUES(?,'item',?,1,0)",(self.game.progression.cid,iid))
        before=self.state();self.send(1716,1271,4000,2);after=self.state()
        self.assertIn(next(r for r in before['items'] if r[0]==iid),after['items'])
        self.assertEqual(after['gold'],98)

    def test_all_ordinary_merchants_are_catalogued_without_invented_prices(self):
        self.assertEqual(len(DATA['merchants']),24)
        self.assertGreaterEqual(sum(bool(m['goods']) for m in DATA['merchants'].values()),18)
        self.open(2033,1270,'catalog')
        rows=DATA['merchants']['2033:1270']['explicit_items']
        text=''.join(menu(Shops(self.game).ordinary_catalog(1270,page))[0][0][0] for page in range((len(rows)+9)//10))
        self.assertIn('慑妖香',text);self.assertIn('售价待核实',text);self.assertIn('仅限贺卡活动期间',text)
        self.open(2033,1270,'open')
        before=self.state();self.assertEqual(mid(self.send(1716,1270,46998,1)),98);self.assertEqual(self.state(),before)

    def test_review_uses_official_repricing_and_correct_item_ids(self):
        with self.game.progression.transaction() as db:db.execute('UPDATE characters SET gold=10010 WHERE id=?',(self.game.progression.cid,))
        self.open(2033,1270,'open');self.send(1716,1270,6004,2)
        self.assertEqual(self.state()['gold'],10)
        self.assertEqual(self.state()['orders'][-1][7],10000)
        self.assertEqual(DATA['merchants']['2001:3407']['goods']['3249']['price'],300)
        self.assertEqual(DATA['merchants']['2088:3431']['goods']['3227']['price'],20)
        self.assertEqual(DATA['merchants']['2075:3410']['goods']['4023']['price'],4)
        for m in DATA['merchants'].values():
            ids=[r['type'] for r in m['explicit_items']]
            self.assertNotIn(None,ids);self.assertEqual(len(ids),len(set(ids)))
            self.assertNotIn('8037',m['goods']);self.assertNotIn('6213',m['goods'])
        self.assertIn('6011',DATA['merchants']['2033:1270']['goods'])

    def test_review_does_not_open_retired_or_seasonal_stock(self):
        from native946_shops import REVIEW
        self.assertEqual(REVIEW['baseline_rows'],66)
        self.assertEqual(sum(REVIEW['status_counts'].values()),66)
        self.open(2033,1270,'open');before=self.state()
        for kind in (8483,8484,45313,46012,46529,46744,6213,8037):
            self.assertEqual(mid(self.send(1716,1270,kind,1)),98)
        self.assertEqual(before,self.state())
        self.assertFalse(DATA['merchants']['2036:1273']['goods'])

    def test_new_task_purchase_full_bag_rolls_back_price_and_order(self):
        self.open(2001,3407,'open')
        with self.game.progression.transaction() as db:
            cid=self.game.progression.cid
            db.execute('UPDATE characters SET gold=1000 WHERE id=?',(cid,))
            db.execute('DELETE FROM inventory WHERE character_id=?',(cid,))
            for slot in range(1,25):db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,5000,8,?,1)',(cid,slot))
        before=self.state();self.assertEqual(mid(self.send(1716,3407,8078,1)),98);self.assertEqual(before,self.state())

    def test_every_enabled_ordinary_offer_is_wired_to_its_own_npc(self):
        count=0;expected=sum(len(m['goods']) for m in DATA['merchants'].values())
        gold=sum(v['price'] for m in DATA['merchants'].values() for v in m['goods'].values())+100
        for m in DATA['merchants'].values():
            if not m['goods']:continue
            with self.game.progression.transaction() as db:
                db.execute('DELETE FROM inventory WHERE character_id=?',(self.game.progression.cid,))
                db.execute('UPDATE characters SET gold=? WHERE id=?',(gold,self.game.progression.cid))
                db.execute("INSERT OR REPLACE INTO cosmetic_features VALUES(?,'bag_item',3,0)",(self.game.progression.cid,))
                db.execute("INSERT OR REPLACE INTO cosmetic_features VALUES(?,'bag_temp',1,0)",(self.game.progression.cid,))
            packets=self.open(m['scene'],m['npc'],'open')
            p=next(p for p in packets if mid(p)==1697);r=MessageReader(p[3:])
            self.assertEqual((r.integer(),r.integer()),(m['npc'],1))
            rows=[(r.integer(),r.integer()) for _ in range(r.take(1)[0])];r.finish()
            self.assertEqual({k for cost,k in rows},{int(k) for k in m['goods']})
            for cost,k in rows:
                reply=self.send(1716,m['npc'],k,1)
                replies=reply if isinstance(reply,(tuple,list)) else (reply,)
                self.assertNotIn(888,[mid(p) for p in replies],(m['name'],k))
                count+=1
                with self.game.progression.transaction() as db:
                    db.execute('DELETE FROM inventory WHERE character_id=?',(self.game.progression.cid,))
            self.assertEqual(self.state()['gold'],gold-sum(cost for cost,k in rows))
        self.assertEqual(count,expected);self.assertGreaterEqual(count,78)
        self.assertEqual(len(self.state()['orders']),count)

    def test_web_price_sources_do_not_enable_retired_or_ambiguous_variants(self):
        self.assertEqual(DATA['merchants']['2033:1270']['goods']['6015']['price'],500)
        self.assertEqual(DATA['merchants']['2039:1276']['goods']['6109']['price'],8000)
        self.assertEqual(DATA['merchants']['2039:1276']['goods']['6132']['price'],50)
        for m in DATA['merchants'].values():
            for r in m['explicit_items']:
                if r.get('unavailable_reason'):self.assertNotIn(str(r['type']),m['goods'])
        self.open(2033,1270,'open');before=self.state()
        for args in ((1276,6109,1),(1270,45188,1),(1270,6015,-1),(1270,6015,501)):
            self.assertEqual(mid(self.send(1716,*args)),98)
        self.assertEqual(self.state(),before)
        self.game.npc_service_session['expires']=0
        self.assertEqual(mid(self.send(1716,1270,6007,1)),98)

    def test_mall_catalog_pagination_is_wired_and_read_only(self):
        before=self.state();reply=self.open(2001,5657,'shop_catalog');self.assertEqual(mid(reply),1025)
        for i,c in enumerate(DATA['resource_catalogs']):
            for page in range(max(1,(len(c['records'])+5)//6)):
                p=Shops(self.game).catalog_details(5657,i,page)
                self.assertEqual(mid(p),1025);self.assertTrue(menu(p)[1])
        for page in range((len(DATA['merchants'])+len(DATA['mall_npcs'])+5)//6):self.assertEqual(mid(Shops(self.game).npc_catalog(5657,page)),1025)
        self.assertEqual(mid(self.open(2001,5657,'shop_npcs')),1025)
        self.assertEqual(before,self.state())

    def test_legacy_liuguang_migrates_both_ledgers_by_owner_once(self):
        from native946_progression import Progression
        p = self.game.progression
        sibling = self.characters.create(p.owner, '同账号流光', 113)
        with p.transaction() as db:
            db.execute('ALTER TABLE mall_account_wallet DROP COLUMN liuguang')
            db.execute('INSERT INTO mall_account_wallet(owner,yubo,coupon) VALUES(?,955,8)', (p.owner,))
            db.execute("INSERT INTO accounts(username,salt,password_hash) VALUES('credit_other',X'01',X'01')")
            other = db.execute(
                'INSERT INTO characters(owner,name,role_id,race,sex,created_at,map_id,x,y) '
                "VALUES('credit_other','另一账号流光',702,1,2,0,2001,1,1)").lastrowid
            db.executemany('INSERT INTO native_shop_balances VALUES(?,?)', [
                (p.cid, 2050), (sibling['id'], 300), (other, 900),
            ])
            db.executemany("INSERT INTO cosmetic_features VALUES(?,'point',?,0)", [
                (p.cid, 100), (sibling['id'], 50), (other, 20),
            ])
            db.execute("INSERT INTO cosmetic_features VALUES(?,'test_style',42,0)", (p.cid,))
        cold = Progression(self.path, p.owner, p.cid)
        self.assertEqual(cold.attribute_values()['TimeCredit'], 2500)
        with p.transaction() as db:
            migrate_liuguang(db)
            self.assertEqual(liuguang_balance(db, sibling['id']), 2500)
            self.assertEqual(liuguang_balance(db, other), 920)
            self.assertEqual(tuple(db.execute(
                'SELECT yubo,coupon,liuguang FROM mall_account_wallet WHERE owner=?', (p.owner,)).fetchone()),
                (955, 8, 2500))
            self.assertEqual(db.execute('SELECT sum(liuguang) FROM native_shop_balances').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT key,value FROM cosmetic_features WHERE character_id=?',
                                        (p.cid,)).fetchone()[:], ('test_style',42))

    def test_same_account_item_credit_and_competing_special_purchases(self):
        from native946_cosmetics import Cosmetics
        from native946_game_login import NativeGameLogin,message_id
        from native946_sessions import LEASES

        self.enable_special()
        first = self.game
        p = first.progression
        sibling = self.characters.create(p.owner, '特卖同账号', 113)
        login = NativeGameLogin(555, None, number, None, self.characters)
        login.username = p.owner
        self.addCleanup(LEASES.release_port, self.path, 555)
        login.receive(message_id(915) + number(sibling['id']))
        second = login.gameplay
        offer_price = DATA['specmall']['item_list'][0]['Promote']
        with p.transaction() as db:
            db.execute('UPDATE characters SET level=140 WHERE id=?', (sibling['id'],))
            db.execute('UPDATE cultivation_state SET rebirth=2,ascension=1 WHERE character_id=?', (sibling['id'],))
            db.execute('UPDATE mall_account_wallet SET liuguang=0 WHERE owner=?', (p.owner,))
            voucher = Cosmetics(first)._add(db, 55634, backpack=True)
        self.assertIn(815, [mid(reply) for reply in self.send(807, 0, voucher)])
        self.assertEqual(second.progression.attribute_values()['TimeCredit'], 50)
        with p.transaction() as db:
            db.execute('UPDATE mall_account_wallet SET liuguang=? WHERE owner=?', (offer_price, p.owner))
        self.game = second
        opened = self.open(2001, 5657, 'specmall')
        panel = MessageReader(next(reply for reply in opened if mid(reply) == 3700)[3:])
        self.assertEqual((panel.integer(), panel.integer()), (100000, offer_price))
        self.game = first

        def request(game, message, *args):
            return game.receive(message, MessageReader(b''.join(number(arg) for arg in args)))

        for peer in (first, second):
            self.assertEqual(mid(request(peer, 3703, 1)), 3701)
        ready = Barrier(2)

        def checkout(peer):
            ready.wait(timeout=3)
            reply = request(peer, 3702, 1, 1)
            return mid(reply) if isinstance(reply, bytes) else next(mid(r) for r in reply if mid(r) == 3709)

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(checkout, (first, second)), [3709, 98])
        self.assertEqual(first.progression.attribute_values()['TimeCredit'], 0)
        self.assertEqual(second.progression.attribute_values()['TimeCredit'], 0)
        with p.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM native_shop_orders WHERE currency='liuguang'").fetchone()[0], 1)

    def test_special_open_and_request_contracts_reject_missing_session(self):
        before=self.state()
        self.assertEqual(mid(self.send(3703,1)),98);self.assertEqual(mid(self.send(3702,1,1)),98)
        self.assertEqual(before,self.state())
        reply=self.open(2001,5657,'specmall');p=next(p for p in reply if mid(p)==3700)
        r=MessageReader(p[3:]);self.assertEqual((r.integer(),r.integer()),(0,0));self.assertTrue(r.string().startswith(b'{'));r.finish()

    def test_special_grade_server_age_and_rotation_are_enforced(self):
        self.open(2001,5657,'specmall');before=self.state()
        self.assertEqual(mid(self.send(3703,1)),98)
        self.enable_special()
        with patch.dict(DATA['server'],opened_at=None):self.assertEqual(mid(self.send(3703,1)),3701)
        with patch.dict(DATA['server'],opened_at=None,ignore_open_delay=False):self.assertEqual(mid(self.send(3703,1)),98)
        import time
        with patch.dict(DATA['server'],opened_at=int(time.time()),ignore_open_delay=False):self.assertEqual(mid(self.send(3703,1)),98)
        index=next(i for i,r in enumerate(DATA['specmall']['item_list'],1) if r.get('PromoteWeek',[0])[0]>0 and not on_shelf(r,time.time()))
        self.assertEqual(mid(self.send(3703,index)),98);self.assertEqual(before['orders'],self.state()['orders'])

    def test_special_paid_order_counter_replay_and_cold_reload(self):
        self.enable_special();row=DATA['specmall']['item_list'][0]
        reply=self.send(3703,1);self.assertEqual(mid(reply),3701)
        r=MessageReader(reply[3:]);self.assertEqual([r.integer() for _ in range(5)],[1,1,2,100000,row['Promote']]);r.finish()
        packets=self.send(3702,1,1);self.assertIn(3709,[mid(p) for p in packets])
        after=self.state();self.assertEqual(len(after['orders']),1)
        self.assertEqual(mid(self.send(3702,1,1)),98);self.assertEqual(after,self.state())
        self.open(2001,5657,'specmall');self.assertEqual(mid(self.send(3703,1)),98)
        with self.game.progression.transaction(write=False) as db:
            self.assertEqual(Shops(self.game).wallet(db),(100000,100000-row['Promote']))
        from native946_progression import Progression
        cold=Progression(self.path,'npc_owner',self.game.progression.cid)
        with cold.transaction(write=False) as db:self.assertEqual(db.execute('SELECT count(*) FROM native_shop_orders').fetchone()[0],1)

    def test_classified_catalog_native_entry_search_and_purchase(self):
        from native946_classified_mall import CATEGORIES, ClassifiedMall
        from native946_progression import Progression

        p = self.game.progression
        with p.transaction() as db:
            db.execute('INSERT INTO mall_account_wallet(owner,yubo) VALUES(?,450)', (p.owner,))
        store = ClassifiedMall(self.game)
        self.assertEqual(mid(self.send(3414, 55228, 1)), 98)
        self.assertEqual([mid(reply) for reply in self.send(1390, 83)], [3404, 3405])
        self.assertEqual([mid(reply) for reply in self.send(3409)], [3404, 3405])
        opened = self.open(2001, 5657, 'classified_mall')
        self.assertEqual([mid(reply) for reply in opened], [1183, 3404, 3405])
        panel = MessageReader(opened[1][3:])
        self.assertEqual([panel.integer() for _ in range(4)], [700, 100, 450, 0])
        self.assertTrue(panel.string())
        panel.finish()

        for category, kinds in CATEGORIES.items():
            response = self.send(3411, category)
            reader = MessageReader(response[3:])
            self.assertEqual((mid(response), reader.integer()), (3416, category))
            rows = [[reader.integer() for _ in range(5)] for _ in range(reader.take(1)[0])]
            self.assertEqual([row[0] for row in rows], list(kinds))
            self.assertTrue(all(row[1] >= row[3] > 0 and row[4] == category for row in rows))
            reader.finish()
        result = self.game.receive(3412, MessageReader(string_field('神速')))
        reader = MessageReader(result[3:])
        self.assertEqual((mid(result), reader.take(1)[0]), (3417, 1))
        self.assertEqual([reader.integer() for _ in range(5)], [55228, 200, 1, 200, 700])
        reader.finish()
        self.assertEqual(mid(self.send(3407, 47564)), 98)
        quote = MessageReader(self.send(3407, 55228)[3:])
        self.assertEqual([quote.integer(), quote.integer()], [55228, 200])
        quote.finish()
        self.assertEqual(mid(self.send(3414, 55228, 3)), 98)
        self.assertIn(3415, [mid(reply) for reply in self.send(3414, 55228, 1)])
        self.assertEqual(mid(self.send(3414, 55228, 1)), 98)

        for kind in (55580, 55358):
            self.assertEqual(mid(self.send(3406, kind)), 3405)
        self.assertEqual(mid(self.send(3413)), 98)
        quote = MessageReader(self.send(3408)[3:])
        self.assertEqual([quote.integer(), quote.integer()], [2, 250])
        quote.finish()
        self.send(3410, 2)
        self.assertEqual(mid(self.send(3413)), 98)
        self.send(3406, 55358)
        self.send(3408)
        result = self.send(3413)
        self.assertIn(3415, [mid(reply) for reply in result])
        self.assertEqual(mid(self.send(3413)), 98)
        cold = Progression(self.path, p.owner, p.cid)
        with cold.transaction(write=False) as db:
            self.assertEqual(store.cosmetics.wallet(db), (0, 0))
            self.assertEqual(db.execute('SELECT count(*) FROM cosmetic_orders').fetchone()[0], 2)
            records = db.execute('SELECT i.type,e.expires FROM inventory i JOIN cosmetic_expiry e ON e.item_id=i.id '
                                 'WHERE i.type IN (55228,55580,55358) ORDER BY i.type').fetchall()
            self.assertEqual([row['type'] for row in records], [55228, 55358, 55580])
            self.assertTrue(all(row['expires'] for row in records))

    def test_classified_catalog_refresh_removes_stale_cart_item_before_checkout(self):
        p = self.game.progression
        with p.transaction() as db:
            db.execute('INSERT INTO mall_account_wallet(owner,yubo) VALUES(?,1000)', (p.owner,))
        self.send(1390, 83)
        self.send(3406, 55228)
        self.send(3408)
        self.game.classified_mall_cart = (55413, 55228)

        refreshed = self.send(3413)
        self.assertEqual([mid(reply) for reply in refreshed], [3405, 98])
        cart = MessageReader(refreshed[0][3:])
        self.assertEqual(cart.take(1)[0], 1)
        self.assertEqual([cart.integer(), cart.integer()], [55228, 200])
        cart.finish()
        self.assertEqual(mid(self.send(3413)), 98)
        with p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT yubo FROM mall_account_wallet WHERE owner=?',
                                        (p.owner,)).fetchone()[0], 1000)
            self.assertEqual(db.execute('SELECT count(*) FROM cosmetic_orders').fetchone()[0], 0)

    def test_special_wrong_quantity_distance_battle_and_balance_do_not_charge(self):
        self.enable_special();before=self.state()
        self.send(3703,1)
        for amount in (-1,0,2,501):self.assertEqual(mid(self.send(3702,1,amount)),98)
        self.game.character.update(x=0,y=0);self.assertEqual(mid(self.send(3702,1,1)),98)
        self.approach(2001,5657);self.game.battle={'id':'fixture'}
        self.assertEqual(mid(self.send(3702,1,1)),98);self.game.battle=None
        with self.game.progression.transaction() as db:db.execute('UPDATE mall_account_wallet SET liuguang=0 WHERE owner=?',(self.game.progression.owner,))
        self.assertEqual(mid(self.send(3702,1,1)),98);self.assertEqual(before,self.state())

    def test_full_bag_special_purchase_rolls_back_currency_and_receipt(self):
        self.enable_special();self.send(3703,1)
        with self.game.progression.transaction() as db:
            occupied={r[0] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8',(self.game.progression.cid,))}
            for s in range(1,25):
                if s not in occupied:db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4001,8,?,500)',(self.game.progression.cid,s))
        before=self.state()
        with self.game.progression.transaction(write=False) as db:balance=Shops(self.game).wallet(db)
        self.assertEqual(mid(self.send(3702,1,1)),98);self.assertEqual(before,self.state())
        with self.game.progression.transaction(write=False) as db:self.assertEqual(Shops(self.game).wallet(db),balance)

    def test_timezone_period_boundaries_and_native_four_week_rotation(self):
        monday=datetime(2026,11,23,tzinfo=TZ)
        self.assertEqual(period({'PeriodSaleRefresh':7},monday.timestamp()),'week:2026-11-23')
        self.assertEqual(period({'PeriodSaleRefresh':7},monday.timestamp()-1),'week:2026-11-16')
        self.assertEqual(period({'PeriodSaleRefresh':1},monday.timestamp()-1),'day:2026-11-22')
        self.assertEqual([cycle((monday+timedelta(weeks=i)).timestamp()) for i in range(5)],[4,1,2,3,4])


if __name__=='__main__':unittest.main()
