# plandog-cli 설치 스크립트 (Windows PowerShell)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ── Color helpers ────────────────────────────────────────────────
function Write-Info    { param([string]$Message) Write-Host "[INFO] $Message" -ForegroundColor Blue }
function Write-Success { param([string]$Message) Write-Host "[OK] $Message" -ForegroundColor Green }
function Write-Warn    { param([string]$Message) Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Write-Err     { param([string]$Message) Write-Host "[ERROR] $Message" -ForegroundColor Red }

Write-Host ""
Write-Host "+=======================================+" -ForegroundColor Cyan
Write-Host "|       plandog-cli install script       |" -ForegroundColor Cyan
Write-Host "+=======================================+" -ForegroundColor Cyan
Write-Host ""

# ── 1. uv 설치 확인 및 자동 설치 ────────────────────────────────
function Install-Uv {
    try {
        $uvCmd = Get-Command uv -ErrorAction Stop
        Write-Success "uv가 이미 설치되어 있습니다: $(uv --version)"
        return
    } catch {
        # uv not found
    }

    Write-Info "uv를 설치합니다..."
    try {
        $uvInstaller = Join-Path $env:TEMP "uv-install.ps1"
        Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $uvInstaller
        & $uvInstaller
        Remove-Item $uvInstaller -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Err "uv 설치에 실패했습니다. 네트워크 연결을 확인하세요."
        exit 1
    }

    # PATH 갱신
    $uvPath = "$env:USERPROFILE\.local\bin"
    if (Test-Path $uvPath) {
        $env:Path = "$uvPath;$env:Path"
    }
    # cargo/uv default path
    $cargoPath = "$env:USERPROFILE\.cargo\bin"
    if (Test-Path $cargoPath) {
        $env:Path = "$cargoPath;$env:Path"
    }

    try {
        $null = Get-Command uv -ErrorAction Stop
        Write-Success "uv 설치 완료: $(uv --version)"
    } catch {
        Write-Err "uv가 PATH에서 발견되지 않습니다. 터미널을 다시 열고 시도하세요."
        exit 1
    }
}

# ── 2. plandog-cli 설치 ─────────────────────────────────────────
function Install-PlandogCli {
    $forceFlag = ""

    # 이미 설치되어 있는지 확인
    try {
        $null = Get-Command plandog-cli -ErrorAction Stop
        Write-Warn "plandog-cli가 이미 설치되어 있습니다."
        $answer = Read-Host "재설치하시겠습니까? (y/N)"
        if ($answer -match '^[yY]') {
            $forceFlag = "--force"
        } else {
            Write-Info "설치를 건너뜁니다."
            return
        }
    } catch {
        # not installed
    }

    # 로컬 vs 원격 판별 ($PSScriptRoot는 irm|iex 실행 시 빈 문자열)
    $scriptDir = $PSScriptRoot

    if ($scriptDir -and (Test-Path (Join-Path $scriptDir "pyproject.toml"))) {
        Write-Info "로컬 프로젝트에서 설치합니다..."
        $installPath = $scriptDir
    } else {
        Write-Info "GitHub에서 다운로드하여 설치합니다..."

        $uniqueId = [guid]::NewGuid().ToString('N').Substring(0,8)
        $tmpZip = Join-Path $env:TEMP "plandog-install-$uniqueId.zip"
        $tmpDir = Join-Path $env:TEMP "plandog-install-$uniqueId"

        try {
            Write-Info "소스 코드를 다운로드합니다..."
            Invoke-WebRequest -Uri "https://github.com/fz19/plandog/archive/refs/heads/main.zip" -OutFile $tmpZip
        } catch {
            Write-Err "다운로드에 실패했습니다. 네트워크 연결을 확인하세요."
            exit 1
        }

        try {
            Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
        } catch {
            Write-Err "압축 해제에 실패했습니다."
            exit 1
        }

        $installPath = Join-Path $tmpDir "plandog-main"

        if (-not (Test-Path (Join-Path $installPath "pyproject.toml"))) {
            Write-Err "다운로드한 패키지에서 plandog-cli를 찾을 수 없습니다."
            exit 1
        }
    }

    Write-Info "plandog-cli를 설치합니다..."
    try {
        if ($forceFlag) {
            uv tool install $forceFlag $installPath
        } else {
            uv tool install $installPath
        }
        Write-Success "plandog-cli 설치 완료!"
    } catch {
        Write-Err "plandog-cli 설치에 실패했습니다."
        exit 1
    } finally {
        # 임시 파일 정리
        if (Test-Path variable:tmpZip) {
            Remove-Item -Path $tmpZip -Force -ErrorAction SilentlyContinue
            Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# ── 3. API 키 설정 ──────────────────────────────────────────────
function Set-ApiKey {
    Write-Host ""
    $apiKey = Read-Host "PLANDOG_API_KEY를 입력하세요 (건너뛰려면 Enter)"

    if ([string]::IsNullOrWhiteSpace($apiKey)) {
        Write-Info "API 키 설정을 건너뜁니다."
        return
    }

    # 기존 환경변수 확인
    $existingKey = [Environment]::GetEnvironmentVariable("PLANDOG_API_KEY", "User")
    if ($existingKey) {
        Write-Warn "PLANDOG_API_KEY가 이미 설정되어 있습니다."
        $replace = Read-Host "교체하시겠습니까? (y/N)"
        if ($replace -notmatch '^[yY]') {
            Write-Info "기존 API 키를 유지합니다."
            return
        }
    }

    # 사용자 환경변수로 영구 저장
    [Environment]::SetEnvironmentVariable("PLANDOG_API_KEY", $apiKey, "User")
    # 현재 세션에도 설정
    $env:PLANDOG_API_KEY = $apiKey
    Write-Success "API 키를 설정했습니다."
}

# ── 4. PATH에 uv tool bin 추가 ──────────────────────────────────
function Ensure-Path {
    $uvBin = "$env:USERPROFILE\.local\bin"

    if (-not ($env:Path -split ';' | Where-Object { $_ -eq $uvBin })) {
        $currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($currentUserPath -and -not ($currentUserPath -split ';' | Where-Object { $_ -eq $uvBin })) {
            [Environment]::SetEnvironmentVariable("Path", "$uvBin;$currentUserPath", "User")
            Write-Info "PATH에 `"$uvBin`"을 추가했습니다."
        }
        $env:Path = "$uvBin;$env:Path"
    }
}

# ── Main ─────────────────────────────────────────────────────────
Install-Uv
Install-PlandogCli
Ensure-Path
Set-ApiKey

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Success "설치가 완료되었습니다!"
Write-Host ""
Write-Host "  사용법:"
Write-Host "    plandog-cli ws://your-server:8765"
Write-Host ""
Write-Host "  도움말:"
Write-Host "    plandog-cli --help"
Write-Host ""
Write-Host "  제거:"
Write-Host "    uv tool uninstall plandog-cli"
Write-Host ""
Write-Warn "변경사항을 적용하려면 새 터미널을 열어주세요."
Write-Host ""
