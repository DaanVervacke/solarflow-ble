"""Typed SolarFlow BLE client."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from types import TracebackType
from typing import Any, NoReturn, Protocol, Self

from .const import (
    BLESPP_OK_MESSAGE_ID,
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
from .limits import DEFAULT_LIMITS, MODEL_LIMITS, SolarFlowLimits
from .models import AcMode, ConnectionStatus, SolarFlowState, SolarFlowUpdate
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
        model: str | None = None,
        limits: SolarFlowLimits | None = None,
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
        self.model = model
        self.limits = limits
        self.state = SolarFlowState()
        self.status = ConnectionStatus.DISCONNECTED
        self.last_error: SolarFlowDeviceError | None = None
        self._lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._warned_unknown_model = False
        self._target_device_id = device_id
        self._reports: asyncio.Queue[dict[str, Any] | _SessionClosed] = asyncio.Queue()
        self._write_results: asyncio.Queue[dict[str, Any] | _SessionClosed] = (
            asyncio.Queue()
        )
        self._reports_wait_active = False
        self._keepalive_task: asyncio.Task[None] | None = None
        self._processing_chain: asyncio.Task[None] | None = None
        self._session_failure: Exception | None = None
        self._cleanup_done = False
        self._closing = False
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
            self._cleanup_done = False
            # Queue reports only while the handshake waits consume them:
            # afterwards state is applied and delivered through
            # update_callback, so a device reporting every few seconds
            # cannot grow the queue without bound. Anything left in the
            # queue from an earlier session is stale before the window
            # opens.
            while not self._reports.empty():
                self._reports.get_nowait()
            self._reports_wait_active = True
            try:
                await self.transport.connect()
                self.status = ConnectionStatus.CONNECTED
                await self.transport.start_notify(
                    NOTIFY_CHARACTERISTIC_UUID, self._notification
                )
                ble_spp = await self._wait_for_method("BLESPP")
                self._establish_identity(ble_spp)
                await self._write(
                    {"messageId": BLESPP_OK_MESSAGE_ID, "method": "BLESPP_OK"}
                )
                await asyncio.sleep(self.ble_spp_delay)
                await self._write(self._build_request("getInfo"))
                await self._wait_for_get_info()
                self.status = ConnectionStatus.PROTOCOL_READY
                await asyncio.sleep(self.initial_read_delay)
                await self._write(
                    self._build_request("read", {"properties": ["getAll"]})
                )
                await self._wait_for_initial_reports()
                self._reports_wait_active = False
                self._refresh_status()
                self._keepalive_task = asyncio.create_task(self._keepalive())
            except BaseException:
                await self._disconnect_locked()
                raise

    async def disconnect(self) -> None:
        async with self._lifecycle_lock:
            # Mark the close as user-initiated so a session failure that
            # races this disconnect leaves the cleanup and the
            # connection-lost notification to it.
            self._closing = True
            try:
                await self._disconnect_locked()
            finally:
                self._closing = False

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.disconnect()

    async def _disconnect_locked(self) -> None:
        if self._keepalive_task:
            self._keepalive_task.cancel()
            await asyncio.gather(self._keepalive_task, return_exceptions=True)
            self._keepalive_task = None
        await self._cleanup_transport()
        self.status = ConnectionStatus.DISCONNECTED
        self._reset_session()

    async def _cleanup_transport(self) -> None:
        """Stop notifications and disconnect at most once per session.

        disconnect() and session-failure handling can race; the first
        path to reach the transport performs the teardown and the other
        becomes a no-op.
        """
        if self._cleanup_done:
            return
        self._cleanup_done = True
        with suppress(Exception):
            await self.transport.stop_notify(NOTIFY_CHARACTERISTIC_UUID)
        with suppress(Exception):
            await self.transport.disconnect()

    def _reset_session(self) -> None:
        self._reports = asyncio.Queue()
        self._write_results = asyncio.Queue()
        self._reports_wait_active = False
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
        if self._closing:
            # A user-initiated disconnect is already tearing the session
            # down; leave the cleanup and the connection-lost notification
            # to it.
            return
        # Best-effort cleanup, mirroring _disconnect_locked. The keepalive
        # task cannot be cancelled and awaited when this runs inside it.
        current = asyncio.current_task()
        keepalive = self._keepalive_task
        if keepalive is not None and keepalive is not current:
            keepalive.cancel()
            await asyncio.gather(keepalive, return_exceptions=True)
        self._keepalive_task = None
        await self._cleanup_transport()
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
            await self._enqueue_report(message)
            return
        await self._apply_message(message, method)
        self._refresh_status()
        callback = self.update_callback
        if callback:
            try:
                await callback(SolarFlowUpdate(self.state, self.status, message))
            except Exception:
                _LOGGER.exception("SolarFlow update callback failed")

    async def _enqueue_report(self, message: dict[str, Any]) -> None:
        """Queue a report for a handshake wait to consume.

        Report messages are queued only while the handshake waits are
        actively consuming the queue: afterwards nothing drains it, so
        an always-reporting device would grow it without bound.
        """
        if self._reports_wait_active:
            await self._reports.put(message)

    async def _apply_message(self, message: dict[str, Any], method: str | None) -> None:
        if method in {"getInfo-rsp", "read_reply"}:
            await self._enqueue_report(message)
        elif method == "report":
            properties = message.get("properties")
            if isinstance(properties, dict) and "writeRsp" in properties:
                await self._write_results.put(message)
            self.state = self.state.with_report(message)
            await self._enqueue_report(message)
        elif method == "error":
            if self.connected:
                self.last_error = SolarFlowDeviceError(
                    f"SolarFlow reported error: {message.get('data')}"
                )
                _LOGGER.warning("SolarFlow device error: %s", message.get("data"))
            await self._enqueue_report(message)

    async def _write(self, message: dict[str, Any]) -> None:
        await self.transport.write_gatt_char(
            WRITE_CHARACTERISTIC_UUID, encode_json(message), response=False
        )

    async def _wait_for_response(
        self,
        queue: asyncio.Queue[dict[str, Any] | _SessionClosed],
        *,
        context: str,
        accept: Callable[[dict[str, Any]], bool],
        sentinel_context: str | None = None,
    ) -> dict[str, Any]:
        """Wait for one queue message that ``accept`` approves.

        Owns the deadline handling shared by every pending wait: raises
        SolarFlowTimeoutError when the response deadline passes and
        SolarFlowConnectionError when the session fails or closes while
        waiting. Messages that ``accept`` rejects are consumed and
        discarded; ``accept`` may raise to surface a matched error.
        """
        deadline = asyncio.get_running_loop().time() + self.response_timeout
        while True:
            self._raise_if_session_failed()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise SolarFlowTimeoutError(f"Timed out waiting for {context}")
            try:
                message = await asyncio.wait_for(queue.get(), remaining)
            except TimeoutError as err:
                raise SolarFlowTimeoutError(f"Timed out waiting for {context}") from err
            if isinstance(message, _SessionClosed):
                self._raise_session_closed(message, sentinel_context or context)
            if accept(message):
                return message

    async def _wait_for_method(self, method: str) -> dict[str, Any]:
        return await self._wait_for_response(
            self._reports,
            context=method,
            accept=lambda message: message.get("method") == method,
        )

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
        # 1009 is reserved for the BLESPP_OK handshake; never hand it out here.
        while self._message_id == BLESPP_OK_MESSAGE_ID:
            self._message_id += 1
        message_id = self._message_id
        self._message_id += 1
        return message_id

    def _build_request(
        self, method: str, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build an outbound request envelope with the session identity."""
        message: dict[str, Any] = {
            "deviceId": self._require_device_id(),
            "messageId": self._next_message_id(),
            "timestamp": int(time.time() * 1000),
            "method": method,
        }
        if extra is not None:
            message.update(extra)
        return message

    @staticmethod
    def _accept_method_or_device_error(message: dict[str, Any], method: str) -> bool:
        """Accept ``method``, raising when the device reported an error."""
        if message.get("method") == "error":
            raise SolarFlowDeviceError(
                f"SolarFlow reported error: {message.get('data')}"
            )
        return message.get("method") == method

    async def _wait_for_get_info(self) -> None:
        await self._wait_for_response(
            self._reports,
            context="getInfo-rsp",
            accept=lambda message: self._accept_method_or_device_error(
                message, "getInfo-rsp"
            ),
        )

    async def _wait_for_initial_reports(self) -> None:
        await self._wait_for_response(
            self._reports,
            context="initial report state",
            sentinel_context="initial reports",
            accept=lambda message: self._accept_method_or_device_error(
                message, "report"
            ),
        )

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
            request = self._build_request(
                "write", {"properties": {property_name: value}}
            )
            try:
                await self._write(request)
            except Exception as err:
                await self._handle_session_failure(err)
                raise SolarFlowConnectionError(
                    f"Writing {property_name} to SolarFlow failed"
                ) from err
            message_id = request["messageId"]

            def accept(response: dict[str, Any]) -> bool:
                properties = response.get("properties")
                if not isinstance(properties, dict) or "writeRsp" not in properties:
                    return False
                # Correlate the acknowledgement with this request so a
                # late writeRsp from an earlier, timed-out write cannot
                # satisfy this wait.
                echoed_id = response.get("messageId")
                correlated = property_name in properties or (
                    echoed_id is not None and str(echoed_id) == str(message_id)
                )
                if not correlated:
                    return False
                if properties["writeRsp"] != 0:
                    raise SolarFlowCommandError(
                        f"SolarFlow rejected {property_name} "
                        f"(writeRsp={properties['writeRsp']})"
                    )
                return True

            await self._wait_for_response(
                self._write_results,
                context=f"{property_name} acknowledgement",
                accept=accept,
            )

    async def set_input_limit(self, value: int) -> None:
        limits = self._resolve_limits()
        self._validate_limit(value, limits.max_input_power_w, "Input power limit")
        await self._request_write("inputLimit", value)

    async def set_output_limit(self, value: int) -> None:
        limits = self._resolve_limits()
        self._validate_limit(value, limits.max_output_power_w, "Output power limit")
        await self._request_write("outputLimit", value)

    async def set_min_soc(self, value: int) -> None:
        limits = self._resolve_limits()
        if not 0 <= value <= limits.max_min_soc:
            raise SolarFlowValidationError(
                f"Minimum SOC must be between 0 and {limits.max_min_soc} percent"
            )
        await self._request_write("minSoc", value * 10)

    async def set_soc(self, value: int) -> None:
        limits = self._resolve_limits()
        if not limits.min_target_soc <= value <= 100:
            raise SolarFlowValidationError(
                f"Maximum SOC must be between {limits.min_target_soc} and 100 percent"
            )
        await self._request_write("socSet", value * 10)

    async def set_ac_mode(self, value: AcMode | int) -> None:
        try:
            mode = AcMode(value)
        except ValueError as err:
            valid = " or ".join(str(member.value) for member in AcMode)
            raise SolarFlowValidationError(f"AC mode must be {valid}") from err
        await self._request_write("acMode", int(mode))

    async def _keepalive(self) -> None:
        # An abrupt BLE disconnect surfaces through the next failing write,
        # at most one keepalive interval after the link drops (30 seconds
        # by default); the transport offers no disconnect notification.
        try:
            while True:
                await asyncio.sleep(self.keepalive_seconds)
                async with self._lock:
                    await self._write(
                        self._build_request("read", {"properties": ["getAll"]})
                    )
        except Exception as err:  # noqa: BLE001 - any transport error ends the session
            await self._handle_session_failure(err)

    @staticmethod
    def _validate_limit(value: int, maximum: int, label: str) -> None:
        if not 0 <= value <= maximum:
            raise SolarFlowValidationError(f"{label} must be between 0 and {maximum} W")

    def _resolve_limits(self) -> SolarFlowLimits:
        """Resolve validation bounds for the current device.

        Resolution order: explicit ``limits``, then the registry entry for
        the resolved model identity (the ``model`` constructor argument,
        or the ``productKey`` the device reports once known), then the
        verified SolarFlow 2400AC default with a one-time warning.
        """
        if self.limits is not None:
            return self.limits
        identity = self.model if self.model is not None else self.state.product_key
        if identity is not None:
            limits = MODEL_LIMITS.get(identity.lower())
            if limits is not None:
                return limits
        if not self._warned_unknown_model:
            self._warned_unknown_model = True
            _LOGGER.warning(
                "SolarFlow model %s has no verified limits; assuming SolarFlow "
                "2400AC control bounds, pass model= or limits= to override",
                identity if identity is not None else "(unreported)",
            )
        return DEFAULT_LIMITS
