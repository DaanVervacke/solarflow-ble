"""Python library for Zendure SolarFlow BLE devices."""

from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _version

from .client import ConnectionLostCallback, SolarFlowClient, UpdateCallback
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
from .interfaces import BleTransport, NotificationCallback
from .limits import (
    DEFAULT_LIMITS,
    MODEL_LIMITS,
    MODEL_SOLARFLOW_2400AC,
    MODEL_SOLARFLOW_2400AC_PRODUCT_KEY,
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

try:
    __version__ = _version("solarflow-ble")
except _PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = [
    "DEFAULT_LIMITS",
    "MODEL_LIMITS",
    "MODEL_SOLARFLOW_2400AC",
    "MODEL_SOLARFLOW_2400AC_PRODUCT_KEY",
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
    "__version__",
    "parse_advertisement",
]
