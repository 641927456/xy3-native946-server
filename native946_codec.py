"""Native field encodings recovered from the 3.0.946 protocol registry."""

import json
from pathlib import Path
from struct import pack

from native946_game_login import message_id
from native946_login import string_field


SCHEMAS = json.loads(Path(__file__).with_name("native946_schema.json").read_text(encoding="utf-8"))


def number(value):
    value = int(value)
    magnitude = abs(value)
    width = max(1, (magnitude.bit_length() + 7) // 8)
    if width > 5:
        raise ValueError("Native number is outside the supported range")
    return bytes([width * 2 + (value < 0)]) + magnitude.to_bytes(width, "little")


def record(kind, values):
    result = bytearray()
    for name, field_type in SCHEMAS[kind]:
        value = values.get(name, "" if field_type == "string" else [] if isinstance(field_type, list) else 0)
        if field_type == "number":
            result.extend(number(value))
        elif field_type == "string":
            result.extend(string_field(value))
        elif field_type == "byte":
            result.extend(pack("<B", value))
        elif field_type == "word":
            result.extend(pack("<H", value))
        else:
            result.extend(field(field_type, value))
    return bytes(result)


def field(kind, value):
    if isinstance(kind, (tuple, list)) and kind[0] == "fixed_array":
        if len(value) != kind[2]:
            raise ValueError("Native fixed array has the wrong size")
        return b"".join(field(kind[1], item) for item in value)
    if isinstance(kind, (tuple, list)) and kind[0] == "array":
        if len(value) > 254:
            raise ValueError("Native array is too long")
        return bytes([len(value)]) + b"".join(field(kind[1], item) for item in value)
    if kind == "number":
        return number(value)
    if kind == "string":
        return string_field(value)
    if kind == "byte":
        return pack("<B", value)
    if kind == "word":
        return pack("<H", value)
    return record(kind, value)


def packet(mid, types, values):
    if len(types) != len(values):
        raise ValueError("Native packet field count differs from registry")
    # c_sys_gnotify has two required fields in revision946. Legacy callers
    # use a text-only shorthand; complete it here so every such notification
    # still reaches the original renderer without an unpack failure.
    if mid==97 and list(types)==['string']:
        types=[*types,'number'];values=[*values,0]
    return message_id(mid) + b"".join(field(kind, value) for kind, value in zip(types, values))


def character_list(characters):
    if len(characters) > 6:
        raise ValueError("The local account character limit was exceeded")
    return (message_id(1012) + bytes([len(characters)])
            + b"".join(record("player_info", character) for character in characters))


def text_notice(text):
    """Nonblocking Chinese text in system chat, using the native text renderer.

    c_sys_gnotify (97) uses UGString's limited char/*.tcp sprite alphabet.
    c_sys_message (98) preserves arbitrary text, links and native markup.
    """
    return packet(98, ['string', 'string', 'number', 'number'], [text, '', 1, 0])


def screen_notice(text, *, optimize_display=False):
    """Show native rich text on screen. MessageBox logs it when dismissed."""
    return packet(100, ['string', 'number'], [text, int(optimize_display)])
