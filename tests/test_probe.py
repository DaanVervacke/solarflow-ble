import asyncio
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_SPEC = spec_from_file_location("probe_solarflow", "scripts/probe_solarflow.py")
assert _SPEC is not None
assert _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

CaptureWriter = _MODULE.CaptureWriter
CaptureTransport = _MODULE.CaptureTransport
ProbeConfig = _MODULE.ProbeConfig
ProbeBluetoothManager = _MODULE.ProbeBluetoothManager
_advertisement_matches = _MODULE._advertisement_matches
_find_device = _MODULE._find_device
_list_advertisements = _MODULE._list_advertisements
_redact_capture = _MODULE._redact_capture
_parser = _MODULE._parser
_run_passive_capture = _MODULE._run_passive_capture
run_probe = _MODULE.run_probe
main = _MODULE.main


def test_probe_config_normalizes_address() -> None:
    config = ProbeConfig(
        proxy="proxy.local",
        noise_psk=None,
        target_address="aa:bb",
        target_identifier=None,
        output=None,
        scan_seconds=1,
        capture_seconds=1,
        send_handshake=False,
        list_advertisements=False,
    )
    assert config.target_address == "AA:BB"
    assert not config.show_identities


def test_parser_enables_identity_display() -> None:
    args = _parser().parse_args(["--proxy", "proxy.local", "--show-identities"])

    assert args.show_identities


def test_probe_bluetooth_manager_implements_discovery_hook() -> None:
    ProbeBluetoothManager()


def test_probe_address_filter() -> None:
    config = ProbeConfig(
        proxy="proxy.local",
        noise_psk=None,
        target_address="AA:BB",
        target_identifier=None,
        output=None,
        scan_seconds=1,
        capture_seconds=1,
        send_handshake=False,
        list_advertisements=False,
    )
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


def test_capture_writer_preserves_non_json_payload(tmp_path: Path) -> None:
    path = tmp_path / "capture.jsonl"
    writer = CaptureWriter(path)
    writer.write("rx", b"\x00\xffraw")
    writer.close()

    content = path.read_text()
    assert '"hex":"00ff726177"' in content
    assert '"json":null' in content
    assert '"text":"\\u0000\\ufffdraw"' in content


@pytest.mark.asyncio
async def test_capture_transport_forwards_and_captures_traffic(
    tmp_path: Path,
) -> None:
    class FakeTransport:
        def __init__(self) -> None:
            self.callback: Any = None
            self.calls: list[tuple[Any, ...]] = []

        async def connect(self) -> None:
            self.calls.append(("connect",))

        async def disconnect(self) -> None:
            self.calls.append(("disconnect",))

        async def start_notify(self, characteristic: str, callback: Any) -> None:
            self.calls.append(("start_notify", characteristic))
            self.callback = callback

        async def stop_notify(self, characteristic: str) -> None:
            self.calls.append(("stop_notify", characteristic))

        async def write_gatt_char(
            self, characteristic: str, data: bytes, response: bool = False
        ) -> None:
            self.calls.append(("write", characteristic, data, response))

    path = tmp_path / "capture.jsonl"
    capture = CaptureWriter(path)
    underlying = FakeTransport()
    transport = CaptureTransport(underlying, capture)
    received: list[tuple[str, bytes]] = []

    async def callback(characteristic: str, payload: bytes) -> None:
        received.append((characteristic, payload))

    await transport.connect()
    await transport.start_notify("notify", callback)
    await transport.write_gatt_char("write", b'{"deviceId":"secret"}', response=True)
    assert underlying.callback is not None
    await underlying.callback("notify", b'{"method":"report"}')
    await transport.stop_notify("notify")
    await transport.disconnect()
    capture.close()

    assert underlying.calls == [
        ("connect",),
        ("start_notify", "notify"),
        ("write", "write", b'{"deviceId":"secret"}', True),
        ("stop_notify", "notify"),
        ("disconnect",),
    ]
    assert received == [("notify", b'{"method":"report"}')]
    content = path.read_text()
    assert '"direction":"tx"' in content
    assert '"direction":"rx"' in content
    assert "secret" not in content
    assert "DEVICE_ID" in content


