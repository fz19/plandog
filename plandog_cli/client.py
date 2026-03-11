"""WebSocket client for plandog terminal server."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Optional


# Message type constants (mirrors plandog.server.protocol)
MSG_AUTH = "auth"
MSG_AUTH_REQUIRED = "auth_required"
MSG_AUTH_ERROR = "auth_error"
MSG_SESSIONS = "sessions"
MSG_SESSION_SELECT = "session_select"
MSG_SESSION_NEW = "session_new"
MSG_HISTORY = "history"
MSG_MESSAGE = "message"
MSG_CANCEL = "cancel"
MSG_DOWNLOAD = "download"
MSG_DOWNLOAD_DATA = "download_data"
MSG_DOWNLOAD_NONE = "download_none"
MSG_SESSION_CLOSE = "session_close"
MSG_SESSION_CLOSED = "session_closed"
MSG_CLOSE_CONFIRM_NEEDED = "close_confirm_needed"
MSG_DISCONNECT = "disconnect"
MSG_THINKING = "thinking"
MSG_TOOL = "tool"
MSG_CHUNK = "chunk"
MSG_DONE = "done"
MSG_CANCELLED = "cancelled"
MSG_AUTO_START = "auto_start"
MSG_AUTO_TURN = "auto_turn"
MSG_AUTO_TURN_DONE = "auto_turn_done"
MSG_AUTO_DONE = "auto_done"
MSG_ERROR = "error"


class PlandogClient:
    """
    Async WebSocket client for a plandog terminal server.

    Usage:
        async with PlandogClient(url, api_key) as client:
            sessions = await client.authenticate()
            await client.new_session()
            await client.send_message("hello")
    """

    def __init__(self, url: str, api_key: str, on_event: Optional[Callable[[dict], Any]] = None):
        self._url = url
        self._api_key = api_key
        self._ws = None
        self._on_event = on_event  # callback for all incoming messages
        self._receive_task: Optional[asyncio.Task] = None
        self._sessions: list[dict] = []
        self._session_id: Optional[str] = None
        self._pending: dict[str, asyncio.Queue] = {}  # type → Queue for specific reply waits
        self._closed = False

    async def connect(self) -> None:
        """Open WebSocket connection."""
        import websockets

        self._ws = await websockets.connect(
            self._url,
            max_size=None,
            ping_interval=20,
            ping_timeout=60,
        )

    async def disconnect(self) -> None:
        """Cleanly disconnect."""
        self._closed = True
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.send(json.dumps({"type": MSG_DISCONNECT}))
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    async def _send(self, data: dict) -> None:
        await self._ws.send(json.dumps(data, ensure_ascii=False))

    async def _recv(self) -> dict:
        raw = await self._ws.recv()
        return json.loads(raw)

    async def _wait_for(self, *msg_types: str, timeout: float = 30) -> dict:
        """Wait for a specific message type (or one of several types)."""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"Timed out waiting for {msg_types}")
            msg = await asyncio.wait_for(self._recv(), timeout=remaining)
            if msg.get("type") in msg_types:
                return msg
            # Deliver unexpected messages via callback
            if self._on_event:
                cb = self._on_event
                if asyncio.iscoroutinefunction(cb):
                    await cb(msg)
                else:
                    cb(msg)

    async def authenticate(self) -> list[dict]:
        """
        Perform auth handshake.
        Returns list of existing sessions for this API key.
        """
        msg = await self._wait_for(MSG_AUTH_REQUIRED, timeout=10)
        if msg.get("type") != MSG_AUTH_REQUIRED:
            raise RuntimeError(f"Expected auth_required, got: {msg}")

        await self._send({"type": MSG_AUTH, "api_key": self._api_key})

        reply = await self._wait_for(MSG_SESSIONS, MSG_AUTH_ERROR, timeout=10)
        if reply.get("type") == MSG_AUTH_ERROR:
            raise PermissionError(reply.get("message", "인증 실패"))

        self._sessions = reply.get("sessions", [])
        return self._sessions

    async def select_session(self, session_id: str) -> list[dict]:
        """Select an existing session. Returns message history."""
        await self._send({"type": MSG_SESSION_SELECT, "session_id": session_id})
        reply = await self._wait_for(MSG_HISTORY, MSG_ERROR, timeout=10)
        if reply.get("type") == MSG_ERROR:
            raise RuntimeError(reply.get("message", "세션 선택 실패"))
        self._session_id = session_id
        return reply.get("messages", [])

    async def new_session(self, upload: Optional[str] = None) -> list[dict]:
        """Start a new session. Returns empty history."""
        payload: dict = {"type": MSG_SESSION_NEW}
        if upload:
            payload["upload"] = upload
        await self._send(payload)
        reply = await self._wait_for(MSG_HISTORY, MSG_ERROR, timeout=10)
        if reply.get("type") == MSG_ERROR:
            raise RuntimeError(reply.get("message", "세션 생성 실패"))
        return reply.get("messages", [])

    async def send_message(self, text: str) -> None:
        """Send a chat message."""
        await self._send({"type": MSG_MESSAGE, "text": text})

    async def cancel(self) -> None:
        """Send cancel signal."""
        await self._send({"type": MSG_CANCEL})

    async def request_download(self) -> Optional[dict]:
        """Request a download. Returns download_data or download_none message."""
        await self._send({"type": MSG_DOWNLOAD})
        return await self._wait_for(MSG_DOWNLOAD_DATA, MSG_DOWNLOAD_NONE, MSG_ERROR, timeout=60)

    async def close_session(self, force: bool = False) -> dict:
        """Close current session. Returns session_closed or close_confirm_needed."""
        await self._send({"type": MSG_SESSION_CLOSE, "force": force})
        return await self._wait_for(
            MSG_SESSION_CLOSED, MSG_CLOSE_CONFIRM_NEEDED, MSG_ERROR, timeout=10
        )

    async def recv_event(self) -> dict:
        """Receive one event from the server."""
        return await self._recv()

    async def stream_response(self) -> AsyncIterator:
        """
        Async generator that yields events until done/cancelled/error.
        Yields dicts with type in: thinking, tool, chunk, done, cancelled, error.
        """
        while True:
            msg = await self._recv()
            t = msg.get("type")
            yield msg
            if t in (MSG_DONE, MSG_CANCELLED, MSG_SESSION_CLOSED):
                return


# Python 3.11+ AsyncIterator type alias
from typing import AsyncIterator  # noqa: E402
