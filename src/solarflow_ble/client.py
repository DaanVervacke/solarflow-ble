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
        """Initialize the client.

        Args:
            transport: BLE transport that owns the GATT connection.
            device_id: Require this deviceId in the BLESPP handshake. The
                reported identity is accepted when omitted.
            response_timeout: Seconds to wait for each device response.
            keepalive_seconds: Interval between keepalive read requests.
            ble_spp_delay: Pause after BLESPP_OK before the getInfo request.
            initial_read_delay: Pause between getInfo and the initial read.
            update_callback: Called with every decoded update after the
                handshake.
            connection_lost_callback: Called with the exception that ended
                a session.
            allow_control: Enable the control methods.
            model: Model key used to resolve validation limits.
            limits: Explicit validation bounds, overriding model resolution.
        """
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
        self._notification_queue: asyncio.Queue[
            tuple[bytes, asyncio.Future[None]] | None
        ] = asyncio.Queue()
        self._notification_worker: asyncio.Task[None] | None = None
        self._session_failure: Exception | None = None
        self._cleanup_done = False
        self._closing = False
        self._message_id = 1

    @property
    def connected(self) -> bool:
        """Whether the transport is connected, handshake pending or not."""
        return self.status is not ConnectionStatus.DISCONNECTED

    @property
    def protocol_ready(self) -> bool:
        """Whether the handshake completed, initial reports pending or not."""
        return self.status in (ConnectionStatus.PROTOCOL_READY, ConnectionStatus.READY)

    @property
    def ready(self) -> bool:
        """Whether the session is ready for control writes."""
        return self.status is ConnectionStatus.READY

    async def connect(self) -> None:
        """Connect and complete the protocol handshake.

        Connects the transport, subscribes to notifications, and drives the
        BLESPP handshake: BLESPP and BLESPP_OK, getInfo, then an initial
        getAll read. The session becomes ready once the first report
        arrives. A no-op when already connected. The client is reusable
        after ``disconnect()``.

        Raises:
            SolarFlowProtocolError: The handshake data is invalid or the
                device identity does not match ``device_id``.
            SolarFlowTimeoutError: A handshake step exceeds
                ``response_timeout``. The message quotes any device error
                recorded while waiting. Device errors themselves do not
                abort the handshake and remain available through
                ``last_error``.
        """
        async with self._lifecycle_lock:
            if self.connected:
                return
            self._reset_session()
            self._session_failure = None
            self.last_error = None
            self._cleanup_done = False
            self._reports_wait_active = True
            try:
                await self.transport.connect()
                self.status = ConnectionStatus.CONNECTED
                self._notification_queue = asyncio.Queue()
                self._notification_worker = asyncio.create_task(
                    self._consume_notifications(self._notification_queue)
                )
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
        """Disconnect and tear down the session.

        Cancels the keepalive, stops notifications, and disconnects the
        transport. Errors during teardown are suppressed. A no-op when
        already disconnected.
        """
        async with self._lifecycle_lock:
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
        await self._stop_notification_worker()
        self.status = ConnectionStatus.DISCONNECTED
        self._reset_session()

    async def _cleanup_transport(self) -> None:
        """Stop notifications and disconnect at most once per session.

        disconnect() and session-failure handling can race. The first
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
        self.state = SolarFlowState()
        self.device_id = self._target_device_id

    async def _handle_session_failure(self, error: Exception) -> None:
        """Fail the session, clean it up, and wake every pending consumer.

        Consumers blocked on the current queues are woken before cleanup
        replaces them. Consumers that start waiting later fail fast through
        the recorded session failure. A user-initiated disconnect owns
        cleanup and the lost-connection notice, so it returns early. The
        cleanup is best effort and skips cancelling the keepalive task when
        it runs inside that task.
        """
        if self._session_failure is not None or not self.connected:
            return
        self._session_failure = error
        self.status = ConnectionStatus.DISCONNECTED
        self._reports.put_nowait(_SessionClosed(error))
        self._write_results.put_nowait(_SessionClosed(error))
        if self._closing:
            return
        current = asyncio.current_task()
        keepalive = self._keepalive_task
        if keepalive is not None and keepalive is not current:
            keepalive.cancel()
            await asyncio.gather(keepalive, return_exceptions=True)
        self._keepalive_task = None
        await self._cleanup_transport()
        await self._stop_notification_worker()
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
        if self._notification_worker is None:
            await self._process_notification(payload)
            return
        done = asyncio.get_running_loop().create_future()
        await self._notification_queue.put((payload, done))
        await done

    async def _consume_notifications(
        self, queue: asyncio.Queue[tuple[bytes, asyncio.Future[None]] | None]
    ) -> None:
        while True:
            item = await queue.get()
            if item is None:
                return
            payload, done = item
            try:
                await self._process_notification(payload)
            except asyncio.CancelledError:
                if not done.done():
                    done.set_exception(
                        SolarFlowConnectionError(
                            "The SolarFlow session closed while processing "
                            "a notification"
                        )
                    )
                raise
            except Exception as err:
                _LOGGER.exception("SolarFlow notification processing failed")
                if not done.done():
                    done.set_exception(err)
            else:
                if not done.done():
                    done.set_result(None)

    async def _stop_notification_worker(self) -> None:
        """Stop the notification worker after draining queued payloads.

        The sentinel wakes the worker, which applies everything already
        queued (matching the previous per-message tasks, which always ran
        to completion) and then exits. The reference is cleared first so
        notifications arriving after this point apply inline instead of
        queueing behind the sentinel. When the session failure itself is
        being handled inside the worker, the worker drains the queue and
        exits on its own.
        """
        worker = self._notification_worker
        self._notification_worker = None
        if worker is None:
            return
        self._notification_queue.put_nowait(None)
        if worker is not asyncio.current_task():
            await asyncio.gather(worker, return_exceptions=True)

    async def _process_notification(self, payload: bytes) -> None:
        try:
            message = decode_json(payload)
            method = message.get("method")
            if method != "BLESPP":
                self._validate_message_identity(message)
        except SolarFlowProtocolError as err:
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
        discarded. A device error recorded while waiting is quoted in
        the timeout message.
        """
        deadline = asyncio.get_running_loop().time() + self.response_timeout
        while True:
            self._raise_if_session_failed()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise SolarFlowTimeoutError(self._timeout_message(context))
            try:
                message = await asyncio.wait_for(queue.get(), remaining)
            except TimeoutError as err:
                raise SolarFlowTimeoutError(self._timeout_message(context)) from err
            if isinstance(message, _SessionClosed):
                self._raise_session_closed(message, sentinel_context or context)
            if accept(message):
                return message

    def _timeout_message(self, context: str) -> str:
        """Describe a wait timeout, quoting the last recorded device error."""
        message = f"Timed out waiting for {context}"
        if self.last_error is not None:
            message = f"{message} (last device error: {self.last_error})"
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

    async def _wait_for_get_info(self) -> None:
        await self._wait_for_response(
            self._reports,
            context="getInfo-rsp",
            accept=lambda message: message.get("method") == "getInfo-rsp",
        )

    async def _wait_for_initial_reports(self) -> None:
        await self._wait_for_response(
            self._reports,
            context="initial report state",
            sentinel_context="initial reports",
            accept=lambda message: message.get("method") == "report",
        )

    def _refresh_status(self) -> None:
        if self.status is ConnectionStatus.DISCONNECTED or not self.protocol_ready:
            return
        self.status = ConnectionStatus.READY

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
        """Set the solar input power limit.

        Args:
            value: Limit in watts, between 0 and the model's
                ``max_input_power_w`` (2400 for the SolarFlow 2400AC
                default).

        Raises:
            SolarFlowValidationError: The value is outside the model
                bounds.
            SolarFlowNotReadyError: Controls are disabled or the session
                is not ready.
            SolarFlowConnectionError: The write failed or the session
                failed while waiting for the acknowledgement.
            SolarFlowTimeoutError: The acknowledgement exceeds
                ``response_timeout``.
            SolarFlowCommandError: The device rejected the write.
        """
        limits = self._resolve_limits()
        self._validate_limit(value, limits.max_input_power_w, "Input power limit")
        await self._request_write("inputLimit", value)

    async def set_output_limit(self, value: int) -> None:
        """Set the AC/battery output power limit.

        Args:
            value: Limit in watts, between 0 and the model's
                ``max_output_power_w`` (2400 for the SolarFlow 2400AC
                default).

        Raises:
            SolarFlowValidationError: The value is outside the model
                bounds.
            SolarFlowNotReadyError: Controls are disabled or the session
                is not ready.
            SolarFlowConnectionError: The write failed or the session
                failed while waiting for the acknowledgement.
            SolarFlowTimeoutError: The acknowledgement exceeds
                ``response_timeout``.
            SolarFlowCommandError: The device rejected the write.
        """
        limits = self._resolve_limits()
        self._validate_limit(value, limits.max_output_power_w, "Output power limit")
        await self._request_write("outputLimit", value)

    async def set_min_soc(self, value: int) -> None:
        """Set the minimum state-of-charge reserve.

        The device stores the value as per-mille, so the percent value
        given here is multiplied by 10 on the wire.

        Args:
            value: Minimum SOC in percent, between 0 and the model's
                ``max_min_soc`` (50 for the SolarFlow 2400AC default).

        Raises:
            SolarFlowValidationError: The value is outside the model
                bounds.
            SolarFlowNotReadyError: Controls are disabled or the session
                is not ready.
            SolarFlowConnectionError: The write failed or the session
                failed while waiting for the acknowledgement.
            SolarFlowTimeoutError: The acknowledgement exceeds
                ``response_timeout``.
            SolarFlowCommandError: The device rejected the write.
        """
        limits = self._resolve_limits()
        if not 0 <= value <= limits.max_min_soc:
            raise SolarFlowValidationError(
                f"Minimum SOC must be between 0 and {limits.max_min_soc} percent"
            )
        await self._request_write("minSoc", value * 10)

    async def set_soc(self, value: int) -> None:
        """Set the target state of charge.

        The device stores the value as per-mille, so the percent value
        given here is multiplied by 10 on the wire.

        Args:
            value: Target SOC in percent, between the model's
                ``min_target_soc`` (70 for the SolarFlow 2400AC default)
                and 100.

        Raises:
            SolarFlowValidationError: The value is outside the model
                bounds.
            SolarFlowNotReadyError: Controls are disabled or the session
                is not ready.
            SolarFlowConnectionError: The write failed or the session
                failed while waiting for the acknowledgement.
            SolarFlowTimeoutError: The acknowledgement exceeds
                ``response_timeout``.
            SolarFlowCommandError: The device rejected the write.
        """
        limits = self._resolve_limits()
        if not limits.min_target_soc <= value <= 100:
            raise SolarFlowValidationError(
                f"Maximum SOC must be between {limits.min_target_soc} and 100 percent"
            )
        await self._request_write("socSet", value * 10)

    async def set_ac_mode(self, value: AcMode | int) -> None:
        """Set the AC operating mode.

        Args:
            value: ``AcMode.CHARGING`` (1) or ``AcMode.DISCHARGING`` (2).
                Plain ints are accepted.

        Raises:
            SolarFlowValidationError: The value is not a known AC mode.
            SolarFlowNotReadyError: Controls are disabled or the session
                is not ready.
            SolarFlowConnectionError: The write failed or the session
                failed while waiting for the acknowledgement.
            SolarFlowTimeoutError: The acknowledgement exceeds
                ``response_timeout``.
            SolarFlowCommandError: The device rejected the write.
        """
        try:
            mode = AcMode(value)
        except ValueError as err:
            valid = " or ".join(str(member.value) for member in AcMode)
            raise SolarFlowValidationError(f"AC mode must be {valid}") from err
        await self._request_write("acMode", int(mode))

    async def _keepalive(self) -> None:
        """Send periodic read requests so a dropped link surfaces as a failed write.

        The transport offers no disconnect notification, so an abrupt BLE
        disconnect shows up through the next failing write, at most one
        keepalive interval after the link drops (30 seconds by default).
        """
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
