# Plan 003: Make probe scan windows accurate

> **Executor instructions**: Follow this plan step by step. Stop and report on
> any STOP condition; do not improvise timing semantics.
>
> **Drift check (run first)**: `git diff --stat f4af627..HEAD -- scripts/probe_solarflow.py tests/test_probe.py README.md`

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: correctness
- **Planned at**: commit `f4af627`, 2026-09-21

## Why this matters

Both probe scan paths sleep for five seconds before inspecting scanners, but
both start their `scan_seconds` deadline before that sleep. A value of five
seconds or less can therefore time out without performing a meaningful scan,
and every normal run spends part of the advertised window in an unmeasured
warm-up. The command-line flag should describe the period during which the
probe actually searches.

## Current state

- `scripts/probe_solarflow.py:154-159` starts `_find_device`'s deadline and then
  sleeps five seconds.
- `scripts/probe_solarflow.py:202-209` repeats the same sequence in
  `_list_advertisements`.
- `scripts/probe_solarflow.py:343-344` exposes defaults of 30 seconds for scan
  and 15 seconds for capture.
- `tests/test_probe.py` has no timing tests for either scan path.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `uv run pytest tests/test_probe.py -q` | all pass |
| Lint/types | `uv run ruff check scripts/probe_solarflow.py tests/test_probe.py && uv run mypy src tests scripts` | no issues |
| Full gate | `uv run python -m scripts.check` | all stages pass |

## Scope

**In scope**:

- `scripts/probe_solarflow.py`
- `tests/test_probe.py`
- `README.md` only if timing text becomes inaccurate after the fix

**Out of scope**:

- Do not change the default durations without a maintainer decision.
- Do not remove the warm-up delay unless scanner readiness is proven another
  way; change deadline placement first.
- Do not require a real proxy or Bluetooth hardware in tests.

## Steps

### Step 1: Define the timing contract

Choose and document in code that `scan_seconds` measures active discovery time
after the proxy/scanner warm-up. Place the deadline after the intentional
five-second warm-up, or use a single helper that makes this ordering explicit
for both scan modes.

**Verify**: `uv run pytest tests/test_probe.py -q` -> existing tests pass.

### Step 2: Add deterministic timing tests

Use monkeypatching or a fake clock/sleep boundary, following the current
module-loading pattern in `tests/test_probe.py`. Cover a short scan window and
assert that the scanner is inspected after warm-up rather than timing out
immediately. Cover both `_find_device` and `_list_advertisements` enough to
prove they share the intended window semantics.

**Verify**: `uv run pytest tests/test_probe.py -q` -> all tests pass, including
the new timing regression tests.

### Step 3: Recheck CLI documentation

Confirm the README's `--scan-seconds` examples still describe the behavior and
that no claim says the flag includes proxy startup time.

**Verify**: `uv run scripts/probe_solarflow.py --help` -> exits 0; `git diff --check` -> no whitespace errors.

## Done criteria

- [ ] `scan_seconds` is measured consistently in both scan modes.
- [ ] Short windows do not expire before the first post-warm-up inspection.
- [ ] Timing behavior is covered without hardware.
- [ ] `uv run python -m scripts.check` exits 0.
- [ ] No files outside the scope are modified.

## STOP conditions

- Stop if `habluetooth` requires the five-second delay for a documented reason
  that conflicts with this contract; report the dependency behavior.
- Stop if fake-clock tests become flaky; use an injectable clock/sleep seam
  instead of increasing real sleeps.

## Maintenance notes

If proxy startup becomes asynchronous or scanner registration gains an explicit
readiness signal, replace the fixed warm-up with that signal and keep
`scan_seconds` scoped to active discovery.
