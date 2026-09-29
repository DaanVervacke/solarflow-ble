"""Typed SolarFlow BLE client."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, NoReturn, Protocol

from .const import (
    DEFAULT_BLE_SPP_DELAY,
    DEFAULT_KEEPALIVE_SECONDS,
    DEFAULT_RESPONSE_TIMEOUT,
    NOTIFY_CHARACTERISTIC_UUID,
    WRITE_CHARACTERISTIC_UUID,
)
from .exceptions import (
    SolarFlowCommandError,
    SolarFlowConnectionError,
    SolarFlowDeviceError,
    SolarFlowNotReadyError,
    SolarFlowProtocolError,
    SolarFlowTimeoutError,
    SolarFlowValidationError,
)
from .models import ConnectionStatus, SolarFlowState, SolarFlowUpdate
from .protocol import decode_json, encode_json

NotificationCallback = Callable[[str, bytes], Awaitable[None] | None]
UpdateCallback = Callable[[SolarFlowUpdate], Awaitable[None]]
ConnectionLostCallback = Callable[[Exception], Awaitable[None] | None]
_LOGGER = logging.getLogger(__name__)


class _SessionClosed:
    """Sentinel pushed into session queues to wake blocked consumers."""

    __slots__ = ("error",)

    def __init__(self, error: Exception) -> None:
        self.error = error


class BleTransport(Protocol):
    """Minimal BLE transport supplied by the caller."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def start_notify(
        self, characteristic: str, callback: NotificationCallback
    ) -> None: ...
    async def stop_notify(self, characteristic: str) -> None: ...
    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None: ...


