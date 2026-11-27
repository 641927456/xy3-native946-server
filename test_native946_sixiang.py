from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_codec import number
from native946_gameplay import NativeGameplay
from native946_login import MessageReader
from native946_sixiang import EXPERIENCE_ITEM, SOUL_ATTR


class SixiangTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / 'sixiang.sqlite3'
        LocalAccounts(path).register(dict(username='sixiang_owner', password='fixture-password'))
        self.characters = Characters(path)
        self.character = self.characters.create('sixiang_owner', '四象测试', 702)
        self.game = NativeGameplay(self.character, self.characters, lambda: ())
        self.sid = self.game.summons.claim_starter()

    def call(self, mid):
        return self.game.receive(mid, MessageReader(number(self.sid)))

    def test_default_power_and_base_detail_use_native_stage_zero(self):
        current = SOUL_ATTR[0][0]
        following = SOUL_ATTR[0][1]
        base, organization = self.call(3148)
        reader = MessageReader(base[3:])
        self.assertEqual((reader.integer(), reader.integer(), reader.integer()),
                         (self.sid, current['TotalNeedExp'], 0))
        self.assertEqual(reader.string().decode('gb18030'), current['Tip1'])
        self.assertEqual((reader.integer(), reader.integer()), (0, 0))
        self.assertEqual([reader.integer() for _ in range(4)], [0, 0, 0, 0])
        reader.finish()
        reader = MessageReader(organization[3:])
        self.assertEqual([reader.integer() for _ in range(6)],
                         [0, 0, 0, current['TotalNeedExp'], following['TotalNeedExp'], EXPERIENCE_ITEM])
        reader.finish()
        self.assertEqual(self.call(3149), organization)

        power = self.call(3150)
        reader = MessageReader(power[3:])
        self.assertEqual((reader.integer(), reader.integer()), (self.sid, 0))
        self.assertEqual([reader.integer() for _ in range(4)], [0, 0, 0, 0])
        self.assertEqual([reader.string().decode('gb18030') for _ in range(4)],
                         [current['Tip1'], current['Tip2'], current['Tip3'], current['Tip4']])
        self.assertEqual([reader.string().decode('gb18030') for _ in range(4)],
                         [following['Tip1'], following['Tip2'], following['Tip3'], following['Tip4']])
        self.assertEqual(reader.integer(), 0)
        reader.finish()

    def test_saved_realm_stage_and_forge_count_drive_native_descriptions(self):
        with self.game.progression.transaction() as db:
            db.execute('INSERT INTO native_sixiang VALUES(?,?,?,?,?)', (self.sid, 2, 1, 500, 3))
        current = SOUL_ATTR[2][1]
        following = SOUL_ATTR[2][2]
        power = self.call(3150)
        reader = MessageReader(power[3:])
        self.assertEqual((reader.integer(), reader.integer()), (self.sid, 2))
        self.assertEqual([reader.integer() for _ in range(4)], [1, 1, 1, 1])
        self.assertEqual([reader.string().decode('gb18030') for _ in range(4)],
                         [current['Tip1'], current['Tip2'], current['Tip3'], current['Tip4']])
        self.assertEqual([reader.string().decode('gb18030') for _ in range(4)],
                         [following['Tip1'], following['Tip2'], following['Tip3'], following['Tip4']])
        self.assertEqual(reader.integer(), 3)
        reader.finish()
        with self.game.progression.transaction(write=False) as db:
            self.assertEqual(tuple(db.execute('SELECT realm,stage,experience,forge_count FROM native_sixiang WHERE summon_id=?',
                                                (self.sid,)).fetchone()), (2, 1, 500, 3))


if __name__ == '__main__':
    unittest.main()
