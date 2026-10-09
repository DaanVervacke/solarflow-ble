from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

import pytest
from solarflow_ble import (
    ConnectionStatus,
    SolarFlowClient,
    SolarFlowUpdate,
)
from solarflow_ble.const import NOTIFY_CHARACTERISTIC_UUID
from solarflow_ble.exceptions import (
    SolarFlowConnectionError,
    SolarFlowDeviceError,
    SolarFlowTimeoutError,
)

from conftest import (
    FailingTransport,
    FakeTransport,
    connected_control_client,
)


@pytest.mark.asyncio
async def test_notification_worker_cancellation_fails_pending_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SolarFlowClient(FakeTransport())
    processing_started = asyncio.Event()

    async def slow_process(payload: bytes) -> None:
        processing_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(client, "_process_notification", slow_process)
    queue: asyncio.Queue[tuple[bytes, asyncio.Future[None]] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    worker = asyncio.create_task(client._consume_notifications(queue))
    pending = loop.create_future()
    await queue.put((b"{}", pending))
    await processing_started.wait()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert isinstance(pending.exception(), SolarFlowConnectionError)

    processing_started.clear()
    worker = asyncio.create_task(client._consume_notifications(queue))
    completed = loop.create_future()
    completed.set_result(None)
    await queue.put((b"{}", completed))
    await processing_started.wait()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert completed.exception() is None


@pytest.mark.asyncio
async def test_notification_worker_exception_with_completed_future_keeps_running(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SolarFlowClient(FakeTransport())

    async def failing_process(payload: bytes) -> None:
        raise RuntimeError("processing failed")

    monkeypatch.setattr(client, "_process_notification", failing_process)
    queue: asyncio.Queue[tuple[bytes, asyncio.Future[None]] | None] = asyncio.Queue()
    worker = asyncio.create_task(client._consume_notifications(queue))
    done = asyncio.get_running_loop().create_future()
    done.set_result(None)
    await queue.put((b"{}", done))
    await queue.put(None)
    await worker

    assert done.exception() is None
    assert any(
        "notification processing failed" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_notification_processing_failure_keeps_delivering_notifications(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, client = await connected_control_client()
    original = client._process_notification
    failures = iter((RuntimeError("processing failed"),))

    async def failing(payload: bytes) -> None:
        failure = next(failures, None)
        if failure is not None:
            raise failure
        await original(payload)

    monkeypatch.setattr(client, "_process_notification", failing)

    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(RuntimeError, match="processing failed"),
    ):
        await client._notification(
            NOTIFY_CHARACTERISTIC_UUID,
            b'{"method":"report","properties":{"electricLevel":10}}',
        )

    assert "SolarFlow notification processing failed" in caplog.text

    await client._notification(
        NOTIFY_CHARACTERISTIC_UUID,
        b'{"method":"report","properties":{"electricLevel":26}}',
    )

    assert client.state.electric_level == 26
    assert client.status is ConnectionStatus.READY
    await client.disconnect()


@pytest.mark.asyncio
async def test_update_callback_failure_is_logged_and_session_survives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def callback(update: SolarFlowUpdate) -> None:
        raise RuntimeError("callback failed")

    client = SolarFlowClient(
        FakeTransport(),
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
        update_callback=callback,
    )
    await client.connect()

    with caplog.at_level(logging.ERROR):
        await client._notification(
            NOTIFY_CHARACTERISTIC_UUID,
            b'{"method":"report","properties":{"electricLevel":26}}',
        )

    assert "SolarFlow update callback failed" in caplog.text
    assert client.state.electric_level == 26
    assert client.status is ConnectionStatus.READY
    await client.disconnect()


@pytest.mark.asyncio
async def test_update_callbacks_are_delivered_in_order_and_serialized() -> None:
    events: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def callback(update: SolarFlowUpdate) -> None:
        level = update.state.electric_level
        events.append(f"start:{level}")
        if level == 10:
            first_started.set()
            await release_first.wait()
        events.append(f"end:{level}")

    client = SolarFlowClient(
        FakeTransport(),
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        update_callback=callback,
    )
    await client.connect()
    events.clear()

    first = asyncio.create_task(
        client._notification(
            NOTIFY_CHARACTERISTIC_UUID,
            b'{"method":"report","properties":{"electricLevel":10}}',
        )
    )
    await asyncio.wait_for(first_started.wait(), 1)
    second = asyncio.create_task(
        client._notification(
            NOTIFY_CHARACTERISTIC_UUID,
            b'{"method":"report","properties":{"electricLevel":20}}',
        )
    )
    await asyncio.sleep(0.05)

    assert events == ["start:10"]
    assert client.state.electric_level == 10

    release_first.set()
    await asyncio.gather(first, second)

    assert events == ["start:10", "end:10", "start:20", "end:20"]
    assert client.state.electric_level == 20
    await client.disconnect()


@pytest.mark.asyncio
async def test_post_handshake_reports_do_not_grow_the_reports_queue() -> None:
    updates: list[SolarFlowUpdate] = []

    async def callback(update: SolarFlowUpdate) -> None:
        updates.append(update)

    client = SolarFlowClient(
        FakeTransport(),
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        initial_read_delay=0,
        update_callback=callback,
    )
    await client.connect()
    updates.clear()

    for _ in range(100):
        await client._notification(
            NOTIFY_CHARACTERISTIC_UUID,
            b'{"method":"report","properties":{"electricLevel":26}}',
        )

    assert client._reports.qsize() == 0
    assert len(updates) == 100
    assert all(update.raw_message["method"] == "report" for update in updates)
    await client.disconnect()


@pytest.mark.asyncio
async def test_notifications_are_drained_without_per_message_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []
    original_create_task = asyncio.create_task

    def spy_create_task(
        coro: Coroutine[Any, Any, Any], **kwargs: Any
    ) -> asyncio.Task[Any]:
        created.append(coro)
        return original_create_task(coro, **kwargs)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)
    _, client = await connected_control_client()

    baseline = len(created)
    for _ in range(10):
        await client._notification(
            NOTIFY_CHARACTERISTIC_UUID,
            b'{"method":"report","properties":{"electricLevel":26}}',
        )

    assert client.state.electric_level == 26
    assert len(created) == baseline
    await client.disconnect()


@pytest.mark.asyncio
async def test_notification_worker_is_torn_down_on_disconnect() -> None:
    _, client = await connected_control_client()

    assert client._notification_worker is not None
    await client._notification(
        NOTIFY_CHARACTERISTIC_UUID,
        b'{"method":"report","properties":{"electricLevel":26}}',
    )

    await client.disconnect()

    assert client._notification_worker is None
    assert client._notification_queue.empty()


@pytest.mark.asyncio
async def test_notification_worker_is_torn_down_after_failed_handshake() -> None:
    transport = FailingTransport("getInfo")
    client = SolarFlowClient(
        transport, response_timeout=0.01, ble_spp_delay=0, initial_read_delay=0
    )

    with pytest.raises(SolarFlowTimeoutError):
        await client.connect()

    assert client._notification_worker is None
    assert client._notification_queue.empty()


@pytest.mark.asyncio
async def test_disconnect_drains_queued_notifications_without_losing_waiters() -> None:
    _, client = await connected_control_client()

    pending = asyncio.create_task(
        client._notification(
            NOTIFY_CHARACTERISTIC_UUID,
            b'{"method":"report","properties":{"electricLevel":26}}',
        )
    )
    await asyncio.sleep(0)
    await client.disconnect()

    await asyncio.wait_for(pending, 1)


@pytest.mark.asyncio
async def test_mid_session_error_sets_last_error_and_reaches_callback() -> None:
    updates: list[SolarFlowUpdate] = []

    async def callback(update: SolarFlowUpdate) -> None:
        updates.append(update)

    client = SolarFlowClient(
        FakeTransport(),
        response_timeout=0.1,
        keepalive_seconds=60,
        ble_spp_delay=0,
        update_callback=callback,
    )
    await client.connect()
    updates.clear()

    await client._notification(
        NOTIFY_CHARACTERISTIC_UUID, b'{"method":"error","data":[{"code":40}]}'
    )

    assert isinstance(client.last_error, SolarFlowDeviceError)
    assert "40" in str(client.last_error)
    assert client.status is ConnectionStatus.READY
    assert updates[-1].raw_message["method"] == "error"
    assert updates[-1].status is ConnectionStatus.READY
    await client.disconnect()
