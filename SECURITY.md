# Security policy

## Reporting a vulnerability

Please use [GitHub's private vulnerability reporting](https://github.com/DaanVervacke/solarflow-ble/security/advisories/new)
to report security issues. Do not open a public issue for a vulnerability.

## Credentials

Never paste noise PSKs, proxy passwords, or unredacted capture files into
issues, pull requests, discussions, or reports. This includes values that look
expired. Redact addresses, identifiers, device IDs, product keys, pack serials,
and credentials before sharing any log or payload.

If you have already shared credentials publicly, rotate them: re-pair the
ESPHome Bluetooth proxy so it generates a fresh noise PSK, and change the
proxy password.

## Scope

This library talks to a reverse-engineered BLE protocol on untrusted
hardware. Treat any payload from a device as untrusted input, and report
anything that looks like it could affect users of the library.
