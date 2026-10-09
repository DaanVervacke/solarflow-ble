- [ ] `uv run python -m scripts.check` passes completely.
- [ ] The PR carries one of the seven labels: `breaking-change`, `new-feature`, `enhancement`, `bugfix`, `maintenance`, `documentation`, `dependencies`.
- [ ] Every commit has a conventional subject. git-cliff renders `CHANGELOG.md` from them, so do not edit it by hand.

For protocol or behavior changes, all of the following are present:

- [ ] A captured payload under `tests/fixtures/` backing the change.
- [ ] The Zendure app contract fixture updated when wire shapes change.
- [ ] Tests covering the new behavior, including the failure paths.