@pytest.mark.asyncio
async def test_passive_capture_uses_direct_bleak_without_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Services:
        def get_characteristic(self, uuid: str) -> Any:
            return SimpleNamespace(uuid=uuid)

    class FakeBleakClient:
        def __init__(self) -> None:
            self.services = Services()
            self.callback: Any = None
            self.calls: list[str] = []

        async def start_notify(self, _characteristic: Any, callback: Any) -> None:
            self.calls.append("start_notify")
            self.callback = callback

        async def stop_notify(self, _characteristic: Any) -> None:
            self.calls.append("stop_notify")

        async def disconnect(self) -> None:
            self.calls.append("disconnect")

    client = FakeBleakClient()
    monkeypatch.setattr(
        _MODULE,
        "establish_connection",
        lambda *_args, **_kwargs: _async_return(client),
    )
    monkeypatch.setattr(_MODULE.asyncio, "sleep", _async_noop)
    device = SimpleNamespace(address="AA:BB", name="SolarFlow")
    config = _scan_config()
    config.capture_seconds = 0
    capture = CaptureWriter(tmp_path / "passive.jsonl")

    await _run_passive_capture(config, device, capture)
    capture.close()

    assert client.calls == ["start_notify", "stop_notify", "disconnect"]
    assert not hasattr(client, "write_gatt_char")


@pytest.mark.asyncio
async def test_normal_probe_uses_client_and_logs_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class FakeManager:
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    class FakeBluetoothManager:
        async def async_setup(self) -> None:
            pass

        def async_current_scanners(self) -> list[Any]:
            return []

        def async_stop(self) -> None:
            pass

    class FakeTransport:
        def __init__(self, device: Any) -> None:
            self.device = device

    class FakeClient:
        device_id = "REAL_DEVICE"
        status = "ready"

        def __init__(self, transport: Any) -> None:
            self.transport = transport
            self.calls: list[str] = []

        async def connect(self) -> None:
            self.calls.append("connect")

        async def disconnect(self) -> None:
            self.calls.append("disconnect")

    client_holder: list[FakeClient] = []
    monkeypatch.setattr(_MODULE, "APIConnectionManager", lambda _config: FakeManager())
    monkeypatch.setattr(_MODULE, "ProbeBluetoothManager", FakeBluetoothManager)
    monkeypatch.setattr(_MODULE, "_find_device", _async_device)
    monkeypatch.setattr(_MODULE, "BleakTransport", FakeTransport)

    def make_client(transport: Any) -> FakeClient:
        client = FakeClient(transport)
        client_holder.append(client)
        return client

    monkeypatch.setattr(_MODULE, "SolarFlowClient", make_client)
    monkeypatch.setattr(_MODULE.asyncio, "sleep", _async_noop)
    config = _scan_config()
    config.output = tmp_path / "normal.jsonl"
    config.send_handshake = True

    with caplog.at_level("INFO"):
        await run_probe(config)

    assert len(client_holder) == 1
    assert isinstance(client_holder[0].transport, CaptureTransport)
    assert client_holder[0].transport._transport.device.address == "AA:BB"
    assert client_holder[0].calls == ["connect", "disconnect"]
    assert "device_id=DEVICE_ID status=ready" in caplog.text


@pytest.mark.asyncio
async def test_failed_normal_probe_preserves_capture_and_disconnects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeManager:
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    class FakeBluetoothManager:
        async def async_setup(self) -> None:
            pass

        def async_current_scanners(self) -> list[Any]:
            return []

        def async_stop(self) -> None:
            pass

    class FakeTransport:
        def __init__(self, _device: Any) -> None:
            pass

        async def write_gatt_char(
            self, _characteristic: str, _data: bytes, response: bool = False
        ) -> None:
            del response

    class FailingClient:
        def __init__(self, transport: Any) -> None:
            self.transport = transport
            self.disconnected = False

        async def connect(self) -> None:
            await self.transport.write_gatt_char("write", b'{"deviceId":"secret"}')
            raise RuntimeError("connect failed")

        async def disconnect(self) -> None:
            self.disconnected = True

    clients: list[FailingClient] = []
    monkeypatch.setattr(_MODULE, "APIConnectionManager", lambda _config: FakeManager())
    monkeypatch.setattr(_MODULE, "ProbeBluetoothManager", FakeBluetoothManager)
    monkeypatch.setattr(_MODULE, "_find_device", _async_device)
    monkeypatch.setattr(_MODULE, "BleakTransport", FakeTransport)

    def make_client(transport: Any) -> FailingClient:
        client = FailingClient(transport)
        clients.append(client)
        return client

    monkeypatch.setattr(_MODULE, "SolarFlowClient", make_client)
    config = _scan_config()
    config.output = tmp_path / "failed.jsonl"
    config.send_handshake = True

    with pytest.raises(RuntimeError, match="connect failed"):
        await run_probe(config)

    assert clients[0].disconnected
    content = config.output.read_text()
    assert '"direction":"tx"' in content
    assert "secret" not in content


