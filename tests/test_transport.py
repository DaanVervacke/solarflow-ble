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
