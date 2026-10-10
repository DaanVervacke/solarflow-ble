import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from scripts import client_diagnostic
from scripts._redact import redact_value
from solarflow_ble import SolarFlowState
from solarflow_ble.models import BatteryPack, ConnectionStatus, SolarFlowUpdate

from conftest import FakeBluetoothManager, FakeManager, stub

format_update = client_diagnostic.format_update
find_device = client_diagnostic.find_device
load_config = client_diagnostic.load_config
main = client_diagnostic.main
parse_arguments = client_diagnostic.parse_arguments
plan_controls = client_diagnostic.plan_controls
serialize_pack = client_diagnostic.serialize_pack
serialize_state = client_diagnostic.serialize_state
JsonlWriter = client_diagnostic.JsonlWriter


def test_parser_requires_exactly_one_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        parse_arguments(["--proxy", "proxy", "--noise-psk", "psk"])
    with pytest.raises(SystemExit):
        parse_arguments(
            [
                "--proxy",
                "proxy",
                "--noise-psk",
                "psk",
                "--address",
                "AA",
                "--identifier",
                "ID",
            ]
        )


def test_parser_loads_config_values(tmp_path: Path) -> None:
    config_path = tmp_path / "client.json"
    config_path.write_text(
        '{"proxy": "config-proxy", "noise_psk": "config-secret", '
        '"identifier": "config-device"}',
        encoding="utf-8",
    )

    args = parse_arguments(["--config", str(config_path)])

    assert args.proxy == "config-proxy"
    assert args.noise_psk == "config-secret"
    assert args.identifier == "config-device"
    assert args.address is None


def test_cli_values_override_config_values(tmp_path: Path) -> None:
    config_path = tmp_path / "client.json"
    config_path.write_text(
        '{"proxy": "config-proxy", "noise_psk": "config-secret", '
        '"identifier": "config-device"}',
        encoding="utf-8",
    )

    args = parse_arguments(
        [
            "--config",
            str(config_path),
            "--proxy",
            "cli-proxy",
            "--noise-psk",
            "cli-secret",
            "--address",
            "AA:BB",
        ]
    )

    assert args.proxy == "cli-proxy"
    assert args.noise_psk == "cli-secret"
    assert args.address == "AA:BB"
    assert args.identifier is None