async def _async_return(value: Any) -> Any:
    return value


async def _async_noop(_seconds: float) -> None:
    pass


async def _async_device(_config: Any, _manager: Any) -> Any:
    return SimpleNamespace(address="AA:BB", name="SolarFlow")


def test_redact_capture_handles_nested_values() -> None:
    assert _redact_capture({"serial_number": "secret", "value": 1}) == {
        "serial_number": "PACK_SERIAL",
        "value": 1,
    }


def _scan_config() -> Any:
    return ProbeConfig(
        proxy="proxy.local",
        noise_psk=None,
        target_address=None,
        target_identifier=None,
        output=None,
        scan_seconds=2,
        capture_seconds=1,
        send_handshake=False,
        list_advertisements=False,
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_find_device_scan_window_excludes_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(_MODULE.asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(
        _MODULE.asyncio,
        "get_running_loop",
        lambda: SimpleNamespace(time=clock.time),
    )
    manager = SimpleNamespace(
        async_ble_device_from_address=lambda *_args, **_kwargs: None,
        async_discovered_devices=lambda *_args: [],
        async_current_scanners=list,
    )

    with pytest.raises(TimeoutError):
        asyncio.run(_find_device(_scan_config(), manager))

    assert clock.sleeps[0] == 5
    assert sum(clock.sleeps[1:]) == 2


def test_list_advertisements_scan_window_excludes_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(_MODULE.asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(
        _MODULE.asyncio,
        "get_running_loop",
        lambda: SimpleNamespace(time=clock.time),
    )
    manager = SimpleNamespace(async_current_scanners=list)

    asyncio.run(_list_advertisements(_scan_config(), manager))

    assert clock.sleeps[0] == 5
    assert sum(clock.sleeps[1:]) == 2


def test_list_advertisements_redacts_identity_by_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    scanner = SimpleNamespace(
        discovered_devices_and_advertisement_data={
            "device": (
                SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="SolarFlow"),
                SimpleNamespace(
                    manufacturer_data={0x4F48: b"REAL_IDENTIFIER"},
                    rssi=-42,
                    service_uuids=[],
                ),
            )
        }
    )

    clock = FakeClock()
    monkeypatch.setattr(_MODULE.asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(
        _MODULE.asyncio,
        "get_running_loop",
        lambda: SimpleNamespace(time=clock.time),
    )
    config = _scan_config()
    manager = SimpleNamespace(async_current_scanners=lambda: [scanner])

    with caplog.at_level("INFO"):
        asyncio.run(_list_advertisements(config, manager))

    assert "address=DEVICE_ADDRESS" in caplog.text
    assert "AA:BB:CC:DD:EE:FF" not in caplog.text
    assert "REAL_IDENTIFIER" not in caplog.text


def test_list_advertisements_shows_identity_when_enabled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    scanner = SimpleNamespace(
        discovered_devices_and_advertisement_data={
            "device": (
                SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="SolarFlow"),
                SimpleNamespace(
                    manufacturer_data={0x4F48: b"REAL_IDENTIFIER"},
                    rssi=-42,
                    service_uuids=[],
                ),
            )
        }
    )

    clock = FakeClock()
    monkeypatch.setattr(_MODULE.asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(
        _MODULE.asyncio,
        "get_running_loop",
        lambda: SimpleNamespace(time=clock.time),
    )
    config = _scan_config()
    config.show_identities = True
    manager = SimpleNamespace(async_current_scanners=lambda: [scanner])

    with caplog.at_level("INFO"):
        asyncio.run(_list_advertisements(config, manager))

    assert "address=AA:BB:CC:DD:EE:FF" in caplog.text
    assert "identifier=REAL_IDENTIFIER" in caplog.text


def test_capture_writer_redacts_identity_independently_of_probe_display(
    tmp_path: Path,
) -> None:
    path = tmp_path / "capture.jsonl"
    writer = CaptureWriter(path)
    writer.write(
        "rx",
        b'{"deviceId":"real-device"}',
    )
    writer.close()

    content = path.read_text()
    assert "real-device" not in content
    assert "DEVICE_ID" in content


def test_main_reports_probe_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def fail(_config: Any) -> None:
        raise TimeoutError("SolarFlow device was not found through the proxy")

    monkeypatch.setattr(_MODULE, "run_probe", fail)

    with caplog.at_level("WARNING"):
        result = main(["--proxy", "proxy.local"])

    assert result == 1
    assert (
        "Probe failed: SolarFlow device was not found through the proxy" in caplog.text
    )
