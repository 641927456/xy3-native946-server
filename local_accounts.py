"""Accounts and terminal client for the isolated loopback test service."""

from contextlib import closing
from getpass import GetPassWarning, getpass
from hashlib import md5, scrypt, sha256
from hmac import compare_digest
from http.client import HTTPConnection, HTTPException
import json
import re
from secrets import token_bytes, token_urlsafe
import sqlite3
from threading import BoundedSemaphore, Lock
from time import monotonic
import warnings


SESSION_SECONDS = 900
MIN_PASSWORD_CHARACTERS = 9
USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,31}")
SESSION_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
NATIVE_PASSWORD = re.compile(rb"[0-9a-fA-F]{32}")


def native_username(username):
    """Resolve the default mail suffix added by the 3.0.946 account widget."""
    if isinstance(username, str) and username.lower().endswith("@163.com"):
        return username[:-8]
    return username


def native_password(password):
    # The legacy native protocol uses a hexadecimal password digest. Store
    # its verifier under scrypt as well; never store this wire value directly.
    encoded = password.decode("utf-8").encode("gb18030")
    return md5(encoded, usedforsecurity=False).hexdigest().encode("ascii")


class AccountError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class LocalAccounts:
    def __init__(self, database):
        self.database = database
        self.password_checks = BoundedSemaphore(2)
        self.sessions = {}
        self.session_lock = Lock()
        self.unknown_account_salt = token_bytes(16)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """CREATE TABLE IF NOT EXISTS accounts (
                    username TEXT PRIMARY KEY NOT NULL,
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    native_password_hash BLOB
                )"""
            )
            if "native_password_hash" not in {row[1] for row in db.execute("PRAGMA table_info(accounts)")}:
                db.execute("ALTER TABLE accounts ADD COLUMN native_password_hash BLOB")

    def password_hash(self, password, salt):
        if not self.password_checks.acquire(blocking=False):
            raise AccountError(503, "本机登录服务忙，请稍后重试。")
        try:
            return scrypt(password, salt=salt, n=2**15, r=8, p=3, maxmem=64 << 20, dklen=32)
        finally:
            self.password_checks.release()

    def register(self, credentials):
        username, password = validate_credentials(credentials)
        salt = token_bytes(16)
        digest = self.password_hash(password, salt)
        native_digest = self.password_hash(native_password(password), salt)
        with closing(sqlite3.connect(self.database)) as db, db:
            result = db.execute(
                "INSERT INTO accounts (username, salt, password_hash, native_password_hash) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(username) DO NOTHING",
                (username, salt, digest, native_digest),
            )
            if result.rowcount != 1:
                raise AccountError(409, "这个本机账号已注册。")
        return {"username": username}

    def login(self, credentials):
        username, password = validate_credentials(credentials)
        return self._login(username, password, native=False)

    def login_native(self, credentials):
        username, password = validate_credentials({
            **credentials, "username": native_username(credentials.get("username")),
        })
        if not NATIVE_PASSWORD.fullmatch(password):
            raise AccountError(401, "本机账号或密码不正确。")
        return self._login(username, password.lower(), native=True)

    def _login(self, username, password, *, native):
        with closing(sqlite3.connect(self.database)) as db:
            account = db.execute(
                "SELECT salt, password_hash, native_password_hash FROM accounts WHERE username = ?", (username,)
            ).fetchone()
        salt = account[0] if account else self.unknown_account_salt
        expected = account[2 if native else 1] if account else None
        expected = expected if expected is not None else bytes(32)
        matches = compare_digest(self.password_hash(password, salt), expected)
        if not account or not matches:
            raise AccountError(401, "本机账号或密码不正确。")
        if not native and account[2] is None:
            native_digest = self.password_hash(native_password(password), salt)
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute("UPDATE accounts SET native_password_hash = ? WHERE username = ? "
                           "AND native_password_hash IS NULL", (native_digest, username))

        token = token_urlsafe(32)
        with self.session_lock:
            now = monotonic()
            self.sessions = {key: until for key, until in self.sessions.items() if until > now}
            if len(self.sessions) >= 128:
                raise AccountError(503, "本机登录会话已满，请稍后重试。")
            self.sessions[sha256(token.encode("ascii")).digest()] = now + SESSION_SECONDS
        return {"username": username, "session_token": token, "expires_in": SESSION_SECONDS}

    def require_session(self, authorization):
        prefix = "Bearer "
        token = authorization[len(prefix):] if authorization.startswith(prefix) else ""
        if SESSION_TOKEN.fullmatch(token):
            key = sha256(token.encode("ascii")).digest()
            with self.session_lock:
                expires = self.sessions.get(key)
                if expires is not None and expires > monotonic():
                    return
                self.sessions.pop(key, None)
        raise AccountError(401, "请先登录本机账号。")


def validate_credentials(credentials):
    if not isinstance(credentials, dict) or credentials.keys() != {"username", "password"}:
        raise AccountError(400, "请提供本机账号和密码。")
    username = credentials["username"]
    password = credentials["password"]
    if not isinstance(username, str) or not USERNAME.fullmatch(username):
        raise AccountError(400, "账号须为 3 至 32 位字母、数字、点、下划线或连字符，并以字母或数字开头。")
    if isinstance(password, str) and len(password) >= MIN_PASSWORD_CHARACTERS:
        try:
            encoded = password.encode("utf-8")
        except UnicodeError:
            encoded = b""
        if MIN_PASSWORD_CHARACTERS <= len(encoded) <= 512:
            return username.lower(), encoded
    raise AccountError(400, f"密码至少需要 {MIN_PASSWORD_CHARACTERS} 个字符，最长为 512 字节。")


def run_account_client(host, port, action, username):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            password = getpass("本机账号密码：")
            if action == "register" and password != getpass("再次输入密码："):
                raise AccountError(400, "两次输入的密码不一致。")
        payload = {"username": username, "password": password}
        validate_credentials(payload)
        with closing(HTTPConnection(host, port, timeout=5)) as connection:
            connection.request("POST", f"/local/{action}", json.dumps(payload), {"Content-Type": "application/json"})
            response = connection.getresponse()
            account = json.loads(response.read(16 << 10))
            if response.status != (201 if action == "register" else 200):
                raise AccountError(response.status, account["error"])
            if action == "login":
                permission = {"Authorization": f"Bearer {account['session_token']}"}
                connection.request("GET", "/local/channels", headers=permission)
                response = connection.getresponse()
                channel_list = json.loads(response.read(16 << 10))
                if response.status != 200:
                    raise AccountError(response.status, channel_list["error"])
        if action == "register":
            print(f"已注册本机账号 {account['username']}。")
        else:
            print(f"本机账号 {account['username']} 已登录。")
            for channel in channel_list["channels"]:
                print(f"{channel['group_name']} / {channel['server_name']}")
        return 0
    except AccountError as error:
        print(error)
    except (EOFError, GetPassWarning):
        print("请在能隐藏密码输入的终端中运行此命令。")
    except (OSError, HTTPException, ValueError, KeyError, TypeError):
        print("无法连接本机账号服务，或服务返回了无效响应。")
    return 1
