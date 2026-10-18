"""Binary packet framing for the underground emergency mesh simulator."""

from __future__ import annotations

import struct
import zlib
from enum import IntEnum


class PacketType(IntEnum):
    HELLO = 0x00
    RREQ = 0x01
    RREP = 0x02
    DATA = 0x03
    ACK = 0x04


# type(1), source(2), destination(2), sequence(2), ttl(1), battery(1),
# RSSI metric(1), payload length(2), CRC32(4) = exactly 16 bytes.
HEADER_FORMAT = "!BHHHBBBH I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
CRC_OFFSET = HEADER_SIZE - 4
MAX_PAYLOAD = 65507 - HEADER_SIZE
BROADCAST_ID = 0xFFFF


class PacketError(ValueError):
    """Raised when a frame is malformed or its checksum is invalid."""


def create_packet(
    p_type: int,
    src: int,
    dst: int,
    seq: int,
    ttl: int,
    battery: int,
    rssi: int,
    payload: bytes = b"",
) -> bytes:
    payload = bytes(payload)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload exceeds UDP limit ({MAX_PAYLOAD} bytes)")
    if not 0 <= int(p_type) <= 255:
        raise ValueError("packet type must fit in one byte")
    for name, value in (("src", src), ("dst", dst), ("seq", seq)):
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"{name} must be between 0 and 65535")
    for name, value in (("ttl", ttl), ("battery", battery), ("rssi", rssi)):
        if not 0 <= value <= 255:
            raise ValueError(f"{name} must fit in one byte")
    prefix = struct.pack("!BHHHBBBH", int(p_type), src, dst, seq, ttl,
                         battery, rssi, len(payload))
    checksum = zlib.crc32(prefix + payload) & 0xFFFFFFFF
    return prefix + struct.pack("!I", checksum) + payload


def parse_packet(raw_bytes: bytes) -> dict:
    if len(raw_bytes) < HEADER_SIZE:
        raise PacketError(f"packet is shorter than {HEADER_SIZE}-byte header")
    (p_type, src, dst, seq, ttl, battery, rssi,
     payload_length, checksum) = struct.unpack(HEADER_FORMAT, raw_bytes[:HEADER_SIZE])
    payload = raw_bytes[HEADER_SIZE:]
    if len(payload) != payload_length:
        raise PacketError("payload length does not match header")
    prefix = raw_bytes[:CRC_OFFSET]
    expected = zlib.crc32(prefix + payload) & 0xFFFFFFFF
    if checksum != expected:
        raise PacketError("CRC32 checksum mismatch")
    try:
        packet_type: int | str = PacketType(p_type).name
    except ValueError:
        packet_type = p_type
    return {
        "type": packet_type,
        "type_code": p_type,
        "src": src,
        "dst": dst,
        "seq": seq,
        "ttl": ttl,
        "battery": battery,
        "rssi": rssi,
        "payload_length": payload_length,
        "crc32": checksum,
        "payload": payload,
    }


if HEADER_SIZE != 16:  # Keep the wire format contract explicit.
    raise RuntimeError(f"wire header must be 16 bytes, got {HEADER_SIZE}")
