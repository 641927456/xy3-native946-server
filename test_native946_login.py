"""Regression checks for the native 3.0.946 login string framing."""

import random
import sqlite3
from contextlib import closing
from hashlib import scrypt
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from local_accounts import AccountError, LocalAccounts, native_password, native_username, validate_credentials
from native946_login import MessageReader, NativeLocalLogin, string_field
from server import native_channel_integer, native_user_server_list, native_ticket_reply
from native946_tickets import NativeTickets
from native946_game_login import NativeGameLogin, message_id
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class NativeStringTests(unittest.TestCase):
    def test_native_public_key_request_keeps_following_mode(self):
        # Live request: 278 bytes, id 08, length marker 00 11 01,
        # 272-byte PEM public key, and integer mode 02 00.
        # Reconstruct only the non-secret field shape; do not retain key data.
        wire = b"\x08\x00\x11\x01" + b"K" * 272 + b"\x02\x00"
        self.assertEqual(len(wire), 278)
        login = NativeLocalLogin(0, None, native_channel_integer, None, None)
        login.checked = True
        self.assertEqual(login.receive(wire), b"\x03\x02\x00\x02\x00\x01")
        self.assertTrue(login.key_ready)

    def test_encoder_matches_native_length_boundaries(self):
        vectors = ((0, b"\x01"), (254, b"\xff"),
                   (255, b"\x00\x00\x01"), (272, b"\x00\x11\x01"),
                   (65534, b"\x00\xff\xff"))
        for length, prefix in vectors:
            with self.subTest(length=length):
                body = b"X" * length
                self.assertEqual(string_field(body), prefix + body)

    def test_generated_roundtrips_preserve_next_field(self):
        rng = random.Random(946)
        lengths = [0, 1, 253, 254, 255, 256, 272, 65533, 65534]
        lengths += [rng.randrange(65535) for _ in range(200)]
        for length in lengths:
            with self.subTest(length=length):
                body = rng.randbytes(length)
                reader = MessageReader(string_field(body) + b"\x02\x01")
                self.assertEqual(reader.string(), body)
                self.assertEqual(reader.integer(), 1)
                reader.finish()

    def test_truncated_fields_are_rejected(self):
        for wire in (b"", b"\x00", b"\x00\x11", b"\x02", b"\x00\x11\x01" + b"K" * 271):
            with self.subTest(wire_length=len(wire)):
                with self.assertRaises(ValueError):
                    MessageReader(wire).string()

    def test_zero_extended_length_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "string length"):
            MessageReader(b"\x00\x00\x00").string()

    def test_overlong_strings_are_rejected(self):
        with self.assertRaises(ValueError):
            string_field(b"X" * 65535)


class LocalAccountTests(unittest.TestCase):
    def test_native_suffix_is_limited_to_the_default_client_domain(self):
        for suffix in ("@163.com", "@163.COM"):
            self.assertEqual(native_username("123123123" + suffix), "123123123")
        self.assertEqual(native_username("123123123"), "123123123")
        for username in ("abc@example.com", "abc@@163.com", "abc@163.com "):
            with self.subTest(username=username):
                with self.assertRaises(AccountError):
                    validate_credentials({"username": native_username(username), "password": "123456789"})

    def test_nine_character_password_registers_and_authenticates(self):
        with TemporaryDirectory() as folder:
            accounts = LocalAccounts(Path(folder) / "accounts.sqlite3")
            credentials = {"username": "regression946", "password": "123456789"}
            self.assertEqual(accounts.register(credentials)["username"], "regression946")
            session = accounts.login(credentials)
            accounts.require_session("Bearer " + session["session_token"])
            login = NativeLocalLogin(0, accounts, native_channel_integer, None,
                                     lambda: native_user_server_list(946))
            login.key_ready = True
            payload = (b"\x07" + string_field(credentials["username"] + "@163.com")
                       + string_field(native_password(credentials["password"].encode("utf-8")))
                       + b"\x02\x00\x02\x00")
            self.assertEqual(login.receive(payload)[0], 12)
            self.assertTrue(login.authenticated)
            with self.assertRaises(AccountError) as failure:
                accounts.login({**credentials, "password": "987654321"})
            self.assertEqual(failure.exception.status, 401)

    def test_native_proof_is_separate_from_plain_password_login(self):
        with TemporaryDirectory() as folder:
            accounts = LocalAccounts(Path(folder) / "accounts.sqlite3")
            credentials = {"username": "regression946", "password": "123456789"}
            accounts.register(credentials)
            proof = native_password(b"123456789").decode("ascii")
            session = accounts.login_native({"username": "regression946@163.COM", "password": proof.upper()})
            self.assertEqual(session["username"], "regression946")
            for password in ("123456789", "0" * 32):
                with self.assertRaises(AccountError) as failure:
                    accounts.login_native({"username": "regression946@163.com", "password": password})
                self.assertEqual(failure.exception.status, 401)
            with self.assertRaises(AccountError):
                accounts.login({"username": "regression946", "password": proof})
            with self.assertRaises(AccountError):
                accounts.login({"username": "regression946@163.com", "password": "123456789"})
            with closing(sqlite3.connect(accounts.database)) as db:
                stored = db.execute("SELECT native_password_hash FROM accounts").fetchone()[0]
            self.assertNotEqual(stored, proof.encode("ascii"))

    def test_existing_account_schema_and_password_are_preserved(self):
        with TemporaryDirectory() as folder:
            database = Path(folder) / "accounts.sqlite3"
            salt = b"migration-salt!!"
            original = scrypt(b"123456789", salt=salt, n=2**15, r=8, p=3, maxmem=64 << 20, dklen=32)
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("CREATE TABLE accounts (username TEXT PRIMARY KEY, salt BLOB, password_hash BLOB)")
                db.execute("INSERT INTO accounts VALUES (?, ?, ?)", ("regression946", salt, original))
            accounts = LocalAccounts(database)
            accounts.login({"username": "regression946", "password": "123456789"})
            session = accounts.login_native({"username": "regression946@163.com", "password": native_password(b"123456789").decode("ascii")})
            self.assertEqual(session["username"], "regression946")
            with closing(sqlite3.connect(database)) as db:
                stored = db.execute("SELECT password_hash, native_password_hash FROM accounts").fetchone()
            self.assertEqual(stored[0], original)
            self.assertIsNotNone(stored[1])

    def test_short_and_oversized_passwords_are_rejected(self):
        for password in ("12345678", "a" * 513, "密" * 171):
            with self.subTest(length=len(password)):
                with self.assertRaises(AccountError):
                    validate_credentials({"username": "regression946", "password": password})


class NativeTicketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        cls.pem = cls.key.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo)

    def test_ticket_reply_has_native_fields_and_decryptable_ticket(self):
        store = NativeTickets()
        reply = native_ticket_reply(store, "regression946", self.pem, (1, 2, 1))
        reader = MessageReader(reply)
        self.assertEqual(reader.take(1), b"\x10")
        ticket = self.key.decrypt(reader.string(), padding.PKCS1v15())
        self.assertEqual(store.pending[ticket][0], "regression946")
        self.assertEqual(reader.string(), b"127.0.0.1")
        self.assertEqual(reader.integer(), 19127)
        self.assertEqual(reader.string().decode("gb18030"), "本机推荐")
        self.assertEqual(reader.string().decode("gb18030"), "本机接入")
        self.assertEqual(reader.integer(), 0)
        reader.finish()

    def test_unauthenticated_session_cannot_obtain_ticket(self):
        login = NativeLocalLogin(0, None, native_channel_integer, None, None,
                                 lambda *args: self.fail("ticket issued without authentication"))
        login.client_public_key = self.pem
        request = b"\x11" + b"\x02\x01" * 3 + string_field(b"a") + string_field(b"b")
        self.assertEqual(login.receive(request)[0], 4)

    def test_game_handshake_redeems_ticket_once_and_returns_character_list(self):
        store = NativeTickets()
        ticket = self.key.decrypt(store.issue("regression946", self.pem), padding.PKCS1v15())
        configured = []
        game = NativeGameLogin(0, store, native_channel_integer,
                               lambda port, **options: configured.append(options["game_public_key"]))
        challenge = b"native-challenge"
        response = game.receive(message_id(1015) + native_channel_integer(946)
                                + string_field(b"") + string_field(challenge))
        reader = MessageReader(response)
        self.assertEqual(reader.take(3), message_id(1008))
        self.assertEqual(reader.integer(), 1)
        signature = reader.string()
        reader.finish()
        public = store.server_key.public_key().public_numbers()
        recovered = pow(int.from_bytes(signature, "big"), public.e, public.n).to_bytes(128, "big")
        self.assertTrue(recovered.startswith(b"\x00\x01"))
        self.assertTrue(recovered.endswith(b"\x00" + challenge))
        self.assertEqual(configured, [store.public_pem])
        self.assertTrue(game.receive(message_id(1017) + string_field(self.pem)).startswith(message_id(1009)))
        numbers = self.key.private_numbers()
        block = b"\x00\x01" + b"\xff" * (128 - len(ticket) - 3) + b"\x00" + ticket
        signed = pow(int.from_bytes(block, "big"), numbers.d, numbers.public_numbers.n).to_bytes(128, "big")
        response = game.receive(message_id(1016) + string_field(signed) + b"\x00")
        self.assertEqual(game.username, "regression946")
        self.assertEqual(response, (message_id(1010) + b"\x00\x02\x00\x01", message_id(1012) + b"\x00"))
        with self.assertRaises(ValueError):
            store.redeem(signed, self.pem)

    def test_game_ticket_rejects_invalid_signature(self):
        store = NativeTickets()
        store.issue("regression946", self.pem)
        for signed in (b"", b"\x00" * 128, b"\xff" * 128):
            with self.subTest(length=len(signed)):
                with self.assertRaises(ValueError):
                    store.redeem(signed, self.pem)


if __name__ == "__main__":
    unittest.main()
