from __future__ import annotations

import asyncio
import json
import logging

import pytest
from solarflow_ble import (
    DEFAULT_LIMITS,
    MODEL_LIMITS,
    MODEL_SOLARFLOW_2400AC,
    MODEL_SOLARFLOW_2400AC_PRODUCT_KEY,
    AcMode,
    ConnectionStatus,
    SolarFlowClient,
    SolarFlowLimits,
)
from solarflow_ble.const import NOTIFY_CHARACTERISTIC_UUID
from solarflow_ble.exceptions import (
    SolarFlowCommandError,
    SolarFlowConnectionError,
    SolarFlowNotReadyError,
    SolarFlowTimeoutError,
    SolarFlowValidationError,
)

from conftest import (
    FakeTransport,
    LinkLossTransport,
    NoSmartModeTransport,
    WriteFailureTransport,
    connected_control_client,
)


def test_invalid_limits_rejected() -> None:
    client = SolarFlowClient(FakeTransport())
    with pytest.raises(SolarFlowValidationError):
        client._validate_range(2401, 0, 2400, "Power limit", "W")


@pytest.mark.asyncio
async def test_controls_are_disabled_by_default() -> None:
    client = SolarFlowClient(FakeTransport())
    with pytest.raises(SolarFlowNotReadyError, match="disabled"):
        await client.set_input_limit(100)


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
    transport, client = await connected_control_client()

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
@pytest.mark.parametrize("mode", [AcMode.CHARGING, AcMode.DISCHARGING])
async def test_set_ac_mode_accepts_enum_members(mode: AcMode) -> None:
    transport, client = await connected_control_client()

    await client.set_ac_mode(mode)

    message = json.loads(transport.writes[-1])
    assert message["properties"] == {"acMode": int(mode)}
    await client.disconnect()


@pytest.mark.asyncio
async def test_set_ac_mode_rejection_lists_the_valid_enum_values() -> None:
    client = SolarFlowClient(FakeTransport(), allow_control=True)
    valid = " or ".join(str(member.value) for member in AcMode)

    with pytest.raises(SolarFlowValidationError) as exc_info:
        await client.set_ac_mode(3)

    assert str(exc_info.value) == f"AC mode must be {valid}"


def test_default_limits_match_verified_2400ac_values() -> None:
    assert (
        SolarFlowLimits(
            max_input_power_w=2400,
            max_output_power_w=2400,
            max_min_soc=50,
            min_target_soc=70,
        )
        == DEFAULT_LIMITS
    )
    assert MODEL_LIMITS == {
        MODEL_SOLARFLOW_2400AC: DEFAULT_LIMITS,
        MODEL_SOLARFLOW_2400AC_PRODUCT_KEY: DEFAULT_LIMITS,
    }


@pytest.mark.asyncio
async def test_custom_limits_allow_what_defaults_reject() -> None:
    limits = SolarFlowLimits(
        max_input_power_w=2400,
        max_output_power_w=2400,
        max_min_soc=50,
        min_target_soc=10,
    )
    _, client = await connected_control_client(limits=limits)

    await client.set_soc(50)
    await client.set_soc(100)
    await client.disconnect()


@pytest.mark.asyncio
async def test_custom_limits_reject_what_defaults_allow() -> None:
    limits = SolarFlowLimits(
        max_input_power_w=1200,
        max_output_power_w=1200,
        max_min_soc=30,
        min_target_soc=80,
    )
    _, client = await connected_control_client(limits=limits)

    with pytest.raises(SolarFlowValidationError):
        await client.set_input_limit(1500)
    with pytest.raises(SolarFlowValidationError):
        await client.set_output_limit(1500)
    with pytest.raises(SolarFlowValidationError):
        await client.set_min_soc(40)
    with pytest.raises(SolarFlowValidationError):
        await client.set_soc(75)
    await client.disconnect()


@pytest.mark.asyncio
async def test_unknown_model_falls_back_to_default_limits_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, client = await connected_control_client(model="mystery-model")

    with caplog.at_level(logging.WARNING, logger="solarflow_ble.client"):
        await client.set_input_limit(2400)
        with pytest.raises(SolarFlowValidationError):
            await client.set_input_limit(2401)
        await client.set_min_soc(50)
        with pytest.raises(SolarFlowValidationError):
            await client.set_min_soc(51)

    warnings = [
        record for record in caplog.records if "no verified limits" in record.message
    ]
    assert len(warnings) == 1
    assert "mystery-model" in warnings[0].message
    await client.disconnect()


@pytest.mark.asyncio
async def test_registry_model_uses_verified_limits_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, client = await connected_control_client(model=MODEL_SOLARFLOW_2400AC)

    with caplog.at_level(logging.WARNING, logger="solarflow_ble.client"):
        await client.set_input_limit(2400)
        with pytest.raises(SolarFlowValidationError):
            await client.set_input_limit(2401)

    assert not [
        record for record in caplog.records if "no verified limits" in record.message
    ]
    await client.disconnect()


@pytest.mark.asyncio
async def test_product_key_resolves_registry_limits_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, client = await connected_control_client()
    await client._notification(
        NOTIFY_CHARACTERISTIC_UUID,
        json.dumps(
            {
                "method": "report",
                "productKey": MODEL_SOLARFLOW_2400AC.upper(),
                "properties": {"smartMode": 1},
            }
        ).encode(),
    )
    assert client.state.product_key == MODEL_SOLARFLOW_2400AC.upper()

    with caplog.at_level(logging.WARNING, logger="solarflow_ble.client"):
        await client.set_input_limit(2400)
        with pytest.raises(SolarFlowValidationError):
            await client.set_input_limit(2401)

    assert not [
        record for record in caplog.records if "no verified limits" in record.message
    ]
    await client.disconnect()


