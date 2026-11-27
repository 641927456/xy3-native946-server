"""Child aura effects through the native shop and raising messages."""
from time import time
import unittest

from native946_child_halos import RULES, active_halo
from native946_codec import number
from native946_cosmetics import SALE_GOODS, Cosmetics
from native946_login import MessageReader
from native946_role_society import baby, save_baby
import test_native946_mall_uses as fixtures
from test_native946_npc_services import mid


class ChildHaloTests(unittest.TestCase):
    setUp = fixtures.MallUseTests.setUp
    add = fixtures.MallUseTests.add
    add_child = fixtures.MallUseTests.add_child
    rows = fixtures.MallUseTests.rows
    send = fixtures.MallUseTests.send

    def halo(self, child):
        p = self.game.progression
        with p.transaction(write=False) as db:
            return active_halo(baby(db, p.cid, child))

    def test_all_sold_auras_grant_the_correct_effect_and_duration(self):
        child = self.add_child(401)
        p = self.game.progression
        from native946_cosmetics import DATA
        from native946_mall_checkout import eligible
        from native946_mall_uses import DATA as USES

        sold = SALE_GOODS.keys() & RULES.keys()
        self.assertEqual(len(sold), 39)
        for key in sorted(sold):
            with self.subTest(kind=key):
                kind = int(key)
                item = self.add(kind)
                select = self.send(807, 0, item['id'])
                self.assertEqual(mid(select), 3054)
                self.assertIn(2456, [mid(packet) for packet in self.send(3075, child, kind)])
                self.assertNotIn(item['id'], {row['id'] for row in self.rows()})
                self.assertTrue(eligible(kind, DATA, USES))
                aura = RULES[key]['aura']
                with p.transaction() as db:
                    state = baby(db, p.cid, child)
                    self.assertEqual(active_halo(state), aura)
                    expiry = state['halo_licenses'][str(aura)]
                    self.assertLessEqual(abs(expiry-int(time())-15*86400), 2) if DATA['items'][key]['days'] else self.assertEqual(expiry, 0)
                    state.update(halo=0, halo_licenses={})
                    save_baby(db, p.cid, state)

    def test_selection_keeps_exact_item_and_expired_aura_cannot_be_worn(self):
        one, two = self.add_child(401), self.add_child(402)
        p = self.game.progression
        old = self.add(55471)
        with p.transaction() as db:
            Cosmetics(self.game)._add(db, 55471)
        item = next(row for row in self.rows() if row['type']==55471 and row['id']!=old['id'])
        self.assertEqual(mid(self.send(3075, one, 55471)), 98)
        self.assertEqual(mid(self.send(807, 0, item['id'])), 3054)
        self.assertEqual(mid(self.send(3075, 999999, 55471)), 98)
        self.assertIn(2456, [mid(packet) for packet in self.send(3075, one, 55471)])
        self.assertEqual({row['id'] for row in self.rows() if row['type']==55471}, {old['id']})
        self.assertEqual(self.halo(one), 1000)
        self.assertEqual(self.halo(two), 0)
        self.assertEqual(mid(self.send(2458, two, 1000)), 98)
        self.send(2457, one)
        self.assertEqual(self.halo(one), 0)
        self.send(2458, one, 1000)
        self.assertEqual(self.halo(one), 1000)
        with p.transaction() as db:
            state = baby(db, p.cid, one)
            state['halo_licenses']['1000'] = int(time()) - 1
            save_baby(db, p.cid, state)
        self.assertEqual(self.halo(one), 0)
        self.assertEqual(mid(self.send(2458, one, 1000)), 98)
        reply = self.game.receive(3076, MessageReader(number(one) + bytes([9])))
        payload = next(packet for packet in reply if mid(packet)==2456)
        reader = MessageReader(payload[3:])
        self.assertEqual(reader.integer(), one)
        reader.string()
        reader.string()
        reader.integer()
        self.assertEqual(reader.take(1), b'\1')
        self.assertEqual(reader.integer(), 1000)
        self.assertLess(reader.integer(), time())
        self.assertEqual(reader.take(2), b'\1\0')


if __name__ == '__main__':
    unittest.main()
