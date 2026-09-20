"""Developer probe for SolarFlow traffic through an ESPHome proxy."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import bleak
import habluetooth
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak_esphome import APIConnectionManager, ESPHomeDeviceConfig
from habluetooth import BluetoothScanningMode

from .const import NOTIFY_CHARACTERISTIC_UUID, WRITE_CHARACTERISTIC_UUID
from .protocol import encode_json, parse_advertisement

_LOGGER = logging.getLogger(__name__)


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


async def _find_device(
    config: ProbeConfig, bluetooth_manager: habluetooth.BluetoothManager
) -> BLEDevice:
    deadline = asyncio.get_running_loop().time() + config.scan_seconds
    await asyncio.sleep(5)
    while True:
        if config.target_address:
            device = bluetooth_manager.async_ble_device_from_address(
                config.target_address, connectable=True
            )
            if device is not None:
                _LOGGER.info(
                    "Found target address=%s name=%s", "DEVICE_ADDRESS", device.name
                )
                return device
        if config.target_address:
            for device in bluetooth_manager.async_discovered_devices(True):
                if _advertisement_matches(config, device):
                    _LOGGER.info(
                        "Found target address=%s name=%s", "DEVICE_ADDRESS", device.name
                    )
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
                _LOGGER.info("Found target via proxy scanner")
                return device
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("SolarFlow device was not found through the proxy")
        await asyncio.sleep(0.5)


async def _list_advertisements(
    config: ProbeConfig, bluetooth_manager: habluetooth.BluetoothManager
) -> None:
    """Print every advertisement seen by the proxy during the scan window."""
    deadline = asyncio.get_running_loop().time() + config.scan_seconds
    seen: set[str] = set()
    await asyncio.sleep(5)
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
                _LOGGER.info(
                    "ADVERTISEMENT address=%s rssi=%s service_uuids=%s",
                    "DEVICE_ADDRESS",
                    advertisement.rssi,
                    advertisement.service_uuids,
                )
            continue
        await asyncio.sleep(0.5)
    _LOGGER.info("Advertisement scan complete; unique devices=%d", len(seen))


async def _write(
    client: bleak.BleakClient,
    characteristic: BleakGATTCharacteristic,
    message: dict[str, Any],
    capture: CaptureWriter,
) -> None:
    payload = encode_json(message)
    capture.write("tx", payload, characteristic=characteristic.uuid)
    await client.write_gatt_char(characteristic, payload, response=False)


async def run_probe(config: ProbeConfig) -> None:
    """Run one read-only or handshake capture."""
    capture = CaptureWriter(config.output)
    manager = APIConnectionManager(
        ESPHomeDeviceConfig(
            address=_proxy_host(config.proxy), noise_psk=config.noise_psk
        )
    )
    bluetooth_manager = habluetooth.BluetoothManager()
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
        async with bleak.BleakClient(device, timeout=30) as client:
            _LOGGER.info("Connected to DEVICE_ADDRESS")
            for service in client.services:
                _LOGGER.info("Service %s", service.uuid)
                for characteristic in service.characteristics:
                    _LOGGER.info(
                        "Characteristic %s properties=%s",
                        characteristic.uuid,
                        ",".join(characteristic.properties),
                    )

            write_characteristic = _find_characteristic(
                client, WRITE_CHARACTERISTIC_UUID
            )
            notify_characteristic = _find_characteristic(
                client, NOTIFY_CHARACTERISTIC_UUID
            )

            def on_notification(
                characteristic: BleakGATTCharacteristic, payload: bytearray
            ) -> None:
                capture.write("rx", bytes(payload), characteristic=characteristic.uuid)

            await client.start_notify(notify_characteristic, on_notification)
            if config.send_handshake:
                await _write(
                    client,
                    write_characteristic,
                    {"messageId": "1009", "method": "BLESPP_OK"},
                    capture,
                )
                await asyncio.sleep(0.5)
                timestamp = int(time.time() * 1000)
                await _write(
                    client,
                    write_characteristic,
                    {
                        "messageId": str(timestamp),
                        "method": "getInfo",
                        "timestamp": timestamp,
                    },
                    capture,
                )
                await asyncio.sleep(0.3)
                await _write(
                    client,
                    write_characteristic,
                    {
                        "messageId": "11",
                        "timestamp": int(time.time() * 1000),
                        "properties": ["getAll"],
                        "method": "read",
                    },
                    capture,
                )
            await asyncio.sleep(config.capture_seconds)
            await client.stop_notify(notify_characteristic)
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
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run the probe command."""
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
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
            )
        )
    )
