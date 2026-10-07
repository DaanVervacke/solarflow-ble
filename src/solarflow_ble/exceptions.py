"""SolarFlow exceptions."""


class SolarFlowError(Exception):
    """Base SolarFlow error."""


class SolarFlowConnectionError(SolarFlowError):
    """The session failed, or a control write could not be sent."""


class SolarFlowTimeoutError(SolarFlowError):
    """The device did not answer before the timeout."""


class SolarFlowProtocolError(SolarFlowError):
    """The device sent invalid or unexpected protocol data."""


class SolarFlowCommandError(SolarFlowError):
    """The device rejected a command."""


class SolarFlowValidationError(SolarFlowError, ValueError):
    """A command argument is outside the supported range."""


class SolarFlowNotReadyError(SolarFlowError):
    """Controls are disabled or the session is not ``READY``."""


class SolarFlowDeviceError(SolarFlowError):
    """The device sent an ``error`` message.

    The client stores it in ``last_error`` instead of raising it.
    """
