"""Python library for Zendure SolarFlow BLE devices."""

from .client import (
    BleTransport,
    ConnectionLostCallback,
    NotificationCallback,
    SolarFlowClient,
    UpdateCallback,
)
from .exceptions import (
    SolarFlowCommandError,
    SolarFlowConnectionError,
    SolarFlowDeviceError,
    SolarFlowError,
    SolarFlowNotReadyError,
    SolarFlowProtocolError,
    SolarFlowTimeoutError,
    SolarFlowValidationError,
)
from .limits import (
    DEFAULT_LIMITS,
    MODEL_LIMITS,
    MODEL_SOLARFLOW_2400AC,
    SolarFlowLimits,
)
from .models import (
    AcMode,
    Advertisement,
    BatteryPack,
    ConnectionStatus,
    SolarFlowState,
    SolarFlowUpdate,
)
from .protocol import parse_advertisement
from .transport import BleakTransport

__version__ = "0.1.3"
__all__ = [
    "DEFAULT_LIMITS",
    "MODEL_LIMITS",
    "MODEL_SOLARFLOW_2400AC",
    "AcMode",
    "Advertisement",
    "BatteryPack",
    "BleTransport",
    "BleakTransport",
    "ConnectionLostCallback",
    "ConnectionStatus",
    "NotificationCallback",
    "SolarFlowClient",
    "SolarFlowCommandError",
    "SolarFlowConnectionError",
    "SolarFlowDeviceError",
    "SolarFlowError",
    "SolarFlowLimits",
    "SolarFlowNotReadyError",
    "SolarFlowProtocolError",
    "SolarFlowState",
    "SolarFlowTimeoutError",
    "SolarFlowUpdate",
    "SolarFlowValidationError",
    "UpdateCallback",
    "parse_advertisement",
]
