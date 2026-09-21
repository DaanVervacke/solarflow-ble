# Plan 008: Add standalone library test script

> **Executor instructions**: This script uses `SolarFlowClient` directly. Do
> not call or import `probe_solarflow.py`. Keep the default path read-only and
> require two explicit flags before issuing device-setting writes.
>
> **Drift check (run first)**: `git diff --stat f4af627..HEAD -- src tests scripts plans`

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MEDIUM
- **Depends on**: 007
- **Category**: developer tooling
- **Planned at**: commit `f4af627`, 2026-09-21

## Why this matters

The probe validates raw traffic, but it does not exercise the library as an
application would. A standalone hardware script should connect through the
library, print decoded state and live updates, and optionally exercise controls
without duplicating the probe's protocol flow.

## Scope

**In scope**:

- `scripts/test_solarflow_client.py`
- `tests/test_solarflow_client_script.py`
- `README.md` developer usage section
- `plans/README.md` status row for Plan 008

**Out of scope**:

- Do not import or call `scripts/probe_solarflow.py`.
- Do not change `src/solarflow_ble/` behavior.
- Do not add automatic retries.
- Do not persist unredacted device identities, serials, credentials, or raw
  messages.
- Do not make hardware access part of the normal CI gate.

## CLI contract

Run with:

```bash
uv run scripts/test_solarflow_client.py \
  --proxy <host> \
  --noise-psk <psk> \
  (--address <ble-address> | --identifier <solarflow-identifier>)
```

Required connection options are `--proxy`, `--noise-psk`, and exactly one of
`--address` or `--identifier`. The script uses `habluetooth` discovery,
`BleakTransport`, and `SolarFlowClient` directly.

Read-only options:

- `--duration`: live-update collection period after initial connection.
- `--output`: optional decoded-update JSONL path. Records are always redacted.
- `--verbose`: print full callback updates instead of only the concise summary.

Control options:

- `--controls`: enable the optional control suite.
- `--confirm-controls`: required in addition to `--controls` before any write.
- `--input-limit`, `--output-limit`, `--min-soc`, `--soc`, and `--ac-mode`:
  optional explicit values for individual setters.

`--controls` without `--confirm-controls` must fail before connecting or writing.

## Runtime behavior

### Read-only path

1. Start the ESPHome proxy connection and Bluetooth manager.
2. Discover the target by exact address or SolarFlow advertisement identifier.
3. Construct `BleakTransport` and `SolarFlowClient` directly.
4. Connect and verify the learned protocol identity, `protocol_ready`, and
   `ready` status.
5. Print a state snapshot including the connected device identity for local
   hardware diagnostics. Include selected state fields and every known battery
   pack.
6. Collect callback updates for `--duration`. Concise mode prints timestamp,
   method, status, selected state fields, and pack count. `--verbose` prints
   the full decoded update.
7. Disconnect in `finally` and stop proxy/discovery resources.

### Optional control path

The control suite runs only when both `--controls` and
`--confirm-controls` are present, and only after the client is `READY`.

- `inputLimit`, `outputLimit`, and `acMode` default to their current typed
  state values when no explicit CLI override is supplied.
- `minSoc` and `socSet` require explicit CLI values because the current state
  does not expose their original wire-level values safely.
- Run setters one at a time.
- Restore each setting only when its original value is known.
- Skip and report any setter whose restoration value is unavailable.
- Restore known values in a `finally` block after the control suite.
- Never substitute guessed defaults for missing restoration values.

### Failure and cleanup behavior

Connection and control failures print an error, close the optional redacted
JSONL output, disconnect the client when created, stop the proxy manager, and
return exit code `1`. The script performs one attempt and does not retry.

## Decoded JSONL format

Each callback record contains:

- capture timestamp;
- callback method;
- connection status;
- selected decoded state fields;
- battery-pack summaries;
- the raw decoded message after recursive redaction.

This is a decoded library-observation format, not the probe's raw packet format.

## Test boundary

Add hardware-free tests for pure helpers only:

- parser validation for required connection arguments and control confirmation;
- recursive redaction of device IDs, product keys, serials, and credentials;
- state and pack summary serialization;
- control-plan construction, including skipped restoration values;
- concise versus verbose output formatting where practical.

Do not mock an entire BLE session in this script's unit tests. The library's
existing fake-transport tests cover protocol behavior; this script's tests cover
CLI safety and output transformations.

## Verification

Run:

```bash
uv run pytest tests/test_solarflow_client_script.py
uv run ruff check scripts/test_solarflow_client.py tests/test_solarflow_client_script.py
uv run python -m scripts.check
```

Manual hardware validation happens after the implementation gate passes:

```bash
uv run scripts/test_solarflow_client.py \
  --proxy "192.168.1.157" \
  --noise-psk "<psk>" \
  --identifier "A1NAN9N424458" \
  --duration 30 \
  --output /tmp/solarflow-library-test.jsonl
```

The script prints identities to stdout for local diagnostics. The optional JSONL
file is always redacted. Do not include the real PSK or output capture in the
repository.

## Done criteria

- [ ] Standalone script uses `SolarFlowClient` and never imports the probe.
- [ ] Default execution is read-only.
- [ ] Control writes require both explicit flags and restore known values.
- [ ] Stdout identity output is explicit and JSONL is always redacted.
- [ ] Hardware-free helper tests pass.
- [ ] Full repository gate passes.
- [ ] Plan 008 status is updated in `plans/README.md`.

## STOP conditions

- Stop if the script needs to duplicate protocol logic from `SolarFlowClient`.
- Stop if a control cannot be restored safely from a known value.
- Stop if a requested CLI option would require persisting an unredacted capture.
- Stop if a hardware-free test would need to replace the library's protocol
  integration tests rather than cover script-specific behavior.
