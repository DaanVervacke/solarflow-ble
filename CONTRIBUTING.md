# Contributing to solarflow-ble

Thanks for your interest in contributing. This project is an async Python
library for Zendure SolarFlow devices over Bluetooth Low Energy, targeting
Python >= 3.14.

## Setup

Use [uv](https://docs.astral.sh/uv/) (>= 0.12.21, < 0.13) to install the
environment:

```bash
uv sync
```

## Running the checks

Before opening a pull request, run the full gate:

```bash
uv run python -m scripts.check
```

This is the canonical check. It stops at the first failure and runs exactly:

```text
version drift check
ruff format --check .
ruff check .
mypy src tests scripts
coverage run --branch -m pytest
coverage report
uv build
uv audit
```

Coverage measures branches in `src/solarflow_ble` and requires 98%. Your
pull request must pass this gate completely.

The API documentation is built separately and is not part of the gate:

```bash
uv run --group docs sphinx-build -W -b html docs docs/_build
```

## Protocol and behavior changes

A protocol or behavior change is complete only when all of the following are
present:

1. A captured payload under `tests/fixtures/` backing the change. Do not
   guess wire shapes.
2. The Zendure app contract fixture updated when wire shapes change.
3. Tests covering the new behavior, including the failure paths.
4. A conventional commit subject, which git-cliff renders into `CHANGELOG.md`.

## Changelog

`CHANGELOG.md` is generated with git-cliff from conventional commit subjects.
Never edit it by hand. Features, bug fixes, documentation, and maintenance
chores reach the changelog through their `feat:`, `fix:`, `docs:`, and
`chore:` subjects. Regenerate the unreleased section with `git-cliff
--unreleased --prepend CHANGELOG.md` and commit the result. At release, cut
the dated section with `git-cliff --tag vX.Y.Z --prepend CHANGELOG.md`.

## Commit style

One conventional-commit subject line, no body. Write the description as an
imperative sentence, for example: `chore: cap bleak below 4`. Do not mention
the plan or issue number in the subject.

## Deprecation policy

- While the project is on 0.x: breaking changes are allowed in minor releases,
  provided their commit subject marks them breaking (`feat!:` or `fix!:`).
- From 1.0 onwards: deprecated APIs emit a `DeprecationWarning` for at least
  one minor release before being removed in a major release.

## Pull requests

Every pull request must carry one of the seven repository labels
(`breaking-change`, `new-feature`, `enhancement`, `bugfix`, `maintenance`,
`documentation`, `dependencies`). CI fails otherwise. Dependabot pull
requests are labeled `dependencies` automatically.
