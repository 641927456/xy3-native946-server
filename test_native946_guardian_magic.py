import unittest

from native946_guardian_magic import combat_skills, prepared
from native946_login import MessageReader
from test_native946_skillbooks import BasicBookTests


class GuardianMagicTests(unittest.TestCase):
    setUp = BasicBookTests.setUp
    send = BasicBookTests.send

    def test_innate_skill_is_listed_and_resolves_through_native_battle_request(self):
        sid = self.s.acquire(3374, "innate:active")
        with self.p.transaction() as db:
            db.execute("UPDATE summons SET fight=0 WHERE character_id=?", (self.p.cid,))
            db.execute("UPDATE summons SET fight=1 WHERE id=?", (sid,))
        pet = self.s.get(sid)
        skill = next(row for row in combat_skills(pet) if row["skill_id"] == 4906)
        self.assertIn("5个目标", skill["des"])
        self.assertEqual(prepared(pet, 4906)["targets"], 5)
        self.g.begin_battle()
        before = self.g.battle["enemy_hp"]
        mana = self.g.battle["pet"]["mp"]
        self.send(2178, 1)
        replies = self.send(2183, 9, 4906, 900002)
        self.assertIn(2157, [int.from_bytes(row[1:3], "little") if row[0] == 0 else row[0]
                             for row in replies])
        cast = next(row for row in replies if int.from_bytes(row[1:3], "little") == 2157)
        reader = MessageReader(cast[3:])
        reader.integer()
        reader.integer()
        for _ in range(reader.take(1)[0]):
            reader.integer()
        self.assertEqual(reader.integer(), 4906)
        reader.finish()
        self.assertEqual(self.g.battle["pet"]["mp"], mana - 100)
        self.assertLess(self.g.battle["enemy_hp"], before)
        self.assertEqual(self.g.battle["phase"], "drawing")


if __name__ == "__main__":
    unittest.main()
