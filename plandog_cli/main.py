"""CLI entry point for plandog-cli."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(
    name="plandog-cli",
    help="PlanDog 원격 터미널 클라이언트",
    add_completion=False,
)
console = Console()


@app.command()
def main(
    host: str = typer.Argument("wss://plandog.net:8764", help="서버 주소 (기본: wss://plandog.net:8764)"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", "-k",
        help="API 키 (환경변수 PLANDOG_API_KEY 우선)",
        envvar="PLANDOG_API_KEY",
    ),
    upload: Optional[str] = typer.Option(
        None, "--upload", "-u",
        help="새 세션 시작 시 업로드할 로컬 블루프린트 경로",
    ),
    download_dir: Optional[str] = typer.Option(
        None, "--download-dir", "-d",
        help="/download 기본 저장 경로",
    ),
):
    """PlanDog 서버에 접속하여 터미널에서 블루프린트 작업을 수행합니다."""
    if not api_key:
        console.print("[red]API 키가 필요합니다. --api-key 옵션 또는 PLANDOG_API_KEY 환경변수를 설정하세요.[/red]")
        raise typer.Exit(1)

    url = host if "://" in host else f"ws://{host}"

    try:
        asyncio.run(_run(url, api_key, upload, download_dir))
    except KeyboardInterrupt:
        console.print("\n[dim]종료합니다.[/dim]")


async def _run(url: str, api_key: str, upload: Optional[str], download_dir: Optional[str]) -> None:
    from plandog_cli.client import PlandogClient
    from plandog_cli.ui import console, prompt_session_choice, run_interactive_loop
    from plandog_cli.transfer import upload_dir

    console.print(f"[dim]서버에 연결 중: {url}[/dim]")

    async with PlandogClient(url, api_key) as client:
        # Authenticate
        try:
            sessions = await client.authenticate()
        except PermissionError as e:
            console.print(f"[red]인증 실패: {e}[/red]")
            return
        except Exception as e:
            console.print(f"[red]연결 오류: {e}[/red]")
            return

        console.print("[green]✓ 인증 성공[/green]")

        # Session selection
        if sessions and not upload:
            session_id = await prompt_session_choice(sessions)
        else:
            session_id = None  # new session

        if session_id:
            history = await client.select_session(session_id)
            console.print(f"[dim]세션을 이어갑니다. 이력: {len(history)}개 메시지[/dim]")
            if history:
                console.print("[dim]── 이전 대화 ──[/dim]")
                for entry in history[-10:]:  # show last 10
                    role = entry.get("role", "?")
                    text = entry.get("text", "")
                    prefix = "[bold green]You[/bold green]" if role == "user" else "[bold blue]PlanDog[/bold blue]"
                    console.print(f"{prefix}: {text}")
                console.print("[dim]── 대화 시작 ──[/dim]\n")
        else:
            upload_b64 = None
            if upload:
                console.print(f"[dim]업로드 중: {upload}[/dim]")
                try:
                    upload_b64 = upload_dir(upload)
                    console.print("[green]✓ 업로드 완료[/green]")
                except Exception as e:
                    console.print(f"[yellow]업로드 실패: {e}[/yellow]")
            await client.new_session(upload=upload_b64)
            console.print("[dim]새 세션을 시작합니다.[/dim]\n")

        # Interactive loop
        await run_interactive_loop(client, download_dir=download_dir)


def entry():
    """Entry point for the package script."""
    app()


if __name__ == "__main__":
    entry()
