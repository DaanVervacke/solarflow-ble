from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from solarflow_ble import SolarFlowClient, SolarFlowState, parse_advertisement
from solarflow_ble.client import NotificationCallback
from solarflow_ble.const import NOTIFY_CHARACTERISTIC_UUID
from solarflow_ble.exceptions import (
    SolarFlowCommandError,
    SolarFlowDeviceError,
    SolarFlowNotReadyError,
    SolarFlowProtocolError,
    SolarFlowTimeoutError,
    SolarFlowValidationError,
)


def test_parse_advertisement() -> None:
    result = parse_advertisement("AA", {0x4F48: b"TEST_DEVICE\x16"}, rssi=-50)
    assert result is not None
    assert result.identifier == "TEST_DEVICE"


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


def test_state_derives_battery_power() -> None:
    state = SolarFlowState().update({"packInputPower": 100, "outputPackPower": 400})
    assert state.battery_power == 300


def test_captured_report_fixture_contains_two_packs() -> None:
    fixture = Path(__file__).parent / "fixtures" / "solarflow_reports.jsonl"
    records = [json.loads(line)["json"] for line in fixture.read_text().splitlines()]
    pack_records = [
        record
        for record in records
        if isinstance(record, dict) and "packData" in record
    ]
    assert len(pack_records) >= 3
    assert all("sn" in pack for record in pack_records for pack in record["packData"])


def test_report_fields_merge() -> None:
    state = SolarFlowState()
    state = state.update({"electricLevel": 26, "smartMode": 0})
    state = state.update({"packInputPower": 10, "outputPackPower": 40})
    assert state.electric_level == 26
    assert state.smart_mode == 0
    assert state.battery_power == 30


def test_pack_fields_merge_across_partial_pack_data_records() -> None:
    state = SolarFlowState().with_packs(
        {
            "packData": [{"sn": "PACK-1"}, {"sn": "PACK-2"}],
        }
    )
    state = state.with_packs(
        {
            "packData": [
                {
                    "sn": "PACK-1",
                    "packType": 5,
                    "socLevel": 25,
                    "power": 100,
                    "maxTemp": 2971,
                },
                {
                    "sn": "PACK-2",
                    "packType": 6,
                    "socLevel": 40,
                    "power": 200,
                },
            ],
        }
    )
    state = state.with_packs(
        {
            "packData": [
                {"sn": "PACK-1", "socLevel": 26},
                {"sn": "PACK-2", "power": 250},
            ],
        }
    )

    packs = {pack.serial_number: pack for pack in state.packs}
    assert list(packs) == ["PACK-1", "PACK-2"]
    assert packs["PACK-1"].pack_type == 5
    assert packs["PACK-1"].soc_level == 26
    assert packs["PACK-1"].power == 100
    assert packs["PACK-1"].max_temp == 2971
    assert packs["PACK-2"].pack_type == 6
    assert packs["PACK-2"].soc_level == 40
    assert packs["PACK-2"].power == 250


