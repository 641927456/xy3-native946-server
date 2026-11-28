"""Native gold-body practice availability, replies, and settlement."""
import unittest

import test_native946_progression as fixtures
from native946_codec import number, text_notice
from native946_game_login import message_id
from native946_gameplay import NativeGameplay
from native946_login import MessageReader
from native946_role_growth import gold
from native946_role_systems import RULES, save


class GoldBodyTests(unittest.TestCase):
    setUp = fixtures.ProgressionTests.setUp

    def prepare(self, level, rebirth, ranks):
        with self.p.transaction() as db:
            db.execute('UPDATE characters SET level=?,gold=1000000,experience=10000000 WHERE id=?',
                       (level, self.p.cid))
            db.execute('UPDATE cultivation_state SET rebirth=? WHERE character_id=?',
                       (rebirth, self.p.cid))
            db.execute('UPDATE progression SET cor=?,mag=?,strength=?,speed=? WHERE character_id=?',
                       (level, level, level, level, self.p.cid))
            body = gold(db, self.p.cid)
            body.update(ranks=ranks, exp=1000000)
            save(db, self.p.cid, 'goldbody', body)
        self.game = NativeGameplay(self.character, self.characters, lambda: ())

    def send(self, mid, *values):
        return self.game.receive(mid, MessageReader(b''.join(number(value) for value in values)))

    def state(self):
        with self.p.transaction(write=False) as db:
            return gold(db, self.p.cid)

    def rank_list(self, messages):
        payload = next(message for message in messages if message.startswith(message_id(3177)))
        reader = MessageReader(payload[3:])
        ranks = [reader.integer() for _ in range(reader.take(1)[0])]
        self.assertEqual(reader.integer(), 0)
        reader.finish()
        return ranks

    def test_unqualified_role_sees_requirement_and_cannot_spend(self):
        self.prepare(level=30, rebirth=1, ranks=[0] * 10)
        before = self.p.snapshot(), self.state()
        replies = self.send(3180)
        self.assertEqual(self.rank_list(replies), [0] * 10)
        notices = [message for message in replies if message.startswith(message_id(98))]
        self.assertEqual(notices, [text_notice('本机重塑金身需达到二转101级才能修炼。')])
        self.assertEqual(self.send(3181, 1), text_notice('金身修炼需二转101级。'))
        self.assertEqual((self.p.snapshot(), self.state()), before)

    def test_first_realm_can_open_and_practice_at_zero_without_granting_a_rank(self):
        self.prepare(level=101, rebirth=2, ranks=[0] * 10)
        before = self.p.snapshot(), self.state()
        self.assertEqual(self.rank_list(self.send(3180)), [-1] + [0] * 9)
        preview = self.send(3182, 1)
        self.assertEqual(self.rank_list(preview), [-1] + [0] * 9)
        details = MessageReader(next(message for message in preview
                                     if message.startswith(message_id(3178)))[3:])
        self.assertEqual((details.integer(), details.integer()), (1, 0))
        self.assertEqual((self.p.snapshot(), self.state()), before)
        self.assertEqual(self.send(3181, 2), text_notice('请先修满上一境。'))
        self.assertEqual((self.p.snapshot(), self.state()), before)

        self.assertEqual(self.rank_list(self.send(3181, 1)), [1] + [0] * 9)
        current = self.p.snapshot()
        self.assertEqual(current['gold'], before[0]['gold'] - RULES['goldbody_practice_cash'])
        self.assertEqual(current['experience'], before[0]['experience'] - RULES['goldbody_user_exp'])
        self.assertEqual(self.state()['exp'], before[1]['exp'] - RULES['goldbody_practice_exp'])
        self.assertEqual(self.state()['ranks'], [1] + [0] * 9)

    def test_finishing_first_realm_opens_next_realm_and_keeps_later_realms_locked(self):
        self.prepare(level=101, rebirth=2, ranks=[9] + [0] * 9)
        self.assertEqual(self.rank_list(self.send(3180)), [9] + [0] * 9)
        self.assertEqual(self.rank_list(self.send(3181, 1)), [10, -1] + [0] * 8)
        self.assertEqual(self.state()['ranks'], [10] + [0] * 9)
        self.assertEqual(self.rank_list(self.send(3180)), [10, -1] + [0] * 8)


if __name__ == '__main__':
    unittest.main()
