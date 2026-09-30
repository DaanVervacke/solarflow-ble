"""Typed SolarFlow models."""

from dataclasses import dataclass, replace
from enum import IntEnum, StrEnum
from typing import Any, cast

_REPORT_FIELDS = {
    "packInputPower": "pack_input_power",
    "outputPackPower": "output_pack_power",
    "outputHomePower": "output_home_power",
    "remainOutTime": "remain_out_time",
    "dataReady": "data_ready",
    "acMode": "ac_mode",
    "inputLimit": "input_limit",
    "outputLimit": "output_limit",
    "packState": "pack_state",
    "acStatus": "ac_status",
    "electricLevel": "electric_level",
    "gridState": "grid_state",
    "faultLevel": "fault_level",
    "smartMode": "smart_mode",
    "chargeMaxLimit": "charge_max_limit",
    "socLimit": "soc_limit",
    # minSoc/socSet are per-mille on the wire (percent x 10); the client
    # multiplies by 10 when setting them, so keep raw wire values here.
    "minSoc": "min_soc",
    "socSet": "soc_set",
    "gridInputPower": "grid_input_power",
    "solarInputPower": "solar_input_power",
    "solarPower1": "solar_power_1",
    "solarPower2": "solar_power_2",
    "solarPower3": "solar_power_3",
    "solarPower4": "solar_power_4",
    "solarPower5": "solar_power_5",
    "solarPower6": "solar_power_6",
    "gridOffPower": "grid_off_power",
    "socStatus": "soc_status",
    "hyperTmp": "hyper_temperature",
}

_PACK_FIELDS = {
    "packType": "pack_type",
    "socLevel": "soc_level",
    "state": "state",
    "power": "power",
    "maxTemp": "max_temp",
    "totalVol": "total_voltage",
    "batcur": "battery_current",
    "maxVol": "max_voltage",
    "minVol": "min_voltage",
    "softVersion": "software_version",
    "heatState": "heat_state",
}


def _is_int(value: object) -> bool:
    """Accept wire integers, excluding JSON booleans."""
    return isinstance(value, int) and not isinstance(value, bool)


class AcMode(IntEnum):
    """Known AC modes."""

    CHARGING = 1
    DISCHARGING = 2


class ConnectionStatus(StrEnum):
    """Protocol session status."""

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    PROTOCOL_READY = "protocol_ready"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class BatteryPack:
    """Latest state reported for one battery pack."""

    serial_number: str
    pack_type: int | None = None
    soc_level: int | None = None
    state: int | None = None
    power: int | None = None
    max_temp: int | None = None
    total_voltage: int | None = None
    battery_current: int | None = None
    max_voltage: int | None = None
    min_voltage: int | None = None
    software_version: int | None = None
    heat_state: int | None = None


@dataclass(frozen=True, slots=True)
class Advertisement:
    """A SolarFlow BLE advertisement."""

    address: str
    identifier: str
    rssi: int | None = None
    connectable: bool = True
    address_type: int | None = None


def _merged_packs(
    packs: tuple[BatteryPack, ...], raw_packs: list[Any]
) -> tuple[BatteryPack, ...]:
    """Merge raw pack entries into the known packs, keyed by serial number."""
    known = {pack.serial_number: pack for pack in packs}
    for raw in raw_packs:
        if not isinstance(raw, dict) or not isinstance(raw.get("sn"), str):
            continue
        serial_number = raw["sn"]
        current = known.get(serial_number, BatteryPack(serial_number=serial_number))
        changes = {
            field: raw[key]
            for key, field in _PACK_FIELDS.items()
            if key in raw and _is_int(raw[key])
        }
        known[serial_number] = replace(current, **cast(Any, changes))
    return tuple(known.values())


@dataclass(frozen=True, slots=True)
class SolarFlowState:
    """Latest decoded controller state."""

    pack_input_power: int | None = None
    output_pack_power: int | None = None
    battery_power: int | None = None
    ac_mode: int | None = None
    input_limit: int | None = None
    output_limit: int | None = None
    pack_state: int | None = None
    ac_status: int | None = None
    electric_level: int | None = None
    grid_state: int | None = None
    fault_level: int | None = None
    smart_mode: int | None = None
    charge_max_limit: int | None = None
    soc_limit: int | None = None
    # Raw wire values: minSoc/socSet are reported as per-mille (percent x 10).
    min_soc: int | None = None
    soc_set: int | None = None
    output_home_power: int | None = None
    remain_out_time: int | None = None
    data_ready: int | None = None
    grid_input_power: int | None = None
    solar_input_power: int | None = None
    solar_power_1: int | None = None
    solar_power_2: int | None = None
    solar_power_3: int | None = None
    solar_power_4: int | None = None
    solar_power_5: int | None = None
    solar_power_6: int | None = None
    grid_off_power: int | None = None
    soc_status: int | None = None
    hyper_temperature: int | None = None
    device_id: str | None = None
    product_key: str | None = None
    packs: tuple[BatteryPack, ...] = ()
    raw: dict[str, object] | None = None

    def update(self, values: dict[str, object]) -> SolarFlowState:
        return self.with_report({"properties": values})

    def with_report(self, message: dict[str, object]) -> SolarFlowState:
        """Apply one report message with a single reconstruction.

        Combines update(), with_identity(), and with_packs() so the
        per-report hot path rebuilds the state once instead of four
        times. Identity is only applied when the report carries a
        properties object, matching the historical composition.
        """
        changes: dict[str, Any] = {}
        properties = message.get("properties")
        if isinstance(properties, dict):
            changes.update(
                {
                    _REPORT_FIELDS[key]: value
                    for key, value in properties.items()
                    if key in _REPORT_FIELDS and _is_int(value)
                }
            )
            device_id = message.get("deviceId")
            if isinstance(device_id, str):
                changes["device_id"] = device_id
            product_key = message.get("productKey")
            if isinstance(product_key, str):
                changes["product_key"] = product_key
            pack_input_power = changes.get("pack_input_power", self.pack_input_power)
            output_pack_power = changes.get("output_pack_power", self.output_pack_power)
            if pack_input_power is not None and output_pack_power is not None:
                changes["battery_power"] = output_pack_power - pack_input_power
            changes["raw"] = {**(self.raw or {}), **properties}
        raw_packs = message.get("packData")
        if isinstance(raw_packs, list):
            changes["packs"] = _merged_packs(self.packs, raw_packs)
        return replace(self, **changes)

    def with_identity(self, message: dict[str, object]) -> SolarFlowState:
        device_id = message.get("deviceId")
        product_key = message.get("productKey")
        return replace(
            self,
            device_id=device_id if isinstance(device_id, str) else self.device_id,
            product_key=product_key
            if isinstance(product_key, str)
            else self.product_key,
        )

    def with_packs(self, message: dict[str, object]) -> SolarFlowState:
        raw_packs = message.get("packData")
        if not isinstance(raw_packs, list):
            return self
        return replace(self, packs=_merged_packs(self.packs, raw_packs))


@dataclass(frozen=True, slots=True)
class SolarFlowUpdate:
    """Typed state update delivered to an optional callback."""

    state: SolarFlowState
    status: ConnectionStatus
    raw_message: dict[str, object]
