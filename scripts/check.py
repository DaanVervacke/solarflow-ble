"""Run the local CI gate in a fixed order."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence

VERSION_CHECK = """import tomllib
from pathlib import Path
import solarflow_ble
data = tomllib.loads(Path('pyproject.toml').read_text())
expected = data['project']['version']
actual = solarflow_ble.__version__
if actual != expected:
    raise SystemExit(f'version drift: {expected} != {actual}')
"""

COMMANDS = (
    ("version", ("uv", "run", "python", "-c", VERSION_CHECK)),
    ("format", ("uv", "run", "ruff", "format", "--check", ".")),
    ("lint", ("uv", "run", "ruff", "check", ".")),
    ("types", ("uv", "run", "mypy", "src", "tests", "scripts")),
    ("tests", ("uv", "run", "coverage", "run", "--branch", "-m", "pytest")),
    ("coverage", ("uv", "run", "coverage", "report", "--show-missing")),
    ("build", ("uv", "build")),
    ("twine", ("uvx", "twine>=6.1", "check", "dist/*")),
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run each gate and stop at the first failure."""
    # Parse only so --help works and unknown arguments fail fast.
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    for name, command in COMMANDS:
        print(f"\n==> {name}: {' '.join(command)}", flush=True)
        result = subprocess.run(command, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
