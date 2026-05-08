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

# 安装依赖（跨平台：macOS 不需要 --break-system-packages）
install_deps() {
    python3 -c "import fastapi,uvicorn,httpx,redis,cryptography" 2>/dev/null && return
    if [ "$(uname -s)" = "Darwin" ]; then
        pip3 install -q fastapi "uvicorn[standard]" httpx redis cryptography 2>/dev/null
    else
        pip3 install --break-system-packages -q fastapi "uvicorn[standard]" httpx redis cryptography 2>/dev/null
    fi
}
install_deps

exec python3 proxy.py
