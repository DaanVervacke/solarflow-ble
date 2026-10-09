import pytest
from scripts import check

main = check.main


def test_check_help_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    assert "Run the local CI gate" in capsys.readouterr().out


def test_check_rejects_unknown_arguments() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--bogus"])

    assert exc_info.value.code == 2
