"""Terminal UI for plandog-cli (rich + prompt_toolkit)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

console = Console()


def print_sessions(sessions: list[dict]) -> None:
    """Print a table of existing sessions."""
    if not sessions:
        console.print("[dim]저장된 세션이 없습니다.[/dim]")
        return
    from rich.table import Table

    table = Table(title="기존 세션 목록")
    table.add_column("#", style="dim", width=3)
    table.add_column("세션 ID")
    table.add_column("마지막 활동")
    table.add_column("연결 수")
    for i, s in enumerate(sessions, 1):
        table.add_row(
            str(i),
            s.get("session_id", "")[:16] + "...",
            s.get("last_activity", "-"),
            str(s.get("connections", 0)),
        )
    console.print(table)


async def prompt_session_choice(sessions: list[dict]) -> Optional[str]:
    """
    Interactively ask the user to select an existing session or create a new one.
    Returns session_id string, or None to create a new session.
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML

    print_sessions(sessions)
    console.print()
    console.print("  [dim][N][/dim] 새 세션 시작")
    for i, s in enumerate(sessions, 1):
        sid = s.get("session_id", "")[:16]
        last = s.get("last_activity", "-")
        console.print(f"  [dim][{i}][/dim] 세션 {sid}... ({last})")
    console.print()

    prompt = PromptSession()
    while True:
        try:
            choice = await prompt.prompt_async(HTML("<b>선택 (N 또는 번호)</b>: "))
        except (EOFError, KeyboardInterrupt):
            return None

        choice = choice.strip().upper()
        if choice in ("N", ""):
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]["session_id"]
        except ValueError:
            pass
        console.print(f"[yellow]잘못된 입력입니다. N 또는 1~{len(sessions)} 사이의 숫자를 입력하세요.[/yellow]")


def render_event(event: dict) -> None:
    """Render a single server event to the terminal."""
    t = event.get("type")

    if t == "thinking":
        pass  # spinner handled by caller

    elif t == "tool":
        text = event.get("text", "")
        if text.startswith("✓"):
            console.print(f"  [green]{text}[/green]")
        elif text.startswith("✗"):
            console.print(f"  [red]{text}[/red]")
        elif text.startswith("↳"):
            console.print(f"    [dim]{text}[/dim]")
        else:
            console.print(f"  [dim]⚙ {text}[/dim]")

    elif t == "chunk":
        console.print(event.get("text", ""), end="", highlight=False)

    elif t == "done":
        console.print()

    elif t == "cancelled":
        console.print("\n[dim][응답이 취소되었습니다][/dim]")

    elif t == "error":
        console.print(f"\n[red]오류: {event.get('message', '')}[/red]")

    elif t == "auto_start":
        max_turns = event.get("max_turns", "?")
        console.print(f"\n[bold magenta]자동 진행 모드 시작 (최대 {max_turns}턴)[/bold magenta]")

    elif t == "auto_turn":
        turn = event.get("turn")
        max_turns = event.get("max_turns")
        console.print(f"\n[bold magenta][auto {turn}/{max_turns}][/bold magenta]")

    elif t == "auto_turn_done":
        pass

    elif t == "auto_done":
        console.print("[dim]자동 진행이 완료되었습니다.[/dim]")

    elif t == "message":
        # Echo of message sent by another connection
        console.print(f"\n[bold green][다른 터미널][/bold green]: {event.get('text', '')}")

    elif t == "session_closed":
        console.print("\n[bold yellow]세션이 종료되었습니다.[/bold yellow]")

    elif t == "close_confirm_needed":
        console.print(f"\n[yellow]{event.get('message', '다운로드되지 않은 작업이 있습니다.')}[/yellow]")

    elif t == "download_none":
        console.print("[dim]다운로드할 파일이 없습니다.[/dim]")

    elif t == "download_data":
        filename = event.get("filename", "download.zip")
        console.print(f"[green]다운로드 준비: {filename}[/green]")


