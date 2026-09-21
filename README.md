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
await client.connect()
await client.set_output_limit(800)
await client.disconnect()
```

## Probe

The developer-only probe captures SolarFlow GATT traffic through an ESPHome
Bluetooth proxy (e.g. the Home Assistant Connect AUX-2).
It is read-only by default and sends only the BLESPP
handshake and `getAll` request.

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

Target a specific SolarFlow device by Bluetooth address or advertised
identifier:

```bash
uv run scripts/probe_solarflow.py \
  --proxy "192.168.1.157" \
  --noise-psk "your-esphome-noise-psk" \
  --address "AA:BB:CC:DD:EE:FF" \
  --identifier "DEVICE_IDENTIFIER" \
  --scan-seconds 60 \
  --capture-seconds 30 \
  --output /tmp/solarflow-target.jsonl
```

Capture advertisements and GATT traffic without sending the BLESPP handshake:

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
uv run pytest
uv run ruff check .
uv run mypy src
```

Run one test file or test:

```bash
uv run pytest tests/test_solarflow.py
uv run pytest tests/test_solarflow.py -k connect_handshake
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
