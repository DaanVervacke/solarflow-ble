"""Shared fakes for the SolarFlow BLE test suite."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from solarflow_ble import (
    NotificationCallback,
    SolarFlowClient,
)
from solarflow_ble.const import NOTIFY_CHARACTERISTIC_UUID


def stub(**attributes: Any) -> Any:
    """Build a duck-typed stand-in that type checks as ``Any``."""
    return SimpleNamespace(**attributes)


class FakeManager:
    """Fake ESPHome API connection manager."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class FakeBluetoothManager:
    """Fake habluetooth manager without scanners."""

    async def async_setup(self) -> None:
        pass

    def async_current_scanners(self) -> list[Any]:
        return []

    def async_stop(self) -> None:
        pass


class FakeTransport:
    def __init__(self, write_response: int | None = None) -> None:
        self.writes: list[bytes] = []
        self.events: list[str] = []
        self.callback: NotificationCallback | None = None
        self.write_response = write_response
        self.write_started = asyncio.Event()
        self.write_ack_gate: asyncio.Event | None = None
        self.read_event = asyncio.Event()
        self.keepalive_read = asyncio.Event()
        self.read_count = 0
        self.block_keepalive = False
        self.keepalive_started = asyncio.Event()
        self.keepalive_cancelled = asyncio.Event()
        self.keepalive_gate = asyncio.Event()

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def start_notify(
        self,
        characteristic: str,
        callback: NotificationCallback,
    ) -> None:
        assert characteristic == NOTIFY_CHARACTERISTIC_UUID
        self.callback = callback
        self.events.append("BLESPP")
        await self._emit(callback, b'{"method":"BLESPP","deviceId":"DEVICE-1"}')

    async def stop_notify(self, characteristic: str) -> None:
        pass

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        self.writes.append(data)
        message = json.loads(data)
        self.events.append(message["method"])
        callback = self.callback
        assert callback is not None
        if message["method"] == "getInfo":
            self.events.append("getInfo-rsp")
            await self._emit(callback, b'{"method":"getInfo-rsp"}')
        elif message["method"] == "read":
            self.read_count += 1
            self.read_event.set()
            if self.read_count > 1:
                self.keepalive_read.set()
            if self.block_keepalive:
                self.keepalive_started.set()
                try:
                    await self.keepalive_gate.wait()
                except asyncio.CancelledError:
                    self.keepalive_cancelled.set()
                    raise
                return
            self.events.extend(("read_reply", "report"))
            await self._emit(callback, b'{"method":"read_reply","success":false}')
            await self._emit(
                callback, b'{"method":"report","properties":{"smartMode":1}}'
            )
        elif message["method"] == "write":
            self.write_started.set()
            gate = self.write_ack_gate
            if gate is not None:
                await gate.wait()
            if self.write_response is not None:
                await self._emit(
                    callback,
                    json.dumps(
                        {
                            "method": "report",
                            "messageId": message["messageId"],
                            "properties": {
                                **message["properties"],
                                "writeRsp": self.write_response,
                            },
                        }
                    ).encode(),
                )

    @staticmethod
    async def _emit(callback: NotificationCallback, payload: bytes) -> None:
        result = callback(NOTIFY_CHARACTERISTIC_UUID, payload)
        if result is not None:
            await result


class ReportTransport(FakeTransport):
    """Fake transport that emits captured-style reports."""

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        self.writes.append(data)
        callback = self.callback
        if callback is None:
            return
        message = json.loads(data)
        if message["method"] == "getInfo":
            await self._emit(callback, b'{"method":"getInfo-rsp","messageId":1}')
        elif message["method"] == "read":
            await self._emit(callback, b'{"method":"read_reply"}')
            await self._emit(
                callback,
                b'{"method":"report","messageId":1,"properties":{"smartMode":0,"electricLevel":26}}',
            )
            await self._emit(
                callback,
                b'{"method":"report","messageId":1,"packData":[{"sn":"PACK-1","socLevel":25},{"sn":"PACK-2","socLevel":26}]}',
            )
        if callback is not None and b'"method":"read"' in data:
            await self._emit(
                callback, b'{"messageId":"11","properties":{"smartMode":1}}'
            )


class FailingTransport(FakeTransport):
    def __init__(self, failure: str) -> None:
        super().__init__()
        self.failure = failure
        self.stop_notify_calls = 0
        self.disconnect_calls = 0

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def stop_notify(self, characteristic: str) -> None:
        self.stop_notify_calls += 1

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        self.writes.append(data)
        message = json.loads(data)
        if self.failure == "getInfo":
            return
        if message["method"] == "getInfo":
            assert self.callback is not None
            if self.failure in {"getInfo_error", "getInfo_error_then_ok"}:
                await self._emit(
                    self.callback, b'{"method":"error","data":[{"code":40}]}'
                )
                if self.failure == "getInfo_error":
                    return
            await self._emit(self.callback, b'{"method":"getInfo-rsp"}')
        if self.failure == "initial" and message["method"] == "read":
            assert self.callback is not None
            await self._emit(self.callback, b'{"method":"error","data":[{"code":40}]}')
        if self.failure == "getInfo_error_then_ok" and message["method"] == "read":
            assert self.callback is not None
            await self._emit(
                self.callback, b'{"method":"report","properties":{"electricLevel":80}}'
            )