class StreamingDisplay:
    """Context manager that shows a spinner while waiting and then prints streamed text."""

    def __init__(self):
        self._spinner: Optional[Live] = None
        self._first_chunk = True
        self._printed_header = False

    def start_thinking(self) -> None:
        spin = Spinner("dots", text=Text("생각하는 중...  [dim](Ctrl+C: 취소)[/dim]", style="cyan"))
        self._spinner = Live(spin, console=console, refresh_per_second=10)
        self._spinner.__enter__()
        self._first_chunk = True
        self._printed_header = False

    def stop_thinking(self) -> None:
        if self._spinner:
            self._spinner.__exit__(None, None, None)
            self._spinner = None

    def on_event(self, event: dict) -> None:
        t = event.get("type")
        if t == "thinking":
            self.start_thinking()
            return
        if t in ("chunk", "tool") and self._spinner:
            self.stop_thinking()
            if not self._printed_header and t == "chunk":
                console.print("[bold blue]PlanDog[/bold blue]: ", end="")
                self._printed_header = True
        render_event(event)

    def finish(self) -> None:
        self.stop_thinking()


async def _listen_background(
    client, session_closed: list, remote_responding: list
) -> None:
    """Receive and render server-pushed events while waiting for user input.

    Must be created as an asyncio Task from *within* pre_run so it inherits
    the prompt_toolkit ContextVar that holds the running Application.
    run_in_terminal() then erases the prompt, renders via the module-level
    rich Console (ANSI intact), and redraws the prompt with the user's
    in-progress input restored.

    Exits (breaks) and calls app.exit() in three cases:
    - session_closed : session ended → session_closed[0] = True
    - thinking       : another client started a request → remote_responding[0] = True
                       (main loop will call _consume_response for the full response)
    - connection lost: unexpected disconnect → session_closed[0] = True
    done/cancelled are NOT exit conditions — the session remains open.
    """
    from prompt_toolkit.application.current import get_app_or_none
    from prompt_toolkit.application.run_in_terminal import run_in_terminal

    try:
        while True:
            try:
                event = await client.recv_event()
            except Exception:
                # Connection lost (e.g. keepalive ping timeout).
                if not session_closed[0]:
                    app = get_app_or_none()

                    def _print_disconnected():
                        console.print("\n[yellow]서버 연결이 끊어졌습니다.[/yellow]")

                    if app is not None and app._is_running:
                        await run_in_terminal(_print_disconnected)
                    else:
                        _print_disconnected()
                    session_closed[0] = True
                    if app is not None:
                        app.exit(result="")
                break
            t = event.get("type")

            def _print():
                render_event(event)

            app = get_app_or_none()
            if app is not None and app._is_running:
                await run_in_terminal(_print)
            else:
                _print()

            # message/thinking: another client's request is in progress.
            # Exit the prompt immediately so the main loop can stream the full
            # reply via _consume_response (same foreground UX as own requests).
            # - message : show the other client's text, then close the prompt.
            # - thinking : fallback in case thinking arrives without message.
            if t in ("message", "thinking"):
                remote_responding[0] = True
                if app is not None:
                    app.exit(result="")
                break

            if t == "session_closed":
                session_closed[0] = True
                if app is not None:
                    app.exit(result="")
                break
            # done/cancelled mark the end of one response stream but the
            # session remains open — keep listening for subsequent broadcasts.
    except asyncio.CancelledError:
        raise