def test_missing_default_config_preserves_cli_behavior(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    args = parse_arguments(
        ["--proxy", "cli-proxy", "--noise-psk", "cli-secret", "--identifier", "ID"]
    )

    assert args.proxy == "cli-proxy"
    assert args.noise_psk == "cli-secret"
    assert args.identifier == "ID"


def test_explicit_missing_config_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        parse_arguments(
            [
                "--config",
                str(tmp_path / "missing.json"),
                "--proxy",
                "proxy",
                "--noise-psk",
                "secret",
                "--identifier",
                "ID",
            ]
        )


def test_config_requires_complete_target_and_connection_values(tmp_path: Path) -> None:
    config_path = tmp_path / "client.json"
    config_path.write_text(
        '{"proxy": "proxy", "noise_psk": "secret"}', encoding="utf-8"
    )

    with pytest.raises(SystemExit):
        parse_arguments(["--config", str(config_path)])


def test_config_rejects_both_target_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "client.json"
    config_path.write_text(
        '{"proxy": "proxy", "noise_psk": "secret", "address": "AA", '
        '"identifier": "ID"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="only one"):
        load_config(config_path)


def test_parser_rejects_controls_without_confirmation() -> None:
    with pytest.raises(SystemExit):
        parse_arguments(
            [
                "--proxy",
                "proxy",
                "--noise-psk",
                "psk",
                "--identifier",
                "ID",
                "--controls",
            ]
        )


def test_parser_requires_soc_values_for_controls() -> None:
    with pytest.raises(SystemExit):
        parse_arguments(
            [
                "--proxy",
                "proxy",
                "--noise-psk",
                "psk",
                "--identifier",
                "ID",
                "--controls",
                "--confirm-controls",
            ]
        )


def test_redaction_is_recursive() -> None:
    value = {
        "deviceId": "device",
        "nested": [{"product_key": "product", "packData": [{"sn": "serial"}]}],
        "credentials": {"noise-psk": "secret"},
        "safe": "kept",
    }

    redacted = redact_value(value)

    assert redacted == {
        "deviceId": "DEVICE_ID",
        "nested": [{"product_key": "PRODUCT_KEY", "packData": [{"sn": "PACK_SERIAL"}]}],
        "credentials": {"noise-psk": "REDACTED"},
        "safe": "kept",
    }


def test_redaction_covers_shared_key_union() -> None:
    redacted = redact_value(
        {
            "identifier": "real-identifier",
            "address": "real-address",
            "name": "real-name",
            "password": "real-password",
            "token": "real-token",
            "secret": "real-secret",
            "apiKey": "real-apikey",
        }
    )

    assert redacted == {
        "identifier": "DEVICE_IDENTIFIER",
        "address": "DEVICE_ADDRESS",
        "name": "DEVICE_NAME",
        "password": "REDACTED",
        "token": "REDACTED",
        "secret": "REDACTED",
        "apiKey": "REDACTED",
    }


def test_state_and_pack_serialization() -> None:
    pack = BatteryPack(serial_number="PACK-1", soc_level=80, power=42)
    state = replace(
        SolarFlowState(device_id="DEVICE-1", product_key="PRODUCT-1", packs=(pack,)),
        input_limit=500,
    )

    assert serialize_pack(pack)["serial_number"] == "PACK-1"
    serialized = serialize_state(state)
    assert serialized["input_limit"] == 500
    assert serialized["packs"] == [serialize_pack(pack)]


def test_control_plan_never_guesses_restore_values() -> None:
    plan = plan_controls(
        SolarFlowState(input_limit=600, output_limit=None, ac_mode=1),
        input_limit=700,
        output_limit=800,
        min_soc=20,
        soc=90,
    )

    assert [(action.property_name, action.value) for action in plan.actions] == [
        ("inputLimit", 700),
        ("outputLimit", 800),
        ("acMode", 1),
    ]
    assert plan.actions[0].restore_value == 600
    assert plan.actions[1].restore_value is None
    assert "minSoc: cannot restore safely: original value unknown" in plan.skipped
    assert "socSet: cannot restore safely: original value unknown" in plan.skipped


def test_control_plan_restores_soc_overrides_from_reported_state() -> None:
    plan = plan_controls(
        SolarFlowState(min_soc=255, soc_set=900),
        min_soc=20,
        soc=95,
    )

    assert [
        (action.property_name, action.value, action.restore_value)
        for action in plan.actions
    ] == [
        ("minSoc", 20, 25),
        ("socSet", 95, 90),
    ]


def test_control_plan_refuses_overrides_without_restorable_originals() -> None:
    plan = plan_controls(
        SolarFlowState(min_soc=200),
        min_soc=20,
        soc=90,
        ac_mode=1,
    )

    assert [action.property_name for action in plan.actions] == ["minSoc"]
    assert plan.actions[0].restore_value == 20
    assert "socSet: cannot restore safely: original value unknown" in plan.skipped
    assert "acMode: cannot restore safely: original value unknown" in plan.skipped


def test_control_plan_reports_unavailable_current_values() -> None:
    plan = plan_controls(SolarFlowState())

    assert not plan.actions
    assert "inputLimit: current value unavailable" in plan.skipped
    assert "minSoc: current value unavailable" in plan.skipped


def test_update_formatting_has_concise_and_verbose_modes() -> None:
    update = SolarFlowUpdate(
        SolarFlowState(input_limit=300, packs=(BatteryPack("PACK-1"),)),
        ConnectionStatus.READY,
        {"method": "report", "deviceId": "DEVICE-1"},
    )

    concise = format_update(update)
    verbose = format_update(update, verbose=True)

    assert "method=report" in concise
    assert "packs=1" in concise
    assert '"raw"' in verbose
    assert "DEVICE-1" not in verbose
    assert '"deviceId": "DEVICE_ID"' in verbose
    assert "PACK-1" not in verbose
    assert '"serial_number": "PACK_SERIAL"' in verbose


def test_jsonl_writer_redacts_serialized_update(tmp_path: Path) -> None:
    update = SolarFlowUpdate(
        replace(
            SolarFlowState(
                device_id="secret-device",
                product_key="secret-product",
                packs=(BatteryPack("secret-pack"),),
            )
        ),
        ConnectionStatus.READY,
        {
            "method": "report",
            "deviceId": "secret-device",
            "productKey": "secret-product",
            "credentials": {"token": "secret-token"},
        },
    )
    path = tmp_path / "updates.jsonl"
    writer = JsonlWriter(path)
    writer.write(client_diagnostic.serialize_update(update))
    writer.close()

    content = path.read_text()
    assert "secret-device" not in content
    assert "secret-product" not in content
    assert "secret-pack" not in content
    assert "secret-token" not in content
    record = json.loads(content)
    assert record["state"]["input_limit"] is None
    assert record["state"]["device_id"] == "DEVICE_ID"
    assert record["packs"][0]["serial_number"] == "PACK_SERIAL"


@pytest.mark.asyncio
async def test_run_redacts_summary_state_updates_and_jsonl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = SolarFlowState(
        device_id="secret-device",
        product_key="secret-product",
        packs=(BatteryPack("secret-pack"),),
    )
    update = SolarFlowUpdate(
        state,
        ConnectionStatus.READY,
        {
            "method": "report",
            "deviceId": "secret-device",
            "productKey": "secret-product",
            "credentials": {"token": "secret-token"},
        },
    )

    class FakeTransport:
        def __init__(self, _device: object) -> None:
            pass

    class FakeClient:
        device_id = "secret-device"
        status = ConnectionStatus.READY
        protocol_ready = True
        ready = True

        def __init__(
            self, _transport: object, update_callback: object, **_kwargs: object
        ) -> None:
            self.state = state
            self._update_callback = update_callback

        async def connect(self) -> None:
            callback = self._update_callback
            assert callable(callback)
            result = callback(update)
            if asyncio.iscoroutine(result):
                await result

        async def disconnect(self) -> None:
            pass

    monkeypatch.setattr(
        client_diagnostic, "APIConnectionManager", lambda _config: FakeManager()
    )
    monkeypatch.setattr(
        client_diagnostic, "DiagnosticBluetoothManager", FakeBluetoothManager
    )
    monkeypatch.setattr(client_diagnostic, "BleakTransport", FakeTransport)
    monkeypatch.setattr(client_diagnostic, "SolarFlowClient", FakeClient)
    monkeypatch.setattr(
        client_diagnostic,
        "find_device",
        lambda *_args, **_kwargs: _async_result(
            (stub(address="secret-address", name="secret-name"), "secret-id")
        ),
    )
    monkeypatch.setattr(asyncio, "sleep", _async_sleep)

    args = stub(
        output=tmp_path / "updates.jsonl",
        proxy="proxy.local",
        noise_psk="secret-psk",
        address=None,
        identifier="secret-id",
        duration=0,
        verbose=True,
        controls=False,
    )
    await client_diagnostic.run(args)

    stdout = capsys.readouterr().out
    assert "secret-device" not in stdout
    assert "secret-product" not in stdout
    assert "secret-pack" not in stdout
    assert "secret-token" not in stdout
    assert "secret-address" not in stdout
    assert "secret-name" not in stdout
    assert "secret-id" not in stdout
    assert "DEVICE_ID" in stdout
    assert "PACK_SERIAL" in stdout
    persisted = (tmp_path / "updates.jsonl").read_text()
    assert "secret-device" not in persisted
    assert "secret-product" not in persisted
    assert "secret-pack" not in persisted
    assert "secret-token" not in persisted


async def _async_result(value: object) -> object:
    return value


async def _async_sleep(_seconds: float) -> None:
    pass


def test_find_device_warms_up_before_discovery_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, float | None]] = []
    clock = stub(now=0.0)

    async def sleep(seconds: float) -> None:
        events.append(("sleep", seconds))
        clock.now += seconds

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(
        asyncio,
        "get_running_loop",
        lambda: stub(time=lambda: clock.now),
    )
    manager = stub(
        async_ble_device_from_address=lambda *_args, **_kwargs: events.append(
            ("discover", None)
        ),
        async_current_scanners=list,
    )

    with pytest.raises(TimeoutError):
        asyncio.run(
            find_device(
                manager,
                address="AA:BB",
                identifier=None,
                scan_seconds=0,
            )
        )

    assert events[0] == ("sleep", 5.0)
    assert events[1] == ("discover", None)


def test_main_reports_client_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail(_args: object) -> None:
        raise TimeoutError("SolarFlow device was not found through the proxy")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(client_diagnostic, "run", fail)

    result = main(["--proxy", "proxy", "--noise-psk", "psk", "--identifier", "ID"])

    assert result == 1
    assert (
        "SolarFlow client diagnostic failed: SolarFlow device was not found through "
        "the proxy" in capsys.readouterr().err
    )
