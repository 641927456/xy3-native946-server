"""Loopback endpoints for the XY3 native connection test."""

from native946_sqlite_runtime import bootstrap as prepare_sqlite_runtime
prepare_sqlite_runtime(strict=__name__=='__main__')

import argparse
from configparser import ConfigParser
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from secrets import randbelow
from queue import Empty, Queue
import socketserver
from struct import pack
from threading import BoundedSemaphore, Lock, Thread
from time import monotonic
from urllib.parse import urlsplit

from local_accounts import AccountError, LocalAccounts, SESSION_SECONDS, run_account_client
from native946_login import NativeLocalLogin, string_field
from native946_tickets import NativeTickets
import native946_game_login
from native946_hot_reload import LocalHotReload, RELOAD_PATH, TOKEN_HEADER, TOKEN_NAME
from native946_characters import Characters


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
HTTP_PORT = 18127
GAME_PORT = 19127
NATIVE_LOGIN_IDLE_TIMEOUT = 90
NATIVE_LOGIN_REQUESTS = {
    1: "s_l_check_client",
    7: "s_l_account_enter",
    8: "s_l_public_key",
    13: "s_l_group_servers",
    14: "s_l_grouplist",
    15: "s_l_serverlist",
    17: "s_l_get_ticket",
    1015: "s_g_check_pto",
    1016: "s_g_login_with_ticket",
    1017: "s_g_public_key",
    913: "s_login_player_add",
    915: "s_login_player_enter",
    2427: "s_choose_random_name",
    1643: "s_hero_move",
    1644: "s_hero_tele",1645:"s_npc_tele",
    1034: "s_npc_look",
    1032: "s_npc_answer",
    2176: "s_do_kill",
    2177: "s_operate_others",
    2178: "s_operate_self",
    2184: "s_turn_end",
    2185: "s_use",
    739: "s_hero_mod_point", 748: "s_hero_upgrade", 749: "s_learn_skill",
    742: "s_hero_skill_list", 803: "s_item_move", 795: "s_item_pure_move",
    804: "s_item_movel_all_temppage", 810: "s_temppage_item_spemove",
    842: "s_item_transfer", 3236: "s_storage_item_can_tidy", 3816: "s_storage_item_can_one_key_put_in",
    4512: "s_item_search_open", 4513: "s_item_search",
    805: "s_item_split", 807: "s_item_use", 806: "s_item_string", 808: "s_new_item_string",
    1483: "s_skill_shortcut_add", 1484: "s_skill_shortcut_del",
    1492: "s_task_accept", 1493: "s_task_accept1", 1986: "s_query_task_info", 1990: "s_task_trace_info",
    1985: "s_query_task",1980: "s_cancel_task",802: "s_item_merge",
        1390: "s_festival_icon_info",
        2574: "s_gr_get_charge_reward", 2575: "s_gr_get_reward", 2576: "s_gr_set_open_panel_flag",
}
BASE_URL = f"http://{HOST}:{HTTP_PORT}"
LOG_FILE = ROOT / "connections.jsonl"
LOG_LOCK = Lock()


@dataclass(frozen=True)
class LocalChannel:
    group_id: int
    group_x: int
    group_y: int
    group_name: str
    server_x: int
    server_y: int


LOCAL_CHANNELS = (
    LocalChannel(1, 1, 1, "本机推荐", 2, 1),
    LocalChannel(5, 2, 3, "本机测试", 1, 1),
)
LOCAL_SERVER_NAME = "本机接入"
LOCAL_SERVER_DESCRIPTION = "本机连接测试"
LOCAL_SERVER_STATUS = 4
LOCAL_LINE_TYPE = 1


