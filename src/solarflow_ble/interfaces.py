"""Transport interface that the client drives."""

from collections.abc import Awaitable, Callable
from typing import Protocol

NotificationCallback = Callable[[str, bytes], Awaitable[None] | None]
"""Transport callback taking the characteristic UUID and the raw payload."""


class BleTransport(Protocol):
    """Minimal BLE transport supplied by the caller."""

    async def connect(self) -> None:
        """Establish the GATT connection."""

    async def disconnect(self) -> None:
        """Tear down the GATT connection."""

    async def start_notify(
        self, characteristic: str, callback: NotificationCallback
    ) -> None:
        """Subscribe to notifications on ``characteristic``."""

    async def stop_notify(self, characteristic: str) -> None:
        """Unsubscribe from notifications on ``characteristic``."""

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        """Write ``data`` to ``characteristic``."""
