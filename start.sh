#!/bin/bash
# Chat <-> Responses API Proxy for DeepSeek v4 Pro
#
# Usage:
#   ./start.sh              # default port 8080
#   PROXY_PORT=9000 ./start.sh
#   DEEPSEEK_KEY=sk-xxx DEFAULT_MODEL=deepseek-chat ./start.sh

cd "$(dirname "$0")"

export PROXY_PORT="${PROXY_PORT:-8080}"
export DEEPSEEK_KEY="${DEEPSEEK_KEY:-sk-your-deepseek-api-key}"
export DEEPSEEK_BASE="${DEEPSEEK_BASE:-https://api.deepseek.com}"
export DEFAULT_MODEL="${DEFAULT_MODEL:-deepseek-v4-pro}"

echo "=== Chat-Responses Proxy ==="
echo "Target:  $DEEPSEEK_BASE/v1/chat/completions"
echo "Model:   $DEFAULT_MODEL"
echo "Port:    $PROXY_PORT"
echo ""

# Install deps if needed
python3 -c "import fastapi,uvicorn,httpx" 2>/dev/null || \
  pip install --break-system-packages -q fastapi uvicorn httpx 2>/dev/null

exec python3 proxy.py