@pytest.mark.asyncio
async def test_reported_2400ac_product_key_resolves_limits_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, client = await connected_control_client()
    await client._notification(
        NOTIFY_CHARACTERISTIC_UUID,
        json.dumps(
            {
                "method": "report",
                "productKey": MODEL_SOLARFLOW_2400AC_PRODUCT_KEY.upper(),
                "properties": {"smartMode": 1},
            }
        ).encode(),
    )
    assert client.state.product_key == MODEL_SOLARFLOW_2400AC_PRODUCT_KEY.upper()

    with caplog.at_level(logging.WARNING, logger="solarflow_ble.client"):
        await client.set_input_limit(2400)
        with pytest.raises(SolarFlowValidationError):
            await client.set_input_limit(2401)

    assert not [
        record for record in caplog.records if "no verified limits" in record.message
    ]
    await client.disconnect()


@pytest.mark.asyncio
async def test_explicit_limits_win_over_registry_entry() -> None:
    limits = SolarFlowLimits(
        max_input_power_w=1200,
        max_output_power_w=1200,
        max_min_soc=30,
        min_target_soc=80,
    )
    _, client = await connected_control_client(
        model=MODEL_SOLARFLOW_2400AC, limits=limits
    )

    with pytest.raises(SolarFlowValidationError):
        await client.set_input_limit(2400)
    with pytest.raises(SolarFlowValidationError):
        await client.set_soc(75)
    await client.disconnect()


@pytest.mark.asyncio
async def test_control_write_acknowledgement_succeeds() -> None:
    transport, client = await connected_control_client(write_response=0)

    await client.set_input_limit(100)

    assert len(transport.writes) == 4
    await client.disconnect()


@pytest.mark.asyncio
async def test_control_write_rejection_raises_command_error() -> None:
    _, client = await connected_control_client(write_response=1)

    with pytest.raises(
        SolarFlowCommandError, match=r"rejected inputLimit \(writeRsp=1\)"
    ):
        await client.set_input_limit(100)

    await client.disconnect()


@pytest.mark.asyncio
async def test_control_write_without_acknowledgement_times_out() -> None:
    transport, client = await connected_control_client(write_response=None)

    with pytest.raises(SolarFlowTimeoutError, match="inputLimit acknowledgement"):
        await client.set_input_limit(100)

    assert len(transport.writes) == 4
    await client.disconnect()


@pytest.mark.asyncio
async def test_stale_write_acknowledgement_does_not_satisfy_current_write() -> None:
    _, client = await connected_control_client(write_response=None)
    await client._write_results.put(
        {
            "method": "report",
            "messageId": 999,
            "properties": {"outputLimit": 200, "writeRsp": 0},
        }
    )

    with pytest.raises(SolarFlowTimeoutError, match="inputLimit acknowledgement"):
        await client.set_input_limit(100)

    await client.disconnect()


@pytest.mark.asyncio
async def test_control_writes_are_serialized_until_acknowledgement() -> None:
    transport, client = await connected_control_client(write_response=0)
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
async def test_controls_enabled_but_not_ready_are_rejected() -> None:
    client = SolarFlowClient(
        NoSmartModeTransport(),
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
        allow_control=True,
    )

    with pytest.raises(SolarFlowNotReadyError, match="not ready"):
        await client.set_input_limit(100)

    await client.connect()
    assert client.ready
    await client.disconnect()


@pytest.mark.asyncio
async def test_control_write_transport_failure_fails_the_session() -> None:
    transport = WriteFailureTransport()
    lost: list[Exception] = []

    def on_connection_lost(error: Exception) -> None:
        lost.append(error)

    client = SolarFlowClient(
        transport,
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
        allow_control=True,
        connection_lost_callback=on_connection_lost,
    )
    await client.connect()

    with pytest.raises(SolarFlowConnectionError, match="Writing inputLimit"):
        await client.set_input_limit(100)

    assert client.status is ConnectionStatus.DISCONNECTED
    assert len(lost) == 1
    assert isinstance(lost[0], RuntimeError)
    assert transport.stop_notify_calls == 1
    assert transport.disconnect_calls == 1


@pytest.mark.asyncio
async def test_non_acknowledgement_message_does_not_satisfy_write_wait() -> None:
    _, client = await connected_control_client(write_response=None)
    await client._write_results.put(
        {"method": "report", "properties": {"electricLevel": 26}}
    )

    with pytest.raises(SolarFlowTimeoutError, match="inputLimit acknowledgement"):
        await client.set_input_limit(100)

    await client.disconnect()


@pytest.mark.asyncio
async def test_pending_control_write_fails_fast_when_session_dies() -> None:
    transport = LinkLossTransport(write_response=None)
    client = SolarFlowClient(
        transport,
        response_timeout=5.0,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
        allow_control=True,
    )
    await client.connect()

    pending = asyncio.create_task(client.set_input_limit(100))
    await asyncio.wait_for(transport.write_started.wait(), 1)
    await asyncio.sleep(0)

    await client._notification(NOTIFY_CHARACTERISTIC_UUID, b"{invalid")
    with pytest.raises(SolarFlowConnectionError):
        await asyncio.wait_for(pending, 2.0)

    assert client.status is ConnectionStatus.DISCONNECTED
