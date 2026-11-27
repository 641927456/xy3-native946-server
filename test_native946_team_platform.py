import unittest

from local_accounts import LocalAccounts
from native946_codec import packet
from native946_gameplay import NativeGameplay
from native946_login import MessageReader
import test_native946_wilderness_party as fixtures
from test_native946_wilderness_party import mid


class TeamPlatformTests(unittest.TestCase):
    setUp = fixtures.WildernessPartyTests.setUp
    call = fixtures.WildernessPartyTests.call
    team = fixtures.WildernessPartyTests.team

    def operate(self, game, op, *args):
        payload = packet(4422, ['number', ('array', 'number')], [op, args])
        return self.call(game, 4422, tail=payload[3:])

    def add_player(self, index):
        owner = f'match{index}'
        LocalAccounts(self.path).register(dict(username=owner, password='fixture-password'))
        game = NativeGameplay(self.characters.create(owner, f'集结测试{index}', 702), self.characters, lambda: ())
        self.games.append(game)

    def eligible(self):
        with self.a.progression.transaction() as db:
            db.execute('UPDATE characters SET level=100')

    def test_team_and_solo_queue_fills_exactly_five_and_clears_status(self):
        for index in range(3, 6):
            self.add_player(index)
        self.eligible()
        self.team()
        leader = self.a.character['id']
        self.assertIn(4420, list(map(mid, self.operate(self.a, 5))))
        self.assertIn(98, list(map(mid, self.operate(self.a, 15, 1, 5))))
        self.assertIn(4418, list(map(mid, self.operate(self.a, 11))))
        for game in self.games[2:-1]:
            reply = self.operate(game, 10, 0, 5)
            self.assertIn(4418, list(map(mid, reply)))
            self.assertIsNone(self.svc.team(game.character['id']))
        reply = self.operate(self.games[-1], 10, 1, 5)
        self.assertIn(packet(4417, ['string'], ['百年后的来客']), reply)
        team = self.svc.team(leader)
        self.assertEqual(team['leader_id'], leader)
        self.assertEqual({row['character_id'] for row in team['members']}, {g.character['id'] for g in self.games})
        self.assertFalse(self.svc.match_entries)
        self.assertIn(packet(4418, [('array', 'number'), 'number', 'number'], [[], 0, 0]),
                      self.operate(self.a, 3))

    def test_queue_select_cancel_and_disconnect(self):
        self.assertEqual([mid(p) for p in self.operate(self.a, 1)], [4421, 4418])
        self.assertIn(98, list(map(mid, self.operate(self.a, 10, 0, 5))))
        self.eligible()
        self.assertIn(98, list(map(mid, self.operate(self.a, 10, 0, 5, 5))))
        self.operate(self.a, 10, 0, 5)
        status = self.operate(self.a, 3)[-1]
        reader = MessageReader(status[3:])
        self.assertEqual(reader.take(1), b'\x01')
        self.assertEqual((reader.integer(), reader.integer()), (5, 0))
        self.assertGreater(reader.integer(), 0)
        reader.finish()
        self.operate(self.a, 12)
        self.assertFalse(self.svc.match_entries)
        self.operate(self.a, 10, 0, 5)
        self.svc.detach(self.a.character['id'])
        self.assertFalse(self.svc.match_entries)

    def test_retired_lottery_replies_without_creating_team_or_drawing(self):
        before = self.a.progression.snapshot()
        for mid_, args in ((3836, (3,)), (3837, (1,)), (3855, (self.b.character['id'],)), (3861, (1,))):
            replies = self.call(self.a, mid_, *args)
            self.assertEqual(list(map(mid, replies)), [98])
            self.assertIn('活动已结束', MessageReader(replies[0][1:]).string().decode('gb18030'))
        self.assertEqual(self.a.progression.snapshot(), before)
        self.assertIsNone(self.svc.team(self.a.character['id']))

    def test_existing_team_clears_queue_when_member_leaves(self):
        from native946_team_platform import Platform

        self.team()
        self.eligible()
        self.operate(self.a, 15, 5)
        self.operate(self.a, 11)
        self.svc.drain(self.b)
        self.call(self.b, 2051)
        Platform(self.svc).match()
        self.assertFalse(self.svc.match_entries)
        empty = packet(4418, [('array', 'number'), 'number', 'number'], [[], 0, 0])
        self.assertIn(empty, self.svc.drain(self.a))
        self.assertIn(empty, self.svc.drain(self.b))
