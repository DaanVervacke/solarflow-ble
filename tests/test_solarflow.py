from __future__ import annotations

import json
from pathlib import Path

import pytest

from solarflow_ble import SolarFlowClient, SolarFlowState, parse_advertisement
from solarflow_ble.const import NOTIFY_CHARACTERISTIC_UUID
from solarflow_ble.exceptions import SolarFlowValidationError


def test_parse_advertisement() -> None:
    result = parse_advertisement("AA", {0x4F48: b"TEST_DEVICE\x16"}, rssi=-50)
    assert result is not None
    assert result.identifier == "TEST_DEVICE"

def test_state_derives_battery_power() -> None:
    state = SolarFlowState().update({"packInputPower": 100, "outputPackPower": 400})
    assert state.battery_power == 300


def test_captured_report_fixture_contains_two_packs() -> None:
    fixture = Path(__file__).parent / "fixtures" / "solarflow_reports.jsonl"
    records = [json.loads(line)["json"] for line in fixture.read_text().splitlines()]
    pack_records = [record for record in records if isinstance(record, dict) and "packData" in record]
    assert len(pack_records) >= 3
    assert all("sn" in pack for record in pack_records for pack in record["packData"])


def test_report_fields_merge() -> None:
    state = SolarFlowState()
    state = state.update({"electricLevel": 26, "smartMode": 0})
    state = state.update({"packInputPower": 10, "outputPackPower": 40})
    assert state.electric_level == 26
    assert state.smart_mode == 0
    assert state.battery_power == 30

class FakeTransport:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.callback = None

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass
    async def start_notify(self, characteristic, callback) -> None:
        assert characteristic == NOTIFY_CHARACTERISTIC_UUID
        self.callback = callback
    async def stop_notify(self, characteristic) -> None: pass
    async def write_gatt_char(self, characteristic, data, response=False) -> None:
        self.writes.append(data)
        if self.callback and b'"method":"getInfo"' in data:
            await self.callback(NOTIFY_CHARACTERISTIC_UUID, b'{"method":"getInfo-rsp"}')
        if self.callback and b'"method":"read"' in data:
            await self.callback(
                NOTIFY_CHARACTERISTIC_UUID,
                b'{"method":"report","properties":{"smartMode":1}}',
            )


class ReportTransport(FakeTransport):
    """Fake transport that emits captured-style reports."""

    async def write_gatt_char(self, characteristic, data, response=False) -> None:
        await super().write_gatt_char(characteristic, data, response)
        if not self.callback:
            return
        if b'"method":"getInfo"' in data:
            await self.callback(NOTIFY_CHARACTERISTIC_UUID, b'{"method":"getInfo-rsp","messageId":1}')
        elif b'"method":"read"' in data:
            await self.callback(NOTIFY_CHARACTERISTIC_UUID, b'{"method":"report","messageId":1,"properties":{"smartMode":0,"electricLevel":26}}')
            await self.callback(NOTIFY_CHARACTERISTIC_UUID, b'{"method":"report","messageId":1,"packData":[{"sn":"PACK-1","socLevel":25},{"sn":"PACK-2","socLevel":26}]}')
        if self.callback and b'"method":"read"' in data:
            await self.callback(
                NOTIFY_CHARACTERISTIC_UUID,
                b'{"messageId":"11","properties":{"smartMode":1}}',
            )

@pytest.mark.asyncio
async def test_connect_handshake() -> None:
    transport = FakeTransport()
    client = SolarFlowClient(transport, response_timeout=0.1, keepalive_seconds=60)
    await client.connect()
    assert client.ready
    assert len(transport.writes) == 3
    await client.disconnect()


@pytest.mark.asyncio
async def test_captured_report_stream_stays_protocol_ready_and_merges_packs() -> None:
    client = SolarFlowClient(ReportTransport(), response_timeout=0.1, keepalive_seconds=60)
    await client.connect()
    assert not client.ready
    assert client.protocol_ready
    assert client.state.electric_level == 26
    assert {pack.serial_number for pack in client.state.packs} == {"PACK-1", "PACK-2"}
    await client.disconnect()


@pytest.mark.asyncio
async def test_device_error_report_is_exposed() -> None:
    transport = FakeTransport()
    client = SolarFlowClient(transport, response_timeout=0.1, keepalive_seconds=60)
    await transport.start_notify(NOTIFY_CHARACTERISTIC_UUID, client._notification)
    await client._notification(NOTIFY_CHARACTERISTIC_UUID, b'{"method":"error","data":[{"code":40}]}')
    assert client.state.smart_mode is None

def test_invalid_limits_rejected() -> None:
    client = SolarFlowClient(FakeTransport())
    with pytest.raises(SolarFlowValidationError):
        client._validate_limit(2401)
