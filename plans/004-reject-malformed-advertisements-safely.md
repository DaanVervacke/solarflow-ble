# Plan 004: Reject malformed advertisements safely

> **Executor instructions**: Follow this plan step by step. Stop and report if
> the parser's public error contract differs from the current code excerpts.
>
> **Drift check (run first)**: `git diff --stat f4af627..HEAD -- src/solarflow_ble/protocol.py scripts/probe_solarflow.py tests/test_solarflow.py tests/test_probe.py`

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: MED
- **Depends on**: none
- **Category**: correctness
- **Planned at**: commit `f4af627`, 2026-09-21

## Why this matters

Advertisement data is external BLE input. `parse_advertisement` decodes the
SolarFlow manufacturer payload as ASCII without catching decode errors. A
malformed or unexpected payload under manufacturer ID `0x4F48` can raise
`UnicodeDecodeError` while the probe scans, aborting discovery instead of
ignoring one invalid advertisement.

## Current state

- `src/solarflow_ble/protocol.py:11-32` gets manufacturer data at `0x4F48`,
  strips a trailing `0x16`, and calls `.decode("ascii")` without handling
  `UnicodeDecodeError`.
- `scripts/probe_solarflow.py:181-195` calls `parse_advertisement` for scanner
  records inside the discovery loop and does not isolate parser failures per
  advertisement.
- `tests/test_solarflow.py:19-22` covers only a valid ASCII advertisement.
- `SolarFlowProtocolError` exists at `src/solarflow_ble/exceptions.py:16-18`
  for invalid protocol payloads, but the parser's scan-facing behavior should
  be chosen explicitly rather than inferred.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `uv run pytest tests/test_solarflow.py tests/test_probe.py -q` | all pass |
| Lint/types | `uv run ruff check src/solarflow_ble/protocol.py tests/test_solarflow.py && uv run mypy src tests scripts` | no issues |
| Full gate | `uv run python -m scripts.check` | all stages pass |

## Scope

**In scope**:

- `src/solarflow_ble/protocol.py`
- `tests/test_solarflow.py`
- `scripts/probe_solarflow.py` only if the chosen parser contract needs a
  narrow caller-side guard

**Out of scope**:

- Do not change valid advertisement decoding or identifier normalization.
- Do not log raw malformed payload bytes if they could contain device data.
- Do not broaden this into generic protocol error handling.

## Steps

### Step 1: Choose the malformed-input contract

For scan input, prefer returning `None` for a payload that has the expected
manufacturer ID but cannot be decoded as the expected ASCII identifier. This
matches the existing `None` result for absent/empty identifiers and keeps one
bad advertisement from stopping discovery. If maintainers require strict
library diagnostics instead, raise `SolarFlowProtocolError` in the public
parser and catch only that error in the probe; stop and report before choosing
that higher-impact alternative.

**Verify**: add a focused test with non-ASCII bytes and confirm the selected
contract explicitly in the test name and assertion.

### Step 2: Preserve valid and empty-payload behavior

Keep the existing trailing `0x16` stripping, valid ASCII identifier result,
missing manufacturer ID result, and empty identifier result unchanged. Add
tests beside `test_parse_advertisement` in `tests/test_solarflow.py`.

**Verify**: `uv run pytest tests/test_solarflow.py -q` -> all tests pass.

### Step 3: Confirm scanner resilience

If the parser returns `None`, verify `_find_device` continues scanning after a
malformed advertisement and can still accept a later valid SolarFlow record.
Use a fake scanner or a direct parser-level test; do not require Bluetooth.

**Verify**: `uv run pytest tests/test_solarflow.py tests/test_probe.py -q` -> all pass.

## Done criteria

- [ ] Malformed manufacturer data cannot abort a scan loop.
- [ ] Valid advertisement behavior remains unchanged.
- [ ] Tests cover malformed, missing, empty, and valid payloads.
- [ ] `uv run python -m scripts.check` exits 0.
- [ ] No files outside the scope are modified.

## STOP conditions

- Stop if upstream callers rely on `UnicodeDecodeError` as a public signal;
  identify those callers before changing the contract.
- Stop if malformed payloads are expected to contain a different documented
  encoding; do not silently replace ASCII with permissive decoding.

## Maintenance notes

Keep parser input handling defensive because manufacturer data is scanner input,
not trusted application JSON. Any new advertisement field should have malformed
payload tests before it is used by the probe or a future integration.