class FakeTransport:
    def __init__(self, write_response: int | None = None) -> None:
        self.writes: list[bytes] = []
        self.events: list[str] = []
        self.callback: NotificationCallback | None = None
        self.write_response = write_response
        self.write_started = asyncio.Event()
        self.write_ack_gate: asyncio.Event | None = None
        self.read_event = asyncio.Event()
        self.keepalive_read = asyncio.Event()
        self.read_count = 0
        self.block_keepalive = False
        self.keepalive_started = asyncio.Event()
        self.keepalive_cancelled = asyncio.Event()
        self.keepalive_gate = asyncio.Event()

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def start_notify(
        self,
        characteristic: str,
        callback: NotificationCallback,
    ) -> None:
        assert characteristic == NOTIFY_CHARACTERISTIC_UUID
        self.callback = callback
        self.events.append("BLESPP")
        await self._emit(callback, b'{"method":"BLESPP","deviceId":"DEVICE-1"}')

    async def stop_notify(self, characteristic: str) -> None:
        pass

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        self.writes.append(data)
        message = json.loads(data)
        self.events.append(message["method"])
        callback = self.callback
        assert callback is not None
        if message["method"] == "getInfo":
            self.events.append("getInfo-rsp")
            await self._emit(callback, b'{"method":"getInfo-rsp"}')
        elif message["method"] == "read":
            self.read_count += 1
            self.read_event.set()
            if self.read_count > 1:
                self.keepalive_read.set()
            if self.block_keepalive:
                self.keepalive_started.set()
                try:
                    await self.keepalive_gate.wait()
                except asyncio.CancelledError:
                    self.keepalive_cancelled.set()
                    raise
                return
            self.events.extend(("read_reply", "report"))
            await self._emit(callback, b'{"method":"read_reply","success":false}')
            await self._emit(
                callback, b'{"method":"report","properties":{"smartMode":1}}'
            )
        elif message["method"] == "write":
            self.write_started.set()
            gate = self.write_ack_gate
            if gate is not None:
                await gate.wait()
            if self.write_response is not None:
                await self._emit(
                    callback,
                    json.dumps(
                        {
                            "method": "report",
                            "properties": {"writeRsp": self.write_response},
                        }
                    ).encode(),
                )

    @staticmethod
    async def _emit(callback: NotificationCallback, payload: bytes) -> None:
        result = callback(NOTIFY_CHARACTERISTIC_UUID, payload)
        if result is not None:
            await result


class ReportTransport(FakeTransport):
    """Fake transport that emits captured-style reports."""

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        self.writes.append(data)
        callback = self.callback
        if callback is None:
            return
        message = json.loads(data)
        if message["method"] == "getInfo":
            await self._emit(callback, b'{"method":"getInfo-rsp","messageId":1}')
        elif message["method"] == "read":
            await self._emit(callback, b'{"method":"read_reply"}')
            await self._emit(
                callback,
                b'{"method":"report","messageId":1,"properties":{"smartMode":0,"electricLevel":26}}',
            )
            await self._emit(
                callback,
                b'{"method":"report","messageId":1,"packData":[{"sn":"PACK-1","socLevel":25},{"sn":"PACK-2","socLevel":26}]}',
            )
        if callback is not None and b'"method":"read"' in data:
            await self._emit(
                callback, b'{"messageId":"11","properties":{"smartMode":1}}'
            )


class FailingTransport(FakeTransport):
    def __init__(self, failure: str) -> None:
        super().__init__()
        self.failure = failure
        self.stop_notify_calls = 0
        self.disconnect_calls = 0

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def stop_notify(self, characteristic: str) -> None:
        self.stop_notify_calls += 1

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        self.writes.append(data)
        message = json.loads(data)
        if self.failure == "getInfo":
            return
        if message["method"] == "getInfo":
            assert self.callback is not None
            await self._emit(self.callback, b'{"method":"getInfo-rsp"}')
        if self.failure == "initial" and message["method"] == "read":
            assert self.callback is not None
            await self._emit(self.callback, b'{"method":"error","data":[{"code":40}]}')


class PreHandshakeTimeoutTransport(FailingTransport):
    async def start_notify(
        self,
        characteristic: str,
        callback: NotificationCallback,
    ) -> None:
        assert characteristic == NOTIFY_CHARACTERISTIC_UUID
        self.callback = callback


