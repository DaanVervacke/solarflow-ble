- [ ] `uv run python -m scripts.check` passes completely.
- [ ] The PR carries one of the seven labels: `breaking-change`, `new-feature`, `enhancement`, `bugfix`, `maintenance`, `documentation`, `dependencies`.
- [ ] There is a `[Unreleased]` entry in `CHANGELOG.md`.

For protocol or behavior changes, all of the following are present:

- [ ] A captured payload under `tests/fixtures/` backing the change.
- [ ] The Zendure app contract fixture updated when wire shapes change.
- [ ] An entry under `[Unreleased]` in `CHANGELOG.md`.
