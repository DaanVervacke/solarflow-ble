from __future__ import annotations

import json
from pathlib import Path

import pytest
from solarflow_ble import (
    SolarFlowState,
)


def _report(state: SolarFlowState, properties: dict[str, object]) -> SolarFlowState:
    return state.with_report({"properties": properties})


def test_state_derives_battery_power() -> None:
    state = _report(SolarFlowState(), {"packInputPower": 100, "outputPackPower": 400})
    assert state.battery_power == 300


def test_captured_report_fixture_contains_two_packs() -> None:
    fixture = Path(__file__).parent / "fixtures" / "solarflow_reports.jsonl"
    records = [json.loads(line)["json"] for line in fixture.read_text().splitlines()]
    pack_records = [
        record
        for record in records
        if isinstance(record, dict) and "packData" in record
    ]
    assert len(pack_records) >= 3
    assert all("sn" in pack for record in pack_records for pack in record["packData"])


def test_report_fields_merge() -> None:
    state = SolarFlowState()
    state = _report(state, {"electricLevel": 26, "smartMode": 0})
    state = _report(state, {"packInputPower": 10, "outputPackPower": 40})
    assert state.electric_level == 26
    assert state.smart_mode == 0
    assert state.battery_power == 30


def test_pack_fields_merge_across_partial_pack_data_records() -> None:
    state = SolarFlowState().with_report(
        {
            "packData": [{"sn": "PACK-1"}, {"sn": "PACK-2"}],
        }
    )
    state = state.with_report(
        {
            "packData": [
                {
                    "sn": "PACK-1",
                    "packType": 5,
                    "socLevel": 25,
                    "power": 100,
                    "maxTemp": 2971,
                },
                {
                    "sn": "PACK-2",
                    "packType": 6,
                    "socLevel": 40,
                    "power": 200,
                },
            ],
        }
    )
    state = state.with_report(
        {
            "packData": [
                {"sn": "PACK-1", "socLevel": 26},
                {"sn": "PACK-2", "power": 250},
            ],
        }
    )

    packs = {pack.serial_number: pack for pack in state.packs}
    assert list(packs) == ["PACK-1", "PACK-2"]
    assert packs["PACK-1"].pack_type == 5
    assert packs["PACK-1"].soc_level == 26
    assert packs["PACK-1"].power == 100
    assert packs["PACK-1"].max_temp == 2971
    assert packs["PACK-2"].pack_type == 6
    assert packs["PACK-2"].soc_level == 40
    assert packs["PACK-2"].power == 250


def test_report_string_and_boolean_values_are_dropped() -> None:
    state = _report(
        SolarFlowState(), {"electricLevel": "25", "smartMode": True, "inputLimit": 30}
    )
    assert state.electric_level is None
    assert state.smart_mode is None
    assert state.input_limit == 30


def test_pack_string_and_boolean_values_are_dropped() -> None:
    state = SolarFlowState().with_report(
        {"packData": [{"sn": "PACK-1", "socLevel": "25", "power": True, "state": 2}]}
    )
    pack = state.packs[0]
    assert pack.soc_level is None
    assert pack.power is None
    assert pack.state == 2


def test_with_report_skips_malformed_pack_entries() -> None:
    state = SolarFlowState().with_report(
        {
            "packData": [
                "not-a-dict",
                {"sn": 123},
                {"sn": "PACK-1", "socLevel": 25},
                {"socLevel": 40},
            ]
        }
    )
    assert [pack.serial_number for pack in state.packs] == ["PACK-1"]
    assert state.packs[0].soc_level == 25


def test_with_report_without_pack_data_keeps_state() -> None:
    state = SolarFlowState(device_id="DEVICE-1")
    assert state.with_report({}) == state


def test_with_report_applies_properties_identity_and_packs_in_one_pass() -> None:
    state = _report(SolarFlowState(), {"packInputPower": 100, "outputPackPower": 400})
    state = state.with_report(
        {
            "deviceId": "DEVICE-1",
            "productKey": "SOLARFLOW-2400AC",
            "properties": {"electricLevel": 26, "outputPackPower": 500},
            "packData": [{"sn": "PACK-1", "socLevel": 25}],
        }
    )
    assert state.device_id == "DEVICE-1"
    assert state.product_key == "SOLARFLOW-2400AC"
    assert state.electric_level == 26
    assert state.pack_input_power == 100
    assert state.output_pack_power == 500
    assert state.battery_power == 400
    assert [pack.serial_number for pack in state.packs] == ["PACK-1"]
    assert state.packs[0].soc_level == 25


def test_with_report_without_properties_keeps_identity_and_merges_packs() -> None:
    state = SolarFlowState(device_id="DEVICE-1")
    state = state.with_report(
        {"deviceId": "DEVICE-2", "packData": [{"sn": "PACK-1", "socLevel": 25}]}
    )
    assert state.device_id == "DEVICE-1"
    assert [pack.serial_number for pack in state.packs] == ["PACK-1"]
    assert state.packs[0].soc_level == 25


def test_report_mapping_updates_fields() -> None:
    state = _report(
        SolarFlowState(), {"inputLimit": 100, "outputLimit": 200, "solarPower6": 30}
    )
    assert state.input_limit == 100
    assert state.output_limit == 200
    assert state.solar_power_6 == 30


def test_report_mapping_keeps_soc_controls_in_wire_units() -> None:
    state = _report(SolarFlowState(), {"minSoc": 200, "socSet": 900})
    assert state.min_soc == 200
    assert state.soc_set == 900


def test_update_keeps_only_known_keys_in_raw() -> None:
    state = _report(SolarFlowState(), {"electricLevel": 26, "hostileKey": "payload"})
    assert state.raw == {"electricLevel": 26}

    state = _report(state, {"anotherHostileKey": 1})
    assert state.raw == {"electricLevel": 26}


def test_raw_is_read_only_and_not_shared_with_later_states() -> None:
    state = _report(SolarFlowState(), {"electricLevel": 26})
    assert state.raw is not None
    with pytest.raises(TypeError):
        state.raw["electricLevel"] = 99  # type: ignore[index]

    later = _report(state, {"inputLimit": 100})
    assert later.raw == {"electricLevel": 26, "inputLimit": 100}
    assert state.raw == {"electricLevel": 26}
