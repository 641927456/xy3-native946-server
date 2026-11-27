"""Direct acquisition through real native mall confirmations and owned items."""
import unittest
from native946_codec import number
from native946_login import MessageReader
from native946_cosmetics import Cosmetics,DATA as CATALOG,PAGES,appearance,sale_type
from native946_fashion import RULES,score
from native946_fashion_shop import FashionShop,current_frame
from native946_progression import Progression
from native946_game_login import NativeGameLogin,message_id
from native946_sessions import LEASES
from test_native946_npcs import NpcGameplayTests

def mid(p):return int.from_bytes(p[1:3],'little') if p[0]==0 else p[0]
def packets(p):return p if isinstance(p,tuple) else (p,)

class FashionShopTests(unittest.TestCase):
    approach=NpcGameplayTests.approach
    def setUp(self):
        NpcGameplayTests.setUp(self)
        with self.game.progression.transaction() as db:
            db.execute('INSERT INTO mall_account_wallet(owner,yubo) VALUES(?,1000)',(self.game.progression.owner,))

    def send(self,mid,*values,tail=b''):
        return self.game.receive(mid,MessageReader(b''.join(number(x) for x in values)+tail))
    def rows(self):return self.game.progression.snapshot()['items']
    def balance(self):
        with self.game.progression.transaction(write=False) as db:return Cosmetics(self.game).wallet(db)[0]
    def buy(self,kind):self.send(3663,kind,1);return self.send(3665,kind,1,0,0)
    def selected(self,reply):
        p=next(p for p in packets(reply) if mid(p)==3658);r=MessageReader(p[3:]);r.string()
        for _ in range(6):r.integer()
        r.string();value=r.integer();r.finish();return value

    def test_entry_opens_correct_female_set_without_score_or_charge(self):
        before=self.rows();self.assertEqual(self.selected(self.send(5407)),sale_type(66765))
        self.assertEqual(self.balance(),1000);self.assertEqual(self.rows(),before)
        for index in (1,2,3):self.assertEqual(self.selected(self.send(5410,index)),sale_type(66765))

    def test_all_six_accessory_entries_use_real_mall_quotes(self):
        for index,kind in enumerate([66765,66772,48278,66768,66771,66773],1):
            self.assertEqual(self.selected(self.send(5409,index)),sale_type(kind))
        self.assertEqual(self.balance(),1000)
        for index in (0,7):self.assertEqual(mid(self.send(5409,index)),98)

    def test_buy_female_suit_delivers_three_permanent_parts_and_replay_rejects(self):
        self.buy(66765);parts=set(CATALOG['sets']['66765'])
        self.assertEqual({r['type'] for r in self.rows()}&parts,parts);self.assertEqual(self.balance(),900)
        before=self.rows();self.assertEqual(mid(self.send(3665,66765,1,0,0)),98)
        self.assertEqual(self.rows(),before);self.assertEqual(self.balance(),900)
        for row in self.rows():
            if row['type'] in parts:self.send(807,0,row['id'])
        with self.game.progression.transaction(write=False) as db:
            self.assertTrue(FashionShop(self.game).g.character['sex']==2)
            self.assertEqual(db.execute('SELECT count(*) FROM cosmetic_expiry e JOIN inventory i ON e.item_id=i.id WHERE i.character_id=? AND i.page=29 AND e.expires<>0',(self.game.progression.cid,)).fetchone()[0],0)
            self.assertGreater(score(db,self.game.progression.cid),0)
        cold=Progression(self.path,'npc_owner',self.game.progression.cid)
        with cold.transaction(write=False) as db:self.assertEqual(appearance(db,cold.cid)['suit_id'],22029)

    def test_male_character_selects_and_buys_male_parts(self):
        row=self.characters.create('npc_owner','男装购买',113)
        login=NativeGameLogin(544,None,number,None,self.characters);login.username='npc_owner'
        self.addCleanup(lambda:LEASES.release_port(self.path,544))
        login.receive(message_id(915)+number(row['id']));self.game=login.gameplay
        self.assertEqual(self.balance(),1000)
        self.assertEqual(self.selected(self.send(5407)),sale_type(66761));self.buy(66761)
        self.assertTrue(set(CATALOG['sets']['66761'])<={r['type'] for r in self.rows()})

    def test_insufficient_currency_and_unconfirmed_calls_preserve_items(self):
        before=self.rows();self.assertEqual(mid(self.send(3665,66765,1,0,0)),98)
        with self.game.progression.transaction() as db:db.execute('UPDATE mall_account_wallet SET yubo=99 WHERE owner=?',(self.game.progression.owner,))
        self.assertEqual(mid(self.buy(66765)),98);self.assertEqual(self.rows(),before);self.assertEqual(self.balance(),99)

    def test_partial_suit_delivery_rolls_back_all_parts_and_payment(self):
        with self.game.progression.transaction() as db:
            for page in PAGES:
                for slot in range(1,25):
                    if (page,slot) in [(PAGES[-1],23),(PAGES[-1],24)]:continue
                    db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)',(self.game.progression.cid,55140,page,slot))
        before=self.rows();self.assertEqual(mid(self.buy(66765)),98)
        self.assertEqual(self.balance(),1000);self.assertEqual(self.rows(),before)

    def test_halo_bundle_and_three_other_accessories_can_be_used(self):
        self.buy(66772);bundle=next(r for r in self.rows() if r['type']==sale_type(66772));self.send(807,0,bundle['id'])
        for kind in [56769,56770,66768,66771,66773]:
            if kind>=66768:self.buy(kind)
            row=next(r for r in self.rows() if r['type']==sale_type(kind))
            reply=self.send(807,0,row['id']);self.assertNotEqual(mid(packets(reply)[0]),888)
        with self.game.progression.transaction(write=False) as db:
            a=appearance(db,self.game.progression.cid)
            self.assertEqual((a['halo'],a['foot_halo']),(129,573));self.assertGreater(a['sayframe'],0)
            self.assertGreater(a['team_frame'],0);self.assertGreater(a['enter_effect'],0)

    def test_frame_purchase_activation_and_native_switch_persist(self):
        self.buy(48278);row=next(r for r in self.rows() if r['type']==48278)
        self.assertEqual(self.balance(),900);self.assertEqual(CATALOG['items']['48278']['icon'],8986)
        reply=self.send(807,0,row['id']);self.assertIn(5031,[mid(p) for p in packets(reply)])
        cold=Progression(self.path,'npc_owner',self.game.progression.cid)
        self.assertEqual(cold.attribute_values()['IconFrame'],1160)
        self.send(5032,0);self.assertEqual(self.game.progression.attribute_values()['IconFrame'],0)
        self.send(5032,1160);self.assertEqual(self.game.progression.attribute_values()['IconFrame'],1160)
        with cold.transaction(write=False) as db:self.assertEqual(score(db,cold.cid),RULES['ItemConfig']['48278']['FashionScore'])

    def test_forged_frame_and_duplicate_activation_do_not_consume(self):
        self.assertEqual(mid(self.send(5032,1160)),98)
        self.buy(48278);row=next(r for r in self.rows() if r['type']==48278);self.send(807,0,row['id'])
        self.buy(48278);before=self.rows();row=next(r for r in before if r['type']==48278)
        self.assertEqual(mid(self.send(807,0,row['id'])),98);self.assertEqual(self.rows(),before)
        self.assertEqual(mid(self.send(5032,0,tail=b'x')),98)
        self.assertEqual(self.game.progression.attribute_values()['IconFrame'],1160)

    def test_original_npc_menu_exposes_all_goods_at_existing_prices(self):
        self.approach(2001,5657);cs=Cosmetics(self.game)
        cs.menu({'npc_id':5657,'title':''})
        # Execute uses the ordinary NPC range/session validation.
        cs.execute(5657,dict(op='fashion_shop'))
        self.assertEqual(len(self.game.dialog_actions),7)
        self.assertEqual(self.selected(cs.execute(5657,dict(op='fashion_shop_item',item_type=48278))),48278)

if __name__=='__main__':unittest.main()
