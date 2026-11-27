from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from local_accounts import AccountError, LocalAccounts
from native946_characters import Characters, player_info
from native946_codec import SCHEMAS, character_list, number
from native946_game_login import NativeGameLogin, message_id
from native946_login import MessageReader, string_field
from native946_tickets import NativeTickets


class CharacterTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "accounts.sqlite3"
        accounts = LocalAccounts(self.database)
        accounts.register({"username": "owner946", "password": "123456789"})
        accounts.register({"username": "other946", "password": "123456789"})
        self.characters = Characters(self.database)

    def test_creation_survives_new_store_and_has_native_role_properties(self):
        created = self.characters.create("owner946", "本机测试", 702)
        loaded = Characters(self.database).list("owner946")
        self.assertEqual(loaded, [created])
        self.assertEqual((created["race"], created["sex"]), (7, 2))
        self.assertEqual(self.characters.enter("owner946", created["id"])["name"], "本机测试")

    def test_retry_is_idempotent_including_concurrent_requests(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.characters.create("owner946", "重复请求", 713), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.characters.list("owner946")), 1)

    def test_foreign_character_and_duplicate_name_are_rejected(self):
        created = self.characters.create("owner946", "角色归属", 713)
        with self.assertRaises(AccountError):
            self.characters.enter("other946", created["id"])
        with self.assertRaises(AccountError):
            self.characters.create("other946", "角色归属", 713)
        self.assertEqual(self.characters.list("other946"), [])

    def test_invalid_creation_does_not_leave_rows(self):
        for name, role in (("a", 713), ("bad name", 713), ("12345", 713), ("正常角色", 9999)):
            with self.subTest(name=name), self.assertRaises(AccountError):
                self.characters.create("owner946", name, role)
        self.assertEqual(self.characters.list("owner946"), [])

    def test_native_creation_and_enter_packet_flow(self):
        game = NativeGameLogin(0, NativeTickets(), number, None, self.characters)
        game.username = "owner946"
        request = (message_id(913) + string_field("协议创建") + number(713) + number(0) + b"\x00")
        reply = game.receive(request)
        created = self.characters.list("owner946")[0]
        self.assertEqual(reply, game.account_packets())
        packets = game.receive(message_id(915) + number(created["id"]))
        ids=[int.from_bytes(p[1:3], "little") for p in packets]
        self.assertEqual(ids[:8], [893, 904, 891, 693, 1597, 1590, 1585, 1589])
        self.assertIn(786,ids)
        self.assertIn(699,ids)
        self.assertIn(1978,ids)
        self.assertGreater(ids.index(1275), ids.index(1978))
        from native946_codec import packet
        self.assertEqual((created['map_id'], created['x'], created['y']), (2008, 172, 122))
        self.assertEqual(packets[-1], packet(1275, ['number', 'number', 'string'], [1432, 1, '']))
        welcome = packet(1222, ['number', 'number'], [100, 1])
        self.assertIsNone(game.receive(packet(1410, ['number', 'string'], [100, '0'])))
        self.assertIn(welcome, game.receive(packet(1458, ['number'], [1432])))
        self.assertEqual(game.receive(packet(1410, ['number', 'string'], [100, '0'])),
                         packet(1222, ['number', 'number'], [101, 1]))
        self.assertIsNone(game.receive(packet(1410, ['number', 'string'], [100, '0'])))
        self.assertEqual(game.current_character["id"], created["id"])
        self.assertGreater(Characters(self.database).list("owner946")[0]["last_entered_at"], 0)

    def test_native_creation_grants_each_eligible_first_tier_at_zero_practice(self):
        for index, (role, expected) in enumerate((
                (113, {11, 21, 31}), (102, {1, 11, 31}),
                (212, {91, 101, 111}), (204, {81, 91, 111}),
                (312, {51, 61, 71}), (301, {41, 51, 71}),
                (713, {6100, 6107, 6114}), (703, {6100, 6107, 6128}),
        )):
            with self.subTest(role=role):
                game = NativeGameLogin(index, NativeTickets(), number, None, self.characters)
                game.username = "owner946" if index < 4 else "other946"
                request = message_id(913) + string_field(f"新手法术{index}") + number(role) + number(0) + b"\x00"
                game.receive(request)
                packets = game.receive(message_id(915) + number(game.created_character["id"]))
                reader = MessageReader(next(p for p in packets if p[:3] == message_id(699))[3:])
                self.assertEqual(reader.integer(), 0)
                self.assertEqual(reader.take(1), b"\x00")
                received = {}
                for _ in range(reader.take(1)[0]):
                    cost = reader.string().decode("gb18030")
                    description = reader.string().decode("gb18030")
                    practice, skill = reader.integer(), reader.integer()
                    received[skill] = practice
                    if skill == 4592:
                        self.assertEqual(cost, "法力：60")
                        self.assertIn("#G160#n点固定气血伤害", description)
                self.assertEqual(received, dict.fromkeys(expected | {4592}, 0))
                self.assertEqual(game.gameplay.progression.snapshot()["skills"], received)

    def test_same_account_refreshes_and_enters_different_characters(self):
        first = self.characters.create("owner946", "第一角色", 702)
        login = NativeGameLogin(100, NativeTickets(), number, None, self.characters)
        login.username = "owner946"
        request = (message_id(913) + string_field("第二角色") + number(713) + number(0) + b"\x00")
        reply = login.receive(request)
        characters = self.characters.list("owner946", stage_fields=True)
        second = characters[1]
        self.assertEqual(reply, (
            message_id(1010) + b"\x00" + number(2) + b"\x01",
            character_list([player_info(character) for character in characters]),
        ))

        other_login = NativeGameLogin(101, NativeTickets(), number, None, self.characters)
        other_login.username = "owner946"
        self.assertEqual(other_login.account_packets()[0], message_id(1010) + b"\x00" + number(2) + b"\x01")
        self.assertIsInstance(other_login.receive(message_id(915) + number(second["id"])), tuple)
        self.assertIsInstance(login.receive(message_id(915) + number(first["id"])), tuple)
        self.assertEqual((login.current_character["id"], other_login.current_character["id"]),
                         (first["id"], second["id"]))
        self.assertEqual({row["id"] for row in Characters(self.database).list("owner946")},
                         {first["id"], second["id"]})

    def test_new_character_has_latest_activity_for_native_default_selection(self):
        existing = self.characters.create("owner946", "已有角色", 702)
        with closing(self.characters.connect()) as db, db:
            db.execute("UPDATE characters SET created_at=100,last_entered_at=100 WHERE id=?", (existing["id"],))
        game = NativeGameLogin(0, NativeTickets(), number, None, self.characters)
        game.username = "owner946"
        reply = game.receive(message_id(913) + string_field("新建角色") + number(713) + number(0) + b"\x00")
        reader = MessageReader(reply[1][3:])
        rows = []
        for _ in range(reader.take(1)[0]):
            row = {}
            for name, kind in SCHEMAS["player_info"]:
                if kind == "number":
                    row[name] = reader.integer()
                elif kind == "string":
                    row[name] = reader.string().decode("gb18030")
                else:
                    row[name] = int.from_bytes(reader.take(1 if kind == "byte" else 2), "little")
            rows.append(row)
        reader.finish()
        old = next(row for row in rows if row["id"] == existing["id"])
        new = next(row for row in rows if row["name"] == "新建角色")
        self.assertEqual(old["logout_time"], 100)
        self.assertGreater(new["logout_time"], old["logout_time"])
        self.assertEqual(new["logout_time"], game.created_character["created_at"])

    def test_character_list_includes_saved_cosmetics_and_weapon(self):
        from native946_progression import Progression

        created = self.characters.create("owner946", "外观同步", 702)
        progression = Progression(self.database, "owner946", created["id"])
        with progression.transaction() as db:
            db.execute("INSERT INTO cosmetic_state VALUES(?,?,?,?,?,?)",
                       (created["id"], 19, 2, 11, 12, 13))
            db.execute("INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,5597,1,4,1)",
                       (created["id"],))
            for kind, slot in ((56005, 10), (56006, 11), (56007, 12), (56222, 15)):
                db.execute("INSERT INTO inventory(character_id,type,page,slot,amount) VALUES(?,?,29,?,1)",
                           (created["id"], kind, slot))

        game = NativeGameLogin(0, NativeTickets(), number, None, self.characters)
        game.username = "owner946"
        reply = game.account_packets()[1]
        reader = MessageReader(reply[3:])
        self.assertEqual(reader.take(1)[0], 1)
        row = {}
        for name, kind in SCHEMAS["player_info"]:
            if kind == "number":
                row[name] = reader.integer()
            elif kind == "string":
                row[name] = reader.string().decode("gb18030")
            else:
                row[name] = int.from_bytes(reader.take(1 if kind == "byte" else 2), "little")
        reader.finish()
        self.assertEqual({key: row[key] for key in (
            "hat_id", "suit_id", "cloak_id", "charm_nimbus", "weapon_idx", "weapon_color",
            "color1", "color2", "color3")}, {
            "hat_id": 12001, "suit_id": 22001, "cloak_id": 32001, "charm_nimbus": 50001,
            "weapon_idx": 19, "weapon_color": 2, "color1": 11, "color2": 12, "color3": 13})

    def test_character_list_refreshes_a_pre_reload_store_without_recreating_it(self):
        self.characters.create('owner946','旧版对象',702)
        def legacy_list(store,owner):return Characters.list(store,owner)
        old_type=type('Characters',(Characters,),dict(__module__='native946_characters',list=legacy_list))
        store=self.characters;store.__class__=old_type
        marker=object();store.position_writer_marker=marker
        game=NativeGameLogin(0,NativeTickets(),number,None,store);game.username='owner946'
        packets=game.account_packets()
        self.assertIs(game.characters,store)
        self.assertIs(type(store),Characters)
        self.assertIs(store.position_writer_marker,marker)
        expected=character_list([player_info(row) for row in store.list('owner946',stage_fields=True)])
        self.assertEqual(packets[1],expected)

    def test_other_character_store_implementations_are_not_reclassified(self):
        from types import SimpleNamespace
        store=SimpleNamespace(list=lambda owner,**kwargs:[])
        game=NativeGameLogin(0,NativeTickets(),number,None,store);game.username='owner946'
        self.assertEqual(len(game.account_packets()),2)
        self.assertIs(game.characters,store)


if __name__ == "__main__":
    unittest.main()
