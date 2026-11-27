"""Native town menus and committed NPC services, using disposable saves."""
import struct
import unittest
from unittest.mock import patch

from native946_codec import SCHEMAS,field,number,packet
from native946_login import MessageReader
from native946_npc_services import NpcServices,STORAGE_PAGE,service_kind
from native946_progression import Progression
from native946_trade import MAX_CASH
from native946_world import world
import test_native946_npcs as fixtures


def mid(p):return int.from_bytes(p[1:3],'little') if p[0]==0 else p[0]


def menu(p):
    r=MessageReader(p[3:]);chat=[]
    for _ in range(r.take(1)[0]):chat.append((r.string().decode('gb18030'),r.integer()))
    options=[r.string().decode('gb18030') for _ in range(r.take(1)[0])]
    session,close=r.integer(),r.integer();r.finish()
    return chat,options,session,close


def item_fields(payload):
    reader=MessageReader(payload[3:])
    reader.take(1)  # can_trade
    result,_,iid,page,slot=(reader.integer() for _ in range(5))
    return result,iid,page,slot


def nice_id_list(payload):
    assert mid(payload) == 1857
    reader = MessageReader(payload[3:])
    tab, theme = reader.integer(), reader.integer()
    rows = []
    for _ in range(reader.take(1)[0]):
        original, showid, text = reader.integer(), reader.integer(), reader.string().decode('gb18030')
        end_day, current = reader.integer(), reader.integer()
        rows.append((showid, text, current, original, end_day))
    page, total, renew = reader.integer(), reader.integer(), reader.take(1)[0]
    reader.finish()
    return tab, theme, rows, page, total, renew


