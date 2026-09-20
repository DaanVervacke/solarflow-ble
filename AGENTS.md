# Agent instructions

## Project shape

- This is a single `uv`-managed Python package, not a monorepo. Runtime code is under `src/solarflow_ble/`; tests and JSONL protocol fixtures are under `tests/`.
- `solarflow_ble` is a proxy-neutral SolarFlow JSON-over-BLE library. `SolarFlowClient` receives an injected `BleTransport`; `BleakTransport` adapts Bleak, while `probe.py` is the developer-only ESPHome proxy capture CLI.
- Keep this repository backend-only. Do not add Home Assistant integrations, entities, config flows, or `custom_components/` here.
- Protocol writes target characteristic `C304`; notifications use `C305`. Keep proxy credentials and device identifiers out of source, fixtures, and captured logs.

## Commands

- Install or refresh the locked environment with `uv sync`.
- Run the full test suite with `uv run pytest`.
- Run one test file with `uv run pytest tests/test_solarflow.py`; select one test with `uv run pytest tests/test_solarflow.py -k connect_handshake`.
- Run static checks with `uv run ruff check .` and `uv run mypy src`.
- Use the package entry points from the environment as `uv run solarflow-ble` or `uv run solarflow-ble-probe`. The probe uses `SOLARFLOW_PROXY` and `SOLARFLOW_NOISE_PSK`; the old `SOLARFLOW_PROXY_HOST` and `SOLARFLOW_PROXY_NOISE_PSK` names remain fallback aliases.

## Development constraints

- Preserve the async transport boundary so library tests can use fake transports without Bluetooth hardware. Update protocol behavior with saved payloads in `tests/fixtures/` and focused fake-transport tests.
- The probe is read-only by default. Its handshake and `getAll` request are the intended capture traffic; do not add device-setting writes to the default capture path.
- CaptureWriter redacts identity and credential fields; preserve that behavior when changing capture formats or logs.
- Disconnect BLE and ESPHome sessions in cleanup paths. ESPHome proxies have finite active BLE connection slots.