def build_server_list(patch_version):
    endpoint = f"{HOST} {GAME_PORT} {LOCAL_LINE_TYPE}"
    server = (f"{LOCAL_SERVER_NAME} {endpoint} {endpoint} {endpoint} "
              f"{LOCAL_SERVER_STATUS} {LOCAL_SERVER_DESCRIPTION}")
    lines = [
        "NetType 1",
        f"PatchUrl {BASE_URL}/patch/",
        f"HttpUrl {BASE_URL}/notice.html",
        f"PatchVer {patch_version}",
        "X3PatchVer -1",
        "",
    ]
    lines.extend(
        f"ServerGroup {channel.group_id} {channel.group_x} {channel.group_y} {channel.group_name}"
        for channel in LOCAL_CHANNELS
    )
    lines.extend(
        f"{patch_version} {channel.group_id} {channel.server_x} {channel.server_y} {server}"
        for channel in LOCAL_CHANNELS
    )
    return ("\r\n".join(lines) + "\r\n").encode("gb18030")


def prepare_client(client, version, x3_version, server_list):
    for directory in ("patch", "log", "local", "temp"):
        (client / directory).mkdir(exist_ok=True)
    (client / "server.ini").write_bytes(server_list)
    (client / "update.ini").write_bytes(
        (
            "[Setting]\r\n"
            f"Version={version}\r\n"
            f"X3Version={x3_version}\r\n"
            f"ServerList={BASE_URL}/server.ini\r\n"
            f"PatchSHAList={BASE_URL}/patchsha.ini\r\n"
            f"Note2URL={BASE_URL}/notice.html\r\n"
        ).encode("ascii")
    )


def record(event, **details):
    entry = {"utc": datetime.now(timezone.utc).isoformat(), "event": event, **details}
    with LOG_LOCK, LOG_FILE.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


