"""ULID: 48-bit millisecond timestamp + 80 bits of randomness, Crockford base32.

Lexicographically sortable, so listing jobs by id is listing them by creation time, and
unguessable, so a job id cannot be enumerated.
"""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford: no I, L, O, U
_LEN = 26


def new_id() -> str:
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    out = bytearray(_LEN)
    for i in range(_LEN - 1, -1, -1):
        out[i] = ord(_ALPHABET[value & 0x1F])
        value >>= 5
    return out.decode()


def is_valid(value: str) -> bool:
    return (
        isinstance(value, str) and len(value) == _LEN and all(c in _ALPHABET for c in value.upper())
    )
