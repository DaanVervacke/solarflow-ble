import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_SPEC = spec_from_file_location(
    "check", Path(__file__).parent.parent / "scripts" / "check.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

main = _MODULE.main


def test_check_help_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    assert "Run the local CI gate" in capsys.readouterr().out


def test_check_rejects_unknown_arguments() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--bogus"])

    assert exc_info.value.code == 2
