"""Shared ESPHome-proxy helpers for the developer scripts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlsplit

import habluetooth
from bleak.backends.device import BLEDevice
from habluetooth import BluetoothScanningMode, BluetoothServiceInfoBleak

DISCOVERY_WARMUP_SECONDS = 5.0
DISCOVERY_POLL_SECONDS = 0.5
DISCOVERY_TIMEOUT_MESSAGE = "SolarFlow device was not found through the proxy"


class DiscoveryBluetoothManager(habluetooth.BluetoothManager):
    """Bluetooth manager that keeps scanner-owned advertisement data."""

    def _discover_service_info(self, service_info: BluetoothServiceInfoBleak) -> None:
        """Handle discovery through the scanner collections read directly."""


def proxy_host(value: str) -> str:
    """Return the host portion accepted by bleak-esphome."""
    parsed = urlsplit(value if "://" in value else f"//{value}")
    return parsed.hostname or value.removeprefix("//")


def set_active_scanning(
    manager: habluetooth.BluetoothManager,
    on_scanner: Callable[[Any], None] | None = None,
) -> None:
    """Request active scanning from every connectable proxy scanner."""
    for scanner in manager.async_current_scanners():
        if getattr(scanner, "connectable", False):
            scanner.set_requested_mode(BluetoothScanningMode.ACTIVE)
            if on_scanner is not None:
                on_scanner(scanner)


async def scan_for_target[T](
    manager: habluetooth.BluetoothManager,
    *,
    lookup: Callable[[], T | None],
    match: Callable[[BLEDevice, Any], T | None],
    scan_seconds: float,
    warmup_seconds: float = DISCOVERY_WARMUP_SECONDS,
    poll_seconds: float = DISCOVERY_POLL_SECONDS,
) -> T:
    """Find a device through the proxy scanners within the scan window.

    Sleeps ``warmup_seconds`` first, then runs discovery passes every
    ``poll_seconds`` until ``scan_seconds`` elapse. Each pass calls
    ``lookup`` (fast paths such as an address lookup) and then offers
    every advertised ``(device, advertisement)`` pair to ``match``; the
    first non-None result from either hook is returned. Raises
    TimeoutError when the scan window closes without a match.
    """
    await asyncio.sleep(warmup_seconds)
    deadline = asyncio.get_running_loop().time() + scan_seconds
    while True:
        found = lookup()
        if found is not None:
            return found
        for scanner in manager.async_current_scanners():
            discovered = cast(
                "dict[str, tuple[BLEDevice, Any]]",
                scanner.discovered_devices_and_advertisement_data,
            )
            for device, advertisement in discovered.values():
                result = match(device, advertisement)
                if result is not None:
                    return result
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(DISCOVERY_TIMEOUT_MESSAGE)
        await asyncio.sleep(poll_seconds)
