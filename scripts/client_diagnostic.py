"""Standalone hardware diagnostic for the SolarFlow library client."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import habluetooth
from bleak.backends.device import BLEDevice
from bleak_esphome import APIConnectionManager, ESPHomeDeviceConfig
from solarflow_ble import BleakTransport, SolarFlowClient, __version__
from solarflow_ble.models import BatteryPack, SolarFlowState, SolarFlowUpdate
from solarflow_ble.protocol import parse_advertisement

from scripts._proxy_common import (
    DiscoveryBluetoothManager as DiagnosticBluetoothManager,
)
from scripts._proxy_common import proxy_host, scan_for_target, set_active_scanning
from scripts._redact import redact_value

DEFAULT_DURATION = 15.0
DEFAULT_SCAN_SECONDS = 30.0
DEFAULT_CONFIG = Path("scripts/client_diagnostic.local.json")
_CONFIG_KEYS = frozenset({"proxy", "noise_psk", "address", "identifier"})
_STATE_FIELDS = (
    "input_limit",
    "output_limit",
    "ac_mode",
    "electric_level",
    "pack_input_power",
    "output_pack_power",
    "output_home_power",
    "grid_input_power",
    "solar_input_power",
    "fault_level",
    "smart_mode",
)


@dataclass(frozen=True, slots=True)
class ControlAction:
    """One requested setter and its safe-to-use restoration value."""

    property_name: str
    setter_name: str
    value: int
    restore_value: int | None


@dataclass(frozen=True, slots=True)
class ControlPlan:
    """Setters that can run and controls that must be skipped."""

    actions: tuple[ControlAction, ...]
    skipped: tuple[str, ...]


class JsonlWriter:
    """Write decoded callback observations to a redacted JSONL file."""

    def __init__(self, path: Path | None) -> None:
        self._file = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("w", encoding="utf-8")

    def write(self, record: dict[str, Any]) -> None:
        if self._file is None:
            return
        self._file.write(
            json.dumps(redact_value(record), separators=(",", ":"), default=str) + "\n"
        )
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def serialize_pack(pack: BatteryPack) -> dict[str, Any]:
    """Serialize one typed battery pack for diagnostics."""
    return asdict(pack)


def serialize_state(state: SolarFlowState) -> dict[str, Any]:
    """Serialize selected state fields, identity, and every known pack."""
    result = {field: getattr(state, field) for field in _STATE_FIELDS}
    result.update(
        {
            "device_id": state.device_id,
            "product_key": state.product_key,
            "packs": [serialize_pack(pack) for pack in state.packs],
        }
    )
    return result


def serialize_update(update: SolarFlowUpdate) -> dict[str, Any]:
    """Build the decoded callback record written to JSONL."""
    return {
        "time": time.time(),
        "method": update.raw_message.get("method"),
        "status": update.status.value,
        "state": serialize_state(update.state),
        "packs": [serialize_pack(pack) for pack in update.state.packs],
        "raw": update.raw_message,
    }


def format_update(update: SolarFlowUpdate, *, verbose: bool = False) -> str:
    """Format a callback update for local stdout diagnostics."""
    record = redact_value(serialize_update(update))
    if verbose:
        return json.dumps(record, sort_keys=True, default=str)
    state = update.state
    selected = " ".join(f"{field}={getattr(state, field)}" for field in _STATE_FIELDS)
    return (
        f"time={record['time']:.3f} method={record['method']} "
        f"status={record['status']} {selected} packs={len(state.packs)}"
    )


def _stdout(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def _stderr(message: str) -> None:
    sys.stderr.write(f"{message}\n")


def _value_or_override(
    property_name: str,
    setter_name: str,
    state_value: int | None,
    override: int | None,
    skipped: list[str],
) -> ControlAction | None:
    value = override if override is not None else state_value
    if value is None:
        skipped.append(f"{property_name}: current value unavailable")
        return None
    return ControlAction(property_name, setter_name, value, state_value)


def _restorable_value_or_override(
    property_name: str,
    setter_name: str,
    state_value: int | None,
    override: int | None,
    skipped: list[str],
) -> ControlAction | None:
    if override is not None and state_value is None:
        skipped.append(
            f"{property_name}: cannot restore safely: original value unknown"
        )
        return None
    return _value_or_override(
        property_name, setter_name, state_value, override, skipped
    )


def _wire_per_mille_to_percent(value: int | None) -> int | None:
    """Convert a per-mille wire value (percent x 10) to whole percent."""
    if value is None:
        return None
    return value // 10


def plan_controls(
    state: SolarFlowState,
    *,
    input_limit: int | None = None,
    output_limit: int | None = None,
    min_soc: int | None = None,
    soc: int | None = None,
    ac_mode: int | None = None,
) -> ControlPlan:
    """Plan controls without inventing values for missing state.

    Overrides whose original value is unknown are refused so that every
    applied control can be restored.
    """
    skipped: list[str] = []
    actions = [
        action
        for action in (
            _value_or_override(
                "inputLimit", "set_input_limit", state.input_limit, input_limit, skipped
            ),
            _value_or_override(
                "outputLimit",
                "set_output_limit",
                state.output_limit,
                output_limit,
                skipped,
            ),
            _restorable_value_or_override(
                "minSoc",
                "set_min_soc",
                _wire_per_mille_to_percent(state.min_soc),
                min_soc,
                skipped,
            ),
            _restorable_value_or_override(
                "socSet",
                "set_soc",
                _wire_per_mille_to_percent(state.soc_set),
                soc,
                skipped,
            ),
            _restorable_value_or_override(
                "acMode", "set_ac_mode", state.ac_mode, ac_mode, skipped
            ),
        )
        if action is not None
    ]
    return ControlPlan(tuple(actions), tuple(skipped))


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--proxy")
    parser.add_argument("--noise-psk")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--address")
    target.add_argument("--identifier")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--confirm-controls", action="store_true")
    parser.add_argument("--input-limit", type=int)
    parser.add_argument("--output-limit", type=int)
    parser.add_argument("--min-soc", type=int)
    parser.add_argument("--soc", type=int)
    parser.add_argument("--ac-mode", type=int)
    return parser


def load_config(path: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    """Load the supported diagnostic settings from a JSON config file."""
    if not path.exists():
        if allow_missing:
            return {}
        raise ValueError(f"config file does not exist: {path}")
    try:
        raw_config = json.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise ValueError(f"could not read config file: {path}") from err
    except json.JSONDecodeError as err:
        raise ValueError(f"config file is not valid JSON: {path}") from err
    if not isinstance(raw_config, dict):
        raise TypeError("config file must contain a JSON object")
    unknown_keys = set(raw_config) - _CONFIG_KEYS
    if unknown_keys:
        raise ValueError(f"unsupported config key: {min(unknown_keys)}")
    if (
        raw_config.get("address") is not None
        and raw_config.get("identifier") is not None
    ):
        raise ValueError("config file must contain only one of address or identifier")
    return cast(dict[str, Any], raw_config)


def merge_config(args: argparse.Namespace, config: dict[str, Any]) -> None:
    """Apply config values to unset CLI arguments, with CLI precedence."""
    if args.proxy is None:
        args.proxy = config.get("proxy")
    if args.noise_psk is None:
        args.noise_psk = config.get("noise_psk")

    if args.address is not None:
        args.identifier = None
    elif args.identifier is not None:
        args.address = None
    else:
        args.address = config.get("address")
        args.identifier = config.get("identifier")


def validate_arguments(args: argparse.Namespace) -> None:
    """Validate safety-sensitive arguments before any proxy or BLE setup."""
    for name, value in (
        ("proxy", args.proxy),
        ("noise-psk", args.noise_psk),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"--{name} is required (provide it on the CLI or in --config)"
            )
    targets = (args.address, args.identifier)
    if sum(value is not None for value in targets) != 1:
        raise ValueError("exactly one of --address or --identifier is required")
    if any(
        not isinstance(value, str) or not value.strip()
        for value in targets
        if value is not None
    ):
        raise ValueError(
            "the selected address or identifier must be a non-empty string"
        )
    if args.duration < 0:
        raise ValueError("--duration must not be negative")
    if args.controls and not args.confirm_controls:
        raise ValueError("--controls requires --confirm-controls")
    if args.confirm_controls and not args.controls:
        raise ValueError("--confirm-controls requires --controls")
    if args.controls and args.min_soc is None:
        raise ValueError("--controls requires explicit --min-soc")
    if args.controls and args.soc is None:
        raise ValueError("--controls requires explicit --soc")


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse and validate arguments without starting hardware access."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        merge_config(
            args,
            load_config(args.config, allow_missing=args.config == DEFAULT_CONFIG),
        )
        validate_arguments(args)
    except ValueError as err:
        parser.error(str(err))
    return args


async def find_device(
    manager: habluetooth.BluetoothManager,
    *,
    address: str | None,
    identifier: str | None,
    scan_seconds: float = DEFAULT_SCAN_SECONDS,
) -> tuple[BLEDevice, str | None]:
    """Find one exact target from habluetooth scanner advertisements."""
    normalized_address = address.upper() if address else None

    def lookup() -> tuple[BLEDevice, str | None] | None:
        if not normalized_address:
            return None
        device = manager.async_ble_device_from_address(
            normalized_address, connectable=True
        )
        if device is not None:
            return device, None
        return None

    def match(
        device: BLEDevice, advertisement: Any
    ) -> tuple[BLEDevice, str | None] | None:
        if normalized_address and device.address.upper() != normalized_address:
            return None
        parsed = parse_advertisement(
            device.address,
            advertisement.manufacturer_data,
            rssi=advertisement.rssi,
            connectable=True,
        )
        if parsed is None or (identifier and parsed.identifier != identifier):
            return None
        return device, parsed.identifier

    return await scan_for_target(
        manager,
        lookup=lookup,
        match=match,
        scan_seconds=scan_seconds,
    )


async def run_controls(client: SolarFlowClient, args: argparse.Namespace) -> None:
    """Run and safely restore the opt-in control suite."""
    if not client.ready:
        raise RuntimeError("Controls require a READY SolarFlow client")
    plan = plan_controls(
        client.state,
        input_limit=args.input_limit,
        output_limit=args.output_limit,
        min_soc=args.min_soc,
        soc=args.soc,
        ac_mode=args.ac_mode,
    )
    for reason in plan.skipped:
        _stdout(f"CONTROL SKIP {reason}")
    setters = {
        name: getattr(client, name)
        for name in (
            "set_input_limit",
            "set_output_limit",
            "set_min_soc",
            "set_soc",
            "set_ac_mode",
        )
    }
    applied: list[ControlAction] = []
    try:
        for action in plan.actions:
            applied.append(action)
            await setters[action.setter_name](action.value)
            _stdout(f"CONTROL SET {action.property_name}={action.value}")
    finally:
        restore_error: Exception | None = None
        for action in reversed(applied):
            if action.restore_value is None:
                _stdout(
                    f"CONTROL RESTORE SKIP {action.property_name}: value unavailable"
                )
                continue
            try:
                await setters[action.setter_name](action.restore_value)
            except Exception as err:  # noqa: BLE001
                _stdout(f"CONTROL RESTORE FAILED {action.property_name}: {err}")
                restore_error = restore_error or err
                continue
            _stdout(f"CONTROL RESTORED {action.property_name}={action.restore_value}")
        if restore_error is not None:
            raise restore_error


async def run(args: argparse.Namespace) -> None:
    """Run one direct library-client diagnostic session."""
    writer = JsonlWriter(args.output)
    manager = APIConnectionManager(
        ESPHomeDeviceConfig(address=proxy_host(args.proxy), noise_psk=args.noise_psk)
    )
    bluetooth_manager = DiagnosticBluetoothManager()
    client: SolarFlowClient | None = None

    async def on_update(update: SolarFlowUpdate) -> None:
        _stdout(format_update(update, verbose=args.verbose))
        writer.write(serialize_update(update))

    try:
        await bluetooth_manager.async_setup()
        await manager.start()
        set_active_scanning(bluetooth_manager)
        device, discovered_identifier = await find_device(
            bluetooth_manager, address=args.address, identifier=args.identifier
        )
        transport = BleakTransport(device)
        client = SolarFlowClient(
            transport, update_callback=on_update, allow_control=args.controls
        )
        await client.connect()
        if not client.protocol_ready:
            raise RuntimeError("SolarFlow protocol did not become ready")
        connection = redact_value(
            {
                "address": device.address,
                "name": device.name,
                "identifier": discovered_identifier or args.identifier,
                "device_id": client.device_id,
                "status": client.status.value,
                "ready": client.ready,
            }
        )
        _stdout(
            "CONNECTED "
            f"address={connection['address']} name={connection['name']!r} "
            f"identifier={connection['identifier']} "
            f"device_id={connection['device_id']} "
            f"status={connection['status']} ready={connection['ready']}"
        )
        _stdout(
            json.dumps(
                redact_value(serialize_state(client.state)),
                sort_keys=True,
                default=str,
            )
        )
        if args.controls:
            await run_controls(client, args)
        await asyncio.sleep(args.duration)
    finally:
        try:
            try:
                if client is not None:
                    await client.disconnect()
            finally:
                try:
                    await manager.stop()
                finally:
                    bluetooth_manager.async_stop()
        finally:
            writer.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the standalone SolarFlow client test."""
    try:
        args = parse_arguments(argv)
        asyncio.run(run(args))
    except KeyboardInterrupt:
        _stderr("Interrupted")
        return 1
    except Exception as err:  # noqa: BLE001
        _stderr(f"SolarFlow client test failed: {err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
