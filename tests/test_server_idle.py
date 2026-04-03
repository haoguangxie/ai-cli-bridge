import signal

import pytest

import server


@pytest.fixture(autouse=True)
def reset_server_state():
    server._shutdown_requested = False
    server._last_activity_monotonic = 100.0
    server._inflight_requests = 0
    yield
    server._shutdown_requested = False
    server._last_activity_monotonic = 100.0
    server._inflight_requests = 0


def test_request_lifecycle_refreshes_activity_and_tracks_inflight():
    server._begin_request(now=12.5)
    assert server._inflight_requests == 1
    assert server._last_activity_monotonic == 12.5

    server._end_request(now=18.0)
    assert server._inflight_requests == 0
    assert server._last_activity_monotonic == 18.0


def test_should_trigger_idle_shutdown_requires_no_inflight():
    assert server._should_trigger_idle_shutdown(
        200.0,
        last_activity_monotonic=100.0,
        inflight_requests=0,
        idle_timeout_seconds=60.0,
    )
    assert not server._should_trigger_idle_shutdown(
        200.0,
        last_activity_monotonic=100.0,
        inflight_requests=1,
        idle_timeout_seconds=60.0,
    )
    assert not server._should_trigger_idle_shutdown(
        120.0,
        last_activity_monotonic=100.0,
        inflight_requests=0,
        idle_timeout_seconds=60.0,
    )


@pytest.mark.asyncio
async def test_monitor_server_idle_triggers_signal_when_quiet(monkeypatch):
    raised = []

    async def fake_sleep(_seconds):
        return None

    def fake_raise_signal(signum):
        raised.append(signum)
        server._shutdown_requested = True

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(server.signal, "raise_signal", fake_raise_signal)
    server._last_activity_monotonic = 0.0

    await server._monitor_server_idle(10.0, poll_interval=0.0)

    expected = signal.SIGTERM if hasattr(signal, "SIGTERM") else signal.SIGINT
    assert raised == [expected]


@pytest.mark.asyncio
async def test_monitor_server_idle_does_not_interrupt_inflight_request(monkeypatch):
    raised = []
    sleeps = 0

    async def fake_sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 2:
            server._shutdown_requested = True

    def fake_raise_signal(signum):
        raised.append(signum)

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(server.signal, "raise_signal", fake_raise_signal)
    server._last_activity_monotonic = 0.0
    server._inflight_requests = 1

    await server._monitor_server_idle(10.0, poll_interval=0.0)

    assert raised == []
