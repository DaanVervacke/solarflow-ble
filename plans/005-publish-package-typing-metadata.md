# Plan 005: Publish package typing metadata

> **Executor instructions**: Follow this plan step by step. Stop and report if
> the build backend already publishes typing metadata through another mechanism.
>
> **Drift check (run first)**: `git diff --stat f4af627..HEAD -- pyproject.toml src/solarflow_ble tests`

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/001-remove-invalid-package-entry-points.md
- **Category**: dx
- **Planned at**: commit `f4af627`, 2026-09-21

## Why this matters

This is a typed library with strict mypy configuration, but it does not include
`src/solarflow_ble/py.typed`. Type checkers consuming the installed wheel may
treat the package as untyped even though the source contains annotations. A
marker and wheel-content check make the published package's typing contract
match its implementation.

## Current state

- `pyproject.toml:7-13` declares the package and Python 3.14 requirement but no
  typing metadata.
- `src/solarflow_ble/` contains Python modules but no `py.typed` marker.
- `pyproject.toml:20-22` uses `uv_build`; the plan must verify how that backend
  includes package data rather than assuming a configuration key.
- `pyproject.toml:85-87` enables strict mypy for this repository, showing that
  annotations are an intentional project convention.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Build | `uv build` | wheel and sdist build successfully |
| Inspect wheel | `unzip -l dist/*.whl` | lists `solarflow_ble/py.typed` |
| Full gate | `uv run python -m scripts.check` | all stages pass |

## Scope

**In scope**:

- `src/solarflow_ble/py.typed` (new empty marker file)
- `pyproject.toml` only if `uv_build` requires explicit package-data config
- A packaging smoke test only if the repository already has a suitable test
  location and the check cannot remain a shell verification

**Out of scope**:

- Do not change public annotations or add stubs.
- Do not loosen mypy settings.
- Do not publish a separate type-stub package.

## Steps

### Step 1: Add the PEP 561 marker

Create an empty `src/solarflow_ble/py.typed` file. Do not put secrets,
documentation, or executable code in it.

**Verify**: `test -f src/solarflow_ble/py.typed` -> exits 0.

### Step 2: Verify source and wheel inclusion

Build the package and inspect both the wheel and source distribution. If the
marker is absent, inspect `uv_build`'s documented package-data behavior and add
the smallest explicit configuration needed. Do not copy generated artifacts
into the repository.

**Verify**: `uv build && unzip -l dist/*.whl | grep 'solarflow_ble/py.typed'` -> one matching wheel entry.

### Step 3: Run consumer-facing type smoke check

Use a temporary outside-repository directory or an isolated environment to
install the built wheel and type-check a tiny import/use of
`SolarFlowClient`/`SolarFlowState`. Do not commit the temporary consumer.

**Verify**: the consumer type check exits 0 and sees package annotations rather
than an untyped-package warning.

## Done criteria

- [ ] `src/solarflow_ble/py.typed` exists.
- [ ] Built wheel contains the marker.
- [ ] A consumer type-check smoke test sees the package as typed.
- [ ] `uv run python -m scripts.check` exits 0.
- [ ] No files outside the scope are modified.
- [ ] `plans/README.md` status row is updated.

## STOP conditions

- Stop if `uv_build` excludes the marker and its documented configuration would
  require restructuring the package; report the exact build output.
- Stop if adding the marker exposes existing annotation errors in a consumer;
  capture the error and do not weaken the package types.

## Maintenance notes

Keep the marker in every future source distribution. Any move from `src/` layout
or change of build backend must include a wheel-content check for `py.typed`.