class UpdateHandler(BaseHTTPRequestHandler):
    def setup(self):
        BaseHTTPRequestHandler.setup(self)
        self.connection.settimeout(5)

    routes = {
        "/patchsha.ini": ("text/plain; charset=us-ascii", b""),
        "/notice.html": (
            "text/html; charset=utf-8",
            b"<!doctype html><html><title>Local connection test</title>"
            b"<body>Local connection test</body></html>",
        ),
    }

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/local/channels":
            try:
                accounts = self.local_account_service()
                headers = self.headers.get_all("Authorization", [])
                accounts.require_session(headers[0] if len(headers) == 1 else "")
            except AccountError as error:
                self.send_json(error.status, {"error": str(error)})
                return
            self.send_json(200, {
                "version": self.server.client_version,
                "channels": [
                    {"group_id": channel.group_id, "group_name": channel.group_name,
                     "server_name": LOCAL_SERVER_NAME}
                    for channel in LOCAL_CHANNELS
                ],
            })
            return
        resource = self.routes.get(path)
        record("http_request", path=path, source_port=self.client_address[1], found=resource is not None)
        if resource is None:
            self.send_error(404)
            return
        content_type, body = resource
        self.send_content(200, content_type, body)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == RELOAD_PATH:
            try:
                self.local_account_service()
                reload_control = getattr(self.server, "native_reload_control", None)
                if reload_control is None:
                    raise AccountError(404, "本机热加载未启用。")
                request = self.read_credentials()
                result = reload_control.submit(self.headers.get_all(TOKEN_HEADER, []), request)
                if request["action"] == "reload":
                    record("native_gameplay_reloaded", **result)
                self.send_json(200, result)
            except AccountError as error:
                self.send_json(error.status, {"error": str(error)})
            except Exception as error:
                record("native_gameplay_reload_failed", error_type=type(error).__name__, reason=str(error))
                self.send_json(500, {"error": "本机玩法热加载失败，详见服务端日志。"})
            return
        if path == "/local/native-test":
            try:
                self.local_account_service()  # Reject browser origins and foreign Host headers.
                control = getattr(self.server, "native_test_control", None)
                if control is None:
                    raise AccountError(404, "本机测试控制未启用。")
                from native946_control import LocalTestControl
                if control.__class__ is not LocalTestControl:
                    control.__class__=LocalTestControl
                control.ensure_runtime()
                result = control.submit(self.headers.get("X-Local-Test-Token", ""), self.read_credentials())
                self.send_json(200, result)
            except (ValueError, AccountError) as error:
                details = {"error": str(error)}
                for key in ('command_id','outcome'):
                    if getattr(error,key,None) is not None:
                        details[key] = getattr(error,key)
                self.send_json(getattr(error, "status", 400), details)
            return
        if path not in ("/local/register", "/local/login"):
            self.send_error(501)
            return
        try:
            accounts = self.local_account_service()
            credentials = self.read_credentials()
            if path == "/local/register":
                self.send_json(201, accounts.register(credentials))
            else:
                self.send_json(200, accounts.login(credentials))
        except AccountError as error:
            self.send_json(error.status, {"error": str(error)})

    def local_account_service(self):
        accounts = getattr(self.server, "local_accounts", None)
        if accounts is None:
            raise AccountError(404, "当前版本未启用本机账号服务。")
        expected_host = f"{HOST}:{self.server.server_port}"
        if self.headers.get_all("Host") != [expected_host] or "Origin" in self.headers:
            raise AccountError(403, "请使用本机账号客户端。")
        return accounts

    def read_credentials(self):
        lengths = self.headers.get_all("Content-Length", [])
        if (self.headers.get_content_type() != "application/json" or "Transfer-Encoding" in self.headers
                or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,4}", lengths[0])):
            raise AccountError(400, "本机账号请求的格式无效。")
        length = int(lengths[0])
        if not 0 < length <= 4096:
            raise AccountError(400, "本机账号请求的长度无效。")
        self.connection.settimeout(5)
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("Incomplete request body")
            return json.loads(body.decode("utf-8"))
        except (ValueError, RecursionError, TimeoutError):
            raise AccountError(400, "本机账号请求的内容无效。") from None

    def send_json(self, status, payload):
        self.send_content(status, "application/json; charset=utf-8",
                          json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def send_content(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def native_channel_integer(value):
    width = max(1, (value.bit_length() + 7) // 8)
    return bytes([width * 2]) + value.to_bytes(width, "little")


def native_channel_text(value):
    encoded = value.encode("gb18030")
    return bytes([len(encoded) + 1]) + encoded


def native_server_record(channel, version):
    number = native_channel_integer
    string = native_channel_text
    # Order follows the original client's server record registration.
    return b"".join((
        number(LOCAL_LINE_TYPE),  # type2
        string(channel.group_name),
        number(LOCAL_SERVER_STATUS),
        string(HOST),  # ip3
        number(GAME_PORT),  # port1
        number(LOCAL_LINE_TYPE),  # type1
        number(LOCAL_LINE_TYPE),  # type3
        number(channel.group_id),
        string(HOST),  # ip2
        number(channel.server_y),
        number(channel.server_x),
        number(version),
        string(LOCAL_SERVER_DESCRIPTION),
        string(HOST),  # ip1
        number(GAME_PORT),  # port3
        number(GAME_PORT),  # port2
        string(LOCAL_SERVER_NAME),
    ))


def native_channel_reply(request_name, group_id, version):
    if request_name == "s_l_grouplist":
        groups = bytearray((11, len(LOCAL_CHANNELS)))
        for channel in LOCAL_CHANNELS:
            groups.extend(native_channel_integer(channel.group_id))
            groups.extend(native_channel_integer(channel.group_x))
            groups.extend(native_channel_text(channel.group_name))
            groups.extend(native_channel_integer(channel.group_y))
        groups.extend(native_server_record(LOCAL_CHANNELS[0], version))
        return bytes(groups)
    if request_name == "s_l_group_servers" and group_id is not None:
        matches = [channel for channel in LOCAL_CHANNELS if channel.group_id == group_id]
        return bytes([10, len(matches)]) + b"".join(
            native_server_record(channel, version) for channel in matches
        )
    return None


def native_user_server_list(version):
    records = b"".join(native_server_record(channel, version) for channel in LOCAL_CHANNELS)
    return (bytes([12, len(LOCAL_CHANNELS)]) + records + native_channel_integer(0)
            + native_server_record(LOCAL_CHANNELS[0], version))


def native_ticket_reply(tickets, username, public_key, route):
    # Both displayed groups lead to this same isolated local game endpoint.
    channel = next((item for item in LOCAL_CHANNELS if item.group_id == route[0]), LOCAL_CHANNELS[0])
    ticket = tickets.issue(username, public_key)
    return (b"\x10" + string_field(ticket) + string_field(HOST)
            + native_channel_integer(GAME_PORT) + string_field(channel.group_name)
            + string_field(LOCAL_SERVER_NAME) + native_channel_integer(0))


def native_receive_block(payload):
    """Encode one server message as the client's 12-bit literal codes and block delimiter."""
    pending = 0
    bits = 0
    block = bytearray()
    for code in (*payload, 0xFFF):
        pending = (pending << 12) | code
        bits += 12
        while bits >= 8:
            bits -= 8
            block.append(pending >> bits)
            pending &= (1 << bits) - 1
    if bits:
        block.append(pending << (8 - bits))
    return bytes(block)


class Native946Frames:
    """Read frame boundaries for a session initialized by this local server."""

    def __init__(self, seed):
        table = bytearray()
        state = seed
        for _ in range(256):
            table.append(state & 0xFF)
            state = (state * 9 + (state >> 3)) & 0xFFFFFFFF
            if state & 0x80000000:
                state -= 1 << 32
        self.table = bytes(table)
        self.offset = 0
        self.pending = bytearray()
        self.awaiting_first_frame = True

    def feed(self, data):
        self.pending.extend(
            byte ^ self.table[(self.offset + index + 1) & 0xFF]
            for index, byte in enumerate(data)
        )
        self.offset = (self.offset + len(data)) & 0xFF
        while len(self.pending) >= 2:
            size = int.from_bytes(self.pending[:2], "little")
            if size >= 0x8000:
                raise ValueError("Frame length exceeds the 3.0.946 payload limit")
            frame_end = size + 2
            if len(self.pending) < frame_end:
                break
            acknowledgment = self.awaiting_first_frame and size == 1 and self.pending[2] == ord("a")
            request_name = None
            group_id = None
            if size and not acknowledgment:
                packet_id = self.pending[2]
                argument = 3
                if packet_id == 0 and size >= 3:
                    packet_id = int.from_bytes(self.pending[3:5], "little")
                    argument = 5
                request_name = NATIVE_LOGIN_REQUESTS.get(packet_id)
                if request_name == "s_l_grouplist" and frame_end != argument:
                    request_name = None
                if request_name == "s_l_group_servers" and frame_end > argument:
                    tag = self.pending[argument]
                    width = tag >> 1
                    if 1 <= width <= 5 and argument + 1 + width == frame_end:
                        group_id = int.from_bytes(self.pending[argument + 1:frame_end], "little")
                        if tag & 1:
                            group_id = -group_id
            self.awaiting_first_frame = False
            payload = bytes(self.pending[2:frame_end])
            del self.pending[:frame_end]
            yield size, acknowledgment, request_name, group_id, payload


class ConnectionHandler(socketserver.BaseRequestHandler):
    def setup(self):
        from native946_runtime import WakeableSocket
        from native946_connections import enable_thread
        enable_thread()
        self.request = WakeableSocket(self.request)

    def finish(self):
        from native946_connections import close_thread
        close_thread()
        control=getattr(getattr(self,'server',None),'native_test_control',None)
        if control is not None:control.unregister(self.client_address[1])
        reload_control = getattr(getattr(self, "server", None), "native_reload_control", None)
        with reload_control.game_call() if reload_control else nullcontext():
            if reload_control:
                reload_control.unregister(getattr(self, "native_game_login", None))
            ConnectionHandler.detach_game(self)
        from native946_runtime import WakeableSocket
        if isinstance(self.request, WakeableSocket):
            self.request.close_waker()

    def detach_game(self):
        from native946_sessions import LEASES
        characters=getattr(getattr(self,'server',None),'characters',None)
        if characters is not None:
            from native946_party import service
            service(characters.database).detach_port(self.client_address[1])
            LEASES.release_port(characters.database,self.client_address[1])
        active_login=getattr(self,'native_game_login',None)
        if getattr(active_login,'_acceptance_server',None) is not None:
            from native946_acceptance import cleanup
            cleanup(active_login)

    def send_gameplay_tick(self, game_login, source_port):
        from native946_acceptance import late_commands
        late_commands(self,game_login,source_port)
        game=game_login.gameplay if game_login is not None else None
        if game is None:
            tick=getattr(game_login,'tick',None)
            reply=tick() if tick is not None else None
        else:
            reply=game.tick()
            if getattr(game_login,'_acceptance_late_attach',False):
                from native946_acceptance import decorate_packets
                reply=decorate_packets(game,reply)
        if game is None and getattr(game_login,'_acceptance_server',None) is not None:
            from native946_acceptance import cleanup
            cleanup(game_login,notify=True)
        if reply is None:return
        messages=reply if isinstance(reply,tuple) else (reply,)
        self.request.sendall(b''.join(native_receive_block(message) for message in messages))
        if game is not None and game.last_event:
            record('native_gameplay',source_port=source_port,**game.last_event)
            game.last_event=None

    def send_test_commands(self, control, game_login, commands, source_port):
        from native946_control import execute, state
        from native946_runtime import prepare_handler, METRICS, COMMAND_BATCH, COMMAND_BUDGET_SECONDS
        if getattr(game_login,'lease_version',0)<2 and hasattr(game_login,'tickets'):
            from importlib import reload
            import native946_game_login
            if getattr(native946_game_login.NativeGameLogin,'lease_version',0)<2:reload(native946_game_login)
            game_login.__class__=native946_game_login.NativeGameLogin
        prepare_handler(self,control,game_login,commands,source_port)
        from native946_runtime import WakeableSocket
        if isinstance(getattr(self,'request',None),WakeableSocket):
            from native946_connections import enable_thread
            enable_thread()
        characters=getattr(game_login,'characters',None)
        if characters is not None:
            characters.enable_deferred_positions()
            control.position_writer=characters.position_writer
            if game_login.current_character is not None:
                from native946_sessions import LEASES
                LEASES.claim(characters.database,game_login.current_character['id'],game_login)
        with control.lock:
            binding = control.sessions.get(source_port)
        if binding is None:
            return
        batch_start = monotonic()
        for _ in range(COMMAND_BATCH):
            if monotonic()-batch_start >= COMMAND_BUDGET_SECONDS:
                break
            try:
                command = commands.get_nowait()
            except Empty:
                break
            if not hasattr(command,'begin'):
                command.result = {'error':'Legacy pending command cancelled during update'}
                command.done.set()
                continue
            if not command.begin(binding.epoch):
                METRICS.increment('command_cancelled_before_execution')
                continue
            METRICS.observe('command_queue',command.started-command.created)
            result = {'error':'Command interrupted','effect_status':'unknown'}
            try:
                reply = execute(game_login, command.request)
                delivery_error = None
                if reply is not None:
                    messages = reply if isinstance(reply,tuple) else (reply,)
                    try:
                        self.request.sendall(b''.join(native_receive_block(message) for message in messages))
                    except OSError as error:
                        delivery_error = type(error).__name__
                try:
                    result = state(game_login)
                except Exception as error:
                    # The operation may already have committed. A subsequent
                    # diagnostic read failure must not invite a second mutation.
                    result = {'state_available':False,'warning':str(error)}
                if delivery_error is not None:
                    result['delivery_error'] = delivery_error
                result['command_processed'] = True
                record("native_background_test", action=command.request["action"],
                       source_port=source_port, command_id=command.command_id)
            except Exception as error:
                # A diagnostic request must not tear down the user's game socket.
                result = {"error": str(error),'effect_status':'unknown'}
            finally:
                command.complete(result)
                METRICS.observe('command_execution',command.finished-command.started)
        if not commands.empty():
            binding.wake() if binding.wake is not None else None

    def handle(self):
        source_port = self.client_address[1]
        started = monotonic()
        bytes_received = 0
        end_event = "tcp_peer_closed"
        frames = None
        native_acknowledged = False
        login = None
        game_login = None
        control = getattr(self.server, "native_test_control", None)
        reload_control = getattr(self.server, "native_reload_control", None)
        game_call = reload_control.game_call if reload_control else nullcontext
        commands = Queue(maxsize=8)
        last_received = monotonic()
        record("tcp_accepted", source_port=source_port)
        # Interactive account entry can legitimately outlast the handshake probe.
        idle_timeout = (SESSION_SECONDS if self.server.native_local_login else
                        NATIVE_LOGIN_IDLE_TIMEOUT if self.server.native_bootstrap else 8)
        # Native combat input deadlines must advance even with no test API
        # calls or client traffic. Keep the existing overall idle deadline.
        self.request.settimeout(0.1 if control or self.server.native_local_login else idle_timeout)
        try:
            if self.server.native_bootstrap:
                if self.server.native_local_login:
                    from native946_test_client import configure_loopback_client
                    # Only in-process fixtures may replace this callback. The
                    # running service always verifies its owned native client.
                    configure = getattr(self.server,'native_peer_configure',configure_loopback_client)
                    peer = configure(source_port)
                    record("native_local_mode", source_port=source_port, **peer)
                    login = NativeLocalLogin(source_port, self.server.local_accounts,
                                             native_channel_integer, configure,
                                             lambda: native_user_server_list(self.server.patch_version),
                                             lambda username, key, route: native_ticket_reply(
                                                 self.server.local_tickets, username, key, route))
                    with game_call():
                        game_login = native946_game_login.NativeGameLogin(
                            source_port, self.server.local_tickets, native_channel_integer,
                            configure, self.server.characters)
                        game_login.native_wake = self.request.wake
                        self.native_game_login = game_login
                        if reload_control:
                            reload_control.register(game_login)
                # The 3.0.946 initial receive path consumes a length byte and a local session seed.
                seed = randbelow(1 << 30) + 1
                bootstrap = pack("<BI", 4, seed)
                self.request.sendall(bootstrap)
                record("tcp_bootstrap_sent", source_port=source_port, byte_count=len(bootstrap))
                frames = Native946Frames(seed)
            while True:
                if control and game_login is not None:
                    self.send_test_commands(control, game_login, commands, source_port)
                with game_call():
                    self.send_gameplay_tick(game_login, source_port)
                try:
                    data = self.request.recv(4096)
                except TimeoutError:
                    if monotonic() - last_received >= idle_timeout:
                        raise
                    continue
                if not data:
                    break
                last_received = monotonic()
                bytes_received += len(data)
                record("tcp_received", source_port=source_port, byte_count=len(data))
                if frames is not None:
                    try:
                        for size, acknowledgment, request_name, group_id, payload in frames.feed(data):
                            native_acknowledged |= acknowledgment
                            record(
                                "tcp_native_frame", source_port=source_port, payload_bytes=size,
                                bootstrap_acknowledgment=acknowledgment, request_name=request_name,
                                message_id=(int.from_bytes(payload[1:3], "little")
                                            if payload[:1] == b"\x00" else payload[0] if payload else None),
                            )
                            if native_acknowledged:
                                receiver = game_login if game_login is not None and (
                                    game_login.checked or request_name in (
                                        "s_g_check_pto", "s_g_public_key", "s_g_login_with_ticket")) else login
                                with game_call():
                                    reply = receiver.receive(payload) if receiver is not None and not acknowledgment else None
                                if reply is None:
                                    reply = native_channel_reply(request_name, group_id, self.server.patch_version)
                                if reply is not None:
                                    messages=reply if isinstance(reply,tuple) else (reply,)
                                    self.request.sendall(b''.join(native_receive_block(message) for message in messages))
                                    for message in messages:
                                        record(
                                            "tcp_native_reply", source_port=source_port,
                                            request_name=request_name, group_id=group_id,
                                            message_id=(int.from_bytes(message[1:3], "little")
                                                        if message[0] == 0 else message[0]),
                                        )
                                    if login is not None and request_name == "s_l_account_enter":
                                        record("native_local_account_result", source_port=source_port,
                                               authenticated=login.authenticated)
                                    if login is not None and request_name == "s_l_get_ticket":
                                        record("native_local_ticket_issued", source_port=source_port,
                                               **login.last_ticket_request)
                                    if game_login is not None and request_name == "s_g_login_with_ticket":
                                        record("native_local_game_account", source_port=source_port,
                                               authenticated=game_login.username is not None,
                                               character_count=len(self.server.characters.list(game_login.username)))
                                    if game_login is not None and request_name == "s_login_player_add" and game_login.created_character:
                                        record("native_character_created", source_port=source_port,
                                               character_id=game_login.created_character["id"],
                                               role_id=game_login.created_character["role_id"])
                                    if game_login is not None and request_name == "s_login_player_enter" and game_login.current_character:
                                        record("native_character_world_sent", source_port=source_port,
                                               character_id=game_login.current_character["id"],
                                               map_id=game_login.current_character["map_id"])
                                    if game_login is not None and game_login.gameplay is not None and game_login.gameplay.last_event:
                                        record("native_gameplay", source_port=source_port,
                                               **game_login.gameplay.last_event)
                                        game_login.gameplay.last_event = None
                    except ValueError as error:
                        record("tcp_native_frame_error", source_port=source_port, reason=str(error))
                        frames = None
        except TimeoutError:
            end_event = "tcp_idle_timeout"
        except ConnectionError:
            end_event = "tcp_disconnected"
        except (OSError, ValueError) as error:
            record("native_local_login_error", source_port=source_port, error_type=type(error).__name__)
            end_event = "tcp_local_login_failed"
        finally:
            if control:
                control.unregister(source_port)
        record(
            end_event,
            source_port=source_port,
            bytes_received=bytes_received,
            elapsed_ms=round((monotonic() - started) * 1000),
        )


class LoopbackListener(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False
    native_bootstrap = False
    native_local_login = False
    patch_version = 0
    request_queue_size = 128
    max_connections = 64

    def process_request(self, request, client_address):
        if not hasattr(self,'_connection_slots'):
            self._connection_slots = BoundedSemaphore(self.max_connections)
        if not self._connection_slots.acquire(blocking=False):
            from native946_runtime import METRICS
            METRICS.increment('connection_admission_rejected')
            self.shutdown_request(request)
            return
        try:
            socketserver.ThreadingMixIn.process_request(self,request,client_address)
        except BaseException:
            self._connection_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            socketserver.ThreadingMixIn.process_request_thread(self,request,client_address)
        finally:
            self._connection_slots.release()


class DatagramHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, _ = self.request
        record("udp_received", source_port=self.client_address[1], byte_count=len(data))


class LocalHTTPServer(ThreadingHTTPServer):
    request_queue_size = 128
    max_connections = 32
    process_request = LoopbackListener.process_request
    process_request_thread = LoopbackListener.process_request_thread


def main():
    global LOG_FILE, record

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", default="client", help="client snapshot directory under this project")
    parser.add_argument("--native-local-login", action="store_true",
                        help="enable the isolated 3.0.946 runtime test login adjustments")
    parser.add_argument("--native-test-character", type=int,
                        help="opt in to authenticated loopback-only test controls for one character")
    parser.add_argument("--native-trial-server", action="store_true",
                        help="use original trial-server item rules and a separate local account database")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--prepare", action="store_true", help="write the isolated client configuration")
    action.add_argument("--local-account", nargs=2, metavar=("ACTION", "USERNAME"),
                        help="register or log in and list channels through the loopback account client")
    options = parser.parse_args()
    if options.native_trial_server and (
        not options.native_local_login or options.prepare or options.local_account
    ):
        parser.error("--native-trial-server requires serving with --native-local-login")
    if options.local_account:
        command, username = options.local_account
        if command not in ("register", "login"):
            parser.error("choose register or login for --local-account")
        raise SystemExit(run_account_client(HOST, HTTP_PORT, command, username))
    client = (ROOT / options.client).resolve()
    if client.parent != ROOT or not (client / "xymain.bin").is_file():
        parser.error("select a classic client snapshot under this project")

    settings = ConfigParser(interpolation=None)
    settings.read(client / "update.ini", encoding="utf-8-sig")
    version = settings.get("Setting", "Version")
    x3_version = settings.get("Setting", "X3Version")
    patch_version = int(version.rsplit(".", maxsplit=1)[-1])
    if options.native_local_login and (client.name != "client-946" or version != "3.0.946"):
        parser.error("native local login requires the isolated client-946 build")
    if options.native_test_character is not None and not options.native_local_login:
        parser.error("background controls require the isolated native local login mode")
    server_list = build_server_list(patch_version)
    if options.prepare:
        prepare_client(client, version, x3_version, server_list)
        return

    if client.name != "client":
        LOG_FILE = ROOT / f"connections-{client.name.removeprefix('client-')}.jsonl"
    from native946_runtime import AsyncRecorder
    record = AsyncRecorder(LOG_FILE)
    UpdateHandler.routes["/server.ini"] = ("text/plain; charset=gb18030", server_list)
    with (
        LocalHTTPServer((HOST, HTTP_PORT), UpdateHandler) as http,
        LoopbackListener((HOST, GAME_PORT), ConnectionHandler) as game,
        socketserver.ThreadingUDPServer((HOST, GAME_PORT), DatagramHandler) as udp,
    ):
        game.native_bootstrap = version == "3.0.946"
        game.patch_version = patch_version
        game.native_local_login = options.native_local_login
        game.local_tickets = NativeTickets()
        if options.native_test_character is not None:
            from native946_control import LocalTestControl
            control = LocalTestControl(options.native_test_character, ROOT / ".native-test-token")
            http.native_test_control = game.native_test_control = control
        http.client_version = version
        if game.native_bootstrap:
            suffix = "-trial" if options.native_trial_server else ""
            account_database = ROOT / f"{client.name}{suffix}-accounts.sqlite3"
            if options.native_local_login:
                from native946_sqlite_runtime import configure_database
                game.sqlite_runtime=configure_database(account_database)
            http.local_accounts = LocalAccounts(account_database)
            game.local_accounts = http.local_accounts
            game.characters = Characters(account_database, trial_server=options.native_trial_server)
            game.characters.enable_deferred_positions()
            if options.native_local_login and options.native_test_character is None:
                reload_control = LocalHotReload(account_database, ROOT / TOKEN_NAME)
                http.native_reload_control = game.native_reload_control = reload_control
            from native946_world import NPC_VISIBILITY_VERSION, world
            world().warm()
            from native946_timed_npcs import TIMED_NPC_VERSION
            if getattr(game,'native_test_control',None) is not None:
                game.native_test_control.position_writer=game.characters.position_writer
        Thread(target=http.serve_forever, daemon=True).start()
        Thread(target=udp.serve_forever, daemon=True).start()
        record(
            "listening", host=HOST, http_port=HTTP_PORT, game_port=GAME_PORT, udp=True,
            version=version, native_bootstrap=game.native_bootstrap, native_local_login=game.native_local_login,
            native_trial_server=options.native_trial_server,
            native_hot_reload=getattr(game, "native_reload_control", None) is not None,
            world_scenes=len(world().scenes) if game.native_bootstrap else 0,
            world_portals=len(world().data['portals']) if game.native_bootstrap else 0,
            world_npc_routes=len(world().data['npc_transports']) if game.native_bootstrap else 0,
            world_static_npcs=sum(len(rows) for rows in world().npcs.values()) if game.native_bootstrap else 0,
            world_npc_scenes=sum(bool(rows) for rows in world().npcs.values()) if game.native_bootstrap else 0,
            npc_visibility_version=NPC_VISIBILITY_VERSION if game.native_bootstrap else 0,
            timed_activity_npc_version=TIMED_NPC_VERSION if game.native_bootstrap else 0,
            world_closed_event_npcs=sum(len(world().closed_event_npcs(sid)) for sid in world().npcs) if game.native_bootstrap else 0,
            sqlite_runtime=getattr(game,'sqlite_runtime',None),
        )
        try:
            game.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            http.shutdown()
            udp.shutdown()
            if getattr(getattr(game,'characters',None),'position_writer',None) is not None:
                game.characters.position_writer.close(2)
            if getattr(game,'sqlite_runtime',None) is not None:
                from native946_durable_writer import close as close_durable_writer
                close_durable_writer(game.characters.database)
            record.flush()
            record.close()


if __name__ == "__main__":
    main()
