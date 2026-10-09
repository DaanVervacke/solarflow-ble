# solarflow-ble

[![Check](https://github.com/DaanVervacke/solarflow-ble/actions/workflows/check.yml/badge.svg)](https://github.com/DaanVervacke/solarflow-ble/actions/workflows/check.yml)
[![PyPI version](https://img.shields.io/pypi/v/solarflow-ble.svg)](https://pypi.org/project/solarflow-ble/)
[![Python versions](https://img.shields.io/pypi/pyversions/solarflow-ble.svg)](https://pypi.org/project/solarflow-ble/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Async Python library for Zendure SolarFlow devices over Bluetooth Low Energy.
Requires Python >= 3.14.

> Unofficial and reverse-engineered: not endorsed by Zendure, and it may
> break without notice whenever Zendure changes their firmware.

The protocol work builds on
[esphome-solarflow-ble](https://github.com/krumpholz/esphome-solarflow-ble)
and reverse engineering of the Zendure Android app. The library has been
tested with a SolarFlow 2400AC through a Home Assistant Connect AUX-2
Bluetooth proxy.

## Install

```bash
uv add solarflow-ble
```

## Documentation

The API reference is hosted at
[solarflow-ble.readthedocs.io](https://solarflow-ble.readthedocs.io/).

## Usage

`SolarFlowClient` accepts an injected `BleTransport`. Tests can use a fake
transport without Bluetooth hardware.

```python
import asyncio

from bleak import BleakScanner

from solarflow_ble import BleakTransport, SolarFlowClient


async def main() -> None:
    device = await BleakScanner.find_device_by_address("AA:BB:CC:DD:EE:FF")
    if device is None:
        raise SystemExit("SolarFlow not found")
    async with SolarFlowClient(BleakTransport(device)) as client:
        print(client.device_id)
        print(client.state)


asyncio.run(main())
```

Inside Home Assistant, pass the `BLEDevice` from
`bluetooth.async_ble_device_from_address` instead of scanning.
`parse_advertisement` turns SolarFlow manufacturer data into an
`Advertisement` with the device identifier.

The `async with` block connects on entry and always disconnects on exit,
including when the body raises. For reconnect use cases, call `connect()`
and `disconnect()` yourself instead; `connect()` is reusable after
`disconnect()` on the same client instance.

The client waits for `BLESPP`, sends `BLESPP_OK`, requests `getInfo`, then
sends a `read` request for `getAll`. `client.status` moves from
`DISCONNECTED` to `CONNECTED`, `PROTOCOL_READY` after `getInfo-rsp`, and
`READY` after the first report. `connect()` returns once the session is
`READY`. Every later report updates `client.state` and is passed to the
optional `update_callback` as a `SolarFlowUpdate`.

### Controls

Control methods raise `SolarFlowNotReadyError` unless the client was built
with `allow_control=True` and the session is `READY`.

```python
from solarflow_ble import MODEL_SOLARFLOW_2400AC, AcMode

async with SolarFlowClient(
    BleakTransport(device), allow_control=True, model=MODEL_SOLARFLOW_2400AC
) as client:
    await client.set_output_limit(800)
    await client.set_ac_mode(AcMode.DISCHARGING)
```

| Method | Value | Default range |
| --- | --- | --- |
| `set_input_limit` | watts | `0..2400` |
| `set_output_limit` | watts | `0..2400` |
| `set_min_soc` | percent | `0..50` |
| `set_soc` | target percent | `70..100` |
| `set_ac_mode` | `AcMode.CHARGING` (1) or `AcMode.DISCHARGING` (2) | |

Out-of-range values raise `SolarFlowValidationError` before anything is
sent. A rejected write raises `SolarFlowCommandError`. The SOC setters take
percent, but `state.min_soc` and `state.soc_set` hold the raw per-mille wire
value, so a reported `500` means 50%. All exceptions derive from
`SolarFlowError`.

### Model limits

The default ranges are the values verified for the SolarFlow 2400AC and are the
default for every model. Validation bounds are model-specific: applying
2400AC bounds to a smaller unit could forward out-of-spec values to the
hardware, while larger models would have valid values rejected. Pass
`model=MODEL_SOLARFLOW_2400AC` (`"solarflow-2400ac"`, matched case-insensitively against the registry
of verified entries; the `productKey` the device reports is used when
`model` is not given) or explicit `limits=SolarFlowLimits(...)` to the
constructor. At validation time the client resolves bounds in this order:
explicit `limits`, the registry entry for the model, then the 2400AC
default with a one-time warning. Only bounds verified against real
hardware are registered; unverified models always fall back to the
default with that warning.

## Connection loss

The client detects a failed session through the next failing BLE write (the
keepalive writes every 30 seconds by default, so an abrupt disconnect is
detected at most one keepalive interval later) and through client-side
protocol errors such as invalid JSON payloads or device ID mismatches. When a
session fails:

- pending calls raise `SolarFlowConnectionError` immediately instead of
  waiting for the response timeout
- `client.status` flips to `ConnectionStatus.DISCONNECTED`
- an optional `connection_lost_callback` receives the underlying exception
- `client.last_error` holds a `SolarFlowDeviceError` for the most recent
  device-reported error (`method == "error"`), if any. `connect()` clears it

Messages that are still being processed when the session fails keep flowing
to `update_callback`, with `update.status` reflecting `DISCONNECTED`. Call
`connect()` again to start a fresh session.

## Probe

The developer-only probe captures raw GATT traffic through an ESPHome
Bluetooth proxy. It never sends device-setting writes.

List advertisements:

```bash
uv run python -m scripts.probe_solarflow \
  --proxy "the-ip-of-your-esphome-bluetooth-proxy" \
  --noise-psk "the-encryption-key-of-your-esphome-bluetooth-proxy" \
  --list-advertisements \
  --show-identities
```

Capture a target by address or SolarFlow advertisement identifier:

```bash
uv run python -m scripts.probe_solarflow \
  --proxy "the-ip-of-your-esphome-bluetooth-proxy" \
  --noise-psk "the-encryption-key-of-your-esphome-bluetooth-proxy" \
  --identifier "DEVICE_IDENTIFIER" \
  --capture-seconds 30 \
  --output /tmp/solarflow.jsonl
```

Use `--no-handshake` for passive notification capture. Addresses, identifiers,
device IDs, product keys, pack serials, and credentials are redacted from
capture files. `--show-identities` affects logs only. `--proxy`,
`--noise-psk`, `--address`, and `--identifier` fall back to the
`SOLARFLOW_PROXY`, `SOLARFLOW_NOISE_PSK`, `SOLARFLOW_DEVICE_ADDRESS`, and
`SOLARFLOW_DEVICE_IDENTIFIER` environment variables.

The probe uses the same `SolarFlowClient` protocol flow as the library in
normal mode. Passive mode connects directly to the notification characteristic
without sending protocol writes.

## Standalone library test

`scripts/client_diagnostic.py` exercises the library directly. It does not
use the probe script. The default run is read-only and requires exactly one of
`--address` or `--identifier`.

```bash
uv run python -m scripts.client_diagnostic \
  --proxy "the-ip-of-your-esphome-bluetooth-proxy" \
  --noise-psk "the-encryption-key-of-your-esphome-bluetooth-proxy" \
  --identifier "DEVICE_IDENTIFIER" \
  --duration 30 \
  --output /tmp/solarflow-library-test.jsonl
```

You can put the connection settings in the ignored file
`scripts/client_diagnostic.local.json`:

```json
{
  "proxy": "the-ip-of-your-esphome-bluetooth-proxy",
  "noise_psk": "the-encryption-key-of-your-esphome-bluetooth-proxy",
  "identifier": "DEVICE_IDENTIFIER"
}
```

Then run:

```bash
uv run python -m scripts.client_diagnostic --duration 30
```

The script prints decoded updates to stdout and always redacts optional JSONL
output. Controls require `--controls`, `--confirm-controls`, and explicit
`--min-soc` and `--soc` values. `--input-limit`, `--output-limit`, and
`--ac-mode` are optional. Use `--config` to read a different JSON file.
Never commit the local config or a real PSK.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the gate, and the pull
request expectations.

```bash
uv sync
uv run python -m scripts.check
```

Run focused tests:

```bash
uv run pytest tests/test_solarflow.py
uv run pytest tests/test_client_diagnostic.py
```

The full gate runs a version drift check, format, Ruff, mypy, branch-covered
tests, coverage, `uv build`, and `uv audit` in that order.

## License

MIT. See [LICENSE](LICENSE).
