#!/bin/bash
# Chat <-> Responses API Proxy for DeepSeek v4 Pro
#
# Usage:
#   ./start.sh              # 首次运行会提示输入 DeepSeek Key
#   PROXY_PORT=9000 ./start.sh
#   DEEPSEEK_KEY=sk-xxx DEFAULT_MODEL=deepseek-chat ./start.sh

cd "$(dirname "$0")"

export PROXY_PORT="${PROXY_PORT:-8080}"
export ADMIN_PORT="${ADMIN_PORT:-8090}"
export DEEPSEEK_KEY="${DEEPSEEK_KEY:-sk-your-deepseek-api-key}"
export DEEPSEEK_BASE="${DEEPSEEK_BASE:-https://api.deepseek.com}"
export DEFAULT_MODEL="${DEFAULT_MODEL:-deepseek-v4-pro}"
export DEFAULT_REASONING_EFFORT="${DEFAULT_REASONING_EFFORT:-max}"
export MAX_POSITION_EMBEDDINGS="${MAX_POSITION_EMBEDDINGS:-1000000}"
export CONNECT_PORT="${CONNECT_PORT:-8443}"
export TLS_PORT="${TLS_PORT:-8444}"

# ── 检查/配置 DeepSeek API Key ─────────────────────

_OS="$(uname -s)"
if [ "$_OS" = "Darwin" ]; then
    CONFIG_FILE="${PROXY_CONFIG_FILE:-$HOME/Library/Application Support/proxy-config.json}"
elif [ -n "$XDG_CONFIG_HOME" ]; then
    CONFIG_FILE="${PROXY_CONFIG_FILE:-$XDG_CONFIG_HOME/proxy-config.json}"
else
    CONFIG_FILE="${PROXY_CONFIG_FILE:-$HOME/.config/proxy-config.json}"
fi

_NEED_KEY=0
if [ "$DEEPSEEK_KEY" = "sk-your-deepseek-api-key" ] || [ -z "$DEEPSEEK_KEY" ]; then
    _NEED_KEY=1
    # 尝试从配置文件读取
    if [ -f "$CONFIG_FILE" ]; then
        SAVED_KEY=$(python3 -c "
import json, sys
try:
    cfg = json.load(open('$CONFIG_FILE'))
    k = cfg.get('claude_deepseek_key') or cfg.get('deepseek_key', '')
    if k and k != 'sk-your-deepseek-api-key':
        print(k, end='')
except:
    pass
" 2>/dev/null)
        if [ -n "$SAVED_KEY" ]; then
            export DEEPSEEK_KEY="$SAVED_KEY"
            _NEED_KEY=0
            echo "  [+] Loaded API Key from config file"
        fi
    fi
fi

if [ "$_NEED_KEY" = "1" ]; then
    echo ""
    echo "=========================================="
    echo "  First Run - Configure DeepSeek API Key"
    echo "=========================================="
    echo ""
    echo "  Get a key: https://platform.deepseek.com/api_keys"
    echo ""
    read -p "  Enter your DeepSeek API Key (sk-...): " INPUT_KEY
    if [ -z "$INPUT_KEY" ]; then
        echo ""
        echo "  [X] No API Key provided, cannot start"
        echo "      Use: DEEPSEEK_KEY=sk-xxx ./start.sh"
        exit 1
    fi
    export DEEPSEEK_KEY="$INPUT_KEY"

    # 持久化到配置文件
    mkdir -p "$(dirname "$CONFIG_FILE")"
    python3 -c "
import json, sys
try:
    cfg = json.load(open('$CONFIG_FILE'))
except:
    cfg = {}
cfg['deepseek_key'] = '$DEEPSEEK_KEY'
json.dump(cfg, open('$CONFIG_FILE', 'w'), indent=2)
" 2>/dev/null && echo "  [+] API Key saved to $CONFIG_FILE" || echo "  [!] Failed to save config, key only valid for this session"
fi

echo "=== Chat-Responses Proxy ==="
echo "Target:  $DEEPSEEK_BASE/v1/chat/completions"
echo "Model:   $DEFAULT_MODEL"
echo "Port:    $PROXY_PORT (admin: $ADMIN_PORT)"
echo ""

# 安装依赖（跨平台兼容 + 国内镜像回退）
# 优先使用默认源（VPN 下速度快），不可用时切到清华镜像
MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
PIP_PKGS="fastapi \"uvicorn[standard]\" httpx cryptography"

install_deps() {
    python3 -c "import fastapi,uvicorn,httpx,cryptography" 2>/dev/null && return

    _pip_install() {
        if [ "$(uname -s)" = "Darwin" ]; then
            pip3 install -q $PIP_PKGS 2>/dev/null && return
            echo "Default PyPI unavailable, switching to Tsinghua mirror..."
            pip3 install -q -i "$MIRROR" $PIP_PKGS
        else
            pip3 install --break-system-packages -q $PIP_PKGS 2>/dev/null && return
            echo "Default PyPI unavailable, switching to Tsinghua mirror..."
            pip3 install --break-system-packages -q -i "$MIRROR" $PIP_PKGS
        fi
    }
    _pip_install
}
install_deps

# ── 自动配置 Codex CLI config.toml ──────────────────

CODEX_CONFIG_DIR="$HOME/.codex"
CODEX_CONFIG_FILE="$CODEX_CONFIG_DIR/config.toml"

CODEX_CONFIG_CONTENT=$(cat << EOF
model = "gpt-5.5"
model_reasoning_effort = "xhigh"
model_provider = "openai_http"

[model_providers.openai_http]
name = "JinDx Proxy (DeepSeek)"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = true
base_url = "http://127.0.0.1:$PROXY_PORT"
EOF
)

NEED_UPDATE=0

if [ -f "$CODEX_CONFIG_FILE" ]; then
    if ! grep -q "model_provider" "$CODEX_CONFIG_FILE" 2>/dev/null || ! grep -q "127\.0\.0\.1" "$CODEX_CONFIG_FILE" 2>/dev/null; then
        NEED_UPDATE=1
        cp "$CODEX_CONFIG_FILE" "$CODEX_CONFIG_FILE.bak"
        echo "  [=] Backed up existing Codex config to $CODEX_CONFIG_FILE.bak"
    fi
else
    NEED_UPDATE=1
    mkdir -p "$CODEX_CONFIG_DIR"
fi

if [ "$NEED_UPDATE" = "1" ]; then
    echo "$CODEX_CONFIG_CONTENT" > "$CODEX_CONFIG_FILE"
    echo "  [+] Codex config.toml auto-configured -> http://127.0.0.1:$PROXY_PORT"
else
    echo "  [=] Codex config.toml already using JinDx proxy"
fi

exec python3 proxy.py
