"""Shared redaction helpers for the developer scripts."""

from __future__ import annotations

from typing import Any

_REDACTED_KEYS = {
    "address": "DEVICE_ADDRESS",
    "apikey": "REDACTED",
    "deviceaddress": "DEVICE_ADDRESS",
    "deviceid": "DEVICE_ID",
    "identifier": "DEVICE_IDENTIFIER",
    "name": "DEVICE_NAME",
    "noisepsk": "REDACTED",
    "packserial": "PACK_SERIAL",
    "password": "REDACTED",
    "productkey": "PRODUCT_KEY",
    "secret": "REDACTED",
    "serial": "PACK_SERIAL",
    "serialnumber": "PACK_SERIAL",
    "sn": "PACK_SERIAL",
    "token": "REDACTED",
}


def _normal_key(key: object) -> str:
    return str(key).replace("_", "").replace("-", "").lower()


def redact_value(value: Any) -> Any:
    """Recursively redact identities, serials, and credentials in a value."""
    if isinstance(value, dict):
        return {
            key: _REDACTED_KEYS.get(_normal_key(key), redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    return value
