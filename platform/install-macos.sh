#!/bin/bash
# JinDX macOS 安装脚本
# 用法: bash install-macos.sh
# 交互式: 会询问 DeepSeek API Key
#
# 功能:
#   1. 安装 Python 依赖
#   2. 配置 /etc/hosts 劫持（5 个 OpenAI 域名 → 127.0.0.1）
#   3. 配置 pfctl 端口转发（127.0.0.1:443 → 127.0.0.1:8444）
#   4. 创建 launchd 后台服务
#   5. 生成 Claude Code profile
#   6. 输出 Codex CLI 环境变量

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

read -p "TLS 端口 (用于端口转发 443→) [8444]: " TLS_PORT
TLS_PORT="${TLS_PORT:-8444}"

read -p "管理面板端口 [8090]: " ADMIN_PORT
ADMIN_PORT="${ADMIN_PORT:-8090}"

read -p "默认模型 [deepseek-v4-pro]: " DEFAULT_MODEL
DEFAULT_MODEL="${DEFAULT_MODEL:-deepseek-v4-pro}"

echo ""
echo "安装配置:"
echo "  安装目录:   ${INSTALL_DIR}"
echo "  代理端口:   ${PROXY_PORT}"
echo "  TLS 端口:   ${TLS_PORT}"
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
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
log "从 ${SCRIPT_DIR} 复制文件..."
sudo cp -r "${SCRIPT_DIR}/proxy.py" "${SCRIPT_DIR}/jindx" "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/"

# ── 设置 /etc/hosts ──────────────────────────────────
log "配置 /etc/hosts 劫持..."
HOSTS_ENTRIES=(
    "127.0.0.1 api.openai.com"
    "127.0.0.1 chatgpt.com"
    "127.0.0.1 auth.openai.com"
    "127.0.0.1 chat.openai.com"
    "127.0.0.1 ab.chatgpt.com"
)
for entry in "${HOSTS_ENTRIES[@]}"; do
    if ! grep -q "$entry" /etc/hosts 2>/dev/null; then
        echo "$entry" | sudo tee -a /etc/hosts > /dev/null
        log "  hosts: ${entry}"
    fi
done
# 刷新 DNS 缓存
sudo dscacheutil -flushcache 2>/dev/null || true
sudo killall -HUP mDNSResponder 2>/dev/null || true

# ── 配置 pfctl 端口转发 (443 → 8444) ──────────────────
log "配置端口转发 127.0.0.1:443 → 127.0.0.1:${TLS_PORT}..."

# macOS 用 pfctl 做本地端口转发（需要先启用 ip forwarding）
# 注意：pf 规则在 /etc/pf.conf 中，我们添加 anchor 文件避免修改系统配置
PF_ANCHOR="/etc/pf.anchors/jindx"
PF_RULE="rdr pass on lo0 inet proto tcp from any to 127.0.0.1 port 443 -> 127.0.0.1 port ${TLS_PORT}"

# 确保系统 pf.conf 加载了 anchor
if ! grep -q "jindx" /etc/pf.conf 2>/dev/null; then
    echo "anchor \"jindx/*\"" | sudo tee -a /etc/pf.conf > /dev/null
    echo "load anchor \"jindx\" from \"${PF_ANCHOR}\"" | sudo tee -a /etc/pf.conf > /dev/null
fi

sudo mkdir -p /etc/pf.anchors
echo "$PF_RULE" | sudo tee "$PF_ANCHOR" > /dev/null

# 启用 pf（macOS 默认关闭）
sudo pfctl -E 2>/dev/null || true
sudo pfctl -f /etc/pf.conf 2>/dev/null || true
log "  pfctl 端口转发已配置"

# ── 创建 launchd 服务 ─────────────────────────────────
log "创建 launchd 服务..."

PLIST_PATH="$HOME/Library/LaunchAgents/com.jindx.proxy.plist"

cat > /tmp/com.jindx.proxy.plist << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.jindx.proxy</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>${INSTALL_DIR}/proxy.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>DEEPSEEK_BASE</key>
        <string>https://api.deepseek.com</string>
        <key>DEEPSEEK_KEY</key>
        <string>${DEEPSEEK_KEY}</string>
        <key>DEFAULT_MODEL</key>
        <string>${DEFAULT_MODEL}</string>
        <key>PROXY_PORT</key>
        <string>${PROXY_PORT}</string>
        <key>ADMIN_PORT</key>
        <string>${ADMIN_PORT}</string>
        <key>TLS_PORT</key>
        <string>${TLS_PORT}</string>
        <key>REDIS_HOST</key>
        <string>127.0.0.1</string>
        <key>REDIS_PORT</key>
        <string>6379</string>
        <key>REDIS_DB</key>
        <string>0</string>
        <key>CONNECT_PORT</key>
        <string>8443</string>
        <key>DEFAULT_REASONING_EFFORT</key>
        <string>max</string>
        <key>MAX_POSITION_EMBEDDINGS</key>
        <string>1000000</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/logs/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/logs/stderr.log</string>
</dict>
</plist>
PLISTEOF

mkdir -p "$HOME/Library/LaunchAgents"
cp /tmp/com.jindx.proxy.plist "$PLIST_PATH"

# ── 配置 Claude Code ──────────────────────────────────
log "配置 Claude Code..."
CLAUDE_PROFILE_DIR="$HOME/.claude/profiles"
mkdir -p "${CLAUDE_PROFILE_DIR}"
cat > "${CLAUDE_PROFILE_DIR}/deepseek.json" << CLAUDE_EOF
{
  "env": {
    "ANTHROPIC_API_KEY": "${DEEPSEEK_KEY}",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:${PROXY_PORT}",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "${DEFAULT_MODEL}",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "${DEFAULT_MODEL}",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "${DEFAULT_MODEL}",
    "ANTHROPIC_MODEL": "${DEFAULT_MODEL}"
  },
  "model": "sonnet",
  "skipDangerousModePermissionPrompt": true
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
echo "    launchctl unload ${PLIST_PATH}    卸载服务"
echo ""
echo "  代理地址:     http://127.0.0.1:${PROXY_PORT}"
echo "  管理面板:     http://127.0.0.1:${ADMIN_PORT}"
echo ""
echo "  Claude Code 配置已写入:"
echo "    ${CLAUDE_PROFILE_DIR}/deepseek.json"
echo "  使用方式:"
echo "    claude --profile deepseek"
echo ""
echo "  Codex CLI 配置（在终端执行）:"
echo "    export OPENAI_BASE_URL=http://127.0.0.1:${PROXY_PORT}"
echo "    export OPENAI_API_KEY=${DEEPSEEK_KEY}"
echo "    codex"
echo ""
echo "  hosts 劫持的域名:"
echo "    api.openai.com, chatgpt.com, auth.openai.com,"
echo "    chat.openai.com, ab.chatgpt.com → 127.0.0.1"
echo ""
echo "  端口转发:"
echo "    127.0.0.1:443 → 127.0.0.1:${TLS_PORT} (pfctl)"
echo ""
