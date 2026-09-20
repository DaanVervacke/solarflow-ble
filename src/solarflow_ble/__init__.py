"""Python library for Zendure SolarFlow BLE devices."""

from .client import BleTransport, SolarFlowClient
from .models import Advertisement, ConnectionStatus, SolarFlowState, SolarFlowUpdate
from .protocol import parse_advertisement
from .transport import BleakTransport

__version__ = "0.1.0"
__all__ = [
    "Advertisement",
    "BleTransport",
    "BleakTransport",
    "ConnectionStatus",
    "SolarFlowClient",
    "SolarFlowState",
    "SolarFlowUpdate",
    "parse_advertisement",
]
