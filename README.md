# solarflow-ble

Unofficial asynchronous Python library to interact with Zendure SolarFlow
controllers over Bluetooth Low Energy.

Requires Python >= 3.14.

## Install

```bash
pip install solarflow-ble
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
Bluetooth proxy. It is read-only by default and sends only the BLESPP
handshake and `getAll` request.

```bash
export SOLARFLOW_PROXY=proxy.example.local
export SOLARFLOW_NOISE_PSK="..."
uv run solarflow-ble-probe --output /tmp/solarflow.jsonl
```

Optional target filters:

```bash
uv run solarflow-ble-probe \
  --address AA:BB:CC:DD:EE:FF \
  --identifier DEVICE_IDENTIFIER \
  --output /tmp/solarflow.jsonl
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
