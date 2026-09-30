"""Shared fakes for the SolarFlow BLE test suite."""

from __future__ import annotations

from typing import Any


class FakeManager:
    """Fake ESPHome API connection manager."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class FakeBluetoothManager:
    """Fake habluetooth manager without scanners."""

    async def async_setup(self) -> None:
        pass

    def async_current_scanners(self) -> list[Any]:
        return []

    def async_stop(self) -> None:
        pass
