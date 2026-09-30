# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Include the LICENSE file in the source distribution. The wheel already declared MIT metadata, but the sdist shipped no license text.

### Changed

- Remove an unreachable report-drain loop from the connect path. The session reset already installs a fresh report queue, so the loop could never see a message.

## [0.2.2] - 2026-09-30

### Documentation

- Align the README layout with aioengiebelgium: badges, an unofficial-and-reverse-engineered disclaimer, and a pip install command. The pyproject now carries the documentation URL.
- Document each exception class individually in the API reference and build the docs on the latest Ubuntu LTS image, matching the aioengiebelgium documentation setup.

## [0.2.1] - 2026-09-30

### Documentation

- Hosted API documentation on Read the Docs, built from `main` and
  release tags with Sphinx warnings treated as build errors.
- The API reference covers the limit registry constants and the
  notification, update, and connection-lost callback aliases, and
  links every documented object to its source.
- The `SolarFlowState` reconstruction methods document their arguments
  and return values.

## [0.2.0] - 2026-09-30

### Breaking changes

- `SolarFlowState.raw` keeps only the known report keys. Unknown
  properties no longer accumulate there; read the report message itself
  if you need them.
- The diagnostics-only BLE dependencies (`bleak-esphome`, `habluetooth`)
  moved from the runtime dependencies to the dev group. Library installs
  no longer pull them in; the probe and test scripts need `uv sync` with
  the dev group.
- bleak is capped below 4.
- The session becomes `READY` (and controls are allowed) once the
  handshake completes and the first report arrives. The previous
  requirement that the device report `smartMode: 1` is gone: the
  hardware accepts and acknowledges writes with smart mode off.

### Added

- Model-aware control limit registry: `SolarFlowLimits`,
  `MODEL_LIMITS`, `MODEL_SOLARFLOW_2400AC`, and `DEFAULT_LIMITS`, plus
  `model=` and `limits=` constructor arguments on `SolarFlowClient`.
  Validation bounds resolve from explicit `limits`, then the registry
  entry for the model (or the `productKey` the device reports), then the
  verified 2400AC default with a one-time warning.
- The SolarFlow 2400 AC's reported product key (`BC8B7F`) resolves the
  verified limits registry, so the device no longer triggers the
  unknown-model warning.
- Async context manager support on `SolarFlowClient`: `async with
  SolarFlowClient(...)` connects on entry and always disconnects on exit.
- The full public API is re-exported from the package root, including
  `AcMode` (so `set_ac_mode` accepts `AcMode` values imported as
  `from solarflow_ble import AcMode`), the limit registry, the models,
  `parse_advertisement`, and every exception.
- `--version` on the diagnostic scripts.

### Fixed

- Write acknowledgements are correlated with their request, so a late
  `writeRsp` from an earlier timed-out write no longer satisfies the
  wait.
- Device errors no longer abort the handshake: they are recorded in
  `client.last_error` and quoted in the timeout message when a
  handshake step still times out.
- Command errors include the device rejection code (`writeRsp`).
- Session cleanup is idempotent across `disconnect()` and session-failure
  handling; the first path to reach the transport performs the teardown.
- Reports are queued only while the handshake waits consume them, so an
  always-reporting device can no longer grow the queue without bound.

### Changed

- Non-integer and boolean wire values are rejected in state merges.
- JSON notifications above 64 KiB are rejected as protocol errors and
  fail the session.
- Notifications are applied by one consumer worker in arrival order;
  state updates and update callbacks never overlap or reorder.
- `__version__` is derived from the installed package metadata and reads
  `0.0.0` when the package is not installed.

### Security

- Added a bandit and pip-audit security workflow.

### Documentation

- Google-style docstrings with Args and Raises across the public API,
  including the per-mille conversion in `set_min_soc` and `set_soc` and
  the per-mille and `raw` semantics of `SolarFlowState`.
- Sphinx API documentation under `docs/`, built with `uv run --group
  docs sphinx-build -W -b html docs docs/_build`.
- Community files: this changelog, `CONTRIBUTING.md`, `SECURITY.md`, a
  pull request template, and YAML issue templates.

### Maintenance

- The gate installs the built wheel into a clean environment and imports
  the package outside the repository, verifies built distributions with
  `twine check`, rejects unknown check arguments, and starts with a
  version drift check.
- Coverage is gated on the shipped package with a 98% branch floor. The
  suite replays a real-device capture through a connected client and
  cross-checks client wire shapes against the Zendure app contract
  fixture.
- Added an allow-failure Python 3.15 leg to the gate matrix, pre-commit
  hooks for ruff lint and format, project URLs, classifiers, and
  keywords, and ignored local Android app captures.
- The diagnostic scripts share one error contract, and client waits and
  script proxy code are deduplicated.

## [0.1.3] - 2026-09-21

### Fixed

- Concurrent `connect()` calls share one session instead of racing.
  `connect()` after ready is idempotent, and a reconnect discards stale
  messages and session state.

### Maintenance

- Diagnostic output paths are redacted.

## [0.1.2] - 2026-09-21

### Documentation

- Slimmed the README to the library, probe, and development basics.

## [0.1.1] - 2026-09-21

### Maintenance

- Fixed the release workflow.

## [0.1.0] - 2026-09-21

### Added

- Initial release.

[Unreleased]: https://github.com/DaanVervacke/solarflow-ble/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/DaanVervacke/solarflow-ble/compare/v0.2.1...v0.2.2
[0.2.0]: https://github.com/DaanVervacke/solarflow-ble/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/DaanVervacke/solarflow-ble/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/DaanVervacke/solarflow-ble/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/DaanVervacke/solarflow-ble/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/DaanVervacke/solarflow-ble/releases/tag/v0.1.0
