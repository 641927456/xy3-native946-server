import unittest

from native946_codec import number, packet, text_notice
from native946_login import MessageReader, string_field
from native946_mail import initial_packets
from native946_trade import OFFER_FIELDS
import test_native946_wilderness_party as fixtures
from test_native946_wilderness_party import mid


class SocialExchangeTests(unittest.TestCase):
    setUp = fixtures.WildernessPartyTests.setUp
    call = fixtures.WildernessPartyTests.call

    def cash_and_medicine(self, game):
        cid = game.character['id']
        with game.progression.transaction(write=False) as db:
            cash = db.execute('SELECT gold FROM characters WHERE id=?', (cid,)).fetchone()[0]
            items = db.execute('SELECT id,amount FROM inventory WHERE character_id=? AND type=4000', (cid,)).fetchall()
        return cash, {iid: amount for iid, amount in items}

    def offer(self, game, cash, items=()):
        data = packet(2065, OFFER_FIELDS, [cash, list(items), []])[3:]
        return self.call(game, 2065, tail=data)

    def open_gift(self):
        target = self.b.character['id']
        replies = self.call(self.a, 630, target)
        panel = next(p for p in replies if mid(p) == 628)
        reader = MessageReader(panel[3:])
        self.assertEqual([reader.integer() for _ in range(5)], [target, 1, 0, 1, 3])
        token = reader.integer()
        self.assertEqual(reader.string().decode('gb18030'), '给予')
        reader.finish()
        return target, token

    def gift(self, target, token, cash, goods):
        wire = packet(629, ['number', 'number', ('array', 'pair_number'), 'number'],
                      [target, cash, [dict(key=iid, value=amount) for iid, amount in goods], token])
        return self.call(self.a, 629, tail=wire[3:])

    def test_gift_updates_both_saves_once_with_a_native_confirmation(self):
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=20 WHERE id=?', (self.a.character['id'],))
        target, token = self.open_gift()
        before_left, left_goods = self.cash_and_medicine(self.a)
        before_right, right_goods = self.cash_and_medicine(self.b)
        iid = next(iter(left_goods))
        self.assertIn(98, map(mid, self.gift(target, token, 7, [(iid, 2)])))
        sender, items = self.cash_and_medicine(self.a)
        receiver, gained = self.cash_and_medicine(self.b)
        self.assertEqual((sender, items[iid]), (before_left - 7, left_goods[iid] - 2))
        self.assertEqual((receiver, sum(gained.values())), (before_right + 7, sum(right_goods.values()) + 2))
        self.assertIn(98, map(mid, self.svc.drain(self.b)))
        self.assertEqual(list(map(mid, self.gift(target, token, 7, [(iid, 2)]))), [98])
        self.assertEqual(self.cash_and_medicine(self.a), (sender, items))

    def test_gift_cancel_block_and_invalid_amount_leave_both_saves_unchanged(self):
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=20 WHERE id=?', (self.a.character['id'],))
        before = [self.cash_and_medicine(game) for game in (self.a, self.b)]
        iid, amount = next(iter(before[0][1].items()))
        target, token = self.open_gift()
        self.assertEqual(list(map(mid, self.gift(target, token, 5, [(iid, amount+1)]))), [98])
        target, token = self.open_gift()
        self.call(self.a, 632)
        self.assertEqual(list(map(mid, self.gift(target, token, 5, [(iid, 1)]))), [98])
        target, token = self.open_gift()
        self.call(self.b, 599, self.a.character['id'], 2, 0)
        self.assertEqual(list(map(mid, self.gift(target, token, 5, [(iid, 1)]))), [98])
        self.assertEqual([self.cash_and_medicine(game) for game in (self.a, self.b)], before)

    def test_gift_full_recipient_bag_rolls_back_cash_and_items(self):
        from native946_mall_bags import pages

        sender, recipient = self.a.character['id'], self.b.character['id']
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=20 WHERE id=?', (sender,))
            db.execute('UPDATE inventory SET type=4001 WHERE character_id=? AND type=4000', (recipient,))
            occupied = set(tuple(row) for row in db.execute('SELECT page,slot FROM inventory WHERE character_id=?',
                                                           (recipient,)))
            db.executemany('INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,4001,?,?,1)',
                           ((recipient, page, slot) for page in pages(db, recipient) for slot in range(1, 25)
                            if (page, slot) not in occupied))
        iid = next(iter(self.cash_and_medicine(self.a)[1]))
        before = [self.cash_and_medicine(game) for game in (self.a, self.b)]
        target, token = self.open_gift()
        reply = self.gift(target, token, 5, [(iid, 1)])
        self.assertIn('背包空间不足', MessageReader(reply[0][1:]).string().decode('gb18030'))
        self.assertEqual([self.cash_and_medicine(game) for game in (self.a, self.b)], before)

    def test_native_accept_give_setting_is_restored_and_rechecked_at_confirmation(self):
        from native946_give import SETTINGS_FIELDS, saved_settings

        target, token = self.open_gift()
        iid = next(iter(self.cash_and_medicine(self.a)[1]))
        values = [0 if name == 'give' else 1 for name in SETTINGS_FIELDS]
        wire = packet(1490, ['number'] * len(values), values)
        self.call(self.b, 1490, tail=wire[3:])
        expected = packet(1318, ['number'] * len(values), values)
        self.assertEqual(saved_settings(self.b), (expected,))
        self.assertEqual(list(map(mid, self.gift(target, token, 0, [(iid, 1)]))), [98])
        self.assertEqual(list(map(mid, self.call(self.a, 630, target))), [98])

    def test_lock_and_second_confirmation_atomically_exchange_cash_and_items(self):
        a, b = self.a.character['id'], self.b.character['id']
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=100 WHERE id=?', (b,))
        before_a, items_a = self.cash_and_medicine(self.a)
        before_b, items_b = self.cash_and_medicine(self.b)
        iid = next(iter(items_a))
        self.assertIn(2058, map(mid, self.call(self.a, 2066, b)))
        self.assertIn(2058, map(mid, self.svc.drain(self.b)))
        # Native trade_item order is id, type, usable, amount. Use literal wire
        # order here so an encoder and decoder sharing the same bug cannot pass.
        offer = number(0) + b'\x01' + b''.join(map(number, (iid, 4000, 0, 2))) + b'\x00'
        self.assertIn(packet(2062, ['number'], [1]), self.call(self.a, 2065, tail=offer))
        visible = next(p for p in self.svc.drain(self.b) if mid(p) == 2061)
        self.assertEqual(visible[3:], offer)
        self.assertIn(packet(2062, ['number'], [0]), self.offer(self.b, 101))
        self.assertIn(packet(2062, ['number'], [1]), self.offer(self.b, 70))
        self.call(self.a, 2064)
        self.call(self.a, 2064)
        self.assertEqual((self.cash_and_medicine(self.a), self.cash_and_medicine(self.b)),
                         ((before_a, items_a), (before_b, items_b)))
        for replies in (self.call(self.b, 2064), self.svc.drain(self.a)):
            self.assertIn(2060, map(mid, replies))
            self.assertEqual(replies.count(text_notice('交易完成。')), 1)
        self.assertEqual(self.cash_and_medicine(self.a), (before_a+70, {iid: items_a[iid]-2}))
        recipient_cash, recipient_items = self.cash_and_medicine(self.b)
        self.assertEqual(recipient_cash, before_b-70)
        self.assertEqual(sum(recipient_items.values()), sum(items_b.values())+2)
        self.call(self.b, 2064)
        self.assertEqual(self.cash_and_medicine(self.b), (recipient_cash, recipient_items))

    def test_stale_offer_cancels_both_and_does_not_transfer_cash(self):
        b = self.b.character['id']
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=50 WHERE id=?', (b,))
        _, goods = self.cash_and_medicine(self.a)
        iid = next(iter(goods))
        self.call(self.a, 2066, b)
        self.offer(self.a, 0, [dict(id=iid, type=4000, amount=2, usable=0)])
        self.offer(self.b, 50)
        self.call(self.a, 2064)
        with self.a.progression.transaction() as db:
            db.execute('UPDATE inventory SET amount=1 WHERE id=?', (iid,))
        result = self.call(self.b, 2064)
        self.assertIn(2059, map(mid, result))
        peer_result = self.svc.drain(self.a)
        self.assertIn(2059, map(mid, peer_result))
        self.assertEqual(peer_result[-1], result[-1])
        self.assertEqual(self.cash_and_medicine(self.b)[0], 50)
        self.assertEqual(self.cash_and_medicine(self.a)[1][iid], 1)

    def test_trade_receiver_setting_is_checked_on_open_and_on_commit(self):
        from native946_give import SETTINGS_FIELDS

        def setting(value):
            values = [value if name == 'trade' else 1 for name in SETTINGS_FIELDS]
            wire = packet(1490, ['number'] * len(values), values)
            self.call(self.b, 1490, tail=wire[3:])

        a, b = self.a.character['id'], self.b.character['id']
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=20 WHERE id=?', (a,))
        setting(0)
        self.assertEqual(list(map(mid, self.call(self.a, 2066, b))), [98])
        self.assertFalse(self.svc.trades)
        setting(1)
        self.call(self.a, 2066, b)
        self.offer(self.a, 3)
        self.offer(self.b, 0)
        self.call(self.a, 2064)
        setting(0)
        self.assertIn(2059, map(mid, self.call(self.b, 2064)))
        self.assertEqual((self.cash_and_medicine(self.a)[0], self.cash_and_medicine(self.b)[0]), (20, 0))

    def test_exact_offer_retry_is_idempotent_and_changed_locked_offer_cancels(self):
        b = self.b.character['id']
        self.call(self.a, 2066, b)
        iid = next(iter(self.cash_and_medicine(self.a)[1]))
        items = [dict(id=iid, type=4000, amount=1, usable=0)]
        self.offer(self.a, 0, items)
        self.svc.drain(self.b)
        self.assertIn(packet(2062, ['number'], [1]), self.offer(self.a, 0, items))
        self.assertEqual(self.svc.drain(self.b), ())
        self.assertIn(2059, map(mid, self.offer(self.a, 0, [dict(items[0], amount=2)])))
        self.assertIn(2059, map(mid, self.svc.drain(self.b)))
        self.assertFalse(self.svc.trades)

    def test_offline_mail_survives_login_then_disappears_from_pending_once_read(self):
        a, b = self.a.character['id'], self.b.character['id']
        self.svc.detach(b)
        self.svc.drain(self.a)
        tail = packet(930, [('array', 'number'), 'string', 'number', 'string'],
                      [[b, b], '这是一条测试留言', 1, ''])[3:]
        self.assertEqual(self.call(self.a, 930, tail=tail), (text_notice('留言已发送给1位角色。'),))
        self.svc.attach(self.b)
        self.assertEqual([mid(p) for p in initial_packets(self.b)], [922, 923, 924])
        replies = self.call(self.b, 929, 1, a)
        self.assertEqual([mid(p) for p in replies], [925, 3563])
        r = MessageReader(replies[0][3:])
        self.assertEqual((r.integer(), r.integer(), r.integer()), (0, a, 1))
        self.assertEqual(r.string().decode('gb18030'), '这是一条测试留言')
        self.assertEqual([mid(p) for p in self.call(self.b, 929, 1, a)], [3563])

    def test_group_mail_opt_out_and_blocked_sender(self):
        a, b = self.a.character['id'], self.b.character['id']
        self.call(self.a, 599, b, 1, 0)
        self.call(self.b, 927, 1)
        group = packet(928, ['number', 'string', 'number'], [1, '组内留言', 1])[3:]
        self.assertIn(98, map(mid, self.call(self.a, 928, tail=group)))
        self.call(self.b, 599, a, 2, 0)
        direct = packet(930, [('array', 'number'), 'string', 'number', 'string'], [[b], '普通留言', 1, ''])[3:]
        self.assertIn(98, map(mid, self.call(self.a, 930, tail=direct)))
        self.assertEqual([mid(p) for p in initial_packets(self.b)], [922, 923])

    def test_guild_application_only_notifies_on_new_application_and_reports_outcome(self):
        from time import time
        from native946_guilds import _create_guild

        a, b = self.a.character['id'], self.b.character['id']
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET level=50 WHERE id=?', (b,))
            _create_guild(db, 1, '验收帮派', '', a, int(time()))
        self.assertIn(text_notice('入帮申请已提交。'), self.call(self.b, 1107, 1, tail=string_field('申请加入')))
        self.assertEqual(self.svc.drain(self.a), (text_notice(f"{self.b.character['name']}申请加入帮派。"),))
        self.assertIn(text_notice('你已提交入帮申请，请等待处理。'), self.call(self.b, 1108, a))
        self.assertEqual(self.svc.drain(self.a), ())
        self.assertIn(text_notice('已拒绝入帮申请。'), self.call(self.a, 1135, 2, b))
        self.assertEqual(self.svc.drain(self.b), (text_notice('验收帮派已拒绝你的入帮申请。'),))
        self.call(self.b, 1108, a)
        self.assertIn(text_notice('已批准入帮申请。'), self.call(self.a, 1135, 1, b))
        self.assertEqual(self.svc.drain(self.b), (text_notice('验收帮派已批准你的入帮申请。'),))
