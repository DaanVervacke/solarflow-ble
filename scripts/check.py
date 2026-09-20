"""Run the local CI gate in a fixed order."""

from __future__ import annotations

import subprocess
import sys

COMMANDS = (
    ("format", ("uv", "run", "ruff", "format", "--check", ".")),
    ("lint", ("uv", "run", "ruff", "check", ".")),
    ("types", ("uv", "run", "mypy", "src", "tests", "scripts")),
    ("tests", ("uv", "run", "coverage", "run", "--branch", "-m", "pytest")),
    ("coverage", ("uv", "run", "coverage", "report", "--show-missing")),
    ("build", ("uv", "build")),
)


def main() -> int:
    """Run each gate and stop at the first failure."""
    for name, command in COMMANDS:
        print(f"\n==> {name}: {' '.join(command)}", flush=True)
        result = subprocess.run(command, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
