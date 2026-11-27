"""Equipment set transitions, persistence and native protocol boundaries."""
import unittest
from native946_inventory import activate,EQUIP_PAGES
from native946_progression import Progression
from native946_gameplay import NativeGameplay
from native946_cosmetics import Cosmetics,appearance
from native946_codec import number
from native946_login import MessageReader
import test_native946_progression as fixtures
import test_native946_inventory as inventory_fixtures

class EquipmentPageTests(unittest.TestCase):
    setUp=fixtures.ProgressionTests.setUp
    def game(self):return NativeGameplay(self.character,self.characters,lambda:())
    def send(self,g,page,trailing=b''):return g.receive(796,MessageReader(number(page)+trailing))
    def test_three_native_pages_persist_and_only_active_set_supplies_stats(self):
        self.p.move_item(4,1,3);self.p.move_item(5,3,5);base=self.p.snapshot()
        self.assertEqual((base['hpmax'],base['defense'],base['initiative']),(358,3,9))
        activate(self.p,3);s=self.p.snapshot();self.assertEqual((s['hpmax'],s['defense'],s['initiative']),(338,0,11))
        cold=Progression(self.path,'owner946',self.p.cid);self.assertEqual(cold.snapshot()['equipment_page'],3)
        activate(cold,4);self.assertEqual(cold.snapshot()['initiative'],9)
        activate(cold,1);self.assertEqual(cold.snapshot()['defense'],3)
    def test_switch_does_not_duplicate_equipment_or_need_bag_space(self):
        self.p.move_item(4,1,3)
        with self.p.transaction() as db:
            occupied={r[0] for r in db.execute('SELECT slot FROM inventory WHERE character_id=? AND page=8',(self.p.cid,))}
            for slot in set(range(1,25))-occupied:db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4000,8,?,1)',(self.p.cid,slot))
        before=self.p.snapshot()['items']
        for page in [3,4,1,4,3,1]:activate(self.p,page)
        self.assertEqual(self.p.snapshot()['items'],before)
    def test_switch_clamps_vitals_and_never_heals_when_switching_back(self):
        self.p.move_item(4,1,3);self.p.set_vitals(358,100);activate(self.p,3)
        self.assertEqual(self.p.snapshot()['hp'],338);activate(self.p,1);self.assertEqual(self.p.snapshot()['hp'],338)
    def test_native_switch_reply_selects_the_requested_page(self):
        g=self.game();reply=self.send(g,4);p=next(p for p in reply if int.from_bytes(p[1:3],'little')==777)
        r=MessageReader(p[3:]);self.assertEqual((r.integer(),r.integer()),(4,4));r.finish()
        self.assertEqual(g.last_event['to_page'],4)
    def test_invalid_page_malformed_frame_and_battle_leave_active_page_unchanged(self):
        g=self.game()
        for page in [-1,0,2,5,999]:self.assertEqual(self.send(g,page)[0] or int.from_bytes(self.send(g,page)[1:3],'little'),98)
        self.assertEqual(self.send(g,3,b'x')[0] or int.from_bytes(self.send(g,3,b'x')[1:3],'little'),98)
        g.battle={'phase':'input'};self.assertEqual(self.send(g,3)[0] or int.from_bytes(self.send(g,3)[1:3],'little'),98)
        self.assertEqual(g.progression.snapshot()['equipment_page'],1)
    def test_right_click_equips_into_current_set_and_unequips_that_set(self):
        g=self.game();self.send(g,3)
        for expected in [(3,3),(8,4)]:
            g.receive(807,MessageReader(number(0)+number(4)))
            item=next(i for i in g.progression.snapshot()['items'] if i['id']==4)
            self.assertEqual((item['page'],item['slot']),expected)
    def test_weapon_visibility_tracks_active_set_without_losing_selection(self):
        iid=inventory_fixtures.InventoryTests.weapon(self);self.p.move_item(iid,3,4)
        with self.p.transaction(write=False) as db:self.assertEqual(appearance(db,self.p.cid)['weapon_idx'],0)
        activate(self.p,3)
        with self.p.transaction(write=False) as db:self.assertEqual(appearance(db,self.p.cid)['weapon_idx'],19)
        activate(self.p,4)
        with self.p.transaction(write=False) as db:self.assertEqual(appearance(db,self.p.cid)['weapon_idx'],0)
    def test_switch_rechecks_equipment_requirements_of_destination_set(self):
        iid=inventory_fixtures.InventoryTests.weapon(self);self.p.move_item(iid,3,4)
        with self.p.transaction() as db:db.execute("UPDATE native_equipment SET requirements=? WHERE item_id=?",('{"level":99,"rebirth":0}',iid))
        with self.assertRaises(ValueError):activate(self.p,3)
        self.assertEqual(self.p.snapshot()['equipment_page'],1)

if __name__=='__main__':unittest.main()
