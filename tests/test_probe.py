from pathlib import Path

from solarflow_ble.probe import (
    CaptureWriter,
    ProbeConfig,
    _advertisement_matches,
    _env,
    _redact_capture,
)


def test_probe_config_normalizes_address() -> None:
    config = ProbeConfig("proxy.local", None, "aa:bb", None, None, 1, 1, False, False)
    assert config.target_address == "AA:BB"


def test_probe_address_filter() -> None:
    config = ProbeConfig("proxy.local", None, "AA:BB", None, None, 1, 1, False, False)
    from bleak.backends.device import BLEDevice

    assert _advertisement_matches(config, BLEDevice("AA:BB", "SolarFlow", {}))
    assert not _advertisement_matches(config, BLEDevice("CC:DD", "SolarFlow", {}))


def test_capture_writer_writes_json(tmp_path: Path) -> None:
    path = tmp_path / "capture.jsonl"
    writer = CaptureWriter(path)
    writer.write("rx", b'{"method":"getInfo-rsp"}')
    writer.close()
    content = path.read_text()
    assert '"direction":"rx"' in content


def test_capture_writer_redacts_identity(tmp_path: Path) -> None:
    path = tmp_path / "capture.jsonl"
    writer = CaptureWriter(path)
    writer.write(
        "rx",
        b'{"deviceId":"real-device","productKey":"real-product",'
        b'"packData":[{"sn":"real-pack"}]}',
    )
    writer.close()

    content = path.read_text()
    assert "real-device" not in content
    assert "real-product" not in content
    assert "real-pack" not in content
    assert "DEVICE_ID" in content
    assert "PRODUCT_KEY" in content
    assert "PACK_SERIAL" in content


def test_redact_capture_handles_nested_values() -> None:
    assert _redact_capture({"serial_number": "secret", "value": 1}) == {
        "serial_number": "PACK_SERIAL",
        "value": 1,
    }


def test_env_prefers_canonical_name(monkeypatch) -> None:
    monkeypatch.setenv("SOLARFLOW_PROXY", "canonical")
    monkeypatch.setenv("SOLARFLOW_PROXY_HOST", "legacy")
    assert _env("SOLARFLOW_PROXY", "SOLARFLOW_PROXY_HOST") == "canonical"


def test_env_falls_back_to_legacy_name(monkeypatch) -> None:
    monkeypatch.delenv("SOLARFLOW_PROXY", raising=False)
    monkeypatch.setenv("SOLARFLOW_PROXY_HOST", "legacy")
    assert _env("SOLARFLOW_PROXY", "SOLARFLOW_PROXY_HOST") == "legacy"
