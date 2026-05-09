#!/bin/bash
# Chat <-> Responses API Proxy for DeepSeek v4 Pro
#
# Usage:
#   ./start.sh              # default port 8080
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
export REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
export REDIS_PORT="${REDIS_PORT:-6379}"
export REDIS_DB="${REDIS_DB:-0}"
export CONNECT_PORT="${CONNECT_PORT:-8443}"
export TLS_PORT="${TLS_PORT:-8444}"

echo "=== Chat-Responses Proxy ==="
echo "Target:  $DEEPSEEK_BASE/v1/chat/completions"
echo "Model:   $DEFAULT_MODEL"
echo "Port:    $PROXY_PORT (admin: $ADMIN_PORT)"
echo "Redis:   $REDIS_HOST:$REDIS_PORT"
echo ""

# 安装依赖（跨平台兼容 + 国内镜像回退）
# 优先使用默认源（VPN 下速度快），不可用时切到清华镜像
MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
PIP_PKGS="fastapi \"uvicorn[standard]\" httpx redis cryptography"

install_deps() {
    python3 -c "import fastapi,uvicorn,httpx,redis,cryptography" 2>/dev/null && return

    _pip_install() {
        if [ "$(uname -s)" = "Darwin" ]; then
            pip3 install -q $PIP_PKGS 2>/dev/null && return
            echo "默认 PyPI 不可用，切换到清华镜像..."
            pip3 install -q -i "$MIRROR" $PIP_PKGS
        else
            pip3 install --break-system-packages -q $PIP_PKGS 2>/dev/null && return
            echo "默认 PyPI 不可用，切换到清华镜像..."
            pip3 install --break-system-packages -q -i "$MIRROR" $PIP_PKGS
        fi
    }
    _pip_install
}
install_deps

exec python3 proxy.py