class LifecycleTransport(FakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.connect_calls = 0
        self.start_notify_calls = 0
        self.connect_started = asyncio.Event()
        self.connect_gate = asyncio.Event()

    async def connect(self) -> None:
        self.connect_calls += 1
        self.connect_started.set()
        await self.connect_gate.wait()

    async def start_notify(
        self,
        characteristic: str,
        callback: NotificationCallback,
    ) -> None:
        self.start_notify_calls += 1
        await super().start_notify(characteristic, callback)


class SessionTransport(FakeTransport):
    def __init__(self, sessions: list[dict[str, Any]]) -> None:
        super().__init__()
        self.sessions = sessions
        self.session_index = -1
        self.connect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        self.session_index += 1

    async def start_notify(
        self,
        characteristic: str,
        callback: NotificationCallback,
    ) -> None:
        assert characteristic == NOTIFY_CHARACTERISTIC_UUID
        self.callback = callback
        session = self.sessions[self.session_index]
        await self._emit(
            callback,
            json.dumps({"method": "BLESPP", "deviceId": session["device_id"]}).encode(),
        )

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        self.writes.append(data)
        message = json.loads(data)
        session = self.sessions[self.session_index]
        callback = self.callback
        assert callback is not None
        if message["method"] == "getInfo":
            if not session.get("fail_get_info", False):
                await self._emit(callback, b'{"method":"getInfo-rsp"}')
        elif message["method"] == "read":
            await self._emit(callback, b'{"method":"read_reply"}')
            await self._emit(
                callback,
                json.dumps(
                    {
                        "method": "report",
                        "properties": {"smartMode": session["smart_mode"]},
                        "packData": [{"sn": session["pack_serial"]}],
                    }
                ).encode(),
            )


@pytest.mark.asyncio
async def test_connect_failure_cleans_up_after_get_info_timeout() -> None:
    transport = FailingTransport("getInfo")
    client = SolarFlowClient(
        transport, response_timeout=0.01, ble_spp_delay=0, initial_read_delay=0
    )

    with pytest.raises(SolarFlowTimeoutError):
        await client.connect()

    assert not client.connected
    assert transport.stop_notify_calls == 1
    assert transport.disconnect_calls == 1


@pytest.mark.asyncio
async def test_concurrent_connect_calls_share_one_session() -> None:
    transport = LifecycleTransport()
    client = SolarFlowClient(
        transport, response_timeout=0.1, ble_spp_delay=0, initial_read_delay=0
    )

    first = asyncio.create_task(client.connect())
    await transport.connect_started.wait()
    second = asyncio.create_task(client.connect())
    transport.connect_gate.set()
    await asyncio.gather(first, second)

    assert transport.connect_calls == 1
    assert transport.start_notify_calls == 1
    assert client.ready
    assert client._keepalive_task is not None
    await client.disconnect()


@pytest.mark.asyncio
async def test_connect_after_ready_is_idempotent() -> None:
    transport = LifecycleTransport()
    transport.connect_gate.set()
    client = SolarFlowClient(
        transport, response_timeout=0.1, ble_spp_delay=0, initial_read_delay=0
    )

    await client.connect()
    writes = len(transport.writes)
    await client.connect()

    assert transport.connect_calls == 1
    assert transport.start_notify_calls == 1
    assert len(transport.writes) == writes
    await client.disconnect()


@pytest.mark.asyncio
async def test_reconnect_discards_stale_messages_and_session_state() -> None:
    transport = SessionTransport(
        [
            {
                "device_id": "DEVICE-1",
                "fail_get_info": True,
                "smart_mode": 1,
                "pack_serial": "OLD-PACK",
            },
            {
                "device_id": "DEVICE-2",
                "smart_mode": 0,
                "pack_serial": "NEW-PACK",
            },
        ]
    )
    client = SolarFlowClient(
        transport, response_timeout=0.01, ble_spp_delay=0, initial_read_delay=0
    )

    with pytest.raises(SolarFlowTimeoutError):
        await client.connect()

    await client._reports.put({"method": "BLESPP", "deviceId": "DEVICE-1"})
    await client._reports.put({"method": "getInfo-rsp"})
    await client._reports.put({"method": "read_reply"})
    await client._reports.put(
        {
            "method": "report",
            "properties": {"smartMode": 1},
            "packData": [{"sn": "STALE-PACK"}],
        }
    )
    await client._write_results.put({"method": "report", "properties": {"writeRsp": 0}})

    await client.connect()

    assert client.ready is False
    assert client.protocol_ready
    assert client.device_id == "DEVICE-2"
    assert client.state.device_id == "DEVICE-2"
    assert client.state.smart_mode == 0
    assert [pack.serial_number for pack in client.state.packs] == ["NEW-PACK"]
    assert client._reports.qsize() == 2
    assert client._write_results.empty()
    await client.disconnect()


@pytest.mark.asyncio
async def test_disconnect_clears_session_state_but_preserves_target_id() -> None:
    transport = SessionTransport(
        [
            {
                "device_id": "TARGET",
                "smart_mode": 1,
                "pack_serial": "PACK-1",
            }
        ]
    )
    client = SolarFlowClient(
        transport, device_id="TARGET", response_timeout=0.1, ble_spp_delay=0
    )

    await client.connect()
    await client.disconnect()

    assert client.device_id == "TARGET"
    assert client.state == SolarFlowState()
    await client.disconnect()


@pytest.mark.asyncio
async def test_connect_failure_cleans_up_before_ble_spp_handshake() -> None:
    transport = PreHandshakeTimeoutTransport("getInfo")
    client = SolarFlowClient(
        transport, response_timeout=0.01, ble_spp_delay=0, initial_read_delay=0
    )

    with pytest.raises(SolarFlowTimeoutError, match="BLESPP"):
        await client.connect()

    assert not client.connected
    assert transport.stop_notify_calls == 1
    assert transport.disconnect_calls == 1


@pytest.mark.asyncio
async def test_connect_failure_cleans_up_after_initial_report_error() -> None:
    transport = FailingTransport("initial")
    client = SolarFlowClient(
        transport, response_timeout=0.01, ble_spp_delay=0, initial_read_delay=0
    )

    with pytest.raises(SolarFlowDeviceError):
        await client.connect()

    assert not client.connected
    assert transport.stop_notify_calls == 1
    assert transport.disconnect_calls == 1


@pytest.mark.asyncio
async def test_connect_handshake() -> None:
    transport = FakeTransport()
    client = SolarFlowClient(transport, response_timeout=0.1, keepalive_seconds=60)
    await client.connect()
    assert client.ready
    assert client.ble_spp_delay == 0.3
    assert client.device_id == "DEVICE-1"
    assert client.state.device_id == "DEVICE-1"
    assert len(transport.writes) == 3
    messages = [json.loads(write) for write in transport.writes]
    assert [message["method"] for message in messages] == [
        "BLESPP_OK",
        "getInfo",
        "read",
    ]
    assert messages[0]["messageId"] == 1009
    assert messages[1]["deviceId"] == "DEVICE-1"
    assert messages[1]["messageId"] == 1
    assert messages[2]["deviceId"] == "DEVICE-1"
    assert messages[2]["messageId"] == 2
    assert transport.events == [
        "BLESPP",
        "BLESPP_OK",
        "getInfo",
        "getInfo-rsp",
        "read",
        "read_reply",
        "report",
    ]
    await client.disconnect()


@pytest.mark.asyncio
async def test_message_ids_continue_across_reconnect() -> None:
    transport = FakeTransport()
    client = SolarFlowClient(
        transport, response_timeout=0.1, keepalive_seconds=60, ble_spp_delay=0
    )
    await client.connect()
    await client.disconnect()
    await client.connect()
    messages = [json.loads(write) for write in transport.writes]
    assert [message["messageId"] for message in messages] == [1009, 1, 2, 1009, 3, 4]
    await client.disconnect()


@pytest.mark.asyncio
async def test_captured_report_stream_stays_protocol_ready_and_merges_packs() -> None:
    client = SolarFlowClient(
        ReportTransport(), response_timeout=0.1, keepalive_seconds=60
    )
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
    await client._notification(
        NOTIFY_CHARACTERISTIC_UUID, b'{"method":"error","data":[{"code":40}]}'
    )
    assert client.state.smart_mode is None


@pytest.mark.asyncio
async def test_read_reply_reaches_callback_without_interpreting_success() -> None:
    updates: list[Any] = []

    async def callback(update: Any) -> None:
        updates.append(update)

    client = SolarFlowClient(
        FakeTransport(),
        response_timeout=0.1,
        keepalive_seconds=60,
        update_callback=callback,
    )
    await client.connect()
    read_reply = [
        update for update in updates if update.raw_message["method"] == "read_reply"
    ]
    assert len(read_reply) == 1
    assert read_reply[0].state.smart_mode is None
    await client.disconnect()


@pytest.mark.asyncio
async def test_missing_ble_spp_device_id_is_rejected() -> None:
    transport = FakeTransport()
    transport.callback = None

    async def start_notify(characteristic: str, callback: NotificationCallback) -> None:
        transport.callback = callback
        await transport._emit(callback, b'{"method":"BLESPP"}')

    transport.start_notify = start_notify  # type: ignore[method-assign]
    client = SolarFlowClient(transport, response_timeout=0.1, ble_spp_delay=0)
    with pytest.raises(SolarFlowProtocolError, match="deviceId"):
        await client.connect()
    assert not client.connected


@pytest.mark.asyncio
async def test_constructor_device_id_mismatch_is_rejected() -> None:
    transport = FakeTransport()
    client = SolarFlowClient(
        transport, device_id="DEVICE-2", response_timeout=0.1, ble_spp_delay=0
    )
    with pytest.raises(SolarFlowProtocolError, match="does not match"):
        await client.connect()
    assert not client.connected


@pytest.mark.asyncio
async def test_later_device_id_mismatch_is_rejected() -> None:
    transport = FakeTransport()
    client = SolarFlowClient(transport, response_timeout=0.1)
    await transport.start_notify(NOTIFY_CHARACTERISTIC_UUID, client._notification)
    client.device_id = "DEVICE-1"
    with pytest.raises(SolarFlowProtocolError, match="does not match"):
        await client._notification(
            NOTIFY_CHARACTERISTIC_UUID,
            b'{"method":"report","deviceId":"DEVICE-2","properties":{}}',
        )


def test_message_id_counter_skips_reserved_id_and_is_integer() -> None:
    client = SolarFlowClient(FakeTransport())
    client._message_id = 1008
    assert client._next_message_id() == 1008
    assert client._next_message_id() == 1010


def test_invalid_limits_rejected() -> None:
    client = SolarFlowClient(FakeTransport())
    with pytest.raises(SolarFlowValidationError):
        client._validate_limit(2401)


@pytest.mark.asyncio
async def test_controls_are_disabled_by_default() -> None:
    client = SolarFlowClient(FakeTransport())
    with pytest.raises(SolarFlowNotReadyError, match="disabled"):
        await client.set_input_limit(100)


async def _connected_control_client(
    write_response: int | None = 0,
) -> tuple[FakeTransport, SolarFlowClient]:
    transport = FakeTransport(write_response=write_response)
    client = SolarFlowClient(
        transport,
        response_timeout=0.05,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
        allow_control=True,
    )
    await client.connect()
    return transport, client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setter", "value", "property_name", "wire_value"),
    [
        ("set_input_limit", 0, "inputLimit", 0),
        ("set_input_limit", 2400, "inputLimit", 2400),
        ("set_output_limit", 0, "outputLimit", 0),
        ("set_output_limit", 2400, "outputLimit", 2400),
        ("set_min_soc", 0, "minSoc", 0),
        ("set_min_soc", 50, "minSoc", 500),
        ("set_soc", 70, "socSet", 700),
        ("set_soc", 100, "socSet", 1000),
        ("set_ac_mode", 1, "acMode", 1),
        ("set_ac_mode", 2, "acMode", 2),
    ],
)
async def test_control_setters_send_expected_wire_shape(
    setter: str,
    value: int,
    property_name: str,
    wire_value: int,
) -> None:
    transport, client = await _connected_control_client()

    await getattr(client, setter)(value)

    message = json.loads(transport.writes[-1])
    assert message["method"] == "write"
    assert message["deviceId"] == "DEVICE-1"
    assert isinstance(message["messageId"], int)
    assert message["properties"] == {property_name: wire_value}
    await client.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setter", "value"),
    [
        ("set_input_limit", -1),
        ("set_min_soc", 51),
        ("set_soc", 69),
        ("set_ac_mode", 0),
    ],
)
async def test_control_setters_reject_invalid_values(setter: str, value: int) -> None:
    client = SolarFlowClient(FakeTransport(), allow_control=True)

    with pytest.raises(SolarFlowValidationError):
        await getattr(client, setter)(value)


