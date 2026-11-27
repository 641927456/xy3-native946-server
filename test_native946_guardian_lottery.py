from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import unittest

from local_accounts import LocalAccounts
from native946_characters import Characters
from native946_codec import number, packet
from native946_cosmetics import Cosmetics, LOCAL_UNLIMITED_YUBO_BALANCE
from native946_game_login import NativeGameLogin
from native946_guardian_lottery import CONFIG, GuardianLottery
from native946_tickets import NativeTickets


class GuardianLotteryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "accounts.sqlite3"
        accounts = LocalAccounts(self.database)
        accounts.register({"username": "123123123", "password": "123456789"})
        self.characters = Characters(self.database)
        self.game = NativeGameLogin(0, NativeTickets(), number, None, self.characters)
        self.game.username = "123123123"
        created = self.characters.create("123123123", "免费抽奖", 702)
        self.game.created_character = created
        self.game.receive(packet(915, ["number"], [created["id"]]))
        self.game.gameplay.guardian_lottery_panel_until = monotonic() + 900

    def test_single_and_ten_draws_cost_zero_with_zero_balance(self):
        lottery = GuardianLottery(self.game.gameplay)
        lottery.draw(1, 1, 1)
        lottery.draw(10, 1, 0)
        with self.game.gameplay.progression.transaction(write=False) as db:
            self.assertEqual(Cosmetics(self.game.gameplay).wallet(db)[0], LOCAL_UNLIMITED_YUBO_BALANCE)
            stored = db.execute("SELECT yubo FROM mall_account_wallet WHERE owner=?",
                                (self.game.gameplay.progression.owner,)).fetchone()
            self.assertEqual(stored[0] if stored else 0, 0)
            state = db.execute("SELECT total FROM native_guardian_lottery_state WHERE character_id=?",
                               (self.game.gameplay.progression.cid,)).fetchone()
            self.assertEqual(state[0], 11)
            costs = [row[0] for row in db.execute(
                "SELECT json_extract(result,'$.cost_yubo') FROM native_guardian_lottery_orders WHERE operation='draw'")]
            self.assertEqual(costs, [0, 0])

    def test_unlimited_yubo_purchase_does_not_deduct_or_duplicate(self):
        self.game.receive(packet(3663, ["number", "number"], [56006, 1]))
        self.game.receive(packet(3665, ["number"] * 4, [56006, 1, 0, 0]))
        with self.game.gameplay.progression.transaction(write=False) as db:
            self.assertEqual(Cosmetics(self.game.gameplay).wallet(db)[0], LOCAL_UNLIMITED_YUBO_BALANCE)
            stored = db.execute("SELECT yubo FROM mall_account_wallet WHERE owner=?",
                                (self.game.gameplay.progression.owner,)).fetchone()
            self.assertEqual(stored[0] if stored else 0, 0)
            self.assertEqual(db.execute("SELECT count(*) FROM cosmetic_orders").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM inventory WHERE type=56006").fetchone()[0], 1)
        self.game.receive(packet(3665, ["number"] * 4, [56006, 1, 0, 0]))
        with self.game.gameplay.progression.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cosmetic_orders").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM inventory WHERE type=56006").fetchone()[0], 1)

    def sibling(self):
        from native946_sessions import LEASES

        owner = self.game.username
        row = self.characters.create(owner, "同账号积分", 113)
        login = NativeGameLogin(556, None, number, None, self.characters)
        login.username = owner
        self.addCleanup(LEASES.release_port, self.database, 556)
        login.receive(packet(915, ["number"], [row["id"]]))
        return login.gameplay

    def test_legacy_points_merge_once_by_account_season_and_type(self):
        from native946_guardian_channels import GuardianChannels, migrate_account_points as migrate_exchange
        from native946_guardian_lottery import SEASON, migrate_account_points as migrate_lottery
        from native946_progression import Progression

        first = self.game.gameplay
        second = self.sibling()
        p = first.progression
        with p.transaction() as db:
            db.execute("DROP TABLE native_guardian_lottery_account_points")
            db.execute("DROP TABLE native_guardian_account_points")
            db.execute("INSERT INTO accounts(username,salt,password_hash) VALUES('points_other',X'01',X'01')")
            stranger = db.execute(
                "INSERT INTO characters(owner,name,role_id,race,sex,created_at,map_id,x,y) "
                "VALUES('points_other','另一账号天缘',702,1,2,0,2001,1,1)").lastrowid
            db.executemany("INSERT INTO native_guardian_lottery_state VALUES(?,?,?,?,?,?,?)", [
                (p.cid, SEASON, 3, 9, "old-day", 11320, 1),
                (second.progression.cid, SEASON, 8, 20, "", 450, 2),
                (second.progression.cid, "previous-season", 0, 0, "", 700, 0),
                (stranger, SEASON, 0, 0, "", 110, 4),
            ])
            db.executemany("INSERT INTO native_guardian_points VALUES(?,?,?)", [
                (p.cid,1,30), (second.progression.cid,1,20),
                (second.progression.cid,2,10), (stranger,1,60),
            ])
        Progression(self.database, p.owner, p.cid)
        with p.transaction() as db:
            migrate_lottery(db)
            migrate_exchange(db)
            self.assertEqual(GuardianLottery(first)._state(db), {
                "pity":3,"total":9,"first_day":"old-day","score":11770,"flash_score":3})
            self.assertEqual(GuardianLottery(second)._state(db), {
                "pity":8,"total":20,"first_day":"","score":11770,"flash_score":3})
            self.assertEqual([GuardianChannels(second).balance(db, i) for i in (1,2,3)], [50,10,0])
            self.assertEqual(db.execute("SELECT score FROM native_guardian_lottery_account_points "
                                        "WHERE owner=? AND season='previous-season'", (p.owner,)).fetchone()[0],700)
            self.assertEqual(tuple(db.execute("SELECT score,flash_score FROM native_guardian_lottery_account_points "
                                               "WHERE owner='points_other' AND season=?", (SEASON,)).fetchone()), (110,4))
            self.assertEqual(db.execute("SELECT amount FROM native_guardian_account_points "
                                        "WHERE owner='points_other' AND score_type=1").fetchone()[0],60)
            self.assertEqual(db.execute("SELECT sum(score+flash_score) FROM native_guardian_lottery_state").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT sum(amount) FROM native_guardian_points").fetchone()[0],0)

    def test_shared_points_credit_and_native_shop_debits(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from native946_guardian_acquisition import GuardianAcquisition
        from native946_guardian_channels import GuardianChannels
        from native946_guardian_lottery import SCORE_VOUCHER, SEASON
        from native946_login import MessageReader, string_field

        def message_id(payload):
            return payload[0] or int.from_bytes(payload[1:3], "little")

        def send(game, request, *values):
            return game.receive(request, MessageReader(b"".join(number(value) for value in values)))

        first = self.game.gameplay
        second = self.sibling()
        p = first.progression
        with p.transaction() as db:
            voucher = GuardianAcquisition(first)._give_item(db, SCORE_VOUCHER)
            db.execute("INSERT INTO native_guardian_lottery_cards VALUES(?,?,?,1)", (p.cid,SEASON,3355))
            db.execute("UPDATE characters SET gold=10000 WHERE id=?", (p.cid,))
        send(first, 807, 0, voucher)
        send(first, 3778)
        first.receive(3777, MessageReader(string_field("{{1,1}}")))

        for player in (first, second):
            reply = send(player, 3775)
            self.assertEqual(message_id(reply), 3771)
            reader = MessageReader(reply[3:])
            self.assertEqual((reader.integer(),reader.integer()), (8000,1))

        ready = Barrier(2)
        def exchange(player):
            ready.wait(timeout=3)
            reply = send(player, 3774, 48266, 1)
            return message_id(reply) if isinstance(reply, bytes) else 3771

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(exchange, (first,second)), [3771,98])

        with p.transaction(write=False) as db:
            state = GuardianLottery(first)._state(db)
            self.assertEqual((state["score"],state["flash_score"]), (2112,0))
            self.assertEqual(db.execute("SELECT count(*) FROM inventory WHERE type=48266").fetchone()[0],1)

        source = GuardianChannels(first)
        source.quote("points",1,10)
        source.confirm(first.guardian_source_session["token"])
        from native946_lua_values import table
        for player in (first, second):
            for score_type, balance, row_count in ((1, 10, 21), (2, 0, 19), (3, 0, 20)):
                panel, notice = send(player, 3159, score_type)
                self.assertEqual(message_id(panel), 3157)
                reader = MessageReader(panel[3:])
                self.assertEqual((reader.integer(), reader.integer()), (score_type, balance))
                catalog = table(reader.string().decode('ascii'))
                self.assertEqual(len(catalog), row_count)
                self.assertTrue(all(row['person_left_num'] == row['server_left_num'] == 0 for row in catalog.values()))
                self.assertEqual(reader.integer(), 1)
                reader.finish()
                self.assertIn('仅供预览', notice.decode('gb18030', errors='ignore'))

        progress = send(second, 3156)
        self.assertEqual(message_id(progress), 3154)
        reader = MessageReader(progress[3:])
        groups = table(reader.string().decode('ascii'))
        reader.finish()
        self.assertEqual(len(groups), 10)
        self.assertEqual(sum(group['total'] for group in groups.values()), 78)
        collection = send(second, 3155, 2008)
        self.assertEqual(message_id(collection), 3153)
        reader = MessageReader(collection[3:])
        self.assertEqual((reader.integer(), table(reader.string().decode('ascii'))), (2008, {}))
        reader.finish()

        for _ in range(2):
            rejected = send(second, 3158, 1, 46222)
            self.assertIn('积分未扣除', rejected.decode('gb18030', errors='ignore'))
        with p.transaction(write=False) as db:
            self.assertEqual(source.balance(db,1),10)
            self.assertEqual(db.execute("SELECT count(*) FROM inventory WHERE character_id=? AND type=46222",
                                        (second.progression.cid,)).fetchone()[0],0)

    def test_native_rule_text_matches_zero_prices(self):
        self.assertEqual((CONFIG["OnePriceFirst"], CONFIG["OnePrice"], CONFIG["TenPrice"]), (0, 0, 0))
        self.assertIn("0玉帛", CONFIG["HelpContent"])
        self.assertNotIn("之后单抽200", CONFIG["HelpContent"])

    def test_anniversary_card_requires_level_100_and_preserves_card_and_points(self):
        p = self.game.gameplay.progression
        with p.transaction() as db:
            card = db.execute("INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,46901,8,24,2)",
                              (p.cid,)).lastrowid
            db.execute("INSERT INTO native_guardian_account_points VALUES(?,3,217)", (p.owner,))
        request = packet(807, ['number', 'number'], [0, card])

        for level, reason in ((99, '达到100级'), (100, '原版积分数值尚未核实')):
            with p.transaction() as db:
                db.execute("UPDATE characters SET level=? WHERE id=?", (level, p.cid))
            reply = self.game.receive(request)
            self.assertIn(reason, reply.decode('gb18030', errors='ignore'))
            with p.transaction(write=False) as db:
                self.assertEqual(db.execute("SELECT amount FROM inventory WHERE id=?", (card,)).fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT amount FROM native_guardian_account_points WHERE owner=? AND score_type=3",
                                            (p.owner,)).fetchone()[0], 217)


if __name__ == "__main__":
    unittest.main()
