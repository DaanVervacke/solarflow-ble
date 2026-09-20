"""Typed SolarFlow models."""

from dataclasses import dataclass, replace
from enum import IntEnum, StrEnum


class AcMode(IntEnum):
    """Known AC modes."""
    CHARGING = 1
    DISCHARGING = 2


class PackState(IntEnum):
    """Known pack states."""
    STANDBY = 0
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
        fields = {"packInputPower":"pack_input_power", "outputPackPower":"output_pack_power", "outputHomePower":"output_home_power", "remainOutTime":"remain_out_time", "dataReady":"data_ready", "acMode":"ac_mode", "inputLimit":"input_limit", "outputLimit":"output_limit", "packState":"pack_state", "acStatus":"ac_status", "electricLevel":"electric_level", "gridState":"grid_state", "faultLevel":"fault_level", "smartMode":"smart_mode", "chargeMaxLimit":"charge_max_limit", "socLimit":"soc_limit", "gridInputPower":"grid_input_power", "solarInputPower":"solar_input_power", "solarPower1":"solar_power_1", "solarPower2":"solar_power_2", "solarPower3":"solar_power_3", "solarPower4":"solar_power_4", "solarPower5":"solar_power_5", "solarPower6":"solar_power_6", "gridOffPower":"grid_off_power", "socStatus":"soc_status", "hyperTmp":"hyper_temperature"}
        changes: dict[str, int] = {fields[key]: value for key, value in values.items() if key in fields and isinstance(value, int)}
        current = replace(self, raw={**(self.raw or {}), **values})
        current = replace(
            current,
            pack_input_power=changes.get("pack_input_power", current.pack_input_power),
            output_pack_power=changes.get("output_pack_power", current.output_pack_power),
            ac_mode=changes.get("ac_mode", current.ac_mode),
            input_limit=changes.get("input_limit", current.input_limit),
            output_limit=changes.get("output_limit", current.output_limit),
            pack_state=changes.get("pack_state", current.pack_state),
            ac_status=changes.get("ac_status", current.ac_status),
            electric_level=changes.get("electric_level", current.electric_level),
            grid_state=changes.get("grid_state", current.grid_state),
            fault_level=changes.get("fault_level", current.fault_level),
            smart_mode=changes.get("smart_mode", current.smart_mode),
            charge_max_limit=changes.get("charge_max_limit", current.charge_max_limit),
            soc_limit=changes.get("soc_limit", current.soc_limit),
            output_home_power=changes.get("output_home_power", current.output_home_power),
            remain_out_time=changes.get("remain_out_time", current.remain_out_time),
            data_ready=changes.get("data_ready", current.data_ready),
            grid_input_power=changes.get("grid_input_power", current.grid_input_power),
            solar_input_power=changes.get("solar_input_power", current.solar_input_power),
            solar_power_1=changes.get("solar_power_1", current.solar_power_1),
            solar_power_2=changes.get("solar_power_2", current.solar_power_2),
            solar_power_3=changes.get("solar_power_3", current.solar_power_3),
            solar_power_4=changes.get("solar_power_4", current.solar_power_4),
            solar_power_5=changes.get("solar_power_5", current.solar_power_5),
            solar_power_6=changes.get("solar_power_6", current.solar_power_6),
            grid_off_power=changes.get("grid_off_power", current.grid_off_power),
            soc_status=changes.get("soc_status", current.soc_status),
            hyper_temperature=changes.get("hyper_temperature", current.hyper_temperature),
        )
        if current.pack_input_power is not None and current.output_pack_power is not None:
            current = replace(current, battery_power=current.output_pack_power - current.pack_input_power)
        return current

    def with_identity(self, message: dict[str, object]) -> SolarFlowState:
        device_id = message.get("deviceId")
        product_key = message.get("productKey")
        return replace(self, device_id=device_id if isinstance(device_id, str) else self.device_id, product_key=product_key if isinstance(product_key, str) else self.product_key)

    def with_packs(self, message: dict[str, object]) -> SolarFlowState:
        raw_packs = message.get("packData")
        if not isinstance(raw_packs, list):
            return self
        known = {pack.serial_number: pack for pack in self.packs}
        for raw in raw_packs:
            if not isinstance(raw, dict) or not isinstance(raw.get("sn"), str):
                continue
            known[raw["sn"]] = BatteryPack(serial_number=raw["sn"], pack_type=raw.get("packType"), soc_level=raw.get("socLevel"), state=raw.get("state"), power=raw.get("power"), max_temp=raw.get("maxTemp"), total_voltage=raw.get("totalVol"), battery_current=raw.get("batcur"), max_voltage=raw.get("maxVol"), min_voltage=raw.get("minVol"), software_version=raw.get("softVersion"), heat_state=raw.get("heatState"))
        return replace(self, packs=tuple(known.values()))


@dataclass(frozen=True, slots=True)
class SolarFlowUpdate:
    """Typed state update delivered to an optional callback."""
    state: SolarFlowState
    status: ConnectionStatus
    raw_message: dict[str, object]