async def run_interactive_loop(client, download_dir: Optional[str] = None) -> None:
    """
    Main interactive loop: reads user input, sends to server, prints responses.
    Handles /download and /quit slash commands locally.
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML

    prompt = PromptSession()
    display = StreamingDisplay()

    console.print("[dim]'/download [경로]' — 다운로드  '/quit' — 종료  '/auto [N]' — 자동 진행[/dim]\n")

    session_closed: list = [False]
    remote_responding: list = [False]

    while True:
        # bg_task is created inside pre_run so it inherits the ContextVar that
        # holds the running Application — enabling get_app_or_none() and
        # run_in_terminal() to work correctly from the background task.
        bg_task_ref: list = [None]

        def _pre_run():
            bg_task_ref[0] = asyncio.create_task(
                _listen_background(client, session_closed, remote_responding)
            )

        user_input = None
        try:
            user_input = await prompt.prompt_async(
                HTML("<b><ansigreen>You</ansigreen></b>: "),
                pre_run=_pre_run,
            )
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            if bg_task_ref[0] is not None:
                bg_task_ref[0].cancel()
                await asyncio.gather(bg_task_ref[0], return_exceptions=True)

        if session_closed[0]:
            break

        # Another client's request is in progress — consume the full response
        # in the foreground (same display path as self-initiated requests).
        if remote_responding[0]:
            remote_responding[0] = False
            await _consume_response(client, display, session_closed)
            if session_closed[0]:
                break
            continue

        if user_input is None:
            console.print("\n[dim]종료합니다.[/dim]")
            break

        text = user_input.strip()
        if not text:
            continue

        # Local slash commands
        if text.lower() in ("/quit", "/exit", "/q"):
            await _handle_quit(client, display)
            break

        if text.lower().startswith("/download"):
            parts = text.split(maxsplit=1)
            dest = parts[1] if len(parts) > 1 else (download_dir or ".")
            await _handle_download(client, dest)
            continue

        # Send to server and stream response
        await client.send_message(text)
        await _consume_response(client, display, session_closed)

        if session_closed[0]:
            break


async def _handle_quit(client, display: StreamingDisplay) -> None:
    """Handle /quit: check for undownloaded changes and close session."""
    try:
        reply = await client.close_session(force=False)
        if reply.get("type") == "close_confirm_needed":
            display.finish()
            console.print(f"[yellow]{reply.get('message', '미다운로드 작업이 있습니다.')}[/yellow]")
            from prompt_toolkit import PromptSession
            p = PromptSession()
            try:
                confirm = await p.prompt_async("강제 종료하시겠습니까? (y/N): ")
                if confirm.strip().lower() == "y":
                    await client.close_session(force=True)
                    console.print("[dim]세션이 강제 종료되었습니다.[/dim]")
            except (EOFError, KeyboardInterrupt):
                pass
        else:
            console.print("[dim]세션이 종료되었습니다.[/dim]")
    except Exception as e:
        import websockets.exceptions
        if isinstance(e, websockets.exceptions.ConnectionClosed):
            console.print("[dim]서버와의 연결이 이미 끊어진 상태입니다.[/dim]")
        else:
            console.print(f"[red]종료 오류: {e}[/red]")


async def _handle_download(client, dest: str) -> None:
    """Handle /download: request file download from server."""
    console.print("[dim]다운로드 중...[/dim]")
    try:
        reply = await client.request_download()
        if reply is None:
            return
        if reply.get("type") == "download_none":
            console.print("[dim]다운로드할 파일이 없습니다.[/dim]")
        elif reply.get("type") == "download_data":
            filename = reply.get("filename", "download.zip")
            dest_path = Path(dest).resolve()
            if dest_path.is_dir():
                dest_path = dest_path / filename

            if "data_bytes" in reply:
                # HTTP response: raw bytes
                from plandog_cli.transfer import save_download_bytes

                saved = save_download_bytes(reply["data_bytes"], dest_path.parent)
            else:
                # WS fallback: base64-encoded data
                from plandog_cli.transfer import save_download

                saved = save_download(reply["data"], dest_path.parent)
            console.print(f"[green]✓ 다운로드 완료: {saved}[/green]")
        elif reply.get("type") == "error":
            console.print(f"[red]다운로드 오류: {reply.get('message', '')}[/red]")
    except Exception as e:
        console.print(f"[red]다운로드 실패: {e}[/red]")


async def _consume_response(
    client, display: StreamingDisplay, session_closed: Optional[list] = None
) -> None:
    """Consume streaming events until done/cancelled."""
    display_started = False
    try:
        async for event in client.stream_response():
            t = event.get("type")
            if t == "thinking" and not display_started:
                display_started = True
                display.start_thinking()
            else:
                if display._spinner and t in ("chunk", "tool"):
                    display.stop_thinking()
                    if t == "chunk" and not display._printed_header:
                        console.print("[bold blue]PlanDog[/bold blue]: ", end="")
                        display._printed_header = True
                render_event(event)
            if t in ("done", "cancelled", "session_closed"):
                if t == "session_closed" and session_closed is not None:
                    session_closed[0] = True
                break
    except KeyboardInterrupt:
        await client.cancel()
        console.print("\n[dim][취소 요청됨][/dim]")
    finally:
        display.stop_thinking()
        display._printed_header = False
