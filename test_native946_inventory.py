"""Native grid callbacks, stack limits and weapon visibility regressions."""
import unittest
from native946_codec import SCHEMAS,number,packet
from native946_login import MessageReader
from native946_gameplay import NativeGameplay
from native946_cosmetics import Cosmetics,appearance
from native946_progression import Progression
from native946_equipment import persist
import test_native946_progression as fixtures


class NativeGrid:
    """946 c_item_del/new/update callbacks, including old-grid deletion."""
    def __init__(self):self.items={};self.grids={}
    def apply(self,packets):
        for payload in packets:
            mid=int.from_bytes(payload[1:3],'little');r=MessageReader(payload[3:])
            if mid==780:self.delete(r.integer())
            elif mid in (786,789):
                value={}
                for name,kind in SCHEMAS['item_info']:
                    value[name]=r.string() if kind=='string' else r.take(1)[0] if kind=='byte' else r.integer()
                r.finish()
                if mid==789:self.delete(value['id'])
                self.items[value['id']]=value;self.grids[value['page'],value['grid']]=value['id']
    def delete(self,iid):
        old=self.items.pop(iid,None)
        if old:self.grids.pop((old['page'],old['grid']),None)


class InventoryTests(unittest.TestCase):
    setUp=fixtures.ProgressionTests.setUp
    def game(self):return NativeGameplay(self.character,self.characters,lambda:())
    def send(self,g,mid,*values,trailing=b''):
        return g.receive(mid,MessageReader(b''.join(number(v) for v in values)+trailing))
    def assert_grid(self,grid,p):
        self.assertEqual(grid.grids,{(i['page'],i['slot']):i['id'] for i in p.snapshot()['items']})
    def weapon(self):
        with self.p.transaction() as db:
            iid=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,5597,8,12,1)',(self.p.cid,)).lastrowid
            profile=dict(reference_id=100001,grade=11,slot=4,role='通用',need={},level=0,rebirth=0,durability=100)
            persist(db,self.p.cid,iid,profile,{'攻击':10})
            Cosmetics(self.game())._state(db,weapon_idx=19,weapon_color=2)
        return iid

    def test_native_new_item_effect_follows_new_and_increased_stack_packets(self):
        p = self.p
        effect = lambda iid: packet(790, ['number', 'number'], [iid, 1])
        self.assertNotIn(effect(1), p.item_packets())
        with p.transaction() as db:
            db.execute('UPDATE inventory SET amount=amount+2 WHERE id=1')
            iid = db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4001,8,12,3)',
                             (p.cid,)).lastrowid
        partial = p.item_packets(item_ids={iid})
        self.assertEqual([int.from_bytes(row[1:3], 'little') for row in partial], [786, 790])
        self.assertEqual(partial[-1], effect(iid))
        full = p.item_packets()
        self.assertEqual([row for row in full if row[1:3] == b'\x16\x03'], [effect(1)])
        self.assertNotIn(effect(1), p.item_packets())
        self.assertNotIn(effect(iid), Progression(self.path, 'owner946', p.cid).item_packets())

    def test_inventory_rollback_does_not_swallow_the_next_item_effect(self):
        g = self.game()
        p = g.progression
        p.item_packets()
        effect = packet(790, ['number', 'number'], [1, 1])
        with self.assertRaisesRegex(ValueError, 'rollback fixture'):
            with g.operation(write=True), p.transaction() as db:
                db.execute('UPDATE inventory SET amount=amount+1 WHERE id=1')
                self.assertIn(effect, p.item_packets())
                raise ValueError('rollback fixture')
        self.assertNotIn(effect, p.item_packets())
        with p.transaction() as db:
            db.execute('UPDATE inventory SET amount=amount+1 WHERE id=1')
        self.assertIn(effect, p.item_packets())
    def test_native_swaps_leave_both_grid_entries_and_tooltips_available(self):
        g=self.game();grid=NativeGrid();grid.apply(g.progression.item_packets())
        for a,b in [(1,2),(2,4),(4,1),(1,24),(24,3)]:
            moved=sum((8,slot) in grid.grids for slot in (a,b))
            result=self.send(g,795,8,a,8,b)
            self.assertEqual([int.from_bytes(p[1:3],'little') for p in result], [780]*moved+[786]*moved)
            grid.apply(result);self.assert_grid(grid,g.progression)
            for iid in grid.items:self.assertEqual(int.from_bytes(g.progression.item_description(iid)[1:3],'little'),791)

    def test_wardrobe_open_and_bag_page_only_send_their_own_items(self):
        g=self.game();p=g.progression
        with p.transaction() as db:
            cosmetic=db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4001,25,1,1)',
                                (p.cid,)).lastrowid
        grid=NativeGrid();grid.apply(p.item_packets())
        for reply in (self.send(g,3672,1,0),self.send(g,837)):
            items=NativeGrid();items.apply(reply)
            self.assertEqual(set(items.items),{cosmetic})
            self.assertIn(824,[int.from_bytes(pkt[1:3],'little') for pkt in reply])
            grid.apply(reply);self.assert_grid(grid,p)
        reply=self.send(g,801,8)
        items=NativeGrid();items.apply(reply)
        self.assertEqual(set(items.items),{iid for (page,_),iid in grid.grids.items() if page==8})
        grid.apply(reply);self.assert_grid(grid,p)
    def test_native_equipment_swap_and_unwear_keep_every_grid_entry(self):
        g=self.game();grid=NativeGrid();grid.apply(g.progression.item_packets())
        for args in [(8,4,1,3),(1,3,8,4),(8,3,1,1),(1,1,8,3)]:
            grid.apply(self.send(g,803,*args));self.assert_grid(grid,g.progression)
    def test_refresh_after_reload_clears_old_positions_before_readding(self):
        grid=NativeGrid();grid.apply(self.p.item_packets());known=self.p.known_items
        self.p.move_item(1,8,2,stack=False)
        cold=Progression(self.path,'owner946',self.p.cid);cold.known_items=known
        grid.apply(cold.item_packets());self.assert_grid(grid,cold)
    def test_drag_stacks_but_pure_move_preserves_separate_stacks(self):
        g=self.game();g.progression.item_packets()
        self.p.split(1,8,12,2)
        self.assertNotIn(b'\x16\x03',[row[1:3] for row in g.progression.item_packets()])
        self.send(g,795,8,12,8,1)
        self.assertEqual(len([i for i in g.progression.snapshot()['items'] if i['type']==4000]),2)
        merged=self.send(g,803,8,12,8,1)
        self.assertNotIn(b'\x16\x03',[row[1:3] for row in merged])
        self.assertEqual([(i['slot'],i['amount']) for i in g.progression.snapshot()['items'] if i['type']==4000],[(1,5)])
    def test_partial_stack_keeps_remainder_and_total(self):
        self.p.split(1,8,12,2)
        with self.p.transaction() as db:db.execute('UPDATE inventory SET amount=499 WHERE id=1')
        g=self.game();self.send(g,803,8,12,8,1)
        self.assertEqual(sorted(i['amount'] for i in g.progression.snapshot()['items'] if i['type']==4000),[1,500])
    def test_merge_ack_does_not_reopen_native_merge_dialog(self):
        self.p.split(1,8,12,2);g=self.game();ids=[i['id'] for i in self.p.snapshot()['items'] if i['type']==4000]
        reply=g.receive(802,MessageReader(bytes([len(ids)])+b''.join(number(i) for i in ids)))
        self.assertNotIn(784,[int.from_bytes(p[1:3],'little') for p in reply])
        self.assertEqual(sum(i['amount'] for i in self.p.snapshot()['items'] if i['type']==4000),5)
    def test_malformed_drag_rolls_back_rows_and_wire_cache(self):
        g=self.game();g.progression.item_packets();before=g.progression.snapshot()['items'];positions=dict(g.progression._known_item_positions)
        reply=self.send(g,803,8,1,8,2,trailing=b'x')
        self.assertEqual(reply[0] or int.from_bytes(reply[1:3],'little'),98)
        self.assertEqual(g.progression.snapshot()['items'],before);self.assertEqual(g.progression._known_item_positions,positions)
    def test_no_weapon_hides_saved_selection_in_scene_and_battle(self):
        iid=self.weapon();g=self.game();cs=Cosmetics(g);cs.refresh()
        self.assertEqual((g.character['appearance']['weapon_idx'],g.character['appearance']['weapon_color']),(0,0))
        self.send(g,807,0,iid);cs.refresh()
        self.assertEqual((g.character['appearance']['weapon_idx'],g.character['appearance']['weapon_color']),(19,2))
        r=MessageReader(g.fighter(g.character['id'],702,'测试',4,1)[3:])
        for i in range(7):r.string() if i==3 else r.integer()
        self.assertEqual(r.take(3),bytes([19,2,0]))
        self.send(g,807,0,iid);cs.refresh();self.assertEqual(g.character['appearance']['weapon_idx'],0)
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT weapon_idx FROM cosmetic_state WHERE character_id=?',(self.p.cid,)).fetchone()[0],19)
            self.assertEqual(appearance(db,self.p.cid)['weapon_idx'],0)
    def test_right_click_crafted_equipment_uses_its_slot_and_bonuses(self):
        iid=self.weapon();g=self.game();before=g.progression.snapshot()['attack'];reply=self.send(g,807,0,iid)
        self.assertIn(1620,[int.from_bytes(p[1:3],'little') for p in reply])
        item=next(i for i in g.progression.snapshot()['items'] if i['id']==iid)
        self.assertEqual((item['page'],item['slot']),(1,4));self.assertEqual(g.progression.snapshot()['attack'],before+10)

    def test_native_star_title_equipment_changes_stats_after_wear_and_reload(self):
        from native946_star_rewards import grant, profile

        owner = self.character['owner']
        character = self.characters.create(owner,'星冠测试',301)
        game = NativeGameplay(character,self.characters,lambda:())
        progression = game.progression
        with progression.transaction() as db:
            db.execute('UPDATE characters SET level=90 WHERE id=?',(progression.cid,))
            grant(db,progression,'7',profile('7',character['race']))
        before = progression.snapshot()
        iid = next(row['id'] for row in before['items'] if row['type']==13261)
        reply = self.send(game,807,0,iid)
        self.assertIn(1620,[int.from_bytes(p[1:3],'little') for p in reply])
        after = Progression(self.path,owner,progression.cid).snapshot()
        item = next(row for row in after['items'] if row['id']==iid)
        self.assertEqual((item['page'],item['slot']),(after['equipment_page'],1))

        def attributes(snapshot):
            battle_stats = snapshot['cultivation']
            return (snapshot['mag'], *(battle_stats.get(key,0) for key in ('AttValue','AddFire','AddWind')))

        self.assertEqual(tuple(worn-base for worn,base in zip(attributes(after),attributes(before))), (18,80,90,75))
        self.send(game,807,0,iid)
        self.assertEqual(attributes(progression.snapshot()),attributes(before))

    def test_equipment_evaluation_uses_the_same_score_in_tooltips_and_active_total(self):
        from time import time
        from native946_refinement import save

        weapon = self.weapon()
        game = self.game()

        def tooltip(item):
            payload = self.send(game, 806, item, 0)
            self.assertEqual(int.from_bytes(payload[1:3], 'little'), 791)
            reader = MessageReader(payload[3:])
            self.assertEqual(reader.integer(), item)
            description = reader.string().decode('gb18030')
            self.assertEqual(reader.integer(), 0)  # No native item record ID.
            return description, reader.integer()

        def total(packets):
            for payload in packets:
                if int.from_bytes(payload[1:3], 'little') == 695:
                    reader = MessageReader(payload[3:])
                    if reader.integer() == 72:
                        result = reader.integer()
                        reader.finish()
                        return result
            self.fail('Missing EquipScore in the native attribute sync')

        description, weapon_score = tooltip(weapon)
        self.assertIn('【耐久】100/100', description)
        self.assertIn('本机计分', description)
        self.assertEqual(weapon_score, 1105)
        self.assertEqual(tooltip(1)[1], 0)  # A medicine has no equipment score.
        self.assertEqual(total(game.progression.attribute_packets()), 0)
        self.assertEqual(total(self.send(game, 807, 0, weapon)), weapon_score)
        self.assertEqual(total(self.send(game, 807, 0, 5)), weapon_score + tooltip(5)[1])
        self.assertEqual(total(self.send(game, 796, 3)), 0)
        self.assertEqual(total(self.send(game, 796, 1)), weapon_score + tooltip(5)[1])

        with game.progression.transaction() as db:
            db.execute('UPDATE native_equipment SET durability=17 WHERE item_id=?', (weapon,))
            db.execute('INSERT INTO native_gear_sockets(item_id,position,category,gem,property,amount) '
                       'VALUES(?,1,\'ordinary\',1,\'最大气血\',40)', (weapon,))
            save(db, game.progression.cid, weapon, dict(traits=[('DudsAttAll', 12, 1)], expires=0,
                                                        lucky=0, attempts=0, pending={}))
        description, enhanced = tooltip(weapon)
        self.assertIn('【耐久】17/100', description)
        self.assertEqual(enhanced, weapon_score + 10)

        cold = Progression(self.path, 'owner946', self.p.cid)
        self.assertEqual(total(cold.attribute_packets()), enhanced + tooltip(5)[1])
        with game.progression.transaction() as db:
            db.execute('UPDATE native_refinement SET expires=? WHERE item_id=?', (int(time()) - 1, weapon))
        self.assertEqual(tooltip(weapon)[1], weapon_score + 4)
        self.assertEqual(total(self.send(game, 807, 0, weapon)), tooltip(5)[1])

    def test_native_backpack_refresh_and_equipment_page_reply(self):
        g=self.game();self.assertIsInstance(self.send(g,801,8),tuple)
        result=self.send(g,796,3);reply=next(p for p in result if int.from_bytes(p[1:3],'little')==777)
        r=MessageReader(reply[3:]);self.assertEqual((r.integer(),r.integer()),(3,3));r.finish()

if __name__=='__main__':unittest.main()
