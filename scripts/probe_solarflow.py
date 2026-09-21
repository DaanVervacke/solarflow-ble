"""Developer probe for SolarFlow traffic through an ESPHome proxy."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import bleak
import habluetooth
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_esphome import APIConnectionManager, ESPHomeDeviceConfig
from bleak_retry_connector import establish_connection
from habluetooth import (
    BluetoothManager,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from solarflow_ble import BleakTransport, SolarFlowClient
from solarflow_ble.client import BleTransport, NotificationCallback
from solarflow_ble.const import NOTIFY_CHARACTERISTIC_UUID
from solarflow_ble.exceptions import SolarFlowError
from solarflow_ble.protocol import parse_advertisement

_LOGGER = logging.getLogger(__name__)


class ProbeBluetoothManager(BluetoothManager):
    """Keep discovery data available for the probe's direct scanner reads."""

    def _discover_service_info(self, service_info: BluetoothServiceInfoBleak) -> None:
        """Handle discovery through the scanner collections read by the probe."""


@dataclass(slots=True)
class ProbeConfig:
    """Local configuration for a probe run."""

    proxy: str
    noise_psk: str | None
    target_address: str | None
    target_identifier: str | None
    output: Path | None
    scan_seconds: float
    capture_seconds: float
    send_handshake: bool
    list_advertisements: bool
    show_identities: bool = False

    def __post_init__(self) -> None:
        if self.target_address:
            self.target_address = self.target_address.upper()


class CaptureWriter:
    """Write newline-delimited JSON capture records."""

    def __init__(self, path: Path | None) -> None:
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8") if path else None

    def write(self, direction: str, payload: bytes, **extra: Any) -> None:
        text = payload.decode("utf-8", errors="replace")
        try:
            decoded = _redact_capture(json.loads(text))
            payload = json.dumps(
                decoded, separators=(",", ":"), ensure_ascii=False
            ).encode()
            text = payload.decode("utf-8")
        except json.JSONDecodeError:
            decoded = None
            text = payload.decode("utf-8", errors="replace")
        extra = _redact_capture(extra)
        record = {
            "time": time.time(),
            "direction": direction,
            "hex": payload.hex(),
            "text": text,
            "json": decoded,
            **extra,
        }
        _LOGGER.info("%s %s", direction, record)
        if self._file:
            self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._file.flush()

    def close(self) -> None:
        if self._file:
            self._file.close()


class CaptureTransport(BleTransport):
    """Capture transport traffic while delegating to a BLE transport."""

    def __init__(self, transport: BleTransport, capture: CaptureWriter) -> None:
        self._transport = transport
        self._capture = capture

    async def connect(self) -> None:
        await self._transport.connect()

    async def disconnect(self) -> None:
        await self._transport.disconnect()

    async def start_notify(
        self, characteristic: str, callback: NotificationCallback
    ) -> None:
        async def on_notification(received_characteristic: str, payload: bytes) -> None:
            self._capture.write("rx", payload, characteristic=received_characteristic)
            result = callback(received_characteristic, payload)
            if isinstance(result, Awaitable):
                await result

        await self._transport.start_notify(characteristic, on_notification)

    async def stop_notify(self, characteristic: str) -> None:
        await self._transport.stop_notify(characteristic)

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        self._capture.write("tx", data, characteristic=characteristic)
        await self._transport.write_gatt_char(characteristic, data, response=response)


def _find_characteristic(
    client: bleak.BleakClient, uuid: str
) -> BleakGATTCharacteristic:
    characteristic = client.services.get_characteristic(uuid)
    if characteristic is None:
        raise RuntimeError(f"Missing GATT characteristic {uuid}")
    return characteristic


def _proxy_host(value: str) -> str:
    """Return the host portion accepted by bleak-esphome."""
    parsed = urlsplit(value if "://" in value else f"//{value}")
    return parsed.hostname or value.removeprefix("//")


_REDACTED_KEYS = {
    "deviceid": "DEVICE_ID",
    "productkey": "PRODUCT_KEY",
    "sn": "PACK_SERIAL",
    "serial": "PACK_SERIAL",
    "serialnumber": "PACK_SERIAL",
    "noisepsk": "REDACTED",
}


