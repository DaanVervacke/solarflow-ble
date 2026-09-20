"""Typed SolarFlow BLE client."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol

from .const import (
    DEFAULT_KEEPALIVE_SECONDS,
    DEFAULT_RESPONSE_TIMEOUT,
    NOTIFY_CHARACTERISTIC_UUID,
    WRITE_CHARACTERISTIC_UUID,
)
from .exceptions import (
    SolarFlowCommandError,
    SolarFlowDeviceError,
    SolarFlowNotReadyError,
    SolarFlowTimeoutError,
    SolarFlowValidationError,
)
from .models import ConnectionStatus, SolarFlowState, SolarFlowUpdate
from .protocol import decode_json, encode_json

NotificationCallback = Callable[[str, bytes], None | Awaitable[None]]
UpdateCallback = Callable[[SolarFlowUpdate], Awaitable[None]]
_LOGGER = logging.getLogger(__name__)


class BleTransport(Protocol):
    """Minimal BLE transport supplied by the caller."""
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def start_notify(self, characteristic: str, callback: NotificationCallback) -> None: ...
    async def stop_notify(self, characteristic: str) -> None: ...
    async def write_gatt_char(self, characteristic: str, data: bytes, response: bool = False) -> None: ...


class SolarFlowClient:
    """Communicate with one SolarFlow controller."""

    def __init__(self, transport: BleTransport, *, response_timeout: float = DEFAULT_RESPONSE_TIMEOUT, keepalive_seconds: float = DEFAULT_KEEPALIVE_SECONDS, ble_spp_delay: float = 0.5, initial_read_delay: float = 0.3, update_callback: UpdateCallback | None = None, allow_control: bool = False) -> None:
        self.transport = transport
        self.response_timeout = response_timeout
        self.keepalive_seconds = keepalive_seconds
        self.ble_spp_delay = ble_spp_delay
        self.initial_read_delay = initial_read_delay
        self.update_callback = update_callback
        self.allow_control = allow_control
        self.state = SolarFlowState()
        self.status = ConnectionStatus.DISCONNECTED
        self._lock = asyncio.Lock()
        self._reports: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._write_results: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._keepalive_task: asyncio.Task[None] | None = None

    @property
    def connected(self) -> bool:
        return self.status is not ConnectionStatus.DISCONNECTED

    @property
    def protocol_ready(self) -> bool:
        return self.status in (ConnectionStatus.PROTOCOL_READY, ConnectionStatus.READY)

    @property
    def ready(self) -> bool:
        return self.status is ConnectionStatus.READY

    async def connect(self) -> None:
        try:
            await self.transport.connect()
            self.status = ConnectionStatus.CONNECTED
            await self.transport.start_notify(NOTIFY_CHARACTERISTIC_UUID, self._notification)
            await self._write({"messageId": "1009", "method": "BLESPP_OK"})
            await asyncio.sleep(self.ble_spp_delay)
            timestamp = int(time.time() * 1000)
            await self._write({"messageId": str(timestamp), "method": "getInfo", "timestamp": timestamp})
            await self._wait_for_method("getInfo-rsp")
            self.status = ConnectionStatus.PROTOCOL_READY
            await asyncio.sleep(self.initial_read_delay)
            await self._write({"messageId": "11", "timestamp": int(time.time() * 1000), "properties": ["getAll"], "method": "read"})
            await self._wait_for_initial_reports()
            self._refresh_status()
            self._keepalive_task = asyncio.create_task(self._keepalive())
        except BaseException:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        if self._keepalive_task:
            self._keepalive_task.cancel()
            await asyncio.gather(self._keepalive_task, return_exceptions=True)
            self._keepalive_task = None
        with suppress(Exception):
            await self.transport.stop_notify(NOTIFY_CHARACTERISTIC_UUID)
        with suppress(Exception):
            await self.transport.disconnect()
        self.status = ConnectionStatus.DISCONNECTED

    async def _notification(self, _characteristic: str, payload: bytes) -> None:
        message = decode_json(payload)
        method = message.get("method")
        if method == "getInfo-rsp":
            await self._reports.put(message)
        elif method == "report":
            properties = message.get("properties")
            if isinstance(properties, dict):
                self.state = self.state.update(properties)
                self.state = self.state.with_identity(message)
                if "writeRsp" in properties:
                    await self._write_results.put(message)
            self.state = self.state.with_packs(message)
            await self._reports.put(message)
        elif method == "error":
            await self._reports.put(message)
        self._refresh_status()
        if self.update_callback:
            try:
                await self.update_callback(SolarFlowUpdate(self.state, self.status, message))
            except Exception:
                _LOGGER.exception("SolarFlow update callback failed")

    async def _write(self, message: dict[str, Any]) -> None:
        await self.transport.write_gatt_char(WRITE_CHARACTERISTIC_UUID, encode_json(message), response=False)

    async def _wait_for_method(self, method: str) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + self.response_timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise SolarFlowTimeoutError(f"Timed out waiting for {method}")
            try:
                message = await asyncio.wait_for(self._reports.get(), remaining)
            except TimeoutError as err:
                raise SolarFlowTimeoutError(f"Timed out waiting for {method}") from err
            if message.get("method") == method:
                return message

    async def _wait_for_initial_reports(self) -> None:
        deadline = asyncio.get_running_loop().time() + self.response_timeout
        while self.state.smart_mode is None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise SolarFlowTimeoutError("Timed out waiting for initial report state")
            try:
                message = await asyncio.wait_for(self._reports.get(), remaining)
            except TimeoutError as err:
                raise SolarFlowTimeoutError("Timed out waiting for initial report state") from err
            if message.get("method") == "error":
                raise SolarFlowDeviceError(f"SolarFlow reported error: {message.get('data')}")

    def _refresh_status(self) -> None:
        if self.status is ConnectionStatus.DISCONNECTED or not self.protocol_ready:
            return
        self.status = ConnectionStatus.READY if self.state.smart_mode == 1 else ConnectionStatus.PROTOCOL_READY

    async def _request_write(self, property_name: str, value: int) -> None:
        if not self.allow_control:
            raise SolarFlowNotReadyError("SolarFlow controls are disabled for this session")
        if not self.ready:
            raise SolarFlowNotReadyError("SolarFlow controls are not ready")
        async with self._lock:
            timestamp = int(time.time() * 1000)
            await self._write({"method": "write", "timestamp": timestamp, "messageId": str(timestamp), "properties": {property_name: value}})
            deadline = asyncio.get_running_loop().time() + self.response_timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise SolarFlowTimeoutError(f"Timed out waiting for {property_name} acknowledgement")
                try:
                    response = await asyncio.wait_for(self._write_results.get(), remaining)
                except TimeoutError as err:
                    raise SolarFlowTimeoutError(
                        f"Timed out waiting for {property_name} acknowledgement"
                    ) from err
                properties = response.get("properties")
                if isinstance(properties, dict) and "writeRsp" in properties:
                    if properties["writeRsp"] != 0:
                        raise SolarFlowCommandError(f"SolarFlow rejected {property_name}")
                    return

    async def set_input_limit(self, value: int) -> None:
        self._validate_limit(value)
        await self._request_write("inputLimit", value)

    async def set_output_limit(self, value: int) -> None:
        self._validate_limit(value)
        await self._request_write("outputLimit", value)

    async def set_min_soc(self, value: int) -> None:
        if not 0 <= value <= 50:
            raise SolarFlowValidationError("Minimum SOC must be between 0 and 50 percent")
        await self._request_write("minSoc", value * 10)

    async def set_soc(self, value: int) -> None:
        if not 70 <= value <= 100:
            raise SolarFlowValidationError("Maximum SOC must be between 70 and 100 percent")
        await self._request_write("socSet", value * 10)

    async def set_ac_mode(self, value: int) -> None:
        if value not in (1, 2):
            raise SolarFlowValidationError("AC mode must be 1 or 2")
        await self._request_write("acMode", value)

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(self.keepalive_seconds)
            async with self._lock:
                await self._write({"messageId": "11", "timestamp": int(time.time() * 1000), "properties": ["getAll"], "method": "read"})

    @staticmethod
    def _validate_limit(value: int) -> None:
        if not 0 <= value <= 2400:
            raise SolarFlowValidationError("Power limit must be between 0 and 2400 W")