@pytest.mark.asyncio
async def test_control_setters_reject_invalid_upper_power_limit() -> None:
    client = SolarFlowClient(FakeTransport(), allow_control=True)

    with pytest.raises(SolarFlowValidationError):
        await client.set_output_limit(2401)


@pytest.mark.asyncio
async def test_control_write_acknowledgement_succeeds() -> None:
    transport, client = await _connected_control_client(write_response=0)

    await client.set_input_limit(100)

    assert len(transport.writes) == 4
    await client.disconnect()


@pytest.mark.asyncio
async def test_control_write_rejection_raises_command_error() -> None:
    _, client = await _connected_control_client(write_response=1)

    with pytest.raises(SolarFlowCommandError, match="inputLimit"):
        await client.set_input_limit(100)

    await client.disconnect()


@pytest.mark.asyncio
async def test_control_write_without_acknowledgement_times_out() -> None:
    transport, client = await _connected_control_client(write_response=None)

    with pytest.raises(SolarFlowTimeoutError, match="inputLimit acknowledgement"):
        await client.set_input_limit(100)

    assert len(transport.writes) == 4
    await client.disconnect()


@pytest.mark.asyncio
async def test_control_writes_are_serialized_until_acknowledgement() -> None:
    transport, client = await _connected_control_client(write_response=0)
    acknowledgement_gate = asyncio.Event()
    transport.write_ack_gate = acknowledgement_gate

    first = asyncio.create_task(client.set_input_limit(100))
    await transport.write_started.wait()
    transport.write_started.clear()
    second = asyncio.create_task(client.set_output_limit(200))
    await asyncio.sleep(0)

    assert len(transport.writes) == 4
    acknowledgement_gate.set()
    await asyncio.gather(first, second)
    assert len(transport.writes) == 5
    assert json.loads(transport.writes[-1])["properties"] == {"outputLimit": 200}
    await client.disconnect()


