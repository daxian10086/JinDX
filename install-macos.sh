#!/bin/bash
# JinDX macOS 安装脚本
# 用法: bash install-macos.sh
# 交互式: 会询问 DeepSeek API Key

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

echo "========================================="
echo "  JinDX macOS 安装脚本"
echo "========================================="
echo ""

# ── 用户配置 ─────────────────────────────────────────
INSTALL_DIR="/opt/jindx"
read -p "安装目录 [${INSTALL_DIR}]: " INPUT_DIR
INSTALL_DIR="${INPUT_DIR:-$INSTALL_DIR}"

read -p "DeepSeek API Key (输入后回车): " DEEPSEEK_KEY
if [ -z "$DEEPSEEK_KEY" ]; then
    err "必须提供 DeepSeek API Key"
fi

read -p "代理端口 [8080]: " PROXY_PORT
PROXY_PORT="${PROXY_PORT:-8080}"

read -p "管理面板端口 [8090]: " ADMIN_PORT
ADMIN_PORT="${ADMIN_PORT:-8090}"

read -p "默认模型 [deepseek-v4-pro]: " DEFAULT_MODEL
DEFAULT_MODEL="${DEFAULT_MODEL:-deepseek-v4-pro}"

echo ""
echo "安装配置:"
echo "  安装目录:   ${INSTALL_DIR}"
echo "  代理端口:   ${PROXY_PORT}"
echo "  管理面板:   ${ADMIN_PORT}"
echo "  默认模型:   ${DEFAULT_MODEL}"
echo ""
read -p "确认安装? [Y/n] " CONFIRM
if [ "$CONFIRM" = "n" ] || [ "$CONFIRM" = "N" ]; then
    err "取消安装"
fi

# ── 安装依赖 ─────────────────────────────────────────
echo ""
log "安装 Python 依赖..."

if ! command -v python3 &>/dev/null; then
    err "请先安装 Python 3: brew install python3"
fi

# pip 镜像回退：默认源不可用则切到清华镜像
pip3 install -q fastapi "uvicorn[standard]" httpx redis cryptography 2>/dev/null || {
    warn "默认 PyPI 不可用，切换到清华镜像..."
    pip3 install -q \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        fastapi "uvicorn[standard]" httpx redis cryptography
}

# ── 安装 Redis（可选）─────────────────────────────────
if ! command -v redis-server &>/dev/null; then
    warn "Redis 未安装。运行 'brew install redis' 以启用推理缓存。"
    warn "  国内用户可设置 brew 镜像："
    warn "  export HOMEBREW_BOTTLE_DOMAIN=https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles"
else
    log "Redis 已安装"
    brew services start redis 2>/dev/null || true
fi

# ── 创建目录 ─────────────────────────────────────────
log "创建安装目录..."
sudo mkdir -p "${INSTALL_DIR}/certs" "${INSTALL_DIR}/logs"

# ── 复制文件 ─────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
log "从 ${SCRIPT_DIR} 复制文件..."
sudo cp -r "${SCRIPT_DIR}/proxy.py" "${SCRIPT_DIR}/jindx" "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/"

# ── 设置 /etc/hosts ──────────────────────────────────
log "配置 /etc/hosts 劫持..."
HOSTS_ENTRIES=(
    "127.0.0.1 api.openai.com"
    "127.0.0.1 chatgpt.com"
)
for entry in "${HOSTS_ENTRIES[@]}"; do
    if ! grep -q "$entry" /etc/hosts 2>/dev/null; then
        echo "$entry" | sudo tee -a /etc/hosts > /dev/null
    fi
done

# ── 创建 launchd 服务 ─────────────────────────────────
log "创建 launchd 服务..."

PLIST_PATH="$HOME/Library/LaunchAgents/com.jindx.proxy.plist"
sed -e "s|/opt/jindx|${INSTALL_DIR}|g" \
    -e "s|sk-your-deepseek-api-key|${DEEPSEEK_KEY}|g" \
    -e "s|8080</key>.*<string>8090|<string>${PROXY_PORT}</string>.*<string>${ADMIN_PORT}|" \
    "${SCRIPT_DIR}/com.jindx.proxy.plist" > /tmp/com.jindx.proxy.plist

mkdir -p "$HOME/Library/LaunchAgents"
cp /tmp/com.jindx.proxy.plist "$PLIST_PATH"

# ── 配置 Claude Code ──────────────────────────────────
log "配置 Claude Code..."
CLAUDE_PROFILE_DIR="$HOME/.claude/profiles"
mkdir -p "${CLAUDE_PROFILE_DIR}"
cat > "${CLAUDE_PROFILE_DIR}/deepseek.json" << CLAUDE_EOF
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "${DEEPSEEK_KEY}",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:${PROXY_PORT}",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "${DEFAULT_MODEL}",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "${DEFAULT_MODEL}",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "${DEFAULT_MODEL}",
    "ANTHROPIC_MODEL": "${DEFAULT_MODEL}"
  },
  "model": "sonnet"
}
CLAUDE_EOF

# ── 启动服务 ─────────────────────────────────────────
log "启动服务..."
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

sleep 2

# ── 验证 ─────────────────────────────────────────────
if launchctl list | grep -q com.jindx.proxy; then
    log "JinDX 服务已启动!"
else
    warn "服务可能未正常启动，查看日志: ${INSTALL_DIR}/logs/stderr.log"
fi

echo ""
echo "========================================="
echo "  macOS 安装完成!"
echo "========================================="
echo ""
echo "  服务管理:"
echo "    launchctl list com.jindx.proxy    查看状态"
echo "    launchctl stop  com.jindx.proxy    停止"
echo "    launchctl start com.jindx.proxy    启动"
echo "    launchctl unload $PLIST_PATH    卸载服务"
echo ""
echo "  代理地址:     http://127.0.0.1:${PROXY_PORT}"
echo "  管理面板:     http://127.0.0.1:${ADMIN_PORT}"
echo ""
echo "  Claude Code 配置已写入:"
echo "    ${CLAUDE_PROFILE_DIR}/deepseek.json"
echo ""