class SolarFlowClient:
    """Communicate with one SolarFlow controller."""

    def __init__(
        self,
        transport: BleTransport,
        *,
        device_id: str | None = None,
        response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
        keepalive_seconds: float = DEFAULT_KEEPALIVE_SECONDS,
        ble_spp_delay: float = DEFAULT_BLE_SPP_DELAY,
        initial_read_delay: float = 0.3,
        update_callback: UpdateCallback | None = None,
        connection_lost_callback: ConnectionLostCallback | None = None,
        allow_control: bool = False,
    ) -> None:
        self.transport = transport
        self.device_id = device_id
        self.response_timeout = response_timeout
        self.keepalive_seconds = keepalive_seconds
        self.ble_spp_delay = ble_spp_delay
        self.initial_read_delay = initial_read_delay
        self.update_callback = update_callback
        self.connection_lost_callback = connection_lost_callback
        self.allow_control = allow_control
        self.state = SolarFlowState()
        self.status = ConnectionStatus.DISCONNECTED
        self.last_error: SolarFlowDeviceError | None = None
        self._lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._target_device_id = device_id
        self._reports: asyncio.Queue[dict[str, Any] | _SessionClosed] = asyncio.Queue()
        self._write_results: asyncio.Queue[dict[str, Any] | _SessionClosed] = (
            asyncio.Queue()
        )
        self._keepalive_task: asyncio.Task[None] | None = None
        self._processing_chain: asyncio.Task[None] | None = None
        self._session_failure: Exception | None = None
        self._message_id = 1

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
        async with self._lifecycle_lock:
            if self.connected:
                return
            self._reset_session()
            self._session_failure = None
            self.last_error = None
            try:
                await self.transport.connect()
                self.status = ConnectionStatus.CONNECTED
                await self.transport.start_notify(
                    NOTIFY_CHARACTERISTIC_UUID, self._notification
                )
                ble_spp = await self._wait_for_method("BLESPP")
                self._establish_identity(ble_spp)
                await self._write({"messageId": 1009, "method": "BLESPP_OK"})
                await asyncio.sleep(self.ble_spp_delay)
                timestamp = int(time.time() * 1000)
                await self._write(
                    {
                        "deviceId": self._require_device_id(),
                        "messageId": self._next_message_id(),
                        "method": "getInfo",
                        "timestamp": timestamp,
                    }
                )
                await self._wait_for_method("getInfo-rsp")
                self.status = ConnectionStatus.PROTOCOL_READY
                await asyncio.sleep(self.initial_read_delay)
                await self._write(
                    {
                        "deviceId": self._require_device_id(),
                        "messageId": self._next_message_id(),
                        "timestamp": int(time.time() * 1000),
                        "properties": ["getAll"],
                        "method": "read",
                    }
                )
                await self._wait_for_initial_reports()
                self._refresh_status()
                self._keepalive_task = asyncio.create_task(self._keepalive())
            except BaseException:
                await self._disconnect_locked()
                raise

    async def disconnect(self) -> None:
        async with self._lifecycle_lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        if self._keepalive_task:
            self._keepalive_task.cancel()
            await asyncio.gather(self._keepalive_task, return_exceptions=True)
            self._keepalive_task = None
        with suppress(Exception):
            await self.transport.stop_notify(NOTIFY_CHARACTERISTIC_UUID)
        with suppress(Exception):
            await self.transport.disconnect()
        self.status = ConnectionStatus.DISCONNECTED
        self._reset_session()

    def _reset_session(self) -> None:
        self._reports = asyncio.Queue()
        self._write_results = asyncio.Queue()
        self._processing_chain = None
        self.state = SolarFlowState()
        self.device_id = self._target_device_id

    async def _handle_session_failure(self, error: Exception) -> None:
        """Fail the session, clean it up, and wake every pending consumer."""
        if self._session_failure is not None or not self.connected:
            return
        self._session_failure = error
        self.status = ConnectionStatus.DISCONNECTED
        # Wake consumers blocked on the current queues before cleanup
        # replaces them; consumers that start waiting later fail fast
        # through the recorded session failure instead.
        self._reports.put_nowait(_SessionClosed(error))
        self._write_results.put_nowait(_SessionClosed(error))
        # Best-effort cleanup, mirroring _disconnect_locked. The keepalive
        # task cannot be cancelled and awaited when this runs inside it.
        current = asyncio.current_task()
        keepalive = self._keepalive_task
        if keepalive is not None and keepalive is not current:
            keepalive.cancel()
            await asyncio.gather(keepalive, return_exceptions=True)
        self._keepalive_task = None
        with suppress(Exception):
            await self.transport.stop_notify(NOTIFY_CHARACTERISTIC_UUID)
        with suppress(Exception):
            await self.transport.disconnect()
        self._reset_session()
        _LOGGER.warning("SolarFlow session failed: %s", error)
        callback = self.connection_lost_callback
        if callback is None:
            return
        try:
            result = callback(error)
            if result is not None:
                await result
        except Exception:
            _LOGGER.exception("SolarFlow connection lost callback failed")

    def _raise_if_session_failed(self) -> None:
        failure = self._session_failure
        if failure is not None:
            raise SolarFlowConnectionError(
                f"The SolarFlow session failed: {failure}"
            ) from failure

    @staticmethod
    def _raise_session_closed(sentinel: _SessionClosed, waiting_for: str) -> NoReturn:
        raise SolarFlowConnectionError(
            f"The SolarFlow session failed while waiting for {waiting_for}"
        ) from sentinel.error

    async def _notification(self, _characteristic: str, payload: bytes) -> None:
        # Notifications are processed in arrival order: each processing task
        # waits for the previous one, so state updates and update callbacks
        # never overlap or reorder. Awaiting our own task keeps direct
        # awaited calls synchronous for inline transports and tests.
        previous = self._processing_chain
        task = asyncio.create_task(self._process_notification(payload, previous))
        self._processing_chain = task
        await task

    async def _process_notification(
        self, payload: bytes, previous: asyncio.Task[None] | None
    ) -> None:
        if previous is not None:
            try:
                await previous
            except asyncio.CancelledError:
                raise
            except Exception:
                # The failed notification already surfaced its error; keep
                # delivering later notifications in order.
                _LOGGER.exception("SolarFlow notification processing failed")
        try:
            message = decode_json(payload)
            method = message.get("method")
            if method != "BLESPP":
                self._validate_message_identity(message)
        except SolarFlowProtocolError as err:
            # Protocol errors in notifications are session failures, not
            # swallowed background task exceptions.
            await self._handle_session_failure(err)
            return
        if method == "BLESPP":
            await self._reports.put(message)
            return
        await self._apply_message(message, method)
        self._refresh_status()
        callback = self.update_callback
        if callback:
            try:
                await callback(SolarFlowUpdate(self.state, self.status, message))
            except Exception:
                _LOGGER.exception("SolarFlow update callback failed")

    async def _apply_message(self, message: dict[str, Any], method: str | None) -> None:
        if method in {"getInfo-rsp", "read_reply"}:
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
            if self.connected:
                self.last_error = SolarFlowDeviceError(
                    f"SolarFlow reported error: {message.get('data')}"
                )
                _LOGGER.warning("SolarFlow device error: %s", message.get("data"))
            await self._reports.put(message)

    async def _write(self, message: dict[str, Any]) -> None:
        await self.transport.write_gatt_char(
            WRITE_CHARACTERISTIC_UUID, encode_json(message), response=False
        )

    async def _wait_for_method(self, method: str) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + self.response_timeout
        while True:
            self._raise_if_session_failed()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise SolarFlowTimeoutError(f"Timed out waiting for {method}")
            try:
                message = await asyncio.wait_for(self._reports.get(), remaining)
            except TimeoutError as err:
                raise SolarFlowTimeoutError(f"Timed out waiting for {method}") from err
            if isinstance(message, _SessionClosed):
                self._raise_session_closed(message, method)
            if message.get("method") == method:
                return message

    def _establish_identity(self, message: dict[str, Any]) -> None:
        device_id = message.get("deviceId")
        if not isinstance(device_id, str) or not device_id:
            raise SolarFlowProtocolError("BLESPP did not include deviceId")
        if self._target_device_id is not None and self._target_device_id != device_id:
            raise SolarFlowProtocolError(
                "BLESPP deviceId does not match the requested device"
            )
        self.device_id = device_id
        self.state = self.state.with_identity({"deviceId": device_id})

    def _validate_message_identity(self, message: dict[str, Any]) -> None:
        message_device_id = message.get("deviceId")
        if message_device_id is None or self.device_id is None:
            return
        if message_device_id != self.device_id:
            raise SolarFlowProtocolError(
                "SolarFlow message deviceId does not match the connected device"
            )

    def _require_device_id(self) -> str:
        if self.device_id is None:
            raise SolarFlowProtocolError("SolarFlow device identity is not established")
        return self.device_id

    def _next_message_id(self) -> int:
        while self._message_id == 1009:
            self._message_id += 1
        message_id = self._message_id
        self._message_id += 1
        return message_id

    async def _wait_for_initial_reports(self) -> None:
        deadline = asyncio.get_running_loop().time() + self.response_timeout
        while True:
            self._raise_if_session_failed()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise SolarFlowTimeoutError(
                    "Timed out waiting for initial report state"
                )
            try:
                message = await asyncio.wait_for(self._reports.get(), remaining)
            except TimeoutError as err:
                raise SolarFlowTimeoutError(
                    "Timed out waiting for initial report state"
                ) from err
            if isinstance(message, _SessionClosed):
                self._raise_session_closed(message, "initial reports")
            if message.get("method") == "error":
                raise SolarFlowDeviceError(
                    f"SolarFlow reported error: {message.get('data')}"
                )
            if message.get("method") == "report":
                return

    def _refresh_status(self) -> None:
        if self.status is ConnectionStatus.DISCONNECTED or not self.protocol_ready:
            return
        self.status = (
            ConnectionStatus.READY
            if self.state.smart_mode == 1
            else ConnectionStatus.PROTOCOL_READY
        )

    async def _request_write(self, property_name: str, value: int) -> None:
        if not self.allow_control:
            raise SolarFlowNotReadyError(
                "SolarFlow controls are disabled for this session"
            )
        if not self.ready:
            raise SolarFlowNotReadyError("SolarFlow controls are not ready")
        async with self._lock:
            self._raise_if_session_failed()
            timestamp = int(time.time() * 1000)
            try:
                await self._write(
                    {
                        "method": "write",
                        "timestamp": timestamp,
                        "deviceId": self._require_device_id(),
                        "messageId": self._next_message_id(),
                        "properties": {property_name: value},
                    }
                )
            except Exception as err:
                await self._handle_session_failure(err)
                raise SolarFlowConnectionError(
                    f"Writing {property_name} to SolarFlow failed"
                ) from err
            deadline = asyncio.get_running_loop().time() + self.response_timeout
            while True:
                self._raise_if_session_failed()
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise SolarFlowTimeoutError(
                        f"Timed out waiting for {property_name} acknowledgement"
                    )
                try:
                    response = await asyncio.wait_for(
                        self._write_results.get(), remaining
                    )
                except TimeoutError as err:
                    raise SolarFlowTimeoutError(
                        f"Timed out waiting for {property_name} acknowledgement"
                    ) from err
                if isinstance(response, _SessionClosed):
                    self._raise_session_closed(
                        response, f"{property_name} acknowledgement"
                    )
                properties = response.get("properties")
                if isinstance(properties, dict) and "writeRsp" in properties:
                    if properties["writeRsp"] != 0:
                        raise SolarFlowCommandError(
                            f"SolarFlow rejected {property_name}"
                        )
                    return

    async def set_input_limit(self, value: int) -> None:
        self._validate_limit(value)
        await self._request_write("inputLimit", value)

    async def set_output_limit(self, value: int) -> None:
        self._validate_limit(value)
        await self._request_write("outputLimit", value)

    async def set_min_soc(self, value: int) -> None:
        if not 0 <= value <= 50:
            raise SolarFlowValidationError(
                "Minimum SOC must be between 0 and 50 percent"
            )
        await self._request_write("minSoc", value * 10)

    async def set_soc(self, value: int) -> None:
        if not 70 <= value <= 100:
            raise SolarFlowValidationError(
                "Maximum SOC must be between 70 and 100 percent"
            )
        await self._request_write("socSet", value * 10)

    async def set_ac_mode(self, value: int) -> None:
        if value not in (1, 2):
            raise SolarFlowValidationError("AC mode must be 1 or 2")
        await self._request_write("acMode", value)

    async def _keepalive(self) -> None:
        # An abrupt BLE disconnect surfaces through the next failing write,
        # at most one keepalive interval after the link drops (30 seconds
        # by default); the transport offers no disconnect notification.
        try:
            while True:
                await asyncio.sleep(self.keepalive_seconds)
                async with self._lock:
                    await self._write(
                        {
                            "deviceId": self._require_device_id(),
                            "messageId": self._next_message_id(),
                            "timestamp": int(time.time() * 1000),
                            "properties": ["getAll"],
                            "method": "read",
                        }
                    )
        except Exception as err:  # noqa: BLE001 - any transport error ends the session
            await self._handle_session_failure(err)

    @staticmethod
    def _validate_limit(value: int) -> None:
        if not 0 <= value <= 2400:
            raise SolarFlowValidationError("Power limit must be between 0 and 2400 W")
