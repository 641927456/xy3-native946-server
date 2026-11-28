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

    def offer(self, game, cash, items=(), guardians=()):
        data = packet(2065, OFFER_FIELDS, [cash, list(items), list(guardians)])[3:]
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

    def test_realm_gem_trade_preserves_the_instance_and_rolls_back_when_blocked(self):
        from native946_jing_gems import TYPES, level, put
        from native946_mall_bags import pages

        a, b = self.a.character['id'], self.b.character['id']
        kind = TYPES[0]
        with self.a.progression.transaction() as db:
            gem = put(self.a, db, kind, 11)
            other = put(self.b, db, kind, 3)
            for cid in (a, b):
                for page in pages(db, cid):
                    db.executemany('INSERT OR IGNORE INTO inventory(character_id,type,page,slot,amount) VALUES(?,4001,?,?,1)',
                                   ((cid, page, slot) for slot in range(1, 25)))

        original = dict(id=gem, type=kind, amount=1, usable=0)
        counter = dict(id=other, type=kind, amount=1, usable=0)
        approved = packet(2062, ['number'], [1])
        self.assertIn(2058, map(mid, self.call(self.a, 2066, b)))
        self.assertIn(approved, self.offer(self.a, 0, [original]))
        self.assertTrue(any('11级'.encode('gb18030') in reply for reply in self.svc.drain(self.b)))
        self.assertIn(approved, self.offer(self.b, 0))
        self.call(self.a, 2064)
        self.assertIn(2059, map(mid, self.call(self.b, 2064)))
        with self.a.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT character_id FROM inventory WHERE id=?', (gem,)).fetchone()[0], a)
            self.assertEqual(level(db, gem), 11)

        self.call(self.a, 2066, b)
        self.assertIn(approved, self.offer(self.a, 0, [original]))
        self.assertIn(approved, self.offer(self.b, 0, [counter]))
        self.call(self.a, 2064)
        self.assertIn(2060, map(mid, self.call(self.b, 2064)))
        with self.a.progression.transaction(write=False) as db:
            for iid, cid, grade in ((gem, b, 11), (other, a, 3)):
                row = db.execute('SELECT character_id,slot,amount FROM inventory WHERE id=?', (iid,)).fetchone()
                self.assertEqual((row['character_id'], row['amount'], level(db, iid)), (cid, 1, grade))
                self.assertIn(row['slot'], range(1, 25))
        self.assertNotIn(2060, map(mid, self.call(self.b, 2064)))

    def test_guardian_trade_preserves_identity_and_rechecks_locks(self):
        a, b = self.a.character['id'], self.b.character['id']
        sid = self.a.summons.acquire(3197, 'capture:trade-fixture')
        self.a.summons.status(sid, 2)
        with self.a.progression.transaction() as db:
            db.execute('UPDATE summons SET amity=850,experience=23 WHERE id=?', (sid,))
            db.execute('INSERT INTO cosmetic_guardian_appearance(summon_id,character_id,color1) VALUES(?,?,32)', (sid,a))
        pet = self.a.summons.get(sid)
        self.assertTrue(self.a.summons.list_record(pet)['CanTrade'])
        self.assertNotIn(684, map(mid, self.call(self.b, 727, sid)))
        for blocked in (True, False):
            self.call(self.a, 2066, b)
            self.svc.drain(self.b)
            if not blocked:
                self.offer(self.b, 0)
                self.svc.trades[b].pop('guardian_offers')  # A cash-only offer locked before hot reload.
            quote = [dict(id=sid, name='客户端假名字')]
            self.assertIn(packet(2062, ['number'], [1]), self.offer(self.a, 0, guardians=quote))
            visible = packet(2061, OFFER_FIELDS, [0, [], [dict(id=sid, name=pet['name'])]])
            self.assertIn(visible, self.svc.drain(self.b))
            self.assertIn(684, map(mid, self.call(self.b, 727, sid)))
            if blocked:
                self.offer(self.b, 0)
            self.call(self.a, 2064)
            if blocked:
                with self.a.progression.transaction() as db:
                    db.execute("INSERT INTO native_object_locks VALUES(?,'summon',?,1,0)", (a,sid))
            result = self.call(self.b, 2064)
            self.assertIn(2059 if blocked else 2060, map(mid, result))
            with self.a.progression.transaction() as db:
                saved = db.execute('SELECT character_id,amity,experience FROM summons WHERE id=?', (sid,)).fetchone()
                self.assertEqual(tuple(saved), (a if blocked else b, 850, 23))
                db.execute("DELETE FROM native_object_locks WHERE kind='summon' AND object_id=?", (sid,))
        self.assertNotIn(2060, map(mid, self.call(self.b, 2064)))
        self.assertEqual(self.b.summons.get(sid)['color1'], 32)
        self.assertTrue(self.b.summons.list_record(self.b.summons.get(sid))['Wildness'])
        with self.assertRaises(ValueError):
            self.a.summons.acquire(3197, 'capture:trade-fixture')
        with self.a.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM native_guardian_trade_transfers').fetchone()[0], 1)

    def test_guardian_trade_checks_net_slots_before_cash_or_pet_moves(self):
        from unittest.mock import patch

        a, b = self.a.character['id'], self.b.character['id']
        left = self.a.summons.acquire(3197, 'fixture:left')
        right = self.b.summons.acquire(3197, 'fixture:right')
        self.a.summons.status(left, 2)
        self.b.summons.status(right, 2)
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET gold=100 WHERE id IN (?,?)', (a,b))
        with patch('native946_mall_bags.summon_limit', return_value=1):
            for swap in (False, True):
                self.call(self.a, 2066, b)
                self.offer(self.a, 25, guardians=[dict(id=left)])
                self.offer(self.b, 0, guardians=[dict(id=right)] if swap else [])
                self.call(self.a, 2064)
                self.assertIn(2060 if swap else 2059, map(mid, self.call(self.b, 2064)))
                with self.a.progression.transaction(write=False) as db:
                    owners = [db.execute('SELECT character_id FROM summons WHERE id=?',(sid,)).fetchone()[0]
                              for sid in (left,right)]
                    balances = [db.execute('SELECT gold FROM characters WHERE id=?',(cid,)).fetchone()[0]
                                for cid in (a,b)]
                self.assertEqual(owners, [b,a] if swap else [a,b])
                self.assertEqual(balances, [75,125] if swap else [100,100])

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

    def test_guild_officer_rights_and_public_namecard(self):
        from time import time
        from local_accounts import LocalAccounts
        from native946_guilds import _create_guild
        from native946_gameplay import NativeGameplay

        a, b = self.a.character['id'], self.b.character['id']
        accounts = LocalAccounts(self.path)
        accounts.register(dict(username='applicant', password='fixture-password'))
        char = self.characters.create('applicant', '帮派申请', 702)
        recruit = NativeGameplay(char, self.characters, lambda: ())
        with self.a.progression.transaction() as db:
            _create_guild(db, 1, '同心帮', '同心协力', a, int(time()))
            db.execute('INSERT INTO native_guild_members(character_id,guild_id,joined) VALUES(?,?,?)', (b,1,int(time())))
            db.execute('UPDATE characters SET level=50 WHERE id=?', (char['id'],))
        self.assertIn(4960, map(mid, self.call(recruit, 4961, 1)))
        self.assertIn(4962, map(mid, self.call(recruit, 4971, 1, 0)))
        self.call(self.a, 1109, b, 21, 6)
        self.assertIn(1067, map(mid, self.call(self.a, 1112)))
        self.svc.drain(self.b)
        self.call(recruit, 1107, 1, tail=string_field('申请入帮'))
        self.assertIn(text_notice('帮派申请申请加入帮派。'), self.svc.drain(self.b))
        self.assertIn(1084, map(mid, self.call(self.b, 1131, 20)))
        self.assertIn(1084, map(mid, self.call(self.b, 1135, 1, char['id'])))
        self.assertNotIn(1072, map(mid, self.call(self.b, 1135, 3, char['id'])))
        self.call(self.a, 1109, b, 21, 3)
        wire = packet(1111, ['number', ('array', 'number')], [b, [3]])
        self.assertIn(1066, map(mid, self.call(self.a, 1111, tail=wire[3:])))
        self.assertNotIn(1072, map(mid, self.call(self.b, 1135, 3, char['id'])))
        wire = packet(1111, ['number', ('array', 'number')], [b, [3,4]])
        self.assertNotIn(1066, map(mid, self.call(recruit, 1111, tail=wire[3:])))
        self.assertIn(1066, map(mid, self.call(self.a, 1111, tail=wire[3:])))
        self.assertIn(1072, map(mid, self.call(self.b, 1135, 3, char['id'])))

    def test_guild_history_tenet_and_leader_permissions_use_native_entrypoints(self):
        from time import time
        from native946_guilds import _create_guild

        a, b = self.a.character['id'], self.b.character['id']
        with self.a.progression.transaction() as db:
            _create_guild(db, 1, '验收帮派', '旧宗旨', a, int(time()))
        self.assertEqual(list(map(mid, self.call(self.b, 4981))), [98])
        with self.a.progression.transaction() as db:
            db.execute('INSERT INTO native_guild_members(character_id,guild_id,joined) VALUES(?,1,?)',
                       (b, int(time())))
        self.assertEqual(self.call(self.b, 4981), (packet(4970, ['string'], ['{}']),))
        self.assertEqual(list(map(mid, self.call(self.b, 4972))), [98])

        def editor(mid_request):
            self.assertIn(1023, map(mid, self.call(self.a, mid_request)))
            return self.a._guild_text_session['token']

        def submit(token, contents):
            payload = bytes([1]) + string_field(contents) + number(token)
            return self.call(self.a, 1033, tail=payload)

        token = editor(4972)
        self.assertIn(4970, map(mid, submit(token, '周末一起组队')))
        self.assertNotIn(4970, map(mid, submit(token, '重复提交')))
        history = MessageReader(self.call(self.b, 4981)[0][3:])
        self.assertIn('周末一起组队', history.string().decode('gb18030'))
        history.finish()
        self.assertIn(3506, map(mid, submit(editor(4973), '新的帮派宗旨')))
        with self.a.progression.transaction(write=False) as db:
            self.assertEqual(db.execute('SELECT tenet FROM native_guilds WHERE id=1').fetchone()[0], '新的帮派宗旨')
            announcement = db.execute('SELECT id FROM native_guild_announcements WHERE guild_id=1').fetchone()[0]
        self.assertNotIn(4970, map(mid, self.call(self.b, 4974, announcement)))
        self.assertIn(packet(4970, ['string'], ['{}']), self.call(self.a, 4974, announcement))
