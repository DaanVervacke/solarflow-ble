from __future__ import annotations

import asyncio
import json
import logging

import pytest
from solarflow_ble import (
    ConnectionStatus,
    SolarFlowClient,
)
from solarflow_ble.exceptions import (
    SolarFlowConnectionError,
)

from conftest import (
    CleanupCountingTransport,
    FakeTransport,
    LinkLossTransport,
    connected_control_client,
)


@pytest.mark.asyncio
async def test_keepalive_requests_all_state() -> None:
    transport, client = await connected_control_client()
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
    transport, client = await connected_control_client()
    client.keepalive_seconds = 0
    transport.block_keepalive = True

    await asyncio.wait_for(transport.keepalive_started.wait(), 0.05)
    await client.disconnect()

    assert transport.keepalive_cancelled.is_set()
    writes_after_disconnect = len(transport.writes)
    await asyncio.sleep(0)
    assert len(transport.writes) == writes_after_disconnect


@pytest.mark.asyncio
async def test_keepalive_write_failure_fails_session_and_notifies_caller() -> None:
    transport = LinkLossTransport()
    lost: list[Exception] = []
    connection_lost = asyncio.Event()

    async def on_connection_lost(error: Exception) -> None:
        lost.append(error)
        connection_lost.set()

    client = SolarFlowClient(
        transport,
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
        connection_lost_callback=on_connection_lost,
    )
    await client.connect()
    transport.fail_reads.set()
    client.keepalive_seconds = 0

    await asyncio.wait_for(connection_lost.wait(), 1)

    assert client.status is ConnectionStatus.DISCONNECTED
    assert client._keepalive_task is None
    assert len(lost) == 1
    assert isinstance(lost[0], RuntimeError)


@pytest.mark.asyncio
async def test_failure_during_disconnect_skips_connection_lost_callback() -> None:
    transport = CleanupCountingTransport()
    transport.stop_notify_gate = asyncio.Event()
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

    disconnecting = asyncio.create_task(client.disconnect())
    await transport.stop_notify_started.wait()
    await client._handle_session_failure(RuntimeError("link lost"))
    transport.stop_notify_gate.set()
    await disconnecting

    assert lost == []
    assert transport.stop_notify_calls == 1
    assert transport.disconnect_calls == 1
    assert client.status is ConnectionStatus.DISCONNECTED


@pytest.mark.asyncio
async def test_double_session_failure_is_idempotent() -> None:
    transport = CleanupCountingTransport()
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

    await client._handle_session_failure(RuntimeError("first failure"))
    await client._handle_session_failure(RuntimeError("second failure"))

    assert len(lost) == 1
    assert isinstance(lost[0], RuntimeError)
    assert str(lost[0]) == "first failure"
    assert transport.stop_notify_calls == 1
    assert transport.disconnect_calls == 1
    assert client.status is ConnectionStatus.DISCONNECTED


@pytest.mark.asyncio
async def test_connection_lost_callback_failure_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def on_connection_lost(error: Exception) -> None:
        raise RuntimeError("callback failed")

    client = SolarFlowClient(
        FakeTransport(),
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
        connection_lost_callback=on_connection_lost,
    )
    await client.connect()

    with caplog.at_level(logging.ERROR):
        await client._handle_session_failure(RuntimeError("link lost"))

    assert "SolarFlow connection lost callback failed" in caplog.text
    assert client.status is ConnectionStatus.DISCONNECTED


@pytest.mark.asyncio
async def test_waits_started_after_session_failure_fail_fast() -> None:
    client = SolarFlowClient(
        FakeTransport(),
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
    )
    await client.connect()
    await client._handle_session_failure(RuntimeError("link lost"))

    with pytest.raises(SolarFlowConnectionError, match="session failed"):
        await client._wait_for_method("BLESPP")
