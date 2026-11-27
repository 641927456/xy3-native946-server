"""Opt-in runtime adjustments for the isolated 3.0.946 loopback test client."""

import ctypes as ct
from ctypes import wintypes as w
from hashlib import sha256
from pathlib import Path
import socket
import struct


COPY = Path(__file__).resolve().parent / "client-946"
CLIENT_SHA256 = "93e958c243c611535ffde000b480a4fa2d1c1945aca321a7988cae382de15cbb"


class TestClient:
    def __init__(self, source_port):
        self.handle = None
        kernel = ct.WinDLL("kernel32", use_last_error=True)
        self.kernel = kernel
        kernel.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
        kernel.OpenProcess.restype = w.HANDLE
        kernel.CloseHandle.argtypes = (w.HANDLE,)
        kernel.QueryFullProcessImageNameW.argtypes = (w.HANDLE, w.DWORD, w.LPWSTR, ct.POINTER(w.DWORD))
        kernel.ReadProcessMemory.argtypes = (w.HANDLE, w.LPCVOID, w.LPVOID, ct.c_size_t, ct.POINTER(ct.c_size_t))
        kernel.WriteProcessMemory.argtypes = (w.HANDLE, w.LPVOID, w.LPCVOID, ct.c_size_t, ct.POINTER(ct.c_size_t))
        ip = ct.WinDLL("iphlpapi")
        capacity = w.DWORD(256 << 10)
        table = ct.create_string_buffer(capacity.value)
        result = ip.GetExtendedTcpTable(table, ct.byref(capacity), False, 2, 5, 0)
        if result:
            raise OSError(result, "Cannot identify the loopback client")
        matches = []
        for index in range(struct.unpack_from("<I", table)[0]):
            state, local, lp, remote, rp, pid = struct.unpack_from("<6I", table, 4 + 24 * index)
            if (state == 5 and local == remote == 0x0100007F
                    and socket.ntohs(lp & 0xFFFF) == source_port and socket.ntohs(rp & 0xFFFF) == 19127):
                matches.append(pid)
        if len(matches) != 1:
            raise ValueError("The native loopback peer is missing or ambiguous")
        self.pid = matches[0]
        self.handle = kernel.OpenProcess(0x1438, False, self.pid)
        if not self.handle:
            raise ct.WinError(ct.get_last_error())
        try:
            path = ct.create_unicode_buffer(2048)
            length = w.DWORD(len(path))
            if not kernel.QueryFullProcessImageNameW(self.handle, 0, path, ct.byref(length)):
                raise ct.WinError(ct.get_last_error())
            if Path(path.value) != COPY / "xymain.bin":
                raise ValueError("The loopback peer is outside the isolated test copy")
            if sha256((COPY / "xymain.bin").read_bytes()).hexdigest() != CLIENT_SHA256:
                raise ValueError("The 3.0.946 client build differs from the verified sample")
            psapi = ct.WinDLL("psapi", use_last_error=True)
            psapi.EnumProcessModulesEx.argtypes = (w.HANDLE, ct.POINTER(ct.c_void_p), w.DWORD, ct.POINTER(w.DWORD), w.DWORD)
            modules = (ct.c_void_p * 256)()
            needed = w.DWORD()
            if not psapi.EnumProcessModulesEx(self.handle, modules, ct.sizeof(modules), ct.byref(needed), 3):
                raise ct.WinError(ct.get_last_error())
            self.base = modules[0]
            if not self.base or self.read(self.base, 2) != b"MZ":
                raise ValueError("The client module base is invalid")
        except Exception:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def read(self, address, length):
        if not 0 < length <= 4 << 20:
            raise ValueError("Unexpected native memory read size")
        buffer = ct.create_string_buffer(length)
        count = ct.c_size_t()
        if not self.kernel.ReadProcessMemory(self.handle, address, buffer, length, ct.byref(count)) or count.value != length:
            raise ct.WinError(ct.get_last_error())
        return buffer.raw

    def integer(self, address):
        return struct.unpack("<I", self.read(address, 4))[0]

    def write_integer(self, address, before, after):
        if self.integer(address) != before:
            raise ValueError("The native setting changed during inspection")
        data = struct.pack("<I", after)
        count = ct.c_size_t()
        if not self.kernel.WriteProcessMemory(self.handle, address, data, 4, ct.byref(count)) or count.value != 4:
            raise ct.WinError(ct.get_last_error())
        if self.integer(address) != after:
            raise ValueError("The native setting update was not retained")

    def table_field(self, table, name):
        header = self.read(table + 4, 4)
        if header[0] != 5 or header[3] > 16:
            raise ValueError("Unexpected native script table layout")
        start = self.integer(table + 0x10)
        nodes = self.read(start, 32 * (1 << header[3]))
        encoded = name.encode("ascii")
        for position in range(0, len(nodes), 32):
            value, _, kind, _, key, _, key_kind, _ = struct.unpack_from("<8I", nodes, position)
            if key_kind == 4 and kind and self.integer(key + 12) == len(encoded):
                if self.read(key + 16, len(encoded)) == encoded:
                    return start + position, value, kind
        raise ValueError(f"The native script field {name} is unavailable")

    def select_local_login_mode(self):
        ntdll = ct.WinDLL("ntdll")
        ntdll.NtQueryInformationProcess.argtypes = (w.HANDLE, w.ULONG, w.LPVOID, w.ULONG, ct.POINTER(w.ULONG))
        peb = ct.c_size_t()
        returned = w.ULONG()
        status = ntdll.NtQueryInformationProcess(self.handle, 26, ct.byref(peb), ct.sizeof(peb), ct.byref(returned))
        if status or not peb.value:
            raise ValueError("The test client's WOW64 environment is unavailable")
        parameters = self.integer(peb.value + 0x10)
        environment = self.integer(parameters + 0x48)
        marker = "ngp_launch_port=0\x00".encode("utf-16le")
        block = self.read(environment, 8192)
        position = block.find(marker)
        if position >= 0:
            if position and block[position - 2:position] != b"\x00\x00":
                raise ValueError("The platform callback setting has an unexpected boundary")
            count = ct.c_size_t()
            if not self.kernel.WriteProcessMemory(self.handle, environment + position, b"_\x00", 2, ct.byref(count)) or count.value != 2:
                raise ct.WinError(ct.get_last_error())
            if self.read(environment + position, 2) != b"_\x00":
                raise ValueError("The local callback environment update was not retained")
        address = self.base + 0xDF1B28
        mode, port = struct.unpack("<II", self.read(address, 8))
        if mode == 3 and port == 0:
            self.write_integer(address, 3, 0)
        elif mode != 0:
            raise ValueError("The native client uses an external platform login mode")

    def disable_server_identity(self):
        vector = self.integer(self.base + 0xDF1F1C)
        entry = self.integer(vector + 2 * 4)
        reference = self.integer(entry + 0x24)
        application = self.integer(self.base + 0xDF0CF0)
        script = self.integer(application + 0x38)
        registry = self.integer(self.integer(script + 0x10) + 0x60)
        size = self.integer(registry + 0x1C)
        if not 0 < reference <= size <= 65536:
            raise ValueError("The native callback reference is invalid")
        array = self.integer(registry + 0xC)
        value = array + (reference - 1) * 16
        if self.integer(value + 8) != 6:
            raise ValueError("The native login callback is not a function")
        closure = self.integer(value)
        table = self.integer(closure + 0xC)
        for key in ("import", "_loaded", "dlg_new_login_panel"):
            _, table, kind = self.table_field(table, key)
            if kind != 5:
                raise ValueError("The native login module is not a table")
        address, enabled, kind = self.table_field(table, "rsa_enable")
        if kind != 1 or enabled not in (0, 1):
            raise ValueError("The native RSA flag is not a boolean")
        if enabled:
            self.write_integer(address, 1, 0)
        return {"client_pid": self.pid, "server_identity_enabled": False}

    def set_game_server_public_key(self, public_pem):
        """Configure the isolated client's existing game-key source for loopback."""
        if not public_pem.startswith(b"-----BEGIN PUBLIC KEY-----\n") or not 200 <= len(public_pem) <= 2048:
            raise ValueError("Unexpected local game server public key")
        # GameServerPublicKeyDecrypt reloads this PEM pointer before each
        # challenge check (verified 3.0.946 RVA 0x117D60).
        address = self.base + 0xDD9B58
        original = self.integer(address)
        prefix = self.read(original, 27)
        if not prefix.startswith(b"-----BEGIN PUBLIC KEY-----"):
            raise ValueError("The native game key source has changed")
        if self.read(original, len(public_pem) + 1) == public_pem + b"\x00":
            return {"client_pid": self.pid, "local_game_key_configured": True}
        self.kernel.VirtualAllocEx.argtypes = (w.HANDLE, w.LPVOID, ct.c_size_t, w.DWORD, w.DWORD)
        self.kernel.VirtualAllocEx.restype = w.LPVOID
        self.kernel.VirtualFreeEx.argtypes = (w.HANDLE, w.LPVOID, ct.c_size_t, w.DWORD)
        allocation = self.kernel.VirtualAllocEx(self.handle, None, len(public_pem) + 1, 0x3000, 4)
        if not allocation or allocation > 0xFFFFFFFF:
            raise ct.WinError(ct.get_last_error())
        try:
            data = public_pem + b"\x00"
            count = ct.c_size_t()
            if not self.kernel.WriteProcessMemory(self.handle, allocation, data, len(data), ct.byref(count)) or count.value != len(data):
                raise ct.WinError(ct.get_last_error())
            self.write_integer(address, original, allocation)
        except Exception:
            self.kernel.VirtualFreeEx(self.handle, allocation, 0, 0x8000)
            raise
        # This small read-only key buffer belongs to the client until it exits.
        return {"client_pid": self.pid, "local_game_key_configured": True}


def configure_loopback_client(source_port, disable_identity=False, game_public_key=None):
    client = TestClient(source_port)
    try:
        client.select_local_login_mode()
        if game_public_key is not None:
            result=client.set_game_server_public_key(game_public_key)
            from native946_client_ui import schedule_npc_ui
            schedule_npc_ui(source_port,client.pid)
            return result
        if disable_identity:
            return client.disable_server_identity()
        return {"client_pid": client.pid, "platform_mode": 0}
    finally:
        client.close()
