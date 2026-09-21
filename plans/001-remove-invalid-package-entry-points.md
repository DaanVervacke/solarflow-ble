# Plan 001: Remove invalid package entry points

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the STOP conditions occurs, stop and report.
>
> **Drift check (run first)**: `git diff --stat f4af627..HEAD -- pyproject.toml README.md scripts src tests`
> If any listed file changed since this plan was written, compare the current
> metadata against the excerpts below before proceeding.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: dx
- **Planned at**: commit `f4af627`, 2026-09-21

## Why this matters

Both declared console scripts point at objects that do not exist. Installing
the package exposes commands that fail before doing useful work, while the
documented developer probe already has a working direct path through
`scripts/probe_solarflow.py`. Removing the invalid declarations makes the
published package honest instead of advertising broken commands.

## Current state

- `pyproject.toml:16-18` declares:

  ```toml
  [project.scripts]
  solarflow-ble = "solarflow_ble:main"
  solarflow-ble-probe = "solarflow_ble.probe:main"
  ```

- `src/solarflow_ble/__init__.py:8-18` exports library symbols and
  `__version__`, but has no `main` function.
- `src/solarflow_ble/probe.py` is absent. The supported probe implementation
  is `scripts/probe_solarflow.py`, and `README.md` uses that path directly.
- Repository intent explicitly treats the probe as a developer-only script,
  not a package CLI.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Install metadata | `uv sync --locked` | exit 0 |
| Inspect package scripts | `uv run python -c 'import importlib.metadata as m; print(m.metadata("solarflow-ble").get_all("Provides-Extra"))'` | exit 0; no invalid script is relied on |
| Full gate | `uv run python -m scripts.check` | all stages pass |
| Build artifact | `uv build` | wheel and sdist build successfully |

## Scope

**In scope**:

- `pyproject.toml`
- `README.md` only if it contains a stale reference to either removed command
- Tests for package metadata, if the executor adds a focused test without
  creating a new packaging framework

**Out of scope**:

- Do not create `src/solarflow_ble/main.py`.
- Do not move `scripts/probe_solarflow.py` back into the package.
- Do not add a replacement console command. The supported probe invocation is
  `uv run scripts/probe_solarflow.py`.
- Do not change library runtime behavior.

## Steps

### Step 1: Remove the invalid `[project.scripts]` declarations

Delete the two console-script entries from `pyproject.toml`. Keep the package
metadata and dependencies unchanged. Search the repository for
`solarflow-ble-probe` and `solarflow_ble:main`; update only documentation that
would otherwise instruct users to run those broken commands.

**Verify**: `uv run python -c 'import solarflow_ble; print(solarflow_ble.__version__)'` -> prints `0.1.0` and exits 0.

### Step 2: Verify the built distribution does not expose broken scripts

Build the package and inspect the wheel metadata. Do not infer success from a
source import alone: the check must use the built artifact or installed
metadata.

**Verify**: `uv build` -> both artifacts build; inspect the generated wheel
metadata and confirm no `solarflow-ble` or `solarflow-ble-probe` console script
entry points remain.

## Test plan

- Confirm the library import and version still work.
- Confirm the documented probe path remains present and executable with
  `uv run scripts/probe_solarflow.py --help`.
- Run the existing full gate; no library tests need behavioral changes.

## Done criteria

- [ ] `pyproject.toml` contains no invalid console-script entries.
- [ ] `uv build` exits 0 and the wheel metadata contains no broken scripts.
- [ ] `uv run scripts/probe_solarflow.py --help` exits 0.
- [ ] `uv run python -m scripts.check` exits 0.
- [ ] No files outside the scope are modified.
- [ ] `plans/README.md` status row is updated.

## STOP conditions

- Stop if a release policy requires a package CLI that is not documented in the
  repository; report the required command and its intended behavior.
- Stop if removing the declarations changes a tested public API contract.
- Stop if the wheel still contains a console script after the metadata change;
  inspect build metadata before making any additional packaging change.

## Maintenance notes

If a package CLI is desired later, add a real module and tests first, then add
one entry point with a documented command. Do not restore either declaration
until its import target exists and a built-wheel smoke test covers it.
