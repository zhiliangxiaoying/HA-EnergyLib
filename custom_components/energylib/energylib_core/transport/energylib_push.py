"""Recognize only the observed 28-byte EnergyLIB logger envelope.

The five bytes after 0x7e are opaque. No meaning or identity is inferred from
them or the payload. CRC/framing were checked against private hardware captures.
Other sizes/versions remain unsupported and must fail normal Modbus validation.
"""


def crc16(data: bytes) -> int:
    """CRC-16/MODBUS; the logger stores the result most significant byte first."""
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xA001 if value & 1 else value >> 1
    return value


async def skip_logger_push(header: bytes, reader) -> bool:
    """Consume a validated envelope at a frame boundary, never scan for markers."""
    if len(header) != 7 or header[0] != 0x7E or header[6] != 1:
        return False
    length = await reader.readexactly(2)
    if length != b"\x00\x10":
        return False
    tail = await reader.readexactly(19)  # 16 payload + 2 CRC + 0x0d.
    frame = header + length + tail
    return frame[-1] == 0x0D and crc16(frame[1:-3]) == int.from_bytes(frame[-3:-1], "big")
