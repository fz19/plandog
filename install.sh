#!/usr/bin/env bash
set -euo pipefail

# ── Color helpers ────────────────────────────────────────────────
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''
fi

info()    { printf "${BLUE}[INFO]${NC} %s\n" "$*"; }
success() { printf "${GREEN}[OK]${NC} %s\n" "$*"; }
warn()    { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
error()   { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }

INTERACTIVE=true
[ -t 0 ] || INTERACTIVE=false

# ── OS check ─────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Darwin|Linux) ;;
    *)
        error "이 스크립트는 macOS/Linux 전용입니다."
        echo "Windows는 PowerShell에서 install.ps1을 실행하세요."
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 1. uv 설치 확인 및 자동 설치 ────────────────────────────────
install_uv() {
    if command -v uv &>/dev/null; then
        success "uv가 이미 설치되어 있습니다: $(uv --version)"
        return
    fi

    info "uv를 설치합니다..."
    if ! command -v curl &>/dev/null; then
        error "curl이 필요합니다. 먼저 curl을 설치하세요."
        exit 1
    fi

    local uv_installer="/tmp/uv-install-$$.sh"
    if ! curl -LsSf https://astral.sh/uv/install.sh -o "$uv_installer"; then
        error "uv 설치 스크립트 다운로드에 실패했습니다."
        exit 1
    fi
    sh "$uv_installer"
    rm -f "$uv_installer"

    # PATH에 추가 (현재 세션)
    export PATH="$HOME/.local/bin:$PATH"

    if command -v uv &>/dev/null; then
        success "uv 설치 완료: $(uv --version)"
    else
        error "uv 설치에 실패했습니다."
        exit 1
    fi
}

# ── 2. plandog-cli 설치 ─────────────────────────────────────────
install_plandog_cli() {
    local install_path=""
    local force_flag=""

    # 이미 설치되어 있는지 확인
    if command -v plandog-cli &>/dev/null; then
        warn "plandog-cli가 이미 설치되어 있습니다."
        if [ "$INTERACTIVE" = true ]; then
            read -rp "재설치하시겠습니까? (y/N): " answer
            case "$answer" in
                [yY]|[yY][eE][sS]) force_flag="--force" ;;
                *)
                    info "설치를 건너뜁니다."
                    return
                    ;;
            esac
        else
            info "비대화형 모드: --force로 재설치합니다."
            force_flag="--force"
        fi
    fi

    # 로컬 vs 원격 판별
    if [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
        info "로컬 프로젝트에서 설치합니다..."
        install_path="$SCRIPT_DIR"
    else
        info "GitHub에서 다운로드하여 설치합니다..."

        # curl/unzip 확인
        if ! command -v curl &>/dev/null; then
            error "curl이 필요합니다."
            exit 1
        fi
        if ! command -v unzip &>/dev/null; then
            error "unzip이 필요합니다. 먼저 unzip을 설치하세요."
            exit 1
        fi

        local tmp_dir
        tmp_dir="$(mktemp -d /tmp/plandog-install.XXXXXX)"
        local tmp_zip="$tmp_dir/source.zip"
        trap 'rm -rf "$tmp_dir"' EXIT

        info "소스 코드를 다운로드합니다..."
        if ! curl -L "https://github.com/fz19/plandog/archive/refs/heads/main.zip" -o "$tmp_zip"; then
            error "다운로드에 실패했습니다. 네트워크 연결을 확인하세요."
            exit 1
        fi

        mkdir -p "$tmp_dir"
        unzip -q "$tmp_zip" -d "$tmp_dir"
        install_path="$tmp_dir/plandog-main"

        if [ ! -f "$install_path/pyproject.toml" ]; then
            error "다운로드한 패키지에서 plandog-cli를 찾을 수 없습니다."
            exit 1
        fi
    fi

    info "plandog-cli를 설치합니다..."
    # shellcheck disable=SC2086
    if uv tool install $force_flag "$install_path"; then
        success "plandog-cli 설치 완료!"
    else
        error "plandog-cli 설치에 실패했습니다."
        exit 1
    fi
}

