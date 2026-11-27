"""All shop medicines recover the current client values and consume exactly once."""
from random import Random
import unittest
from native946_battle_actions import numbers
from native946_progression import DATA
from native946_shops import Shops
from native946_role_battle import Runtime,solo_fighters
import test_native946_battle_actions as fixtures
mid=fixtures.mid

EXPECTED={4000:(200,0),4001:(0,100),4002:(500,0),4003:(0,200),4004:(900,0),4005:(0,400),
          4006:(1800,0),4007:(0,900),4008:(2400,0),4009:(0,1500),4010:(3000,0),4011:(0,1800),
          4012:(3900,0),4013:(0,2400),4014:(4400,0),4015:(0,2800),4016:(5100,0),4017:(0,3600),
          4018:(3000,2000),4019:(4000,3000)}

class ShopMedicineTests(unittest.TestCase):
    setUp=fixtures.BattleActionTests.setUp
    call=fixtures.BattleActionTests.call
    medicine=fixtures.BattleActionTests.medicine

    def stock(self):
        with self.p.transaction() as db:
            for kind in EXPECTED:Shops(self.g).grant(db,kind,2)

    def replace_bag(self, *stacks):
        with self.p.transaction() as db:
            db.execute('DELETE FROM inventory WHERE character_id=?', (self.c['id'],))
            return [db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',
                               (self.c['id'], kind, page, slot, count)).lastrowid
                    for slot, (kind, page, count) in enumerate(stacks, 1)]

    def test_hero_right_click_uses_low_grade_first_and_stops_at_full(self):
        high, low, spare = self.replace_bag((4002, 8, 3), (4000, 8, 1), (4000, 8, 3))
        self.p.set_vitals(1, 1)
        self.p.item_packets()
        before = self.p.snapshot()
        replies = self.call(1412, 0)
        self.assertIn(numbers(695, int(DATA['attributes']['Hp']), before['hpmax']), replies)
        self.assertIn(numbers(694), replies)
        self.assertIn(numbers(780, low), replies)
        self.assertIn(789, [mid(p) for p in replies])
        after = self.p.snapshot()
        self.assertEqual((after['hp'], after['mp']), (before['hpmax'], 1))
        self.assertEqual({i['id']: i['amount'] for i in after['items']}, {high: 3, spare: 2})
        self.assertIsNone(self.call(1412, 0))
        self.assertEqual(self.p.snapshot(), after)

    def test_hero_right_click_uses_available_stock_and_skips_unusable_items(self):
        usable, locked, expired, blood = self.replace_bag((4001, 8, 1), (4001, 8, 2), (4003, 15, 2), (4000, 8, 2))
        with self.p.transaction() as db:
            db.execute('INSERT INTO native_object_locks VALUES(?,\'item\',?,1,0)', (self.c['id'], locked))
            db.execute('INSERT INTO cosmetic_features VALUES(?,\'bag_temp\',1,1)', (self.c['id'],))
        self.p.set_vitals(1, 1)
        self.p.item_packets()
        replies = self.call(1412, 1)
        self.assertIn(numbers(695, int(DATA['attributes']['Mp']), 101), replies)
        self.assertIn(numbers(780, usable), replies)
        after = self.p.snapshot()
        self.assertEqual((after['hp'], after['mp']), (1, 101))
        self.assertEqual({i['id']: i['amount'] for i in after['items']}, {locked: 2, expired: 2, blood: 2})
        self.assertEqual(mid(self.call(1412, 1)), 98)
        self.assertEqual(self.p.snapshot(), after)

    def test_guardian_right_click_refreshes_its_vitals_and_applies_both_pill_effects(self):
        (medicine,) = self.replace_bag((4018, 8, 2))
        self.s.set_vitals(self.sid, 1, 1)
        hero = self.p.snapshot()
        replies = self.call(1489, 1, self.sid)
        after = self.s.get(self.sid)
        self.assertIn(self.s.info_packets(self.sid)[1], replies)
        self.assertEqual((after['hp'], after['mp']), (after['hpmax'], after['mpmax']))
        self.assertEqual(self.p.snapshot()['items'][0]['id'], medicine)
        self.assertEqual(self.p.snapshot()['items'][0]['amount'], 1)
        self.assertEqual((self.p.snapshot()['hp'], self.p.snapshot()['mp']), (hero['hp'], hero['mp']))

    def test_right_click_rejects_invalid_fields_unowned_guardian_and_battle(self):
        self.replace_bag((4000, 8, 5))
        self.p.set_vitals(1, 1)
        self.s.set_vitals(self.sid, 1, 1)
        hero, guardian = self.p.snapshot(), self.s.get(self.sid)
        self.assertEqual(mid(self.call(1412, 2)), 98)
        self.assertEqual(mid(self.call(1412, 0, tail=b'\0')), 98)
        self.assertEqual(mid(self.call(1489, 0, self.sid + 10)), 98)
        self.g.begin_battle()
        self.assertEqual(mid(self.call(1412, 0)), 98)
        self.assertEqual(mid(self.call(1489, 0, self.sid)), 98)
        self.assertEqual(self.p.snapshot(), hero)
        self.assertEqual(self.s.get(self.sid), guardian)

    def test_every_medicine_can_be_used_outside_battle_and_saves_both_bars(self):
        self.stock()
        for kind,(hp,mp) in EXPECTED.items():
            self.p.set_vitals(1,1);before=self.p.snapshot();item=self.medicine(kind)
            self.p.use_item(item['id']);after=self.p.snapshot()
            self.assertEqual((after['hp'],after['mp']),(min(before['hpmax'],1+hp),min(before['mpmax'],1+mp)),kind)
            self.assertEqual(self.medicine(kind)['amount'],item['amount']-1)
            self.p.set_vitals(after['hpmax'],after['mpmax']);full=self.p.snapshot()
            with self.assertRaises(ValueError):self.p.use_item(item['id'])
            self.assertEqual(full,self.p.snapshot())

    def test_every_battle_medicine_uses_native_base_amount_and_current_modifier(self):
        self.stock();self.g.begin_battle();fighters=solo_fighters(self.g);hero=fighters[self.c['id']]
        runtime=Runtime(fighters,{self.p.cid:self.g},1,Random(1),self.g.battle)
        for kind,(hp,mp) in EXPECTED.items():
            hero.update(hp=1,mp=1,hpmax=20000,mpmax=20000,role_medicine_percent=250)
            item=self.medicine(kind);order=dict(item_id=item['id'],target=self.c['id'])
            with self.p.transaction() as db:runtime.item(self.g,hero,order,db)
            self.assertEqual((hero['hp'],hero['mp']),(1+hp*1250//1000,1+mp*1250//1000),kind)
            self.assertEqual(self.medicine(kind)['amount'],item['amount']-1)
            hero.update(hp=20000,mp=20000);left=self.medicine(kind)['amount']
            with self.p.transaction() as db:runtime.item(self.g,hero,order,db)
            self.assertEqual(self.medicine(kind)['amount'],left)

    def test_every_medicine_can_be_used_on_owned_guardian_and_full_state_is_noop(self):
        self.stock()
        for kind,(hp,mp) in EXPECTED.items():
            self.s.set_vitals(self.sid,1,1);before=self.s.get(self.sid);item=self.medicine(kind)
            self.s.use_item(self.sid,item['id']);after=self.s.get(self.sid)
            self.assertEqual((after['hp'],after['mp']),(min(before['hpmax'],1+hp),min(before['mpmax'],1+mp)),kind)
            self.assertEqual(self.medicine(kind)['amount'],item['amount']-1)
            self.s.set_vitals(self.sid,after['hpmax'],after['mpmax']);full=self.p.snapshot()
            with self.assertRaises(ValueError):self.s.use_item(self.sid,item['id'])
            self.assertEqual(full,self.p.snapshot())

    def test_double_recovery_uses_one_queued_native_turn_and_one_item(self):
        self.stock();self.p.set_vitals(1,1);self.g.begin_battle();item=self.medicine(4019)
        self.call(2185,8,item['id'],self.c['id'])
        self.assertEqual(self.medicine(4019)['amount'],item['amount'])
        replies=self.call(2182,1)
        self.assertIn(numbers(2158,self.c['id'],1,min(self.g.battle['hpmax'],4001)),replies)
        self.assertIn(numbers(2158,self.c['id'],4,min(self.g.battle['mpmax'],3001)),replies)
        self.assertEqual(self.medicine(4019)['amount'],item['amount']-1)
        self.assertEqual(self.g.battle['mp'],min(self.g.battle['mpmax'],3001))
        self.assertEqual(self.g.battle['hp'],min(self.g.battle['hpmax'],4001)-5)

    def test_double_recovery_accepts_a_full_hp_bar_when_mp_is_missing(self):
        self.stock();s=self.p.snapshot();self.p.set_vitals(s['hpmax'],1)
        item=self.medicine(4018);self.p.use_item(item['id']);after=self.p.snapshot()
        self.assertEqual((after['hp'],after['mp']),(s['hpmax'],min(s['mpmax'],2001)))
        self.assertEqual(self.medicine(4018)['amount'],item['amount']-1)

    def test_honey_food_recovers_outside_battle_and_is_rejected_in_native_turn(self):
        self.check_food(4023,150)

    def test_lotus_food_recovers_outside_battle_and_is_rejected_in_native_turn(self):
        self.check_food(4027,600)

    def check_food(self,kind,recovery):
        with self.p.transaction() as db:Shops(self.g).grant(db,kind,3)
        self.p.set_vitals(1,1);before=self.p.snapshot();item=self.medicine(kind)
        self.p.use_item(item['id']);after=self.p.snapshot()
        self.assertEqual((after['hp'],after['mp']),(1,min(before['mpmax'],1+recovery)))
        self.p.set_vitals(after['hpmax'],after['mpmax']);full=self.p.snapshot()
        with self.assertRaises(ValueError):self.p.use_item(item['id'])
        self.assertEqual(self.p.snapshot(),full)
        self.s.set_vitals(self.sid,1,1);pet=self.s.get(self.sid);self.s.use_item(self.sid,item['id'])
        self.assertEqual(self.s.get(self.sid)['mp'],min(pet['mpmax'],1+recovery))
        self.assertEqual(self.medicine(kind)['amount'],1)
        self.g.begin_battle();before=self.p.snapshot()
        rejected=self.call(2185,8,item['id'],self.c['id'])
        self.assertEqual(mid(rejected[0] if isinstance(rejected,tuple) else rejected),98)
        self.assertEqual(mid(self.call(807,self.c['id'],item['id'])),98)
        self.assertEqual(self.p.snapshot(),before)

if __name__=='__main__':unittest.main()
