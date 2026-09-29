"""Python library for Zendure SolarFlow BLE devices."""

from .client import BleTransport, SolarFlowClient
from .limits import (
    DEFAULT_LIMITS,
    MODEL_LIMITS,
    MODEL_SOLARFLOW_2400AC,
    SolarFlowLimits,
)
from .models import Advertisement, ConnectionStatus, SolarFlowState, SolarFlowUpdate
from .protocol import parse_advertisement
from .transport import BleakTransport

__version__ = "0.1.3"
__all__ = [
    "DEFAULT_LIMITS",
    "MODEL_LIMITS",
    "MODEL_SOLARFLOW_2400AC",
    "Advertisement",
    "BleTransport",
    "BleakTransport",
    "ConnectionStatus",
    "SolarFlowClient",
    "SolarFlowLimits",
    "SolarFlowState",
    "SolarFlowUpdate",
    "parse_advertisement",
]
