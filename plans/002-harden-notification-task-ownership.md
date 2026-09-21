# Plan 002: Harden notification task ownership

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If a
> STOP condition occurs, stop and report instead of improvising.
>
> **Drift check (run first)**: `git diff --stat f4af627..HEAD -- src/solarflow_ble/transport.py src/solarflow_ble/client.py tests/test_transport.py tests/test_solarflow.py`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: correctness
- **Planned at**: commit `f4af627`, 2026-09-21

## Why this matters

`BleakTransport.start_notify` schedules an async notification callback with
`asyncio.create_task` and drops the task reference. A callback exception is
therefore reported outside the transport call, and a callback can continue
running after `disconnect()` begins. The library's connection cleanup is
otherwise explicit, so notification tasks should have the same ownership and
cancellation semantics.

## Current state

- `src/solarflow_ble/transport.py:47-57` defines a synchronous Bleak callback,
  calls the injected callback, and schedules coroutine results with
  `asyncio.create_task(result)` without retaining or awaiting the task.
- `src/solarflow_ble/client.py:91-93` registers `_notification`; the callback
  mutates state and may invoke the user `update_callback` at
  `client.py:150-155`.
- `src/solarflow_ble/client.py:122-131` cancels the keepalive task and then
  stops notifications and disconnects the transport, but has no knowledge of
  transport-owned notification tasks.
- `tests/test_transport.py:8-24` covers connect, write, and disconnect but not
  notification delivery or callback failure.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `uv run pytest tests/test_transport.py tests/test_solarflow.py` | all tests pass |
| Lint | `uv run ruff check src/solarflow_ble/transport.py tests/test_transport.py` | exit 0 |
| Types | `uv run mypy src tests scripts` | no issues |
| Full gate | `uv run python -m scripts.check` | all stages pass |

## Scope

**In scope**:

- `src/solarflow_ble/transport.py`
- `tests/test_transport.py`
- `src/solarflow_ble/client.py` or `tests/test_solarflow.py` only if needed to
  expose a transport-lifecycle regression through the existing seam

**Out of scope**:

- Do not change the `BleTransport` protocol shape unless the current API
  cannot support owned task cleanup.
- Do not change SolarFlow message parsing or callback ordering.
- Do not swallow callback exceptions silently; define and test the chosen
  logging/propagation behavior.

## Steps

### Step 1: Define notification task ownership

Add a private task collection to `BleakTransport`. When a notification callback
returns an awaitable, register its task in that collection and remove it when it
finishes. Preserve the existing synchronous callback path. Decide explicitly
whether callback exceptions are logged by the transport or surfaced through a
retrievable task result; do not leave unobserved task exceptions.

**Verify**: `uv run pytest tests/test_transport.py -q` -> existing tests pass.

### Step 2: Cancel and await pending notification tasks during disconnect

In `BleakTransport.disconnect`, stop accepting new work, cancel pending
notification tasks, await them with `return_exceptions=True`, and only then
clear the client reference. Make repeated disconnect calls safe, matching the
existing idempotent cleanup style.

**Verify**: add a test with a blocked async callback, call `disconnect()`, and
assert the callback task is cancelled or completed before disconnect returns.
Run `uv run pytest tests/test_transport.py -q` -> all tests pass.

### Step 3: Cover callback failure and ordering

Add focused tests following the existing `AsyncMock`/`MagicMock` style in
`tests/test_transport.py` for: async callback delivery, callback exception
observation, and cleanup before transport state is cleared. Avoid real BLE
hardware and use a controlled callback event.

**Verify**: `uv run pytest tests/test_transport.py tests/test_solarflow.py -q` -> all pass; `uv run mypy src tests scripts` -> no issues.

## Done criteria

- [ ] No notification task is created without ownership and cleanup.
- [ ] Disconnect returns only after transport-owned notification tasks are
  observed and cleaned up.
- [ ] Callback failures have deterministic, tested handling.
- [ ] `uv run python -m scripts.check` exits 0.
- [ ] No files outside the scope are modified.

## STOP conditions

- Stop if Bleak invokes callbacks from a thread or event loop different from
  the transport loop; verify the installed Bleak contract before changing task
  creation.
- Stop if callback ordering required by existing SolarFlow tests changes.
- Stop after two failed attempts to make callback failure handling deterministic;
  report the observed event-loop behavior.

## Maintenance notes

Future notification callbacks must remain non-blocking. Review any new callback
or queueing logic for ownership, cancellation, and exception observation before
adding more background tasks.