@pytest.mark.asyncio
async def test_keepalive_requests_all_state() -> None:
    transport, client = await _connected_control_client()
    client.keepalive_seconds = 0

    await asyncio.wait_for(transport.keepalive_read.wait(), 0.05)

    message = json.loads(transport.writes[-1])
    assert message["method"] == "read"
    assert message["deviceId"] == "DEVICE-1"
    assert message["properties"] == ["getAll"]
    assert isinstance(message["messageId"], int)
    await client.disconnect()


@pytest.mark.asyncio
async def test_disconnect_cancels_blocked_keepalive_write() -> None:
    transport, client = await _connected_control_client()
    client.keepalive_seconds = 0
    transport.block_keepalive = True

    await asyncio.wait_for(transport.keepalive_started.wait(), 0.05)
    await client.disconnect()

    assert transport.keepalive_cancelled.is_set()
    writes_after_disconnect = len(transport.writes)
    await asyncio.sleep(0)
    assert len(transport.writes) == writes_after_disconnect


def test_report_mapping_updates_fields() -> None:
    state = SolarFlowState().update(
        {"inputLimit": 100, "outputLimit": 200, "solarPower6": 30}
    )
    assert state.input_limit == 100
    assert state.output_limit == 200
    assert state.solar_power_6 == 30
