from __future__ import annotations

import solarflow_ble
from solarflow_ble import client as client_module
from solarflow_ble import exceptions as exceptions_module
from solarflow_ble import models as models_module


def test_package_root_exports_every_all_member() -> None:
    for name in solarflow_ble.__all__:
        assert getattr(solarflow_ble, name) is not None, name


def test_public_api_surface_covers_exceptions_models_and_callbacks() -> None:
    expected = {
        "SolarFlowError",
        "SolarFlowConnectionError",
        "SolarFlowTimeoutError",
        "SolarFlowProtocolError",
        "SolarFlowCommandError",
        "SolarFlowValidationError",
        "SolarFlowNotReadyError",
        "SolarFlowDeviceError",
        "AcMode",
        "BatteryPack",
        "NotificationCallback",
        "UpdateCallback",
        "ConnectionLostCallback",
    }
    assert expected <= set(solarflow_ble.__all__)
    assert (
        solarflow_ble.SolarFlowConnectionError
        is exceptions_module.SolarFlowConnectionError
    )
    assert solarflow_ble.AcMode is models_module.AcMode
    assert solarflow_ble.BatteryPack is models_module.BatteryPack
    assert solarflow_ble.NotificationCallback is client_module.NotificationCallback
    assert solarflow_ble.UpdateCallback is client_module.UpdateCallback
    assert solarflow_ble.ConnectionLostCallback is client_module.ConnectionLostCallback
