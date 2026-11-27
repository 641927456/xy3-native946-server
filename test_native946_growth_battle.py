from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from local_accounts import LocalAccounts
from native946_characters import Characters, ROLES
from native946_codec import number, packet
from native946_game_login import NativeGameLogin
from native946_growth_battle import DATA, FIRST_CHARGE_INDEX, LEVELS, GrowthBattle, eligible
from native946_login import MessageReader
from native946_tickets import NativeTickets


class GrowthBattleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "accounts.sqlite3"
        accounts = LocalAccounts(self.database)
        accounts.register({"username": "owner946", "password": "123456789"})
        self.characters = Characters(self.database)
        self.game = NativeGameLogin(0, NativeTickets(), number, None, self.characters)
        self.game.username = "owner946"
        created = self.characters.create("owner946", "成长验收", 702, newbie=True)
        self.game.created_character = created
        self.entered = self.game.receive(packet(915, ["number"], [created["id"]]))
        self.p = self.game.gameplay.progression

    def send(self, mid, *values):
        return self.game.receive(packet(mid, ["number"] * len(values), values))

    def panel(self, value):
        packets = value if isinstance(value, (tuple, list)) else (value,)
        reply = next(row for row in packets if row.startswith(packet(2573, [], [])))
        reader = MessageReader(reply[3:])
        statuses = [reader.integer() for _ in range(reader.take(1)[0])]
        flag = reader.take(1)[0]
        reader.finish()
        return statuses, flag

    def test_open_panel_saves_flag_and_reports_all_initial_levels_locked(self):
        statuses, flag = self.panel(self.game.receive(packet(2576, ["byte"], [1])))
        self.assertEqual(statuses, [0] * len(LEVELS))
        self.assertEqual(flag, 1)
        with self.p.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT open_flag FROM native_growth_state WHERE character_id=?",
                                        (self.p.cid,)).fetchone()[0], 1)

    def test_native_icon_is_added_once_and_opens_growth_panel(self):
        header = packet(1206, [], [])
        expected = packet(1206, [("array", "number"), ("array", "number"), ("array", "string")],
                          [[95, 17, 72, 83], [0, 0, 0, 0], ["", "", "", ""]])
        self.assertEqual([reply for reply in self.entered if reply.startswith(header)], [expected])
        self.assertEqual(GrowthBattle(self.game.gameplay).entry_packets(), ())
        self.game.gameplay.growth_battle_icon_version = 2
        refreshed = self.game.gameplay.sync_progression()
        self.assertEqual([reply for reply in refreshed if reply.startswith(header)], [expected])
        statuses, _ = self.panel(self.send(1390, 17))
        self.assertEqual(len(statuses), 14)
        other = self.send(1390, 95)
        packets = other if isinstance(other, tuple) else (other,)
        self.assertFalse(any(value.startswith(packet(2573, [], [])) for value in packets))

    def test_level_30_claim_grants_each_item_once_and_repeat_is_idempotent(self):
        with self.p.transaction() as db:
            db.execute("UPDATE characters SET level=29 WHERE id=?", (self.p.cid,))
        before = self.p.snapshot()["items"]
        rejected = self.send(2575, 1)
        self.assertEqual(self.panel(rejected)[0][0], 0)
        self.assertIn("达到30级后可领取。", rejected[-1].decode("gb18030", errors="ignore"))
        self.assertEqual(self.p.snapshot()["items"], before)
        with self.p.transaction() as db:
            db.execute("UPDATE characters SET level=30 WHERE id=?", (self.p.cid,))
        self.assertEqual(self.panel(self.send(1390, 17))[0][:2], [2, 0])
        blocked = self.send(2575, 2)
        self.assertIn("达到50级后可领取。", blocked[-1].decode("gb18030", errors="ignore"))
        self.assertEqual(self.p.snapshot()["items"], before)
        claimed = self.send(2575, 1)
        self.assertEqual(self.panel(claimed)[0][:2], [1, 0])
        items = [row for row in self.p.snapshot()["items"] if row["type"] in LEVELS[0]["items"]]
        self.assertEqual({row["type"] for row in items}, set(LEVELS[0]["items"]))
        self.assertTrue(all(row["amount"] == 1 for row in items))
        counts = sorted((row["type"], row["amount"]) for row in self.p.snapshot()["items"])
        repeated = self.send(2575, 1)
        self.assertEqual(self.panel(repeated)[0][0], 1)
        self.assertEqual(sorted((row["type"], row["amount"]) for row in self.p.snapshot()["items"]), counts)
        with self.p.transaction(write=False) as db:
            claims = db.execute("SELECT reward_index FROM native_growth_claims WHERE character_id=?", (self.p.cid,))
            self.assertEqual([row[0] for row in claims], [0])

    def test_out_of_range_wire_positions_cannot_claim(self):
        with self.p.transaction() as db:
            db.execute("UPDATE characters SET level=30 WHERE id=?", (self.p.cid,))
        before = self.p.snapshot()["items"]
        for position in (0, 15):
            with self.subTest(position=position):
                reply = self.send(2575, position)
                self.assertIn("没有这个成长奖励档位。", reply.decode("gb18030", errors="ignore"))
        self.assertEqual(self.p.snapshot()["items"], before)
        self.assertEqual(self.panel(self.send(1390, 17))[0][:2], [2, 0])

    def test_all_fourteen_levels_claim_with_correct_item_totals(self):
        with self.p.transaction() as db:
            db.execute("UPDATE characters SET level=200 WHERE id=?", (self.p.cid,))
            db.execute("UPDATE cultivation_state SET rebirth=2,ascension=1 WHERE character_id=?", (self.p.cid,))
            db.execute("INSERT INTO cosmetic_features VALUES(?,'bag_item',3,0) ON CONFLICT(character_id,key) "
                       "DO UPDATE SET value=3", (self.p.cid,))
        for position, row in enumerate(LEVELS, start=1):
            reply = self.send(2575, position)
            packets = reply if isinstance(reply, tuple) else (reply,)
            self.assertTrue(any(value.startswith(packet(2573, [], [])) for value in packets),
                            (row["label"], [value[:3] for value in packets],
                             [value.decode("gb18030", errors="ignore") for value in packets]))
            statuses, _ = self.panel(reply)
            self.assertEqual(statuses[row["index"]], 1, row["label"])
        expected = Counter(item for row in LEVELS for item in row["items"])
        actual = Counter()
        for item in self.p.snapshot()["items"]:
            if item["type"] in expected:
                actual[item["type"]] += item["amount"]
        self.assertEqual(actual, expected)

    def test_full_inventory_rolls_back_whole_claim(self):
        with self.p.transaction() as db:
            db.execute("UPDATE characters SET level=30 WHERE id=?", (self.p.cid,))
            from native946_mall_bags import pages
            for page in pages(db, self.p.cid):
                for slot in range(1, 25):
                    db.execute("INSERT OR REPLACE INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)",
                               (self.p.cid, 4000, page, slot))
        reply = self.send(2575, 1)
        self.assertTrue(reply.startswith(packet(98, [], [])))
        self.assertIn("背包空间不足", reply.decode("gb18030", errors="ignore"))
        with self.p.transaction(write=False) as db:
            self.assertFalse(db.execute("SELECT 1 FROM native_growth_claims WHERE character_id=?",
                                        (self.p.cid,)).fetchone())
            self.assertFalse(db.execute("SELECT 1 FROM inventory WHERE character_id=? AND type=6598",
                                        (self.p.cid,)).fetchone())

    def test_cultivation_stages_cannot_be_bypassed_by_level(self):
        base = {"level": 200, "rebirth": 0, "ascension": 0, "sanctification": 0}
        self.assertFalse(eligible(next(row for row in LEVELS if row["label"] == "转生100级"), base))
        self.assertFalse(eligible(next(row for row in LEVELS if row["label"] == "飞升160级"),
                                  dict(base, rebirth=2)))
        self.assertTrue(eligible(next(row for row in LEVELS if row["label"] == "飞升160级"),
                                 dict(base, rebirth=2, ascension=1)))
        self.assertTrue(all(eligible(row, dict(base, rebirth=2, ascension=1)) for row in LEVELS))

    def test_first_charge_grants_five_matching_pieces_immediately_and_only_once(self):
        before = self.p.snapshot()
        self.assertLess(before["level"], 80)
        result = self.send(2574)
        self.assertTrue(result[-1].startswith(packet(100, [], [])))
        self.assertIn("已领取炎龙套装", result[-1].decode("gb18030", errors="ignore"))
        item_header = packet(786, [], [])
        self.assertEqual(sum(reply.startswith(item_header) for reply in result), 5)
        after = self.p.snapshot()
        pieces = {item["type"]: after["equipment"][item["id"]] for item in after["items"]
                  if str(item["type"]) in DATA["first_charge"]["equipment"]}
        self.assertEqual(set(pieces), {25303, 25305, 25306, 25307, 25310})
        self.assertEqual({piece["slot"] for piece in pieces.values()}, {1, 2, 3, 4, 5})
        self.assertTrue(all(piece["requirements"]["level"] == 80 for piece in pieces.values()))
        self.assertEqual(after["gold"], before["gold"])
        self.assertTrue(self.send(2574).startswith(packet(100, [], [])))
        self.assertEqual(self.p.snapshot()["items"], after["items"])
        statuses, _ = self.panel(self.send(1390, 17))
        self.assertEqual(statuses, [0] * len(LEVELS))
        with self.p.transaction(write=False) as db:
            claims = db.execute("SELECT reward_index FROM native_growth_claims WHERE character_id=?",
                                (self.p.cid,)).fetchall()
        self.assertEqual([row[0] for row in claims], [FIRST_CHARGE_INDEX])
        with self.p.transaction() as db:
            db.execute("UPDATE characters SET level=80 WHERE id=?", (self.p.cid,))
        for item in after["items"]:
            if item["type"] in pieces:
                self.p.move_item(item["id"], 1, pieces[item["type"]]["slot"])
        self.assertEqual({item["type"] for item in self.p.snapshot()["items"] if item["page"] == 1},
                         set(pieces))

    def test_first_charge_full_bag_rolls_back_partial_delivery(self):
        with self.p.transaction() as db:
            from native946_mall_bags import pages
            for page in pages(db, self.p.cid):
                for slot in range(1, 25):
                    db.execute("INSERT OR REPLACE INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,?,?,1)",
                               (self.p.cid, 4000, page, slot))
            db.execute("DELETE FROM inventory WHERE character_id=? AND page=? AND slot<=4",
                       (self.p.cid, pages(db, self.p.cid)[0]))
        before = self.p.snapshot()
        reply = self.send(2574)
        self.assertIn("物品栏都已放满", reply.decode("gb18030", errors="ignore"))
        self.assertEqual(self.p.snapshot()["items"], before["items"])
        self.assertEqual(self.p.snapshot()["equipment"], before["equipment"])
        with self.p.transaction(write=False) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM native_growth_claims WHERE character_id=?",
                                         (self.p.cid,)).fetchone())

    def test_every_role_has_one_piece_per_slot_and_guides_match_source(self):
        for role in ROLES.values():
            with self.subTest(role=role["name"]):
                slots = sorted(item["slot"] for item in DATA["first_charge"]["equipment"].values()
                               if role["name"] in item["roles"] or item["race"] == role["race"])
                self.assertEqual(slots, [1, 2, 3, 4, 5])
        self.assertEqual(len(DATA["guides"]), 13)
        self.assertEqual(len(LEVELS), 14)


if __name__ == "__main__":
    unittest.main()
