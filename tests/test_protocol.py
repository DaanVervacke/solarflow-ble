from __future__ import annotations

import pytest
from solarflow_ble import (
    parse_advertisement,
)
from solarflow_ble.const import MAX_JSON_PAYLOAD_BYTES
from solarflow_ble.exceptions import (
    SolarFlowProtocolError,
)
from solarflow_ble.protocol import decode_json


def test_parse_advertisement() -> None:
    result = parse_advertisement("AA", {0x4F48: b"TEST_DEVICE\x16"}, rssi=-50)
    assert result is not None
    assert result.identifier == "TEST_DEVICE"


def test_parse_advertisement_preserves_passthrough_fields() -> None:
    result = parse_advertisement(
        "AA",
        {0x4F48: b"TEST_DEVICE\x16"},
        rssi=-50,
        connectable=False,
        address_type=1,
    )
    assert result is not None
    assert result.address == "AA"
    assert result.identifier == "TEST_DEVICE"
    assert result.rssi == -50
    assert result.connectable is False
    assert result.address_type == 1


def test_parse_advertisement_defaults_to_connectable_without_optional_fields() -> None:
    result = parse_advertisement("AA", {0x4F48: b"TEST_DEVICE"})
    assert result is not None
    assert result.rssi is None
    assert result.connectable is True
    assert result.address_type is None


@pytest.mark.parametrize("payload", [b"\xff", b"VALID\xff", b"\xff\x16"])
def test_parse_advertisement_ignores_malformed_identifier(payload: bytes) -> None:
    assert parse_advertisement("AA", {0x4F48: payload}) is None


@pytest.mark.parametrize("payload", [b"VALID", b"VALID\x16"])
def test_parse_advertisement_preserves_valid_identifier(payload: bytes) -> None:
    result = parse_advertisement("AA", {0x4F48: payload})
    assert result is not None
    assert result.identifier == "VALID"


def test_parse_advertisement_ignores_empty_identifier() -> None:
    assert parse_advertisement("AA", {0x4F48: b""}) is None
    assert parse_advertisement("AA", {0x4F48: b"\x16"}) is None


@pytest.mark.parametrize(
    "manufacturer_data",
    [{}, {0x1234: b"FOREIGN"}, {0x1234: b"FOREIGN", 0x4F48: b"VALID\x16"}],
)
def test_parse_advertisement_requires_solarflow_manufacturer_id(
    manufacturer_data: dict[int, bytes],
) -> None:
    result = parse_advertisement("AA", manufacturer_data)
    if 0x4F48 in manufacturer_data:
        assert result is not None
        assert result.identifier == "VALID"
    else:
        assert result is None


def test_decode_json_accepts_bytes_and_bytearray_payloads() -> None:
    assert decode_json(b'{"method":"report"}') == {"method": "report"}
    assert decode_json(bytearray(b'{"method":"report"}')) == {"method": "report"}


def test_decode_json_rejects_invalid_payloads() -> None:
    for payload in (b"not json", b'"scalar"', b"[1,2]"):
        with pytest.raises(SolarFlowProtocolError):
            decode_json(payload)


@pytest.mark.parametrize("payload", [b'"scalar"', b"[1,2]"])
def test_decode_json_rejects_valid_non_object_payloads(payload: bytes) -> None:
    with pytest.raises(SolarFlowProtocolError, match="not an object"):
        decode_json(payload)


def test_decode_json_enforces_payload_size_ceiling() -> None:
    padding = MAX_JSON_PAYLOAD_BYTES - len(b'{"pad":""}')
    at_ceiling = b'{"pad":"' + b"x" * padding + b'"}'
    assert len(at_ceiling) == MAX_JSON_PAYLOAD_BYTES
    assert decode_json(at_ceiling)["pad"] == "x" * padding
    with pytest.raises(SolarFlowProtocolError, match="size ceiling"):
        decode_json(b"x" + at_ceiling)
