from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from solarflow_ble import (
    ConnectionStatus,
    NotificationCallback,
    SolarFlowClient,
    SolarFlowState,
)
from solarflow_ble.const import NOTIFY_CHARACTERISTIC_UUID
from solarflow_ble.exceptions import (
    SolarFlowDeviceError,
    SolarFlowProtocolError,
    SolarFlowTimeoutError,
)

from conftest import (
    FailingTransport,
    FakeTransport,
    LifecycleTransport,
    NoSmartModeTransport,
    PreHandshakeTimeoutTransport,
    ReportTransport,
    SessionTransport,
    SilentReadTransport,
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

    assert client.ready
    assert client.protocol_ready
    assert client.device_id == "DEVICE-2"
    assert client.state.device_id == "DEVICE-2"
    assert client.state.smart_mode == 0
    assert [pack.serial_number for pack in client.state.packs] == ["NEW-PACK"]
    assert client._reports.qsize() == 0
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

    with pytest.raises(SolarFlowTimeoutError, match="last device error"):
        await client.connect()

    assert not client.connected
    assert isinstance(client.last_error, SolarFlowDeviceError)
    assert transport.stop_notify_calls == 1
    assert transport.disconnect_calls == 1


@pytest.mark.asyncio
async def test_device_error_during_get_info_is_tolerated() -> None:
    transport = FailingTransport("getInfo_error_then_ok")
    client = SolarFlowClient(
        transport, response_timeout=0.5, ble_spp_delay=0, initial_read_delay=0
    )

    await client.connect()

    assert client.connected
    assert isinstance(client.last_error, SolarFlowDeviceError)
    assert "40" in str(client.last_error)
    await client.disconnect()


@pytest.mark.asyncio
async def test_device_error_during_get_info_is_quoted_on_timeout() -> None:
    transport = FailingTransport("getInfo_error")
    client = SolarFlowClient(
        transport, response_timeout=0.01, ble_spp_delay=0, initial_read_delay=0
    )

    with pytest.raises(SolarFlowTimeoutError, match="last device error"):
        await client.connect()

    assert not client.connected
    assert isinstance(client.last_error, SolarFlowDeviceError)
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
async def test_captured_report_stream_becomes_ready_and_merges_packs() -> None:
    client = SolarFlowClient(
        ReportTransport(), response_timeout=0.1, keepalive_seconds=60
    )
    await client.connect()
    assert client.ready
    assert client.protocol_ready
    assert client.state.electric_level == 26
    assert {pack.serial_number for pack in client.state.packs} == {"PACK-1", "PACK-2"}
    await client.disconnect()


@pytest.mark.asyncio
async def test_real_device_capture_replays_through_connected_client() -> None:
    fixture = Path(__file__).parent / "fixtures" / "solarflow_reports.jsonl"
    records = [json.loads(line) for line in fixture.read_text().splitlines()]
    rx_payloads = [
        json.dumps(record["json"]).encode()
        for record in records
        if record["direction"] == "rx"
    ]
    assert len(rx_payloads) > 10

    client = SolarFlowClient(
        FakeTransport(),
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
    )
    await client.connect()

    for payload in rx_payloads:
        await client._notification(NOTIFY_CHARACTERISTIC_UUID, payload)

    state = client.state
    assert client.device_id == "DEVICE-1"
    assert state.device_id == "DEVICE-1"
    expected_fields = {
        "electric_level": 26,
        "smart_mode": 0,
        "input_limit": 198,
        "output_limit": 548,
        "min_soc": 100,
        "soc_set": 1000,
        "ac_mode": 2,
        "ac_status": 0,
        "data_ready": 1,
        "grid_state": 1,
        "fault_level": 3,
        "soc_limit": 0,
        "soc_status": 0,
        "hyper_temperature": 2971,
        "charge_max_limit": 2400,
        "pack_input_power": 0,
        "output_pack_power": 0,
        "output_home_power": 0,
        "remain_out_time": 59940,
        "battery_power": 0,
        "grid_input_power": 0,
        "solar_input_power": 0,
        "solar_power_1": 0,
        "solar_power_6": 0,
        "grid_off_power": 0,
    }
    for field, value in expected_fields.items():
        assert getattr(state, field) == value, field
    assert "pass" not in (state.raw or {})
    assert [pack.serial_number for pack in state.packs] == [
        "PACK-1",
        "PACK-2",
        "PACK-3",
    ]
    expected_pack_fields = {
        "pack_type": 5,
        "soc_level": 26,
        "state": 0,
        "power": 0,
        "max_temp": 2961,
        "total_voltage": 4760,
        "battery_current": 0,
        "max_voltage": 318,
        "min_voltage": 317,
        "software_version": 4109,
        "heat_state": 0,
    }
    for pack in state.packs:
        for field, value in expected_pack_fields.items():
            assert getattr(pack, field) == value, (pack.serial_number, field)
    assert isinstance(client.last_error, SolarFlowDeviceError)
    assert "40" in str(client.last_error)
    assert client.protocol_ready
    assert client.ready
    await client.disconnect()


@pytest.mark.asyncio
async def test_device_error_report_sets_last_error_only_while_connected() -> None:
    error_payload = b'{"method":"error","data":[{"code":40}]}'

    client = SolarFlowClient(
        FakeTransport(),
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
    )
    await client.connect()
    await client._notification(NOTIFY_CHARACTERISTIC_UUID, error_payload)
    assert isinstance(client.last_error, SolarFlowDeviceError)
    assert "40" in str(client.last_error)
    await client.disconnect()

    offline = SolarFlowClient(FakeTransport())
    await offline._notification(NOTIFY_CHARACTERISTIC_UUID, error_payload)
    assert offline.last_error is None
    assert offline.status is ConnectionStatus.DISCONNECTED


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
async def test_missing_ble_spp_device_id_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport()
    transport.callback = None

    async def start_notify(characteristic: str, callback: NotificationCallback) -> None:
        transport.callback = callback
        await transport._emit(callback, b'{"method":"BLESPP"}')

    monkeypatch.setattr(transport, "start_notify", start_notify)
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
async def test_later_device_id_mismatch_fails_the_session() -> None:
    transport = FakeTransport()
    lost: list[Exception] = []

    def on_connection_lost(error: Exception) -> None:
        lost.append(error)

    client = SolarFlowClient(
        transport,
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
        connection_lost_callback=on_connection_lost,
    )
    await client.connect()

    await client._notification(
        NOTIFY_CHARACTERISTIC_UUID,
        b'{"method":"report","deviceId":"DEVICE-2","properties":{}}',
    )

    assert client.status is ConnectionStatus.DISCONNECTED
    assert not client.connected
    assert len(lost) == 1
    assert isinstance(lost[0], SolarFlowProtocolError)


@pytest.mark.asyncio
async def test_matching_device_id_report_is_accepted() -> None:
    transport = FakeTransport()
    client = SolarFlowClient(
        transport, response_timeout=0.1, ble_spp_delay=0, initial_read_delay=0
    )
    await client.connect()

    await client._notification(
        NOTIFY_CHARACTERISTIC_UUID,
        b'{"method":"report","deviceId":"DEVICE-1","properties":{"electricLevel":80}}',
    )

    assert client.connected
    assert client.state.electric_level == 80
    await client.disconnect()


def test_message_id_counter_skips_reserved_id_and_is_integer() -> None:
    client = SolarFlowClient(FakeTransport())
    client._message_id = 1008
    assert client._next_message_id() == 1008
    assert client._next_message_id() == 1010


def test_require_device_id_without_identity_raises() -> None:
    client = SolarFlowClient(FakeTransport())
    with pytest.raises(SolarFlowProtocolError, match="identity"):
        client._require_device_id()


@pytest.mark.asyncio
async def test_wait_for_response_raises_when_deadline_already_passed() -> None:
    client = SolarFlowClient(FakeTransport(), response_timeout=0)
    with pytest.raises(SolarFlowTimeoutError):
        await client._wait_for_response(
            client._reports, context="report", accept=lambda message: True
        )


@pytest.mark.asyncio
async def test_connect_succeeds_without_smart_mode_report() -> None:
    client = SolarFlowClient(
        NoSmartModeTransport(),
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
    )
    await client.connect()

    assert client.status is ConnectionStatus.READY
    assert client.protocol_ready
    assert client.ready
    assert client.state.electric_level == 26
    await client.disconnect()


@pytest.mark.asyncio
async def test_connect_times_out_when_no_report_arrives() -> None:
    client = SolarFlowClient(
        SilentReadTransport(),
        response_timeout=0.05,
        ble_spp_delay=0,
        initial_read_delay=0,
    )

    with pytest.raises(SolarFlowTimeoutError, match="initial report"):
        await client.connect()

    assert not client.connected


@pytest.mark.asyncio
async def test_async_context_manager_connects_and_disconnects() -> None:
    transport = FakeTransport()
    async with SolarFlowClient(
        transport,
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
    ) as client:
        assert client.connected
        assert client.device_id == "DEVICE-1"

    assert client.status is ConnectionStatus.DISCONNECTED
    assert not client.connected
    assert client._keepalive_task is None


@pytest.mark.asyncio
async def test_async_context_manager_disconnects_and_propagates_errors() -> None:
    transport = FakeTransport()
    client = SolarFlowClient(
        transport,
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
    )

    async with client as entered:
        assert entered is client
        assert entered.connected
        with pytest.raises(RuntimeError, match="boom"):
            raise RuntimeError("boom")

    assert client.status is ConnectionStatus.DISCONNECTED
    assert not client.connected
    assert client._keepalive_task is None


@pytest.mark.asyncio
async def test_async_context_manager_reenters_after_exit() -> None:
    transport = LifecycleTransport()
    transport.connect_gate.set()
    client = SolarFlowClient(
        transport,
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
    )

    async with client:
        assert client.connected
    async with client:
        assert client.connected

    assert transport.connect_calls == 2
    assert client.status is ConnectionStatus.DISCONNECTED
    assert not client.connected
