# Plan 006: Add Zendure app source contract fixture

> **Executor instructions**: This plan creates a source-derived contract, not a
> runtime compatibility transcript. Do not invent device responses, timing,
> packet boundaries, fragmentation behavior, or keepalive behavior.
>
> **Drift check (run first)**: `git diff --stat f4af627..HEAD -- src tests plans`

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: -
- **Category**: tests/protocol
- **Planned at**: commit `f4af627`, 2026-09-21

## Why this matters

The installed Zendure app is the strongest available protocol reference, but
the decompiled APK documents app behavior rather than a vendor BLE
specification. A small source-derived fixture should preserve the app's
observable message shapes and lifecycle without presenting inferred data as a
real capture.

This fixture must remain separate from a future Android HCI transcript. A live
capture is still required before calling any artifact a runtime compatibility
fixture.

## Evidence boundary

The source evidence is Zendure app `com.zendure.iot`, version `7.0.0`,
versionCode `7001`, decompiled with jadx `1.5.6`.

The APK directly configures `A002` as its service, `C304` as its write
characteristic, and `C305` as its notification characteristic. Its BLE
dispatcher handles inbound `BLESPP` and then sends `BLESPP_OK`. The app builds
`getInfo` with `deviceId`, timestamp, and integer `messageId` fields through
`BaseBleParamBean`; it builds `read` with `deviceId` and
`properties: ["getAll"]`. The same dispatcher recognizes `getInfo-rsp`,
`read_reply`, `error`, and property-report messages.

Do not promote these source facts into claims about every Zendure model or
firmware version. Do not assert exact field order, runtime values, response
timing, ATT write mode, notification fragmentation, or `Keepalive` usage.

## Scope

**In scope**:

- `tests/fixtures/zendure_app_7_0_0_contract.jsonl`
- `tests/fixtures/zendure_app_7_0_0_contract.meta.json`
- `tests/test_zendure_app_contract.py`
- `plans/README.md` status row for Plan 006

**Out of scope**:

- Do not change `src/solarflow_ble/`.
- Do not alter current handshake, `deviceId`, acknowledgement, framing, or
  keepalive behavior.
- Do not add an Android HCI capture in this plan.
- Do not commit the APK, full decompiled source, decompiler output, real serials,
  credentials, or unredacted traffic.
- Do not create synthetic inbound payloads that look like captured device data.

## Fixture format

Each JSONL record must be a typed contract record with these required fields:

```json
{
  "step": 1,
  "kind": "outbound",
  "method": "getInfo",
  "source": "com/zendure/app/.../File.java:1-2",
  "evidence": "direct_literal_and_control_flow",
  "confidence": "high",
  "shape": {
    "method": "getInfo",
    "deviceId": "<device_id>",
    "timestamp": "<runtime_millis>",
    "messageId": "<runtime_int>"
  },
  "local_status": "diverges",
  "local_reference": "src/solarflow_ble/client.py:96-104",
  "note": "Local getInfo omits deviceId and serializes messageId as a string."
}
```

Use these record categories:

1. Lifecycle setup: connect, notification enablement for `A002/C305`.
2. Baseline outbound messages: `BLESPP_OK`, `getInfo`, and `read` with
   `properties: ["getAll"]`.
3. Baseline inbound method contracts: `BLESPP`, `getInfo-rsp`, `read_reply`,
   property report, and `error`.
4. Recognized dispatch-only methods: `BLEReady`, `deviceInfo`, and `eth.set`,
   clearly marked as recognized branches rather than guaranteed baseline events.
5. Lifecycle teardown: disconnect.

Use explicit `step` values for baseline records. Dispatch-only records may use a
separate `phase` value because the APK does not establish their position in the
normal read sequence.

The `shape` object may contain only source-proven required keys and typed
placeholders. Never put real device identifiers or fabricated response bodies in
it.

The allowed values are `lifecycle`, `inbound`, or `outbound` for `kind`;
`direct_literal`, `direct_control_flow`, `direct_literal_and_control_flow`, or
`inferred_serializer_shape` for `evidence`; `high` or `medium` for `confidence`;
and `aligned`, `diverges`, `unknown`, or `not_applicable` for `local_status`.

## Metadata format

The metadata JSON must include:

- package, version, and versionCode
- SHA-256 of the pulled base APK
- decompiler and decompiler version
- `evidence_mode: "source_only"`
- `runtime_capture: false`
- the APK-relative source references used by records
- explicit unknowns, including timing, MTU effects, write mode, fragmentation,
  keepalive behavior, and model/firmware scope
- redaction statement confirming that no real identities or credentials are
  included
- a short local comparison summary with references into `src/`

The full APK and decompiled source remain external provenance artifacts. The
metadata must be sufficient to identify the artifact without distributing either.

## Steps

### Step 1: Verify provenance and current state

Confirm the package metadata and compute the base APK SHA-256 from the pulled
artifact. Reconfirm the source locations in the decompiled output before copying
any references into the fixture. Stop if the APK version or source locations do
not match the evidence boundary above.

### Step 2: Add the contract records and metadata

Create the JSONL and metadata files under `tests/fixtures/`. Use placeholders for
runtime values and mark every local divergence without changing implementation
code. Keep baseline lifecycle order explicit and optional dispatch branches
separate.

### Step 3: Add structure-only validation

Create `tests/test_zendure_app_contract.py` to validate:

- every line is valid JSON with the required record fields;
- allowed enum values are respected;
- baseline steps are unique and ordered;
- outbound shapes contain only the documented required keys and placeholders;
- metadata identifies the APK and declares `runtime_capture: false`;
- no record contains real serial, credential, or unredacted identity values.

The test must not call Bluetooth and must not assert current client behavior.

### Step 4: Verify the scoped change

Run:

```bash
uv run pytest tests/test_zendure_app_contract.py
uv run python -m scripts.check
```

The full gate is required because the new JSONL and test are part of the normal
package validation surface.

## Done criteria

- [ ] Source-only JSONL contract exists at the planned path.
- [ ] Metadata records APK identity, hash, toolchain, source references, and
  explicit unknowns.
- [ ] Baseline lifecycle order is explicit and source-backed.
- [ ] Local aligned/divergent statuses are recorded without changing behavior.
- [ ] Structure-only tests pass without Bluetooth hardware.
- [ ] `uv run python -m scripts.check` exits 0.
- [ ] No APK, decompiled source, real identity, credential, or runtime capture is
  committed.
- [ ] `plans/README.md` status row is updated.

## STOP conditions

- Stop if a required record would need invented inbound data or undocumented
  timing.
- Stop if the decompiled source cannot establish a field as required or typed.
- Stop if provenance cannot be tied to package `com.zendure.iot` version `7.0.0`.
- Stop if the fixture starts forcing a production behavior decision about
  `deviceId`, handshake timing, acknowledgements, framing, or keepalive.

## Follow-up, not part of this plan

After this contract fixture is reviewed, collect one Android HCI read-only
session for the exact target device. That later artifact may become a separate
runtime compatibility fixture with redacted packet bytes, ATT direction, MTU,
notification boundaries, and observed response ordering.
