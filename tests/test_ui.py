"""Unit tests for plandog-cli ui helpers: _consume_response, _listen_background."""

from __future__ import annotations

import asyncio

import pytest

from plandog_cli.ui import _consume_response, _listen_background


# ── Helpers ───────────────────────────────────────────────────────────────────


class _StreamClient:
    """Mock client whose stream_response() yields a fixed sequence of events."""

    def __init__(self, events):
        self._events = events

    async def stream_response(self):
        for event in self._events:
            yield event

    async def cancel(self):
        pass


class _RecvClient:
    """Mock client whose recv_event() pops from a list, then blocks indefinitely."""

    def __init__(self, events):
        self._events = list(events)

    async def recv_event(self):
        if self._events:
            return self._events.pop(0)
        await asyncio.sleep(3600)  # block until cancelled


class _MockDisplay:
    """Minimal StreamingDisplay stand-in that avoids live terminal rendering."""

    def __init__(self):
        self._spinner = None
        self._printed_header = False

    def start_thinking(self):
        self._spinner = True  # truthy sentinel

    def stop_thinking(self):
        self._spinner = None

    def finish(self):
        self._spinner = None


class _MockApp:
    """Minimal prompt_toolkit Application stand-in."""

    _is_running = True  # allow run_in_terminal path in tests

    def __init__(self):
        self.exit_called = False
        self.exit_result = object()  # sentinel — distinguishable from "" or None

    async def run_in_terminal(self, func):
        func()

    def exit(self, result=None):
        self.exit_called = True
        self.exit_result = result


# ── _consume_response ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consume_response_sets_flag_on_session_closed():
    """session_closed event → session_closed[0] becomes True."""
    client = _StreamClient([
        {"type": "thinking"},
        {"type": "chunk", "text": "응답"},
        {"type": "session_closed", "session_id": "s1"},
    ])
    session_closed = [False]

    await _consume_response(client, _MockDisplay(), session_closed)

    assert session_closed[0] is True


@pytest.mark.asyncio
async def test_consume_response_does_not_set_flag_on_done():
    """done event → session_closed flag stays False."""
    client = _StreamClient([
        {"type": "chunk", "text": "응답"},
        {"type": "done"},
    ])
    session_closed = [False]

    await _consume_response(client, _MockDisplay(), session_closed)

    assert session_closed[0] is False


@pytest.mark.asyncio
async def test_consume_response_does_not_set_flag_on_cancelled():
    """cancelled event → session_closed flag stays False."""
    client = _StreamClient([{"type": "cancelled"}])
    session_closed = [False]

    await _consume_response(client, _MockDisplay(), session_closed)

    assert session_closed[0] is False


@pytest.mark.asyncio
async def test_consume_response_no_crash_without_flag_arg():
    """_consume_response works when session_closed is not passed (default None)."""
    client = _StreamClient([{"type": "done"}])

    await _consume_response(client, _MockDisplay())  # no session_closed arg


# ── _listen_background ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listen_background_sets_flag_and_calls_app_exit(monkeypatch):
    """session_closed event → session_closed flag set and app.exit(result='') called."""
    import prompt_toolkit.application.current as _ptk_current

    app = _MockApp()
    monkeypatch.setattr(_ptk_current, "get_app_or_none", lambda: app)

    client = _RecvClient([{"type": "session_closed", "session_id": "s1"}])
    session_closed = [False]

    await _listen_background(client, session_closed, [False])

    assert session_closed[0] is True
    assert app.exit_called is True
    assert app.exit_result == ""


@pytest.mark.asyncio
async def test_listen_background_message_sets_remote_flag(monkeypatch):
    """message event → remote_responding flag set and app.exit() called immediately."""
    import prompt_toolkit.application.current as _ptk_current

    app = _MockApp()
    monkeypatch.setattr(_ptk_current, "get_app_or_none", lambda: app)

    client = _RecvClient([{"type": "message", "text": "다른 클라이언트"}])
    session_closed = [False]
    remote_responding = [False]

    await _listen_background(client, session_closed, remote_responding)

    assert remote_responding[0] is True
    assert session_closed[0] is False
    assert app.exit_called is True
    assert app.exit_result == ""


@pytest.mark.asyncio
async def test_listen_background_thinking_sets_remote_flag(monkeypatch):
    """thinking event (fallback) → remote_responding flag set and app.exit() called."""
    import prompt_toolkit.application.current as _ptk_current

    app = _MockApp()
    monkeypatch.setattr(_ptk_current, "get_app_or_none", lambda: app)

    client = _RecvClient([{"type": "thinking"}])
    session_closed = [False]
    remote_responding = [False]

    await _listen_background(client, session_closed, remote_responding)

    assert remote_responding[0] is True
    assert session_closed[0] is False
    assert app.exit_called is True
    assert app.exit_result == ""


@pytest.mark.asyncio
async def test_listen_background_no_flag_on_done():
    """done event does not set any flag; listener keeps running until cancelled."""
    client = _RecvClient([
        {"type": "chunk", "text": "응답"},
        {"type": "done"},
        # No more events → recv_event blocks; task must be cancelled externally
    ])
    session_closed = [False]
    remote_responding = [False]

    task = asyncio.create_task(_listen_background(client, session_closed, remote_responding))
    await asyncio.sleep(0.05)   # let it consume done and block on next recv
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session_closed[0] is False
    assert remote_responding[0] is False


@pytest.mark.asyncio
async def test_listen_background_session_closed_no_app():
    """session_closed with no running app → flag set, no crash."""
    client = _RecvClient([{"type": "session_closed", "session_id": "s1"}])
    session_closed = [False]

    await _listen_background(client, session_closed, [False])

    assert session_closed[0] is True


@pytest.mark.asyncio
async def test_listen_background_cancellable():
    """Background task can be cancelled while blocked on recv_event."""
    client = _RecvClient([])  # empty → blocks indefinitely in recv_event
    task = asyncio.create_task(_listen_background(client, [False], [False]))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