class PreHandshakeTimeoutTransport(FailingTransport):
    async def start_notify(
        self,
        characteristic: str,
        callback: NotificationCallback,
    ) -> None:
        assert characteristic == NOTIFY_CHARACTERISTIC_UUID
        self.callback = callback


class LifecycleTransport(FakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.connect_calls = 0
        self.start_notify_calls = 0
        self.connect_started = asyncio.Event()
        self.connect_gate = asyncio.Event()

    async def connect(self) -> None:
        self.connect_calls += 1
        self.connect_started.set()
        await self.connect_gate.wait()

    async def start_notify(
        self,
        characteristic: str,
        callback: NotificationCallback,
    ) -> None:
        self.start_notify_calls += 1
        await super().start_notify(characteristic, callback)


class SessionTransport(FakeTransport):
    def __init__(self, sessions: list[dict[str, Any]]) -> None:
        super().__init__()
        self.sessions = sessions
        self.session_index = -1
        self.connect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        self.session_index += 1

    async def start_notify(
        self,
        characteristic: str,
        callback: NotificationCallback,
    ) -> None:
        assert characteristic == NOTIFY_CHARACTERISTIC_UUID
        self.callback = callback
        session = self.sessions[self.session_index]
        await self._emit(
            callback,
            json.dumps({"method": "BLESPP", "deviceId": session["device_id"]}).encode(),
        )

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        self.writes.append(data)
        message = json.loads(data)
        session = self.sessions[self.session_index]
        callback = self.callback
        assert callback is not None
        if message["method"] == "getInfo":
            if not session.get("fail_get_info", False):
                await self._emit(callback, b'{"method":"getInfo-rsp"}')
        elif message["method"] == "read":
            await self._emit(callback, b'{"method":"read_reply"}')
            await self._emit(
                callback,
                json.dumps(
                    {
                        "method": "report",
                        "properties": {"smartMode": session["smart_mode"]},
                        "packData": [{"sn": session["pack_serial"]}],
                    }
                ).encode(),
            )


class LinkLossTransport(FakeTransport):
    """Fake transport that fails reads once BLE link loss is armed."""

    def __init__(self, write_response: int | None = None) -> None:
        super().__init__(write_response=write_response)
        self.fail_reads = asyncio.Event()

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        message = json.loads(data)
        if message["method"] == "read" and self.fail_reads.is_set():
            raise RuntimeError("BLE link lost")
        await super().write_gatt_char(characteristic, data, response)


class WriteFailureTransport(FakeTransport):
    """Fake transport whose control writes fail at the link."""

    def __init__(self) -> None:
        super().__init__()
        self.stop_notify_calls = 0
        self.disconnect_calls = 0

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def stop_notify(self, characteristic: str) -> None:
        self.stop_notify_calls += 1

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        message = json.loads(data)
        if message["method"] == "write":
            raise RuntimeError("BLE write failed")
        await super().write_gatt_char(characteristic, data, response)


class CleanupCountingTransport(FakeTransport):
    """Fake transport that counts cleanup calls and can gate stop_notify."""

    def __init__(self) -> None:
        super().__init__()
        self.stop_notify_calls = 0
        self.disconnect_calls = 0
        self.stop_notify_started = asyncio.Event()
        self.stop_notify_gate: asyncio.Event | None = None

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def stop_notify(self, characteristic: str) -> None:
        self.stop_notify_calls += 1
        self.stop_notify_started.set()
        gate = self.stop_notify_gate
        if gate is not None:
            await gate.wait()


class NoSmartModeTransport(FakeTransport):
    """Fake transport whose reports never include smartMode."""

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        self.writes.append(data)
        callback = self.callback
        assert callback is not None
        message = json.loads(data)
        if message["method"] == "getInfo":
            await self._emit(callback, b'{"method":"getInfo-rsp"}')
        elif message["method"] == "read":
            await self._emit(callback, b'{"method":"read_reply"}')
            await self._emit(
                callback, b'{"method":"report","properties":{"electricLevel":26}}'
            )


class SilentReadTransport(FakeTransport):
    """Fake transport that answers the handshake but never reports."""

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        self.writes.append(data)
        callback = self.callback
        assert callback is not None
        message = json.loads(data)
        if message["method"] == "getInfo":
            await self._emit(callback, b'{"method":"getInfo-rsp"}')


async def connected_control_client(
    write_response: int | None = 0,
    **client_kwargs: Any,
) -> tuple[FakeTransport, SolarFlowClient]:
    transport = FakeTransport(write_response=write_response)
    client = SolarFlowClient(
        transport,
        response_timeout=0.05,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
        allow_control=True,
        **client_kwargs,
    )
    await client.connect()
    return transport, client
