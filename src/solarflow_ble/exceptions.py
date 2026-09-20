"""SolarFlow exceptions."""


class SolarFlowError(Exception):
    """Base SolarFlow error."""


class SolarFlowConnectionError(SolarFlowError):
    """The BLE transport could not connect or disconnected."""


class SolarFlowTimeoutError(SolarFlowError):
    """The device did not answer before the timeout."""


class SolarFlowProtocolError(SolarFlowError):
    """The device sent invalid or unexpected protocol data."""


class SolarFlowCommandError(SolarFlowError):
    """The device rejected a command."""


class SolarFlowValidationError(SolarFlowError, ValueError):
    """A command argument is outside the supported range."""


class SolarFlowNotReadyError(SolarFlowError):
    """The session is connected but controls are not currently ready."""


class SolarFlowDeviceError(SolarFlowError):
    """The device reported a protocol error event."""
