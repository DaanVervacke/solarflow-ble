# solarflow-ble

Async Python library for Zendure SolarFlow devices over Bluetooth Low Energy.
The protocol work builds on [esphome-solarflow-ble](https://github.com/krumpholz/esphome-solarflow-ble)
and reverse engineering of the Zendure Android app.

The library has been tested with a SolarFlow 2400AC through a Home Assistant
Connect AUX-2 Bluetooth proxy. It requires Python 3.14 or newer.

## Install

```bash
uv add solarflow-ble
```

## Library usage

`SolarFlowClient` accepts an injected `BleTransport`. Tests can use a fake
transport without Bluetooth hardware.

```python
import asyncio

from bleak.backends.device import BLEDevice

from solarflow_ble import BleakTransport, SolarFlowClient


async def main() -> None:
    device = BLEDevice("AA:BB:CC:DD:EE:FF", "SolarFlow", {})
    client = SolarFlowClient(BleakTransport(device))
    try:
        await client.connect()
        print(client.device_id)
        print(client.state)
    finally:
        await client.disconnect()


asyncio.run(main())
```

The client waits for `BLESPP`, sends `BLESPP_OK`, requests `getInfo`, then
sends a `read` request for `getAll`. It keeps the session updated with report
messages. Always disconnect the client.

Control methods are disabled unless `allow_control=True`. Validated ranges are:

- input and output limits: `0..2400 W`
- minimum SOC: `0..50%`
- target SOC: `70..100%`
- AC mode: `1` or `2`

## Probe

The developer-only probe captures raw GATT traffic through an ESPHome
Bluetooth proxy. It never sends device-setting writes.

List advertisements:

```bash
uv run scripts/probe_solarflow.py \
  --proxy "the-ip-of-your-esphome-bluetooth-proxy" \
  --noise-psk "the-encryption-key-of-your-esphome-bluetooth-proxy" \
  --list-advertisements \
  --show-identities
```

Capture a target by address or SolarFlow advertisement identifier:

```bash
uv run scripts/probe_solarflow.py \
  --proxy "the-ip-of-your-esphome-bluetooth-proxy" \
  --noise-psk "the-encryption-key-of-your-esphome-bluetooth-proxy" \
  --identifier "DEVICE_IDENTIFIER" \
  --capture-seconds 30 \
  --output /tmp/solarflow.jsonl
```

Use `--no-handshake` for passive notification capture. Addresses, identifiers,
device IDs, product keys, pack serials, and credentials are redacted from
capture files. `--show-identities` affects logs only.

The probe uses the same `SolarFlowClient` protocol flow as the library in
normal mode. Passive mode connects directly to the notification characteristic
without sending protocol writes.

## Standalone library test

`scripts/test_solarflow_client.py` exercises the library directly. It does not
use the probe script. The default run is read-only and requires exactly one of
`--address` or `--identifier`.

```bash
uv run scripts/test_solarflow_client.py \
  --proxy "the-ip-of-your-esphome-bluetooth-proxy" \
  --noise-psk "the-encryption-key-of-your-esphome-bluetooth-proxy" \
  --identifier "DEVICE_IDENTIFIER" \
  --duration 30 \
  --output /tmp/solarflow-library-test.jsonl
```

You can put the connection settings in the ignored file
`scripts/test_solarflow_client.local.json`:

```json
{
  "proxy": "the-ip-of-your-esphome-bluetooth-proxy",
  "noise_psk": "the-encryption-key-of-your-esphome-bluetooth-proxy",
  "identifier": "DEVICE_IDENTIFIER"
}
```

Then run:

```bash
uv run scripts/test_solarflow_client.py --duration 30
```

The script prints decoded updates to stdout and always redacts optional JSONL
output. Controls require both `--controls` and `--confirm-controls`. Never
commit the local config or a real PSK.

## Development

```bash
uv sync
uv run python -m scripts.check
```

Run focused tests:

```bash
uv run pytest tests/test_solarflow.py
uv run pytest tests/test_solarflow_client_script.py
```

The full gate runs format, Ruff, mypy, branch-covered tests, coverage, and
`uv build` in that order.

## License

MIT. See [LICENSE](LICENSE).
