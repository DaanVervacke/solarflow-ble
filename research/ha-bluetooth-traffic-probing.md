# Using an HA Bluetooth proxy as a SolarFlow BLE probe

## Finding

The Home Assistant Bluetooth stack and ESPHome Bluetooth proxy can be used as a
**GATT-level probe** for the SolarFlow device. They expose advertisements,
proxy source and connectability, GATT service discovery, characteristic reads
and writes, notification payloads, and connection or GATT errors.

They do **not** provide an over-the-air packet sniffer. The proxy forwards
BLE operations through the ESPHome API; it does not expose raw radio traffic,
link-layer packets, encryption exchanges, or every RF retransmission.

For SolarFlow, this is enough to inspect the application protocol on `A002/C304`
and `A002/C305`, which is the traffic the ESPHome YAML consumes.

## Supported host-side route

The supported standalone-Python route is:

```text
Python process
  -> bleak-esphome / habluetooth
  -> ESPHome API over TCP
  -> ESPHome Bluetooth proxy
  -> SolarFlow GATT connection
```

`bleak-esphome` documents that a host application can use the proxy without
Home Assistant. Its architecture is:

1. ESPHome scans for advertisements.
2. The proxy forwards advertisements over the ESPHome native API.
3. `aioesphomeapi` decodes the API messages.
4. `bleak-esphome` exposes them through `habluetooth` and Bleak.
5. A normal `bleak.BleakClient` performs GATT operations through the proxy.

Source: <https://github.com/Bluetooth-Devices/bleak-esphome/blob/main/docs/architecture.md>

The official example starts an `APIConnectionManager`, starts
`habluetooth.BluetoothManager`, waits for proxy advertisements, resolves a
`BLEDevice` with `bleak.BleakScanner.find_device_by_address`, and then uses a
normal `bleak.BleakClient`.

Source: <https://github.com/Bluetooth-Devices/bleak-esphome/blob/main/examples/gatt_connect.py>

The same pattern is documented in the package usage guide. The guide states
that `bleak-esphome` disappears from the caller-facing connection code after
the proxy scanner is registered: the caller uses Bleak normally, and
`habluetooth` routes the client to a proxy that can reach the device.

Source: <https://github.com/Bluetooth-Devices/bleak-esphome/blob/main/docs/usage.md>

## Home Assistant-native route

Inside a Home Assistant integration, do not create a second scanner and do not
connect to the proxy's API directly. Use Home Assistant's Bluetooth manager.

For a known address, the integration should call:

```python
from homeassistant.components import bluetooth

ble_device = bluetooth.async_ble_device_from_address(
    hass,
    address,
    connectable=True,
)
```

The API returns a `BLEDevice` from the nearest configured adapter or remote
scanner that can reach the device. The developer documentation specifically
recommends this API to avoid starting another scanner.

Source: <https://developers.home-assistant.io/docs/core/bluetooth/api>

The integration then passes that device into the protocol library. The library
uses the injected connection/client boundary and knows nothing about whether
the route is local Bluetooth or an ESPHome proxy.

Home Assistant core examples:

- `oralb` uses `async_ble_device_from_address(..., connectable=True)` before
  active polling: <https://github.com/home-assistant/core/blob/dev/homeassistant/components/oralb/__init__.py>
- `fjaraskupan` obtains a connectable device and passes it into a context-managed
  library connection: <https://github.com/home-assistant/core/blob/dev/homeassistant/components/fjaraskupan/coordinator.py>
- `husqvarna_automower_ble` obtains a connectable device and passes it to the
  library's persistent client: <https://github.com/home-assistant/core/blob/dev/homeassistant/components/husqvarna_automower_ble/coordinator.py>

## What can be inspected

### Advertisements

Home Assistant's Bluetooth callback API exposes `BluetoothServiceInfoBleak`
records containing the device, advertisement data, source, RSSI, connectability,
and service or manufacturer data. Integrations can subscribe with
`bluetooth.async_register_callback`.

Source: <https://developers.home-assistant.io/docs/core/bluetooth/api>

For SolarFlow, this can verify the manufacturer ID `0x4F48`, the controller
identifier in manufacturer data, the observed address and address type, the
proxy source, RSSI, and connectable status.

### GATT services and characteristics

Once connected through the proxy, a standard Bleak client can enumerate
services, locate `A002/C304` and `A002/C305`, write handshake and read messages
to `C304`, subscribe to notifications on `C305`, and log every application
payload delivered to the notification callback.

