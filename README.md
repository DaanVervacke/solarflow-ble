# solarflow-ble

Unofficial asynchronous Python library for communicating with Zendure
SolarFlow controllers over Bluetooth Low Energy.

The protocol implementation is based on
[esphome-solarflow-ble](https://github.com/krumpholz/esphome-solarflow-ble).
The library has been tested with a SolarFlow 2400AC through a Home Assistant
Connect AUX-2 acting as a Bluetooth proxy. Traffic probing and debugging use
[`bleak-esphome`](https://github.com/Bluetooth-Devices/bleak-esphome).

All protocol credits and reverse-engineering efforts belong to the
[esphome-solarflow-ble project](https://github.com/krumpholz/esphome-solarflow-ble).
This library builds on that work in Python.

Requires Python >= 3.14.

## Install

```bash
uv add solarflow-ble
```

## Usage

`SolarFlowClient` uses an injected transport, so applications can choose their
own Bluetooth adapter and tests can use a fake transport without Bluetooth
hardware.

```python
import asyncio

from bleak.backends.device import BLEDevice

from solarflow_ble import BleakTransport, SolarFlowClient


async def main() -> None:
    # Replace these values with a device discovered by your BLE adapter.
    device = BLEDevice("AA:BB:CC:DD:EE:FF", "SolarFlow", {})
    client = SolarFlowClient(BleakTransport(device))

    try:
        await client.connect()
        print(client.state)
        print(client.status)
    finally:
        await client.disconnect()


asyncio.run(main())
```

The client connects, completes the BLESPP handshake, reads the initial
`getAll` state, and keeps the session updated. Always call `disconnect()` when
the session ends.

Control methods are disabled by default. Enable them explicitly when the
application is intended to change device settings:

```python
client = SolarFlowClient(BleakTransport(device), allow_control=True)
try:
    await client.connect()
    await client.set_output_limit(800)
finally:
    await client.disconnect()
```

## Probe

The developer-only probe captures SolarFlow GATT traffic through an ESPHome
Bluetooth proxy (e.g. the Home Assistant Connect AUX-2).
It does not send device-setting writes by default. It sends the BLESPP
handshake, `getInfo`, and `getAll` protocol requests.

```bash
# Discover a SolarFlow device and capture its traffic.
uv run scripts/probe_solarflow.py \
  --proxy "192.168.1.157" \
  --noise-psk "your-esphome-noise-psk" \
  --output /tmp/solarflow.jsonl
```

List advertisements without connecting to a SolarFlow device:

```bash
uv run scripts/probe_solarflow.py \
  --proxy "192.168.1.157" \
  --noise-psk "your-esphome-noise-psk" \
  --list-advertisements \
  --scan-seconds 30
```

Advertisement addresses and parsed SolarFlow identifiers are redacted by
default. Add `--show-identities` when selecting values for `--address` or
`--identifier`; capture files remain redacted.

Target a specific SolarFlow device by Bluetooth address:

```bash
uv run scripts/probe_solarflow.py \
  --proxy "192.168.1.157" \
  --noise-psk "your-esphome-noise-psk" \
  --address "AA:BB:CC:DD:EE:FF" \
  --scan-seconds 60 \
  --capture-seconds 30 \
  --output /tmp/solarflow-target.jsonl
```

Alternatively, target a device by its SolarFlow manufacturer-advertisement
identifier:

```bash
uv run scripts/probe_solarflow.py \
  --proxy "192.168.1.157" \
  --noise-psk "your-esphome-noise-psk" \
  --identifier "DEVICE_IDENTIFIER" \
  --scan-seconds 60 \
  --capture-seconds 30 \
  --output /tmp/solarflow-target.jsonl
```

Connect to a device and capture notifications without sending the BLESPP
handshake or the initial `getInfo` and `getAll` requests:

```bash
uv run scripts/probe_solarflow.py \
  --proxy "192.168.1.157" \
  --noise-psk "your-esphome-noise-psk" \
  --address "AA:BB:CC:DD:EE:FF" \
  --no-handshake \
  --capture-seconds 30 \
  --output /tmp/solarflow-passive.jsonl
```

## Development

This project uses [uv](https://docs.astral.sh/uv/) and targets Python 3.14+.

```bash
uv sync
uv run python -m scripts.check
```

The development gate stops at the first failure in this order: format check,
Ruff lint, mypy, branch-covered tests, coverage report, then package build.

Run one test file or test:

```bash
uv run pytest tests/test_solarflow.py
uv run pytest tests/test_solarflow.py -k connect_handshake
```

### Standalone library client test

Use the standalone diagnostic to exercise `SolarFlowClient` through an
ESPHome Bluetooth proxy. It discovers exactly one target by address or
SolarFlow advertisement identifier and is read-only unless controls are
explicitly confirmed.

```bash
uv run scripts/test_solarflow_client.py \
  --proxy "192.168.1.157" \
  --noise-psk "your-esphome-noise-psk" \
  --identifier "DEVICE_IDENTIFIER" \
  --duration 30 \
  --output /tmp/solarflow-library-test.jsonl
```

The script prints device identities and decoded state to stdout for local
diagnostics. The optional JSONL file is always recursively redacted. Controls
require both `--controls` and `--confirm-controls`; they also require explicit
`--min-soc` and `--soc` values because those original wire values are not
available safely for restoration.

The connection settings can be kept in the ignored local config file
`scripts/test_solarflow_client.local.json`. The file may contain `proxy`,
`noise_psk`, and exactly one of `address` or `identifier`:

```json
{
  "proxy": "192.168.1.157",
  "noise_psk": "your-esphome-noise-psk",
  "identifier": "DEVICE_IDENTIFIER"
}
```

Run the diagnostic with the default local file:

```bash
uv run scripts/test_solarflow_client.py --duration 30
```

Use `--config path/to/config.json` for another local file. Command-line values
override values from the config file, so individual settings can be replaced
without editing it:

```bash
uv run scripts/test_solarflow_client.py \
  --config scripts/test_solarflow_client.local.json \
  --identifier "OTHER_DEVICE_IDENTIFIER"
```

Do not commit this file or paste a real `noise_psk` into documentation,
fixtures, logs, or shell history. The default local filename is ignored by
Git; use a file with equivalent local-only handling when choosing another
config path. The script never prints or writes `noise_psk`.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