# ── 3. API 키 설정 ──────────────────────────────────────────────
configure_api_key() {
    if [ "$INTERACTIVE" = false ]; then
        info "비대화형 모드: API 키 설정을 건너뜁니다."
        echo "  수동으로 설정하려면: export PLANDOG_API_KEY=\"your-key\""
        return
    fi

    echo ""
    read -rp "PLANDOG_API_KEY를 입력하세요 (건너뛰려면 Enter): " api_key

    if [ -z "$api_key" ]; then
        info "API 키 설정을 건너뜁니다."
        return
    fi

    # API 키 문자 검증
    if [[ ! "$api_key" =~ ^[a-zA-Z0-9_.:-]+$ ]]; then
        error "API 키에 허용되지 않는 문자가 포함되어 있습니다. (영문, 숫자, ._:- 만 허용)"
        return
    fi

    # 셸 프로필 감지
    local profile=""
    local export_line=""
    local shell_name="${SHELL##*/}"

    case "$shell_name" in
        zsh)
            profile="$HOME/.zshrc"
            export_line="export PLANDOG_API_KEY='$api_key'"
            ;;
        bash)
            if [ -f "$HOME/.bashrc" ]; then
                profile="$HOME/.bashrc"
            else
                profile="$HOME/.profile"
            fi
            export_line="export PLANDOG_API_KEY='$api_key'"
            ;;
        fish)
            profile="${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish"
            export_line="set -gx PLANDOG_API_KEY '$api_key'"
            ;;
        *)
            profile="$HOME/.profile"
            export_line="export PLANDOG_API_KEY='$api_key'"
            ;;
    esac

    # 프로필 파일이 없으면 생성
    [ -f "$profile" ] || touch "$profile"

    # 기존 PLANDOG_API_KEY가 있는지 확인
    if grep -q 'PLANDOG_API_KEY' "$profile" 2>/dev/null; then
        warn "\"$profile\"에 PLANDOG_API_KEY가 이미 존재합니다."
        read -rp "교체하시겠습니까? (y/N): " replace
        case "$replace" in
            [yY]|[yY][eE][sS])
                # 기존 줄 제거
                if [[ "$OS" == "Darwin" ]]; then
                    sed -i '' '/^export PLANDOG_API_KEY=/d; /^set -gx PLANDOG_API_KEY /d' "$profile"
                else
                    sed -i '/^export PLANDOG_API_KEY=/d; /^set -gx PLANDOG_API_KEY /d' "$profile"
                fi
                echo "$export_line" >> "$profile"
                success "API 키를 교체했습니다."
                ;;
            *)
                info "기존 API 키를 유지합니다."
                ;;
        esac
    else
        echo "$export_line" >> "$profile"
        success "API 키를 \"$profile\"에 추가했습니다."
    fi

    # 현재 세션에도 적용
    export PLANDOG_API_KEY="$api_key"
}

# ── 4. PATH에 uv tool bin 추가 ──────────────────────────────────
ensure_path() {
    local uv_bin
    uv_bin="$(uv tool dir 2>/dev/null)/../bin" || uv_bin="$HOME/.local/bin"
    uv_bin="$(cd "$uv_bin" 2>/dev/null && pwd)" || uv_bin="$HOME/.local/bin"

    if echo "$PATH" | tr ':' '\n' | grep -qx "$uv_bin"; then
        return
    fi

    local shell_name="${SHELL##*/}"
    local profile=""
    local path_line=""

    case "$shell_name" in
        zsh)
            profile="$HOME/.zshrc"
            path_line="export PATH=\"$uv_bin:\$PATH\""
            ;;
        bash)
            profile="$HOME/.bashrc"
            [ -f "$profile" ] || profile="$HOME/.profile"
            path_line="export PATH=\"$uv_bin:\$PATH\""
            ;;
        fish)
            profile="${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish"
            path_line="fish_add_path \"$uv_bin\""
            ;;
        *)
            profile="$HOME/.profile"
            path_line="export PATH=\"$uv_bin:\$PATH\""
            ;;
    esac

    [ -f "$profile" ] || touch "$profile"

    if ! grep -q "$uv_bin" "$profile" 2>/dev/null; then
        echo "$path_line" >> "$profile"
        info "PATH에 \"$uv_bin\"을 추가했습니다 ($profile)."
    fi

    export PATH="$uv_bin:$PATH"
}

# ── Main ─────────────────────────────────────────────────────────
echo ""
echo "╔═══════════════════════════════════════╗"
echo "║       plandog-cli 설치 스크립트       ║"
echo "╚═══════════════════════════════════════╝"
echo ""

install_uv
install_plandog_cli
ensure_path
configure_api_key

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
success "설치가 완료되었습니다!"
echo ""
echo "  사용법:"
echo "    plandog-cli ws://your-server:8765"
echo ""
echo "  도움말:"
echo "    plandog-cli --help"
echo ""
echo "  제거:"
echo "    uv tool uninstall plandog-cli"
echo ""
if [ "$INTERACTIVE" = true ]; then
    warn "변경사항을 적용하려면 새 터미널을 열거나 다음을 실행하세요:"
    echo "    source ~/.${SHELL##*/}rc"
fi
echo ""
