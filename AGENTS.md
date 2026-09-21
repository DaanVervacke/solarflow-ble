# Agent instructions

## Repository shape

- This is one `uv`-managed Python package targeting Python 3.14+, not a monorepo. Library code is in `src/solarflow_ble/`; tests and JSONL protocol fixtures are in `tests/`.
- `SolarFlowClient` depends on the injected async `BleTransport`; `BleakTransport` is the hardware adapter. Keep this seam so tests do not need Bluetooth hardware.
- `scripts/probe_solarflow.py` is the developer-only ESPHome proxy capture tool. Do not add Home Assistant integrations, entities, config flows, or `custom_components/` here.

## Commands

- Install the locked environment with `uv sync`; CI uses `uv sync --locked`.
- Run the same local CI gate as GitHub Actions with `uv run python -m scripts.check`. It stops at the first failure in this order: `ruff format --check`, `ruff check`, `mypy src tests scripts`, branch-covered `pytest`, coverage report, then `uv build`.
- Run focused tests with `uv run pytest tests/test_solarflow.py` or `uv run pytest tests/test_solarflow.py -k connect_handshake`.
- Run the probe as `uv run scripts/probe_solarflow.py --proxy <host> --noise-psk <psk> --output <path>`; use the script path because no project console entry point is defined.

## Protocol and safety constraints

- Protocol writes use characteristic `C304`; notifications use `C305`. Preserve the client connection sequence: subscribe, send `BLESPP_OK`, wait for `getInfo-rsp`, send `getAll`, wait for initial reports, then start keepalive.
- Device-setting controls are opt-in with `SolarFlowClient(..., allow_control=True)` and require a ready client. Do not bypass those checks.
- The probe is read-only by default for device settings, but its normal handshake sends `BLESPP_OK`, `getInfo`, and `getAll`; `--no-handshake` is the passive notification-capture mode.
- `CaptureWriter` redacts device identity, pack serials, and credentials. Preserve redaction when changing capture formats or logging.
- Keep proxy credentials, device identifiers, and unredacted captures out of source, fixtures, and committed logs.
- Always disconnect BLE and ESPHome sessions in cleanup paths because ESPHome proxies have finite active BLE connection slots.
