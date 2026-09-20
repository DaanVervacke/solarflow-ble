"""BLE transport adapters for SolarFlow."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice

from .client import BleTransport, NotificationCallback


class BleakTransport(BleTransport):
    """Adapt a Bleak client to the SolarFlow transport protocol."""

    def __init__(self, device: BLEDevice, *, timeout: float = 30.0, client_factory: Callable[..., BleakClient] = BleakClient) -> None:
        self.device = device
        self.timeout = timeout
        self._client_factory = client_factory
        self._client: BleakClient | None = None

    @property
    def client(self) -> BleakClient:
        if self._client is None:
            raise RuntimeError("SolarFlow BLE transport is not connected")
        return self._client

    async def connect(self) -> None:
        if self._client is None:
            self._client = self._client_factory(self.device, timeout=self.timeout)
        if not self._client.is_connected:
            await self._client.connect()

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
        self._client = None

    async def start_notify(self, characteristic: str, callback: NotificationCallback) -> None:
        def on_notification(gatt_characteristic: BleakGATTCharacteristic, payload: bytearray) -> None:
            result = callback(gatt_characteristic.uuid, bytes(payload))
            if isinstance(result, Coroutine):
                asyncio.create_task(result)

        await self.client.start_notify(characteristic, on_notification)

    async def stop_notify(self, characteristic: str) -> None:
        if self._client is not None and self._client.is_connected:
            await self._client.stop_notify(characteristic)

    async def write_gatt_char(self, characteristic: str, data: bytes, response: bool = False) -> None:
        await self.client.write_gatt_char(characteristic, data, response=response)
