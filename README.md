# plandog-cli

PlanDog 서버에 접속하여 터미널에서 블루프린트 작업을 수행하는 원격 클라이언트입니다.

## 빠른 설치

설치 스크립트가 uv 설치부터 CLI 설치, API 키 설정까지 한 번에 처리합니다.

**macOS / Linux:**
```bash
curl -fsSL https://plandog.net/install.sh | bash
```

**Windows (cmd / PowerShell):**
```
powershell -ExecutionPolicy Bypass -Command "irm https://plandog.net/install.ps1 | iex"
```

**제거:**
```bash
uv tool uninstall plandog-cli
```

## 설치 (수동)

```bash
# uv (권장)
uv tool install .

# pip
pip install .
```

### 개발 환경

```bash
uv sync --all-extras
```

## 사용법

```bash
# 기본 접속 (기본 서버: wss://plandog.net:8764)
plandog-cli -k YOUR_API_KEY

# 환경변수로 API 키 설정 후 바로 접속
export PLANDOG_API_KEY=YOUR_API_KEY
plandog-cli

# 다른 서버에 접속
plandog-cli ws://your-server:8765 -k YOUR_API_KEY

# 블루프린트 업로드와 함께 새 세션 시작
plandog-cli -k YOUR_API_KEY -u ./my-blueprint

# 다운로드 저장 경로 지정
plandog-cli -k YOUR_API_KEY -d ./downloads
```

### CLI 옵션

| 옵션 | 단축 | 설명 |
|------|------|------|
| `--api-key` | `-k` | API 키 (환경변수 `PLANDOG_API_KEY` 우선) |
| `--upload` | `-u` | 새 세션 시작 시 업로드할 블루프린트 경로 |
| `--download-dir` | `-d` | `/download` 기본 저장 경로 |

### 세션 내 명령어

| 명령어 | 설명 |
|--------|------|
| `/quit`, `/exit`, `/q` | 세션 종료 |
| `/download [path]` | 파일 다운로드 |
| `/auto [N]` | 자동 모드 실행 |

## 테스트

```bash
uv run pytest
```

## 라이선스

MIT