class NpcServiceTests(unittest.TestCase):
    setUp=fixtures.NpcGameplayTests.setUp
    approach=fixtures.NpcGameplayTests.approach

    def send(self,kind,*args):return self.game.receive(kind,MessageReader(b''.join(number(a) for a in args)))

    def choose(self,nid,choice):return self.game.receive(1032,MessageReader(bytes([choice])+number(nid)))

    def open(self,sid,nid,choice):
        self.approach(sid,nid);self.send(1034,nid);return self.choose(nid,choice)

    def money(self,value):
        with self.game.progression.transaction() as db:db.execute('UPDATE characters SET gold=? WHERE id=?',(value,self.game.progression.cid))

    def rows(self,page=None):
        with self.game.progression.transaction(write=False) as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM inventory WHERE character_id=? ORDER BY id',(self.game.progression.cid,))]
        return rows if page is None else [r for r in rows if r['page']==page]

    def test_transport_menu_uses_native_aliases_and_one_close_flag(self):
        self.approach(2001,4002);p=self.send(1034,4002)
        chat,options,session,close=menu(p)
        self.assertEqual(options,['五指山','傲来海滩','花果山','西市','宫城','乌斯藏'])
        self.assertNotIn('#R',str(chat));self.assertNotIn('（',str(options))
        self.assertEqual((session,close),(4002,1))
        self.assertEqual(self.game.dialog_actions,[1,2,3,4,5,6])

    def test_native_negative_one_cancel_closes_without_transport_or_option(self):
        self.approach(2001,4002);self.send(1034,4002);before=dict(self.game.character)
        self.assertEqual(mid(self.choose(4002,255)),1183)
        self.assertFalse(self.game.dialog_open);self.assertEqual(self.game.dialog_actions,[])
        self.assertEqual(self.game.character,before);self.assertIsNone(self.choose(4002,1))

    def test_stale_cancel_and_invalid_zero_cannot_select_first_option(self):
        self.approach(2001,4002);self.send(1034,4002)
        self.assertIsNone(self.choose(999,255));self.assertTrue(self.game.dialog_open)
        self.assertEqual(mid(self.choose(4002,0)),98)
        self.assertEqual(self.game.character['map_id'],2001)

    def test_every_declared_service_kind_has_a_real_menu(self):
        counts={}
        for sid,rows in world().npcs.items():
            if sid not in world().scenes:continue
            for nid,npc in rows.items():
                kind=service_kind(npc)
                if kind:
                    counts[kind]=counts.get(kind,0)+1
                    self.approach(sid,nid)
                    _,options,session,close=menu(self.send(1034,nid))
                    self.assertTrue(options,(sid,nid,kind));self.assertEqual((session,close),(nid,1))
        self.assertEqual(set(counts),{'bank','pawnshop','medical','storage','medicine','merchant','gear_refinement',
                                      'gear_bench','workshop','task_rules','cosmetic','shengzhu','school','nice_id'})

    def test_niceid_record_matches_original_table(self):
        from pathlib import Path
        import sys
        root = Path(__file__).parent
        sys.path.insert(0,str(root/'analysis/town-portals-20261125'))
        from client_archive import Archive
        from literal_schema import replay

        native = replay(Archive(root/'client-946/bin_script.wdf').read('common/protocol/tbl/niceid.tbl'))
        self.assertEqual(SCHEMAS['niceid'],[[key,value] for key,value in native[1].items()])

    def test_zeng_zhi_native_number_browse_trade_activate_and_renew(self):
        from native946_cosmetics import Cosmetics
        from native946_nice_ids import calendar_shift
        self.approach(2001, 4775)
        self.assertEqual(mid(self.send(1863, 1, 0, 1)), 98)
        dialog = self.send(1034, 4775)
        option = menu(dialog)[1].index('我要极品靓号') + 1
        replies = self.choose(4775, option)
        self.assertEqual([mid(reply) for reply in replies], [1183, 1857, 98])
        tab, theme, rows, page, total, renew = nice_id_list(replies[1])
        self.assertEqual((tab, theme, page, total, renew), (1, 0, 1, 2, 0))
        self.assertEqual((len(rows), rows[0]), (9, (24582, '24582', 16, 16, 0)))
        self.assertEqual(len(nice_id_list(self.send(1863, 1, 0, 2))[2]), 2)
        self.assertEqual(nice_id_list(self.send(1863, 2, 0, 1))[2], [(69828, '69828', 8, 16, 0)])
        self.assertEqual(len(nice_id_list(self.send(1863, 1, 3, 1))[2]), 4)
        match = self.game.receive(1865, MessageReader(field('string', '8502')))
        self.assertEqual(nice_id_list(match)[2], [(19850205, '19850205', 12, 12, 0)])

        before = self.game.progression.snapshot()
        self.assertEqual(mid(self.send(1859, 24582)), 1856)
        self.assertEqual(mid(self.send(1861, 24582)), 98)
        self.assertEqual(mid(self.send(1862)), 98)
        self.assertEqual(mid(self.send(1864)), 98)
        shop = self.send(1866, 0)
        reply = next(packet for packet in shop if mid(packet) == 3658)
        reader = MessageReader(reply[3:])
        reader.string()
        for _ in range(6):
            reader.integer()
        reader.string()
        self.assertEqual(reader.integer(), 55219)
        reader.finish()
        self.assertEqual(mid(self.send(1866, 1)), 98)
        self.assertEqual(self.game.progression.snapshot(), before)
        for row in rows:
            self.assertEqual(mid(self.send(1860, 1, row[0])), 98)
        self.assertEqual(mid(self.send(1860, 1, rows[0][0])), 98)
        self.assertEqual(mid(self.send(1860, 1, 69828)), 98)
        self.assertEqual(len(nice_id_list(self.send(1863, 3, 0, 1))[2]), 9)
        self.assertEqual(len(nice_id_list(self.send(1860, 2, rows[0][0]))[2]), 8)

        p=self.game.progression
        with p.transaction() as db:
            Cosmetics(self.game)._add(db,45844,16)
            Cosmetics(self.game)._add(db,55197)
        self.send(1859,24582)
        self.assertEqual(mid(self.send(1861,82444)),98)
        self.assertIn(1857,[mid(r) for r in self.send(1861,24582)])
        cert=next(r for r in self.rows() if r['type']==45805)
        self.assertEqual(mid(self.send(1861,24582)),98)
        self.assertEqual(self.game.character.get('showid',p.cid),p.cid)
        result=self.send(807,0,cert['id'])
        self.assertIn(695,[mid(r) for r in result])
        self.assertEqual(self.game.character['showid'],24582)
        self.assertEqual(self.game.character['id'],p.cid)
        self.assertEqual(self.game.characters.list('npc_owner')[0]['showid'],24582)
        self.assertEqual(mid(self.send(1864)),98)
        expiry=self.game.character['showid_expires']
        with p.transaction() as db:Cosmetics(self.game)._add(db,45844,9)
        with patch('native946_nice_ids.time',return_value=expiry-60):
            self.assertEqual(mid(self.send(1864)),1858)
            self.assertIn(1857,[mid(r) for r in self.send(1862)])
            self.assertEqual(self.game.character['showid_expires'],calendar_shift(expiry,12))
        self.assertFalse(any(r['type'] in (45844,55197) for r in self.rows()))
        self.approach(2048, 1280)
        self.assertEqual(mid(self.send(1860, 1, 69828)), 98)

    def assert_bank_packets(self, replies, cash, savings, operation=None):
        expected=[1183,695,695,694,1165] if operation is not None else [695,695,694,98]
        self.assertEqual([mid(p) for p in replies],expected)
        for p, pair in zip((p for p in replies if mid(p)==695), [(34,cash),(35,savings)]):
            r=MessageReader(p[3:])
            self.assertEqual((r.integer(),r.integer()),pair)
            r.finish()
        if operation is not None:
            r=MessageReader(replies[-1][3:])
            self.assertEqual(r.integer(),operation)
            r.finish()

    def bank_unlock(self, *codes):
        self.assertEqual(mid(self.send(884)),1023)
        payload=packet(1033,[('array','string'),'number'],[codes,self.game.inventory_lock_session['id']])
        return self.game.receive(1033,MessageReader(payload[3:]))

    def test_bank_native_panel_balance_conservation_and_one_shot(self):
        self.money(100)
        self.approach(2048,1280)
        chat,options,nid,_=menu(self.send(1034,1280))
        self.assertEqual((nid,options),(1280,['我想存钱','我想取钱']))
        self.assertIn('高级操作',chat[0][0])
        self.assert_bank_packets(self.choose(1280,1),100,0,1)
        self.assert_bank_packets(self.send(1350,1,60),40,60)
        s=self.game.progression.snapshot()
        self.assertEqual((s['gold'],s['bank_balance']),(40,60))
        self.assertEqual(self.game.progression.attribute_values()['Account'],60)
        self.assertEqual(mid(self.send(1350,1,60)),98)
        self.assertEqual([mid(p) for p in self.open(2048,1280,2)],[1183,98])
        self.assertEqual(mid(self.send(1350,2,25)),98)
        self.bank_unlock('123456','123456')
        self.assert_bank_packets(self.open(2458,8221,2),40,60,2)
        self.assert_bank_packets(self.send(1350,2,25),65,35)
        self.assertEqual(mid(self.send(1350,2,25)),98)
        s=Progression(self.path,'npc_owner',s['id']).snapshot()
        self.assertEqual((s['gold'],s['bank_balance']),(65,35))

    def test_bank_wrong_operation_overdraft_expiry_distance_and_battle_do_not_write(self):
        self.money(100);self.open(2048,1280,1)
        for op,amount in [(2,1),(1,-1),(1,0),(1,101)]:self.assertEqual(mid(self.send(1350,op,amount)),98)
        self.assertIsNone(self.game.npc_service_session)
        self.open(2048,1280,1)
        self.game.npc_service_session['expires']=0
        self.assertEqual(mid(self.send(1350,1,1)),98)
        self.open(2048,1280,1);self.game.character.update(x=0,y=0)
        self.assertEqual(mid(self.send(1350,1,1)),98)
        self.open(2048,1280,1);self.game.battle={'phase':'input'}
        self.assertEqual(mid(self.send(1350,1,1)),98)
        s=self.game.progression.snapshot();self.assertEqual((s['gold'],s['bank_balance']),(100,0))

    def test_bank_rechecks_advanced_operation_at_submit_and_after_expiry(self):
        self.money(100);self.open(2048,1280,1);self.send(1350,1,60)
        self.bank_unlock('123456','123456')
        self.assert_bank_packets(self.open(2048,1280,2),40,60,2)
        self.game._inventory_advanced_until=0
        self.assertEqual(mid(self.send(1350,2,25)),98)
        self.assertEqual([mid(p) for p in self.open(2048,1280,2)],[1183,98])
        self.assertEqual(mid(self.bank_unlock('654321')),98)
        self.assertEqual([mid(p) for p in self.open(2048,1280,2)],[1183,98])
        self.bank_unlock('123456')
        self.assert_bank_packets(self.open(2048,1280,2),40,60,2)
        self.assertEqual(mid(self.send(1350,2,61)),98)
        self.assert_bank_packets(self.open(2048,1280,2),40,60,2)
        self.assert_bank_packets(self.send(1350,2,60),100,0)

    def test_bank_high_balance_can_move_up_to_wire_limit_and_rejects_overflow(self):
        self.money(MAX_CASH)
        self.open(2458,8220,1)
        self.assert_bank_packets(self.send(1350,1,MAX_CASH),0,MAX_CASH)
        self.money(1);self.open(2458,8220,1)
        self.assertEqual(mid(self.send(1350,1,1)),98)
        self.assert_bank_packets(self.open(2458,8220,1),1,MAX_CASH,1)

    def test_cancel_or_another_npc_does_not_leave_a_replayable_option(self):
        self.approach(2048,1280);self.send(1034,1280);self.choose(1280,255)
        self.assertIsNone(self.choose(1280,1))
        self.open(2048,1280,1);self.send(1034,1280)
        self.assertEqual(mid(self.send(1350,1,1)),98)

    def test_medicine_native_schema_currency_and_durable_purchase(self):
        self.money(100);replies=self.open(2034,1271,1);p=next(p for p in replies if mid(p)==1697)
        r=MessageReader(p[3:]);self.assertEqual((r.integer(),r.integer()),(1271,1))
        self.assertEqual(SCHEMAS['seller_item'],[['price','number'],['type','number']])
        offers=[(r.integer(),r.integer()) for _ in range(r.take(1)[0])]
        self.assertEqual(offers[:6],[(1,4000),(1,4001),(4,4002),(4,4003),(8,4004),(8,4005)])
        self.assertEqual(len(offers),20)
        r.finish()
        self.send(1716,1271,4000,3);s=self.game.progression.snapshot()
        self.assertEqual(s['gold'],97);self.assertEqual(sum(i['amount'] for i in s['items'] if i['type']==4000),8)
        self.assertEqual(Progression(self.path,'npc_owner',s['id']).snapshot()['gold'],97)
        for _, kind in offers[2:6]:
            self.send(1716,1271,kind,1)
        s=self.game.progression.snapshot()
        self.assertEqual(s['gold'],73)
        self.assertEqual({i['type']:i['amount'] for i in s['items'] if 4002<=i['type']<=4005},
                         {kind:1 for kind in range(4002,4006)})
        hp_item=next(i for i in s['items'] if i['type']==4002)
        self.game.progression.set_vitals(1,s['mp'])
        self.send(807,s['id'],hp_item['id'])
        s=self.game.progression.snapshot()
        self.assertEqual(s['hp'],min(s['hpmax'],501))
        self.assertFalse(any(i['id']==hp_item['id'] for i in s['items']))

    def test_shop_rejects_spoofed_npc_items_amount_and_remote_purchase(self):
        self.money(100);self.open(2034,1271,1);before=self.rows()
        for args in [(1280,4000,1),(1271,6052,1),(1271,4000,501),(1271,4000,-1)]:
            self.assertEqual(mid(self.send(1716,*args)),98)
        self.game.character.update(x=0,y=0);self.assertEqual(mid(self.send(1716,1271,4000,1)),98)
        self.assertEqual(self.rows(),before);self.assertEqual(self.game.progression.snapshot()['gold'],100)

    def test_partial_stack_changes_roll_back_if_purchase_needs_a_full_bag(self):
        self.money(100);self.open(2034,1271,1)
        with self.game.progression.transaction() as db:
            db.execute('UPDATE inventory SET amount=499 WHERE character_id=? AND type=4000',(self.game.progression.cid,))
            for slot in range(6,25):db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4001,8,?,500)',(self.game.progression.cid,slot))
        before=self.rows();self.assertEqual(mid(self.send(1716,1271,4000,2)),98)
        self.assertEqual(self.rows(),before);self.assertEqual(self.game.progression.snapshot()['gold'],100)

    def test_medical_restores_hero_and_owned_guardian(self):
        npc=next(n for rows in world().npcs.values() for n in rows.values() if n['npc_type']==6 and n['scene_id'] in world().scenes)
        sid=self.game.summons.claim_starter();self.game.summons.set_vitals(sid,1,0);self.game.progression.set_vitals(1,0)
        self.open(npc['scene_id'],npc['npc_id'],3)
        s=self.game.progression.snapshot();pet=self.game.summons.get(sid)
        self.assertEqual((s['hp'],s['mp']),(s['hpmax'],s['mpmax']))
        self.assertEqual((pet['hp'],pet['mp']),(pet['hpmax'],pet['mpmax']))

    def test_storage_preserves_item_id_and_hides_it_from_native_bag(self):
        self.approach(2001,3439);item=self.rows(8)[0];self.game.progression.item_packets()
        replies=self.open(2001,3439,1);self.assertIn(839,[mid(p) for p in replies])
        replies=self.send(842,item['id'],3439,0,150,-1)
        self.assertEqual([mid(p) for p in replies],[780,786])
        self.assertIn((item['type'],item['id'],150,1),[item_fields(p) for p in replies if mid(p)==786])
        self.assertEqual(self.rows(STORAGE_PAGE)[0]['id'],item['id'])
        p=Progression(self.path,'npc_owner',self.game.progression.cid)
        self.assertNotIn(item['id'],[i['id'] for i in p.snapshot()['items']])
        self.assertIn((item['type'],item['id'],150,1),[item_fields(msg) for msg in p.item_packets() if mid(msg)==786])
        with self.assertRaises(ValueError):p.move_item(item['id'],8,24)
        replies=self.send(842,item['id'],3439,1,8,-1)
        self.assertEqual([mid(msg) for msg in replies],[780,786])
        self.assertIn((item['type'],item['id'],8,item['slot']),[item_fields(msg) for msg in replies if mid(msg)==786])
        self.assertEqual(self.rows(STORAGE_PAGE),[]);self.assertIn(item['id'],[i['id'] for i in self.rows(8)])

    def test_storage_full_bag_and_legacy_neidan_metadata_are_preserved(self):
        from native946_neidan_legacy import LegacyNeidan
        self.approach(2001,3439);LegacyNeidan(self.game.summons).grant_test_kit()
        p=self.game.progression;self.open(2001,3439,1)
        with p.transaction(write=False) as db:
            pill=dict(db.execute('SELECT n.* FROM legacy_neidan_items n JOIN inventory i ON i.id=n.item_id WHERE i.character_id=? LIMIT 1',(p.cid,)).fetchone())
        self.send(842,pill['item_id'],3439,0,150,-1)
        with p.transaction() as db:
            occupied={r['slot'] for r in self.rows(8)}
            for slot in range(1,25):
                if slot not in occupied:db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4001,8,?,1)',(p.cid,slot))
        before=self.rows()
        self.assertEqual(mid(self.send(842,pill['item_id'],3439,1,8,-1)),98)
        self.assertEqual(self.rows(),before)
        with p.transaction(write=False) as db:self.assertEqual(dict(db.execute('SELECT * FROM legacy_neidan_items WHERE item_id=?',(pill['item_id'],)).fetchone()),pill)

    def test_bank_commit_failure_restores_session_and_money(self):
        from native946_storage import StorageBusy
        self.money(100);self.open(2048,1280,1);session=dict(self.game.npc_service_session)
        # Fail after the business mutation but before native replies can leave.
        original=NpcServices.bank
        def fail(svc,*args):original(svc,*args);raise ValueError('rollback fixture')
        with patch.object(NpcServices,'bank',fail):self.assertEqual(mid(self.send(1350,1,20)),98)
        self.assertEqual(self.game.npc_service_session,session)
        s=self.game.progression.snapshot();self.assertEqual((s['gold'],s['bank_balance']),(100,0))

    def test_pawnshop_native_menu_and_unpriced_pawn_does_not_move_assets(self):
        self.approach(2047,1279)
        _,options,nid,_=menu(self.send(1034,1279))
        self.assertEqual((nid,options),(1279,['典当物品','赎回物品']))
        before=self.rows()
        with patch('native946_pawnshop.cash_prices',return_value={}):
            self.assertEqual(mid(self.choose(nid,1)[-1]),840)
        self.assertEqual(mid(self.send(843,before[0]['id'],1)),98)
        self.assertEqual(self.rows(),before)
        r=MessageReader(self.open(2047,1279,2)[-1][3:])
        self.assertEqual((r.take(1)[0],r.string()),(0,b'{}'))
        r.finish()

    def test_pawnshop_partial_pawn_and_redeem_preserve_original_prices_and_quantities(self):
        from native946_pawnshop import PAGE
        source=next(row for row in self.rows() if row['type']==4000)
        self.money(50)
        with patch('native946_pawnshop.cash_prices',return_value={4000:14}):
            self.assertEqual(mid(self.open(2047,1279,1)[-1]),840)
            self.assertEqual(mid(self.send(843,source['id'],source['amount']+1)),98)
            prompt=self.send(843,source['id'],2)
            self.assertEqual(mid(prompt),1020)
            r=MessageReader(prompt[3:])
            for _ in range(4):r.string()
            token=r.integer();r.finish()
            self.assertEqual(self.game.progression.snapshot()['gold'],50)
            payload=packet(1031,['byte','number'],[1,token])
            reply=self.game.receive(1031,MessageReader(payload[3:]))
            self.assertEqual(mid(reply[-1]),98)
            self.assertEqual(mid(self.send(843,source['id'],2)),98)
        self.assertEqual(self.game.progression.snapshot()['gold'],64)
        held,=self.rows(PAGE)
        self.assertEqual(held['amount'],2)
        self.assertEqual(mid(self.send(806,held['id'],0)),98)
        r=MessageReader(self.open(2047,1279,2)[-1][3:])
        self.assertEqual(r.take(1)[0],1)
        self.assertEqual([r.integer() for _ in range(4)],[held['id'],4000,2,14])
        self.assertEqual(r.string(),b'{}');r.finish()
        self.assertEqual(mid(self.send(806,held['id'],0)),791)
        self.send(844,held['id'],1)
        self.assertEqual(mid(self.send(844,held['id'],1)),98)
        self.assertEqual((self.game.progression.snapshot()['gold'],self.rows(PAGE)[0]['amount']),(50,1))
        self.open(2047,1279,2);self.send(844,held['id'],1)
        self.assertEqual((self.game.progression.snapshot()['gold'],self.rows(PAGE)),(36,[]))
        self.assertEqual(sum(row['amount'] for row in self.rows() if row['type']==4000),source['amount'])

    def test_pawnshop_live_catalog_rounding_and_confirm_cancel(self):
        from native946_pawnshop import cash_prices, pawn_cash, PAGE
        prices=cash_prices()
        self.assertEqual([prices[k] for k in (4000,4002,6007,6015)],[1,4,5,500])
        self.assertEqual([pawn_cash(1,count) for count in (1,2,3)],[0,1,1])
        source=next(row for row in self.rows() if row['type']==4000)
        self.money(50);self.open(2047,1279,1)
        self.assertEqual(mid(self.send(843,source['id'],1)),1020)
        session=self.game.npc_service_session
        token=session['pending'][0]
        self.game.receive(1031,MessageReader(packet(1031,['byte','number'],[2,token])[3:]))
        self.assertEqual(self.rows(PAGE),[])
        self.assertEqual(mid(self.send(843,source['id'],1)),1020)
        new=self.game.npc_service_session['pending'][0]
        self.assertNotEqual(new,token)
        pending=packet(1031,['byte','number'],[1,new])[3:]
        self.game.receive(1031,MessageReader(pending))
        self.assertEqual(mid(self.game.receive(1031,MessageReader(pending))),98)
        self.assertEqual((self.game.progression.snapshot()['gold'],self.rows(PAGE)[0]['amount']),(50,1))

    def test_pawnshop_redeem_with_no_money_or_room_keeps_ticket_and_item(self):
        from native946_pawnshop import PAGE
        with self.game.progression.transaction() as db:
            cid=self.game.progression.cid
            iid=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',
                           (cid,4000,PAGE,1)).lastrowid
            db.execute('INSERT INTO native_pawn_tickets VALUES(?,?)',(iid,14))
        before=self.rows(PAGE)
        self.money(13);self.open(2047,1279,2)
        self.assertEqual(mid(self.send(844,iid,1)),98)
        self.money(14)
        with patch.object(self.game.progression,'free_bag_slot',side_effect=ValueError('fixture full bag')):
            self.assertEqual(mid(self.send(844,iid,1)),98)
        self.assertEqual((self.game.progression.snapshot()['gold'],self.rows(PAGE)),(14,before))

    def test_opening_service_panels_and_storage_lists_never_reserve_a_writer(self):
        for sid,nid in [(2048,1280),(2034,1271),(2001,3439)]:
            self.approach(sid,nid);self.send(1034,nid)
            with patch.object(self.characters,'pin_position',side_effect=AssertionError('unexpected writer')):
                self.choose(nid,1)

    def test_native_warehouse_open_record_and_permission_flags(self):
        self.approach(2001,3439)
        dialogue=self.send(1034,3439)
        self.assertEqual(menu(dialogue),([('暂时不用的东西，可以存放在这里。',0)],['打开仓库'],3439,1))
        replies=self.open(2001,3439,1);p=next(p for p in replies if mid(p)==839)
        r=MessageReader(p[3:]);self.assertEqual([r.integer() for _ in range(3)],[3439,150,1])
        self.assertEqual(r.take(1),b'\0');self.assertEqual(r.string(),b'{}')
        self.assertEqual([r.integer() for _ in range(2)],[1,24]);r.finish()
        self.assertEqual(mid(self.send(3236,3439,1)),3235);self.assertEqual(mid(self.send(3816,3439,1)),3815)
        self.assertEqual(mid(self.send(3816,3439,2)),98)

    def test_native_warehouse_accepts_open_expansion_page_and_preserves_timed_item(self):
        self.open(2001,3439,1);p=self.game.progression;item=self.rows(8)[0]
        with p.transaction() as db:
            db.execute("INSERT INTO cosmetic_features VALUES(?,'bag_item',1,0)",(p.cid,))
            db.execute('UPDATE inventory SET page=16 WHERE id=?',(item['id'],))
            db.execute('INSERT INTO cosmetic_expiry VALUES(?,?)',(item['id'],4_000_000_000))
        self.assertIn(786,[mid(msg) for msg in self.send(842,item['id'],3439,0,150,-1)])
        self.assertEqual(self.rows(STORAGE_PAGE)[0]['id'],item['id'])
        self.assertEqual(mid(self.send(842,item['id'],3439,1,17,1)),98)
        self.send(842,item['id'],3439,1,16,-1)
        self.assertEqual([i['id'] for i in self.rows(16)],[item['id']])
        with p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT expires FROM cosmetic_expiry WHERE item_id=?',(item['id'],)).fetchone()[0],4_000_000_000)

    def test_native_warehouse_search_returns_grouped_owned_results_and_locates_page(self):
        self.open(2001,3439,1);item=self.rows(8)[0]
        self.send(842,item['id'],3439,0,150,2)
        request=packet(4513,['number',('array','number')],[2,[item['type']]])
        reply=self.game.receive(4513,MessageReader(request[3:]))
        self.assertEqual(mid(reply),4511)
        reader=MessageReader(reply[3:]);self.assertEqual(reader.integer(),2)
        text=reader.string().decode('gb18030');reader.finish()
        self.assertIn('[2]={[150]={["Name"]="仓库1",["Grids"]={[2]={["Id"]='+str(item['id']),text)
        replies=self.send(4512,2,150)
        self.assertEqual([mid(r) for r in replies],[839,4510])
        self.assertEqual(replies[1],packet(4510,['number','number'],[2,150]))
        self.game.npc_service_session=None
        self.assertEqual(mid(self.game.receive(4513,MessageReader(request[3:]))),98)
        self.assertEqual(mid(self.send(4512,2,150)),98)

    def test_warehouse_swap_and_reorder_conserve_owned_items(self):
        self.open(2001,3439,1);first,second=self.rows(8)[:2];before={i['id']:i['amount'] for i in self.rows()}
        self.send(842,first['id'],3439,0,150,1)
        self.send(842,first['id'],3439,2,150,5)
        self.assertEqual(self.rows(STORAGE_PAGE)[0]['slot'],5)
        self.send(842,second['id'],3439,0,150,5)
        self.assertEqual([i['id'] for i in self.rows(STORAGE_PAGE)],[second['id']])
        self.assertIn(first['id'],[i['id'] for i in self.rows(8)])
        self.assertEqual({i['id']:i['amount'] for i in self.rows()},before)

    def test_warehouse_rejects_forged_directions_pages_and_expired_session(self):
        self.open(2001,3439,1);item=self.rows(8)[0];before=self.rows()
        for args in [(item['id'],3439,0,8,1),(item['id'],3439,1,150,1),(item['id'],3439,0,151,1),
                     (item['id'],3439,0,150,25),(item['id']+100000,3439,0,150,1)]:
            self.assertEqual(mid(self.send(842,*args)),98)
        self.game.npc_service_session['expires']=0
        self.assertEqual(mid(self.send(842,item['id'],3439,0,150,1)),98);self.assertEqual(self.rows(),before)

    def test_warehouse_tooltip_requires_owned_item_and_live_storage_session(self):
        self.open(2001,3439,1);item=self.rows(8)[0];self.send(842,item['id'],3439,0,150,-1)
        self.assertEqual(mid(self.send(806,item['id'],0)),791)
        self.assertEqual(mid(self.send(806,item['id']+100000,0)),98)
        self.game.npc_service_session=None
        self.assertEqual(mid(self.send(806,item['id'],0)),98)

    def test_native_storage_auto_merge_obeys_stack_cap_and_conserves_amount(self):
        self.open(2001,3439,1);p=self.game.progression;source=self.rows(8)[0]
        with p.transaction() as db:
            db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,1,499)',(p.cid,source['type'],STORAGE_PAGE))
        self.send(842,source['id'],3439,0,150,-1)
        stored=self.rows(STORAGE_PAGE);self.assertEqual(sorted(i['amount'] for i in stored),[4,500])
        self.assertEqual(sum(i['amount'] for i in stored),source['amount']+499)
        self.assertEqual(next(i['slot'] for i in stored if i['id']==source['id']),2)

    def test_storage_does_not_merge_different_inner_pill_instances(self):
        from native946_neidan_legacy import LegacyNeidan
        self.open(2001,3439,1);LegacyNeidan(self.game.summons).grant_test_kit()
        pills=[i for i in self.rows(8) if i['type']==6052][:2]
        for pill in pills:self.send(842,pill['id'],3439,0,150,-1)
        self.assertEqual({i['id'] for i in self.rows(STORAGE_PAGE)},{i['id'] for i in pills})
        self.assertEqual([i['amount'] for i in self.rows(STORAGE_PAGE)],[1,1])


if __name__=='__main__':unittest.main()
