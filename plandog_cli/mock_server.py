"""Mock plandog server for testing plandog-cli without a real BlueprintAgent."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import random
import zipfile
from datetime import datetime
from typing import Optional

import websockets

# ── Protocol constants (duplicated to avoid dependency on plandog package) ──

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

# ── Fixture data ─────────────────────────────────────────────────────────────

VALID_KEY = "test-key"

_DUMMY_SESSIONS = [
    {
        "session_id": "aaaa-1111-bbbb-2222",
        "work_dir": "/tmp/mock/aaaa",
        "last_activity": "2026-03-01T10:00:00",
        "connections": 0,
    },
    {
        "session_id": "cccc-3333-dddd-4444",
        "work_dir": "/tmp/mock/cccc",
        "last_activity": "2026-03-02T15:30:00",
        "connections": 1,
    },
]

# Simulated response scenarios
_CHAT_SCENARIO = [
    ("thinking", None),
    ("tool", "프로젝트 분석"),
    ("tool", "✓ 분석 완료"),
    ("chunk", "안녕하세요! PlanDog Mock 서버입니다.\n"),
    ("chunk", "블루프린트 작업을 도와드리겠습니다. "),
    ("chunk", "무엇을 도와드릴까요?\n"),
    ("done", None),
]

_SESSION_HISTORY = [
    {"role": "user", "text": "이전 대화 내용입니다."},
    {"role": "assistant", "text": "이전 에이전트 응답입니다. 프로젝트를 분석했습니다."},
]

# ── Connection state ──────────────────────────────────────────────────────────

class MockSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.connections: set = set()
        self.message_log: list[dict] = []
        self.has_changes: bool = False


_sessions: dict[str, MockSession] = {}


async def _send(ws, data: dict) -> None:
    try:
        await ws.send(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


async def _broadcast(session: MockSession, data: dict, exclude=None) -> None:
    dead = set()
    for ws in list(session.connections):
        if ws is exclude:
            continue
        try:
            await ws.send(json.dumps(data, ensure_ascii=False))
        except Exception:
            dead.add(ws)
    session.connections -= dead


def _make_dummy_zip() -> str:
    """Create a minimal zip with a placeholder file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("README.md", "# Mock Blueprint\n\nThis is a mock download.\n")
        zf.writestr("screens/SCR-001.md", "# 로그인 화면\n")
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def _simulate_chat(session: MockSession, user_text: str, cancel_event: asyncio.Event) -> None:
    """Stream a scripted response to all connections in the session."""
    session.has_changes = True
    for event_type, event_text in _CHAT_SCENARIO:
        if cancel_event.is_set():
            await _broadcast(session, {"type": MSG_CANCELLED})
            return
        msg: dict = {"type": event_type}
        if event_text is not None:
            msg["text"] = event_text
        await _broadcast(session, msg)
        await asyncio.sleep(0.05)


async def _handle_connection(ws) -> None:
    session: Optional[MockSession] = None

    try:
        # Auth
        await _send(ws, {"type": MSG_AUTH_REQUIRED})
        raw = await asyncio.wait_for(ws.recv(), timeout=30)
        msg = json.loads(raw)

        if msg.get("type") != MSG_AUTH or msg.get("api_key") != VALID_KEY:
            await _send(ws, {"type": MSG_AUTH_ERROR, "message": "유효하지 않은 API 키입니다"})
            return

        # Session list
        await _send(ws, {"type": MSG_SESSIONS, "sessions": _DUMMY_SESSIONS})

        # Session select/new
        raw = await asyncio.wait_for(ws.recv(), timeout=60)
        msg = json.loads(raw)
        msg_type = msg.get("type")

        if msg_type == MSG_SESSION_SELECT:
            session_id = msg.get("session_id")
            if session_id not in _sessions:
                # Create it from dummy data
                _sessions[session_id] = MockSession(session_id)
                _sessions[session_id].message_log = list(_SESSION_HISTORY)
            session = _sessions[session_id]
            session.connections.add(ws)
            await _send(ws, {"type": MSG_HISTORY, "messages": list(session.message_log)})

        elif msg_type == MSG_SESSION_NEW:
            import uuid
            session_id = str(uuid.uuid4())
            session = MockSession(session_id)
            _sessions[session_id] = session
            session.connections.add(ws)
            await _send(ws, {"type": MSG_HISTORY, "messages": []})

        else:
            await _send(ws, {"type": MSG_ERROR, "message": "session_select 또는 session_new가 필요합니다"})
            return

        # Chat loop
        while True:
            try:
                raw = await ws.recv()
            except Exception:
                break

            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == MSG_DISCONNECT:
                break

            elif msg_type == MSG_MESSAGE:
                user_text = msg.get("text", "").strip()
                if not user_text:
                    continue

                await _broadcast(session, {"type": MSG_MESSAGE, "text": user_text}, exclude=ws)
                session.message_log.append({"role": "user", "text": user_text})

                cancel_event = asyncio.Event()
                # Stream without a concurrent receiver to avoid consuming subsequent messages
                await _simulate_chat(session, user_text, cancel_event)

            elif msg_type == MSG_DOWNLOAD:
                if not session.has_changes:
                    await _send(ws, {"type": MSG_DOWNLOAD_NONE})
                else:
                    await _send(ws, {
                        "type": MSG_DOWNLOAD_DATA,
                        "filename": f"mock-session-{session.session_id[:8]}.zip",
                        "data": _make_dummy_zip(),
                    })
                    session.has_changes = False

            elif msg_type == MSG_SESSION_CLOSE:
                force = msg.get("force", False)
                if not force and session.has_changes and random.random() < 0.5:
                    await _send(ws, {
                        "type": MSG_CLOSE_CONFIRM_NEEDED,
                        "message": "다운로드되지 않은 작업이 있습니다. force: true로 재전송하면 강제 종료합니다.",
                    })
                    continue

                await _broadcast(session, {
                    "type": MSG_SESSION_CLOSED,
                    "session_id": session.session_id,
                })
                _sessions.pop(session.session_id, None)
                return

    except Exception as e:
        pass
    finally:
        if session is not None:
            session.connections.discard(ws)


async def serve(host: str = "localhost", port: int = 9999) -> None:
    """Start the mock server."""
    print(f"[mock-server] Listening on ws://{host}:{port}")
    print(f"[mock-server] Valid API key: {VALID_KEY}")
    async with websockets.serve(_handle_connection, host, port, max_size=None):
        await asyncio.Future()  # run forever


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="plandog-cli mock server")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()

    try:
        asyncio.run(serve(args.host, args.port))
    except KeyboardInterrupt:
        print("\n[mock-server] Stopped.")


if __name__ == "__main__":
    main()