The `bleak-esphome` backend implements the Bleak client operations through the
ESPHome API. Its source includes explicit handling for GATT reads, writes,
notifications, CCCD notification setup, service caching, connection release,
and GATT/API error conversion.

Source: <https://github.com/Bluetooth-Devices/bleak-esphome/blob/main/src/bleak_esphome/backend/client.py>

### Proxy health and constraints

The proxy reports whether it supports active connections and how many BLE
connection slots remain. A device seen by a scan-only proxy can be visible but
cannot be connected to. Connection slots are finite; a client must disconnect
cleanly.

Source: <https://github.com/Bluetooth-Devices/bleak-esphome/blob/main/docs/usage.md>

The proxy feature flags include `ACTIVE_CONNECTIONS`, `RAW_ADVERTISEMENTS`,
`FEATURE_STATE_AND_MODE`, `REMOTE_CACHING`, `PAIRING`, `CACHE_CLEARING`, and
`CONNECTION_PARAMS_SETTING`.

Source: <https://github.com/Bluetooth-Devices/bleak-esphome/blob/main/docs/architecture.md>

For this project, the important flag is `ACTIVE_CONNECTIONS`. Without it, the
proxy can report the SolarFlow advertisement but cannot open the GATT link.

## What cannot be inspected through this route

This setup is not a radio packet sniffer. It cannot reliably expose raw BLE
link-layer packets, advertising-channel packets before proxy decoding,
connection events and retransmissions, controller-side encryption handshakes at
the RF layer, packets sent by another central such as the Zendure app, or
packets that never reach the proxy.

The ESPHome API carries decoded advertisements and GATT operation results. The
host can log the application bytes read, written, or delivered through
notifications, but it cannot reconstruct a Wireshark-style air capture from
those APIs.

For RF-level work, use a separate BLE sniffer near the device, such as a
compatible nRF52840/Ubertooth setup with Wireshark. That is a different test
path and is unnecessary for porting the SolarFlow JSON-over-GATT protocol.

## Recommended probe design for this repository

Keep the production library independent of proxy credentials. Add a separate
developer-only probe command or script that accepts local configuration through
environment variables or a local ignored file:

```text
SOLARFLOW_PROXY
SOLARFLOW_NOISE_PSK
SOLARFLOW_DEVICE_ADDRESS
```

The probe should:

1. Start `APIConnectionManager` for the existing ESPHome proxy.
2. Start the host-side `habluetooth` manager.
3. Wait for the SolarFlow advertisement.
4. Resolve the target `BLEDevice`.
5. Connect with a normal `bleak.BleakClient`.
6. Dump the discovered service and characteristic table.
7. Subscribe to `A002/C305`.
8. Log notification payloads as hex and decoded UTF-8/JSON.
9. Log every `A002/C304` write made by the probe.
10. Disconnect in a `finally` block.

The probe should have a read-only default. It may send only the protocol
handshake and `getAll` request. Setting writes should require a separate
explicit flag and should not be part of the first traffic-capture command.

## Recommended workflow for SolarFlow

### Probe phase

Use the standalone `bleak-esphome` route to inspect the real device through the
existing proxy. This avoids adding a temporary HA integration before the
protocol is understood.

Capture the exact GATT UUIDs and properties, notification payloads after
subscription, the BLESPP handshake response, `getInfo-rsp`, the initial
`getAll` response, the keepalive response, and the write acknowledgement shape
later with explicit opt-in.

### Library phase

Keep the permanent SolarFlow library proxy-neutral. It should receive an
injected Bleak-compatible client or transport. Its tests use a fake transport
and saved application payloads.

### Home Assistant phase

The future integration should use Home Assistant's
`async_ble_device_from_address(hass, address, connectable=True)` and pass that
device into the library. It should not start `bleak-esphome` itself.

## Security and operational limits

The standalone probe needs the ESPHome API host and, when configured, the
proxy's Noise PSK. Keep those outside Git and outside captured logs. The probe
should redact device identifiers when saving fixtures.

Disconnect after every probe run. ESPHome proxies have finite active BLE slots;
leaked sessions can prevent later tests from connecting.

Do not use the Zendure app during a capture unless the device is known to allow
multiple centrals. A second central can change the observed connection state or
prevent the probe from connecting.

## Bottom line

Use the HA-connected proxy as a **GATT/application-protocol probe**, not as an
RF sniffer. The most direct developer tool is `bleak-esphome` plus a normal
Bleak client. The final Home Assistant integration should use the HA Bluetooth
manager and `async_ble_device_from_address`, while the SolarFlow library stays
independent of Home Assistant and proxy credentials.
