import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from solarflow_ble.transport import BleakTransport


@pytest.mark.asyncio
async def test_bleak_transport_connects_and_writes() -> None:
    client = MagicMock()
    client.is_connected = False
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.write_gatt_char = AsyncMock()
    device = MagicMock()
    transport = BleakTransport(device, client_factory=MagicMock(return_value=client))

    await transport.connect()
    await transport.write_gatt_char("char", b"payload")
    await transport.disconnect()

    client.connect.assert_awaited_once()
    client.write_gatt_char.assert_awaited_once_with("char", b"payload", response=False)
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_notification_async_callback_is_delivered() -> None:
    client = MagicMock()
    client.is_connected = True
    client.start_notify = AsyncMock()
    client.disconnect = AsyncMock()
    transport = BleakTransport(
        MagicMock(), client_factory=MagicMock(return_value=client)
    )
    delivered = asyncio.Event()

    async def callback(_characteristic: str, payload: bytes) -> None:
        assert payload == b"payload"
        delivered.set()

    await transport.connect()
    await transport.start_notify("char", callback)
    registered_callback = client.start_notify.call_args.args[1]
    registered_callback(MagicMock(uuid="char"), bytearray(b"payload"))

    await asyncio.wait_for(delivered.wait(), timeout=1)
    await transport.disconnect()


@pytest.mark.asyncio
async def test_notification_callback_failure_is_observed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = MagicMock()
    client.is_connected = True
    client.start_notify = AsyncMock()
    client.disconnect = AsyncMock()
    transport = BleakTransport(
        MagicMock(), client_factory=MagicMock(return_value=client)
    )

    async def callback(_characteristic: str, _payload: bytes) -> None:
        raise RuntimeError("callback failed")

    await transport.connect()
    await transport.start_notify("char", callback)
    registered_callback = client.start_notify.call_args.args[1]
    with caplog.at_level(logging.ERROR):
        registered_callback(MagicMock(uuid="char"), bytearray())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert "SolarFlow notification callback failed" in caplog.text
    assert "callback failed" in caplog.text
    await transport.disconnect()


@pytest.mark.asyncio
async def test_disconnect_cancels_and_awaits_blocked_notification_callbacks() -> None:
    client = MagicMock()
    client.is_connected = True
    client.start_notify = AsyncMock()
    client.disconnect = AsyncMock()
    transport = BleakTransport(
        MagicMock(), client_factory=MagicMock(return_value=client)
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()
    order: list[str] = []

    async def callback(_characteristic: str, _payload: bytes) -> None:
        started.set()
        try:
            await asyncio.Future[None]()
        except asyncio.CancelledError:
            assert transport._client is client
            order.append("callback-cancelled")
            cancelled.set()
            raise

    client.disconnect.side_effect = lambda: order.append("client-disconnected")
    await transport.connect()
    await transport.start_notify("char", callback)
    registered_callback = client.start_notify.call_args.args[1]
    registered_callback(MagicMock(uuid="char"), bytearray())
    await started.wait()

    await transport.disconnect()

    assert cancelled.is_set()
    assert order == ["callback-cancelled", "client-disconnected"]
    assert transport._client is None
    assert not transport._notification_tasks
    await transport.disconnect()
