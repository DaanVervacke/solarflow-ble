"""SolarFlow protocol helpers."""

import json
from typing import Any

from .const import MANUFACTURER_ID
from .exceptions import SolarFlowProtocolError
from .models import Advertisement


def parse_advertisement(
    address: str,
    manufacturer_data: dict[int, bytes],
    *,
    rssi: int | None = None,
    connectable: bool = True,
    address_type: int | None = None,
) -> Advertisement | None:
    """Parse the SolarFlow manufacturer advertisement."""
    payload = manufacturer_data.get(MANUFACTURER_ID)
    if payload is None:
        return None
    identifier = (
        payload[:-1].decode("ascii")
        if payload.endswith(b"\x16")
        else payload.decode("ascii")
    )
    return (
        Advertisement(address, identifier, rssi, connectable, address_type)
        if identifier
        else None
    )


def decode_json(payload: bytes | bytearray) -> dict[str, Any]:
    """Decode one JSON notification."""
    try:
        value = json.loads(bytes(payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise SolarFlowProtocolError("Invalid SolarFlow JSON payload") from err
    if not isinstance(value, dict):
        raise SolarFlowProtocolError("SolarFlow payload is not an object")
    return value


def encode_json(message: dict[str, Any]) -> bytes:
    """Encode compact JSON for C304."""
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode()
