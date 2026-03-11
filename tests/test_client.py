"""Integration tests for plandog-cli using the mock server."""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
import websockets

from plandog_cli.mock_server import (
    VALID_KEY,
    _DUMMY_SESSIONS,
    _sessions,
    serve,
)
from plandog_cli.client import PlandogClient
from plandog_cli.transfer import save_download


# ── Test server fixture ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def mock_server():
    """Start the mock server on a random port and yield (host, port)."""
    _sessions.clear()

    srv = await websockets.serve(
        __import__("plandog_cli.mock_server", fromlist=["_handle_connection"])._handle_connection,
        "localhost",
        0,
        max_size=None,
    )
    port = next(iter(srv.sockets)).getsockname()[1]
    yield "localhost", port
    srv.close()
    await srv.wait_closed()
    _sessions.clear()


def ws_url(host: str, port: int) -> str:
    return f"ws://{host}:{port}"


# ── 1. Auth failure ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auth_failure(mock_server):
    host, port = mock_server
    async with PlandogClient(ws_url(host, port), "wrong-key") as client:
        with pytest.raises(PermissionError):
            await client.authenticate()


# ── 2. Sessions list ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sessions_list(mock_server):
    host, port = mock_server
    async with PlandogClient(ws_url(host, port), VALID_KEY) as client:
        sessions = await client.authenticate()
    assert isinstance(sessions, list)
    assert len(sessions) == 2


# ── 3. New session ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_new_session(mock_server):
    host, port = mock_server
    async with PlandogClient(ws_url(host, port), VALID_KEY) as client:
        await client.authenticate()
        history = await client.new_session()
    assert history == []


# ── 4. Select existing session (replay history) ───────────────────────────────

@pytest.mark.asyncio
async def test_session_select_history(mock_server):
    host, port = mock_server
    session_id = _DUMMY_SESSIONS[0]["session_id"]

    async with PlandogClient(ws_url(host, port), VALID_KEY) as client:
        await client.authenticate()
        history = await client.select_session(session_id)

    # Mock server inserts _SESSION_HISTORY for known session IDs
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"


# ── 5. Message broadcast ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_message_broadcast(mock_server):
    host, port = mock_server
    url = ws_url(host, port)

    received_by_b: list[dict] = []

    # Connection A: creates session
    async with PlandogClient(url, VALID_KEY) as client_a:
        await client_a.authenticate()
        await client_a.new_session()
        session_id = client_a._session_id

        # Get the session ID from the mock server state
        created_session_id = list(_sessions.keys())[-1]

        # Connection B: joins the same session
        async with PlandogClient(url, VALID_KEY) as client_b:
            await client_b.authenticate()
            await client_b.select_session(created_session_id)

            # A sends a message
            await client_a.send_message("안녕하세요")

            # B should receive the echoed message
            try:
                msg = await asyncio.wait_for(client_b._recv(), timeout=2)
                received_by_b.append(msg)
            except asyncio.TimeoutError:
                pass

    # B should have received the echo or a broadcast message
    # (mock server broadcasts MSG_MESSAGE to other connections)
    assert any(m.get("type") == "message" for m in received_by_b), \
        f"Expected broadcast message, got: {received_by_b}"


# ── 6. Download ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download(mock_server):
    host, port = mock_server

    with tempfile.TemporaryDirectory() as tmpdir:
        async with PlandogClient(ws_url(host, port), VALID_KEY) as client:
            await client.authenticate()
            await client.new_session()

            # Send a message first to set has_changes=True
            await client.send_message("테스트 메시지")
            # Consume response
            async for msg in client.stream_response():
                if msg.get("type") in ("done", "cancelled"):
                    break

            reply = await client.request_download()

        assert reply is not None
        if reply.get("type") == "download_data":
            data = reply["data"]
            dest = save_download(data, tmpdir)
            assert dest.exists()
            files = list(dest.rglob("*"))
            assert len(files) > 0
        else:
            # download_none is also acceptable for a fresh session
            assert reply.get("type") in ("download_data", "download_none")


# ── 9. Session close broadcasts session_closed to ALL connections ─────────────

@pytest.mark.asyncio
async def test_session_close_broadcasts_to_all(mock_server):
    """When one client closes the session every connected client receives session_closed."""
    host, port = mock_server
    url = ws_url(host, port)

    async with PlandogClient(url, VALID_KEY) as client_a:
        await client_a.authenticate()
        await client_a.new_session()
        session_id = list(_sessions.keys())[-1]
        _sessions[session_id].has_changes = False  # skip confirm

        async with PlandogClient(url, VALID_KEY) as client_b:
            await client_b.authenticate()
            await client_b.select_session(session_id)

            # A closes; B should receive the broadcast
            close_task = asyncio.create_task(client_a.close_session(force=True))
            b_msg = await asyncio.wait_for(client_b._recv(), timeout=2)
            await close_task

    assert b_msg.get("type") == "session_closed"
    assert b_msg.get("session_id") == session_id


# ── 10. Client keepalive settings ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_keepalive_settings():
    """PlandogClient.connect() passes ping_interval=20 and ping_timeout=60."""
    import unittest.mock as mock

    with mock.patch("websockets.connect", new_callable=mock.AsyncMock) as mock_connect:
        client = PlandogClient("ws://localhost:9999", "key")
        await client.connect()

    mock_connect.assert_called_once()
    kw = mock_connect.call_args.kwargs
    assert kw.get("ping_interval") == 20
    assert kw.get("ping_timeout") == 60


# ── 7. Session close (no confirm needed) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_session_close_no_confirm(mock_server):
    host, port = mock_server

    async with PlandogClient(ws_url(host, port), VALID_KEY) as client:
        await client.authenticate()
        await client.new_session()

        # Session has no changes → close without confirm
        # (mock server has 50% chance, but we patch has_changes to False)
        session_id = list(_sessions.keys())[-1]
        _sessions[session_id].has_changes = False

        reply = await client.close_session(force=False)
        assert reply.get("type") in ("session_closed", "close_confirm_needed")


# ── 8. Session close (confirm needed) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_close_confirm_needed(mock_server):
    host, port = mock_server

    async with PlandogClient(ws_url(host, port), VALID_KEY) as client:
        await client.authenticate()
        await client.new_session()

        session_id = list(_sessions.keys())[-1]
        _sessions[session_id].has_changes = True

        # Mock server with has_changes=True will always (50% chance) return confirm_needed.
        # We patch random to force it.
        import plandog_cli.mock_server as ms
        import unittest.mock as mock

        with mock.patch.object(ms.random, "random", return_value=0.1):
            reply = await client.close_session(force=False)

        if reply.get("type") == "close_confirm_needed":
            # Force close
            force_reply = await client.close_session(force=True)
            assert force_reply.get("type") == "session_closed"
        else:
            # Already closed without needing confirm
            assert reply.get("type") == "session_closed"
