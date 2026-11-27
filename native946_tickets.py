"""Short-lived local tickets for the native login-to-game transition."""

from secrets import token_hex
from threading import Lock
from time import monotonic

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class NativeTickets:
    def __init__(self):
        self.pending = {}
        self.lock = Lock()
        self.server_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        self.public_pem = self.server_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def sign_challenge(self, challenge):
        numbers = self.server_key.private_numbers()
        size = (numbers.public_numbers.n.bit_length() + 7) // 8
        if not 1 <= len(challenge) <= size - 11:
            raise ValueError("Unexpected native game challenge length")
        block = b"\x00\x01" + b"\xff" * (size - len(challenge) - 3) + b"\x00" + challenge
        return pow(int.from_bytes(block, "big"), numbers.d, numbers.public_numbers.n).to_bytes(size, "big")

    def redeem(self, signed_ticket, public_pem):
        key = serialization.load_pem_public_key(public_pem)
        if not isinstance(key, rsa.RSAPublicKey):
            raise ValueError("Unexpected native ticket key")
        numbers = key.public_numbers()
        size = (numbers.n.bit_length() + 7) // 8
        if len(signed_ticket) != size:
            raise ValueError("Invalid native ticket size")
        encoded = int.from_bytes(signed_ticket, "big")
        if encoded >= numbers.n:
            raise ValueError("Invalid native ticket signature")
        block = pow(encoded, numbers.e, numbers.n).to_bytes(size, "big")
        padding_end = block.find(b"\x00", 2)
        if (block[:2] != b"\x00\x01" or padding_end < 10
                or block[2:padding_end] != b"\xff" * (padding_end - 2)):
            raise ValueError("Invalid native ticket signature padding")
        ticket = block[padding_end + 1:]
        with self.lock:
            data = self.pending.get(ticket)
            if data is None or data[2] <= monotonic() or data[1].public_numbers() != numbers:
                raise ValueError("Native ticket is missing, expired, or belongs to another key")
            del self.pending[ticket]
        return data[0]

    def issue(self, username, public_pem):
        key = serialization.load_pem_public_key(public_pem)
        if not isinstance(key, rsa.RSAPublicKey) or not 1024 <= key.key_size <= 4096:
            raise ValueError("Unexpected native client public key")
        ticket = token_hex(24).encode("ascii")
        encrypted = key.encrypt(ticket, padding.PKCS1v15())
        with self.lock:
            now = monotonic()
            self.pending = {token: data for token, data in self.pending.items() if data[2] > now}
            self.pending[ticket] = (username, key, now + 120)
        return encrypted