def _redact_capture(value: Any) -> Any:
    """Remove device identity and credentials from persisted captures."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = key.replace("_", "").replace("-", "").lower()
            replacement = _REDACTED_KEYS.get(normalized)
            redacted[key] = replacement if replacement else _redact_capture(item)
        return redacted
    if isinstance(value, list):
        return [_redact_capture(item) for item in value]
    return value


def _set_active_scanning(bluetooth_manager: habluetooth.BluetoothManager) -> None:
    """Request active scanning from every connectable proxy scanner."""
    for scanner in bluetooth_manager.async_current_scanners():
        if getattr(scanner, "connectable", False):
            scanner.set_requested_mode(BluetoothScanningMode.ACTIVE)
            _LOGGER.info(
                "Requested active scanning from %s",
                getattr(scanner, "source", "scanner"),
            )


def _advertisement_matches(config: ProbeConfig, device: BLEDevice) -> bool:
    return not config.target_address or device.address.upper() == config.target_address


def _log_found_target(
    config: ProbeConfig,
    device: BLEDevice,
    identifier: str | None = None,
) -> None:
    if not config.show_identities:
        _LOGGER.info("Found target address=%s name=%s", "DEVICE_ADDRESS", device.name)
        return
    identity = f"address={device.address}"
    if identifier is not None:
        identity += f" identifier={identifier}"
    _LOGGER.info("Found target %s name=%s", identity, device.name)


async def _find_device(
    config: ProbeConfig, bluetooth_manager: habluetooth.BluetoothManager
) -> BLEDevice:
    await asyncio.sleep(5)
    deadline = asyncio.get_running_loop().time() + config.scan_seconds
    while True:
        if config.target_address:
            device = bluetooth_manager.async_ble_device_from_address(
                config.target_address, connectable=True
            )
            if device is not None:
                _log_found_target(config, device)
                return device
        if config.target_address:
            for device in bluetooth_manager.async_discovered_devices(True):
                if _advertisement_matches(config, device):
                    _log_found_target(config, device)
                    return device
        for scanner in bluetooth_manager.async_current_scanners():
            discovered = cast(
                dict[str, tuple[BLEDevice, Any]],
                scanner.discovered_devices_and_advertisement_data,
            )
            for device, advertisement in discovered.values():
                if not _advertisement_matches(config, device):
                    continue
                parsed = parse_advertisement(
                    device.address,
                    advertisement.manufacturer_data,
                    rssi=advertisement.rssi,
                    connectable=True,
                )
                if parsed is None or (
                    config.target_identifier
                    and parsed.identifier != config.target_identifier
                ):
                    continue
                _log_found_target(config, device, parsed.identifier)
                return device
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("SolarFlow device was not found through the proxy")
        await asyncio.sleep(0.5)


async def _list_advertisements(
    config: ProbeConfig, bluetooth_manager: habluetooth.BluetoothManager
) -> None:
    """Print every advertisement seen by the proxy during the scan window."""
    seen: set[str] = set()
    await asyncio.sleep(5)
    deadline = asyncio.get_running_loop().time() + config.scan_seconds
    while asyncio.get_running_loop().time() < deadline:
        for scanner in bluetooth_manager.async_current_scanners():
            discovered = cast(
                dict[str, tuple[BLEDevice, Any]],
                scanner.discovered_devices_and_advertisement_data,
            )
            for device, advertisement in discovered.values():
                if device.address in seen:
                    continue
                seen.add(device.address)
                parsed = parse_advertisement(
                    device.address,
                    advertisement.manufacturer_data,
                    rssi=advertisement.rssi,
                    connectable=True,
                )
                if config.show_identities:
                    identity = f"address={device.address}"
                    if parsed is not None:
                        identity += f" identifier={parsed.identifier}"
                else:
                    identity = "address=DEVICE_ADDRESS"
                _LOGGER.info(
                    "ADVERTISEMENT %s rssi=%s service_uuids=%s",
                    identity,
                    advertisement.rssi,
                    advertisement.service_uuids,
                )
            continue
        await asyncio.sleep(0.5)
    _LOGGER.info("Advertisement scan complete; unique devices=%d", len(seen))


async def _run_passive_capture(
    config: ProbeConfig, device: BLEDevice, capture: CaptureWriter
) -> None:
    """Capture notifications through the direct Bleak path without writes."""
    client = await establish_connection(
        bleak.BleakClient,
        device,
        device.name or device.address,
        timeout=30,
    )
    try:
        connected_address = (
            device.address if config.show_identities else "DEVICE_ADDRESS"
        )
        _LOGGER.info("Connected to %s", connected_address)
        notify_characteristic = _find_characteristic(client, NOTIFY_CHARACTERISTIC_UUID)

        def on_notification(
            characteristic: BleakGATTCharacteristic, payload: bytearray
        ) -> None:
            capture.write("rx", bytes(payload), characteristic=characteristic.uuid)

        await client.start_notify(notify_characteristic, on_notification)
        await asyncio.sleep(config.capture_seconds)
        await client.stop_notify(notify_characteristic)
    finally:
        with suppress(Exception):
            await client.disconnect()


async def run_probe(config: ProbeConfig) -> None:
    """Run one read-only or handshake capture."""
    capture = CaptureWriter(config.output)
    manager = APIConnectionManager(
        ESPHomeDeviceConfig(
            address=_proxy_host(config.proxy), noise_psk=config.noise_psk
        )
    )
    bluetooth_manager = ProbeBluetoothManager()
    try:
        await bluetooth_manager.async_setup()
        await manager.start()
        scanners = bluetooth_manager.async_current_scanners()
        _LOGGER.info(
            "Registered scanners=%d connectable=%d scanning=%d",
            len(scanners),
            sum(bool(getattr(scanner, "connectable", False)) for scanner in scanners),
            sum(bool(getattr(scanner, "scanning", False)) for scanner in scanners),
        )
        _set_active_scanning(bluetooth_manager)
        if config.list_advertisements:
            await _list_advertisements(config, bluetooth_manager)
            return
        device = await _find_device(config, bluetooth_manager)
        if not config.send_handshake:
            await _run_passive_capture(config, device, capture)
        else:
            transport = CaptureTransport(BleakTransport(device), capture)
            client = SolarFlowClient(transport)
            try:
                await client.connect()
                device_id = client.device_id if config.show_identities else "DEVICE_ID"
                _LOGGER.info(
                    "SolarFlow connected device_id=%s status=%s",
                    device_id,
                    client.status,
                )
                await asyncio.sleep(config.capture_seconds)
            finally:
                await client.disconnect()
    finally:
        capture.close()
        with suppress(Exception):
            await manager.stop()
        with suppress(Exception):
            bluetooth_manager.async_stop()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--proxy",
        default=os.getenv("SOLARFLOW_PROXY"),
        required=not os.getenv("SOLARFLOW_PROXY"),
    )
    parser.add_argument("--noise-psk", default=os.getenv("SOLARFLOW_NOISE_PSK"))
    parser.add_argument("--address", default=os.getenv("SOLARFLOW_DEVICE_ADDRESS"))
    parser.add_argument(
        "--identifier", default=os.getenv("SOLARFLOW_DEVICE_IDENTIFIER")
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--scan-seconds", type=float, default=30.0)
    parser.add_argument("--capture-seconds", type=float, default=15.0)
    parser.add_argument("--no-handshake", action="store_true")
    parser.add_argument(
        "--list-advertisements",
        action="store_true",
        help="List every advertisement and do not connect to a peripheral",
    )
    parser.add_argument(
        "--show-identities",
        action="store_true",
        help="Show real BLE addresses and SolarFlow advertisement identifiers",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the probe command."""
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        asyncio.run(
            run_probe(
                ProbeConfig(
                    proxy=args.proxy,
                    noise_psk=args.noise_psk,
                    target_address=args.address,
                    target_identifier=args.identifier,
                    output=args.output,
                    scan_seconds=args.scan_seconds,
                    capture_seconds=args.capture_seconds,
                    send_handshake=not args.no_handshake,
                    list_advertisements=args.list_advertisements,
                    show_identities=args.show_identities,
                )
            )
        )
    except (BleakError, OSError, RuntimeError, SolarFlowError, TimeoutError) as err:
        _LOGGER.warning("Probe failed: %s", err)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
