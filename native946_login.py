"""Local login messages consumed by the isolated 3.0.946 native client."""

from local_accounts import AccountError


class MessageReader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def take(self, count):
        end = self.offset + count
        if count < 0 or end > len(self.data):
            raise ValueError("Incomplete native login message")
        result = self.data[self.offset:end]
        self.offset = end
        return result

    def integer(self):
        tag = self.take(1)[0]
        width = tag >> 1
        if not 1 <= width <= 5:
            raise ValueError("Invalid native login integer")
        magnitude = int.from_bytes(self.take(width), "little")
        return -magnitude if tag & 1 else magnitude

    def string(self):
        tag = self.take(1)[0]
        # Both wire forms store the byte count plus one. A zero byte
        # switches to the extended 16-bit count; it is not a raw length.
        count = tag if tag else int.from_bytes(self.take(2), "little")
        if count == 0:
            raise ValueError("Invalid native login string length")
        length = count - 1
        return self.take(length)

    def version(self):
        size = self.take(1)[0]
        if size != 3:
            raise ValueError("Unexpected native version array")
        return tuple(self.integer() for _ in range(size))

    def finish(self):
        if self.offset != len(self.data):
            raise ValueError("Native login message has trailing fields")


def string_field(value):
    if isinstance(value, str):
        value = value.encode("gb18030")
    if len(value) < 255:
        return bytes([len(value) + 1]) + value
    if len(value) > 65534:
        raise ValueError("Native string is too long")
    return b"\x00" + (len(value) + 1).to_bytes(2, "little") + value


class NativeLocalLogin:
    def __init__(self, source_port, accounts, number, configure, server_list, ticket_reply=None):
        self.source_port = source_port
        self.accounts = accounts
        self.number = number
        self.configure = configure
        self.server_list = server_list
        self.ticket_reply = ticket_reply
        self.client_public_key = None
        self.username = None
        self.last_ticket_request = None
        self.checked = False
        self.key_ready = False
        self.authenticated = False

    def receive(self, payload):
        reader = MessageReader(payload)
        message_id = reader.take(1)[0]
        if message_id == 1:
            version = reader.version()
            reader.version()
            reader.integer()  # Native protocol definition checksum.
            challenge = reader.string()
            reader.string()
            reader.string()
            mode = reader.integer()
            reader.finish()
            if version != (3, 0, 946) or mode not in (0, 1) or not 1 <= len(challenge) <= 64:
                raise ValueError("Unexpected native client check")
            self.configure(self.source_port, disable_identity=True)
            self.checked = True
            return bytes([2]) + self.number(1) + string_field(challenge) + self.number(mode)
        if message_id == 8 and self.checked:
            self.client_public_key = reader.string()
            mode = reader.integer()
            reader.finish()
            if mode not in (0, 1):
                raise ValueError("Unexpected native key mode")
            self.key_ready = True
            return bytes([3]) + self.number(0) + self.number(mode) + string_field(b"")
        if message_id == 7 and self.key_ready:
            username = reader.string().decode("gb18030")
            password = reader.string().decode("gb18030")
            reader.integer()
            reader.integer()
            reader.finish()
            try:
                account = self.accounts.login_native({"username": username, "password": password})
            except AccountError as error:
                self.authenticated = False
                return bytes([4]) + self.number(1) + string_field(str(error))
            self.authenticated = True
            self.username = account["username"]
            return self.server_list()
        if message_id == 17:
            route = tuple(reader.integer() for _ in range(3))
            hints = tuple(reader.string() for _ in range(2))
            reader.finish()
            self.last_ticket_request = {"route": route, "hint_lengths": [len(value) for value in hints]}
            if not self.authenticated or self.client_public_key is None:
                return bytes([4]) + self.number(1) + string_field("请先登录本机账号。")
            if self.ticket_reply is None:
                raise ValueError("The native game ticket endpoint is unavailable")
            return self.ticket_reply(self.username, self.client_public_key, route)
        return None
