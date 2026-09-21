# Plan 007: Align client with Zendure app handshake

> **Executor instructions**: Use the decompiled Zendure app 7.0.0 as the
> source reference for this change. Do not broaden compatibility based on
> community implementations or unverified runtime assumptions.
>
> **Drift check (run first)**: `git diff --stat f4af627..HEAD -- src tests plans`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MEDIUM
- **Depends on**: 006
- **Category**: protocol compatibility
- **Planned at**: commit `f4af627`, 2026-09-21

## Why this matters

The current client starts `getInfo` immediately after notification setup and
builds requests without the `deviceId` and integer `messageId` fields used by
the official Zendure app. The app waits for `BLESPP`, sends the fixed integer
`1009` acknowledgement, learns the session identity, then sends `getInfo` and
`read`/`getAll` after a 300 ms delay.

The client should follow that observed source behavior before probe validation.
This plan does not attempt to generalize across other Zendure models or
firmware versions.

## Evidence boundary

Use the source-only contract fixture and these decompiled APK references:

- `BleHelper.java:598-613`: writes use service `A002` and characteristic `C304`.
- `BleHelper.java:622-624`: `BLESPP_OK` is `{"messageId":1009,"method":"BLESPP_OK"}`.
- `BleHelper.java:811-819`: inbound `BLESPP` triggers the acknowledgement and
  300 ms handoff delay.
- `BleHelper.java:853-861`: `getInfo-rsp` completes its callback path.
- `BleHelper.java:881-950`: `read_reply` and `report` are dispatched as raw
  property events; `read_reply.success` is not interpreted.
- `BleHelper.java:978-981`: notifications use `A002/C305`.
- `SbpBleDeviceDetailActivity.java:114-116` and `MsgHelper.java:372-423`:
  `read` includes `deviceId`, integer `messageId`, timestamp, and
  `properties: ["getAll"]`.
- `SfPowerBaseFragment.java:361-373` and `BaseBleParamBean.java:6-18`:
  `getInfo` includes `deviceId`, integer `messageId`, and timestamp.

## Scope

**In scope**:

- `src/solarflow_ble/client.py`
- `src/solarflow_ble/const.py` only if the 300 ms default belongs there
- `src/solarflow_ble/models.py` only for established identity synchronization
- `tests/test_solarflow.py`
- `tests/fixtures/zendure_app_7_0_0_contract.jsonl` and metadata if the source
  contract needs to record the corrected local alignment
- `plans/README.md` status row for Plan 007

**Out of scope**:

- Do not implement fragmented-notification reassembly.
- Do not change ATT write mode; retain `response=False`.
- Do not change keepalive behavior; retain periodic `read`/`getAll`.
- Do not add legacy `write_reply` or top-level `success` acknowledgement support.
- Do not change model-specific control ranges.
- Do not add reconnect fallback when `BLESPP` is absent.
- Do not require `deviceId` on every later message; validate it only when present.
- Do not add a new public exception class or raw protocol-message API.

## Decisions

- Add keyword-only `device_id: str | None = None` to `SolarFlowClient`.
- Wait for inbound `BLESPP` using the existing `response_timeout`.
- Require `deviceId` in `BLESPP`; raise `SolarFlowProtocolError` if absent.
- If a caller-supplied ID differs from the handshake ID, raise
  `SolarFlowProtocolError` and disconnect.
- Expose the established identity as `SolarFlowClient.device_id` and keep
  `SolarFlowState.device_id` synchronized.
- Preserve the full inbound BLESPP message internally, but do not expose a new
  raw-message API or send BLESPP through `update_callback`.
- Send integer `1009` for `BLESPP_OK`.
- Use a monotonic per-client integer counter for normal messages, starting at 1,
  preserving it across reconnects, and skipping reserved ID `1009`.
- Include the established `deviceId` and generated integer `messageId` in
  `getInfo`, `read`, and control writes.
- Change the default BLESPP handoff delay to `0.3` seconds while preserving the
  constructor override.
- Queue `read_reply`, send it through `update_callback` with unchanged state,
  and continue waiting for the existing `smartMode` readiness signal.
- Do not inspect `read_reply.success`; mirror the APK's raw event handling.
- If a later message contains `deviceId`, accept a matching value and fail on a
  mismatch; accept messages that omit it.

## Steps

### Step 1: Add session identity and message ID generation

Add the optional keyword-only `device_id` constructor argument, a client-local
monotonic counter, and a helper that returns the next integer ID while skipping
`1009`. Keep the counter across disconnect/reconnect.

### Step 2: Rework connection setup

After notification setup, wait for `BLESPP`. Validate its method and
`deviceId`, reconcile it with the optional constructor value, send the fixed
integer `BLESPP_OK`, wait the configured 300 ms default, then send `getInfo`.
Use the learned identity and generated integer ID in the request.

### Step 3: Update reads, writes, and identity handling

Include `deviceId` and generated integer IDs in `read`/`getAll` and control
writes. Add identity validation when later decoded messages include `deviceId`.
Keep absent later IDs valid. Synchronize the established ID into
`SolarFlowState` without overwriting it with a conflicting value.

### Step 4: Handle read_reply without inventing semantics

Keep `read_reply` in the decoded message queue and allow it to reach
`update_callback` with unchanged state. Do not interpret a success field. Keep
waiting for the existing initial report/readiness condition.

### Step 5: Update tests and source contract metadata

Update fake transports to emit `BLESPP`, `getInfo-rsp`, `read_reply`, and the
existing report. Assert exact order, integer IDs, device ID fields, the 300 ms
default, strict pre-handshake timeout cleanup, constructor-ID mismatch, missing
BLESPP device ID, later-ID mismatch, and read_reply callback behavior.

Update the source contract's local comparison fields only where the production
client now aligns with the APK. Do not turn the source-only fixture into a
runtime transcript.

## Verification

Run the focused protocol tests first:

```bash
uv run pytest tests/test_solarflow.py
```

Then run the repository gate:

```bash
uv run python -m scripts.check
```

No Bluetooth hardware or probe session is required for this implementation
pass. The probe validation happens after the code and tests pass.

## Done criteria

- [ ] Client requires and validates BLESPP before getInfo.
- [ ] BLESPP device identity is established and exposed without a new raw API.
- [ ] Official app request shapes use deviceId and integer message IDs.
- [ ] Read_reply is preserved and surfaced without invented success semantics.
- [ ] Existing keepalive, ATT write mode, control ranges, and fragmentation
  behavior remain unchanged.
- [ ] Focused tests and the full repository gate pass.
- [ ] Plan 007 status is updated in `plans/README.md`.

## STOP conditions

- Stop if the existing transport cannot deliver a complete BLESPP JSON message
  to the client without adding framing work.
- Stop if a test requires inventing a response field not supported by the APK.
- Stop if a device identity conflict can only be resolved by silently choosing
  one identity.
- Stop if the change requires broadening acknowledgement or keepalive behavior;
  create a separate plan based on probe evidence instead.
