"""Local spirit-box rewards through the ordinary native item-use request."""

from collections import Counter
import unittest
from unittest.mock import patch

from native946_login import MessageReader
from native946_mall_event_boxes import pick
from native946_role_artifacts import BOX_DATA, MODERN_ARTIFACTS, REWARD_WEIGHTS, STORAGE_PAGE, STORAGE_GRIDS
from native946_role_systems import state
import test_native946_battle_actions as actions
from test_native946_skillbooks import decode_record


class ArtifactBoxTests(unittest.TestCase):
    setUp = actions.BattleActionTests.setUp
    call = actions.BattleActionTests.call

    def put_box(self, kind, quantity=1):
        with self.p.transaction() as db:
            page, slot = self.p.free_bag_slot(self.p._snapshot(db))
            return db.execute('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,?)',
                              (self.p.cid, kind, page, slot, quantity)).lastrowid

    def contents(self):
        items = self.p.snapshot()['items']
        return {item['id']: item for item in items}

    def test_local_pool_covers_all_fifteen_tickets_once(self):
        prizes = Counter()
        for ticket in range(15):
            with patch('native946_mall_event_boxes.randbelow', return_value=ticket) as random:
                prizes[pick(REWARD_WEIGHTS)] += 1
                random.assert_called_once_with(15)
        self.assertEqual(prizes, dict.fromkeys((*range(46825, 46838), 47800, 47801), 1))

    def test_three_native_boxes_save_generation_traits_and_result(self):
        for box, prize, generation, count in ((46839, 46825, 2, 0), (46840, 47801, 3, 2),
                                               (46841, 47800, 4, 3)):
            with self.subTest(box=box):
                box_id = self.put_box(box, quantity=2)
                tooltip = MessageReader(self.p.item_description(box_id)[3:])
                self.assertEqual(tooltip.integer(), box_id)
                self.assertIn('6.6667%', tooltip.string().decode('gb18030'))
                before = self.contents()
                self.p.item_packets()
                with patch('native946_mall_event_boxes.pick', return_value=prize) as roll:
                    replies = self.call(807, self.p.cid, box_id)
                    roll.assert_called_once_with(REWARD_WEIGHTS)
                contents = self.contents()
                self.assertEqual(contents[box_id]['amount'], 1)
                reward_id, = contents.keys() - before.keys()
                reward = contents[reward_id]
                self.assertEqual((reward['type'], reward['page'], reward['amount']), (prize, STORAGE_PAGE, 1))
                self.assertIn(786, [actions.mid(packet) for packet in replies])
                self.assertIn(789, [actions.mid(packet) for packet in replies])
                with self.p.transaction(write=False) as db:
                    progress = state(db, self.p.cid, 'artifacts')['item_progress'][str(reward_id)]
                self.assertEqual((progress['grade'], progress['generation']), (1, generation))
                self.assertEqual(len(set(progress['refinements'])), count)
                self.assertTrue(set(progress['refinements']) <= set(MODERN_ARTIFACTS[prize]['refine']))
                detail = next(packet for packet in self.call(313, reward_id) if actions.mid(packet) == 3007)
                reader = MessageReader(detail[3:])
                self.assertEqual(reader.integer(), reward_id)
                fields = decode_record(reader, 'fabao_new_info')
                reader.finish()
                self.assertEqual(fields['level'], generation)
                labels = [line.split(':')[0] for line in fields['jl_trait'].split('#r') if line]
                self.assertEqual(labels, [BOX_DATA['refinements'][str(trait)] for trait in progress['refinements']])
                if prize == 47800:
                    self.call(803, STORAGE_PAGE, reward['slot'], 34, 4)
                    self.assertEqual(self.p.snapshot()['skills'][4709], 1)

    def test_rejected_open_keeps_the_stack_and_never_rolls(self):
        box_id = self.put_box(46841, quantity=2)
        cid = self.p.cid
        with self.p.transaction() as db:
            db.execute("INSERT INTO native_object_locks VALUES(?,'item',?,1,0)", (cid, box_id))
        with patch('native946_mall_event_boxes.pick') as roll:
            self.assertEqual(actions.mid(self.call(807, cid, box_id)), 98)
            self.assertEqual(self.contents()[box_id]['amount'], 2)
            with self.p.transaction() as db:
                db.execute('DELETE FROM native_object_locks WHERE object_id=?', (box_id,))
                db.executemany('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,46825,?,?,1)',
                               [(cid, STORAGE_PAGE, slot) for slot in STORAGE_GRIDS])
            self.assertEqual(actions.mid(self.call(807, cid, box_id)), 98)
            self.assertEqual(self.contents()[box_id]['amount'], 2)
            roll.assert_not_called()

    def test_storage_write_failure_rolls_back_box_and_prize(self):
        box_id = self.put_box(46839)
        before = self.contents()
        with patch('native946_role_artifacts.save', side_effect=ValueError('fixture failure')):
            self.assertEqual(actions.mid(self.call(807, self.p.cid, box_id)), 98)
        self.assertEqual(self.contents(), before)


if __name__ == '__main__':
    unittest.main()
