#!/bin/bash
# JinDX 一键部署脚本
# 用法: sudo ./deploy.sh
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
echo "  JinDX Chat-Responses Proxy 部署脚本"
echo "========================================="
echo ""

# ── 检查 root ──────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    err "请用 sudo 运行: sudo ./deploy.sh"
fi

# ── 用户配置 ─────────────────────────────────────────
INSTALL_DIR="/home/wdmms123/jindx"
SERVICE_USER="${SUDO_USER:-$(logname 2>/dev/null || echo 'root')}"

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
echo "  运行用户:   ${SERVICE_USER}"
echo "  代理端口:   ${PROXY_PORT}"
echo "  管理面板:   ${ADMIN_PORT}"
echo "  默认模型:   ${DEFAULT_MODEL}"
echo ""
read -p "确认安装? [Y/n] " CONFIRM
if [ "$CONFIRM" = "n" ] || [ "$CONFIRM" = "N" ]; then
    err "取消安装"
fi

# ── 安装系统依赖 ─────────────────────────────────────
echo ""
log "安装系统依赖..."

# apt 镜像回退：默认源不可用则切到阿里云镜像
if ! apt-get update -qq 2>/dev/null; then
    warn "默认 apt 源不可用，切换到阿里云镜像..."
    sed -i 's|http://.*archive.ubuntu.com|http://mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true
    sed -i 's|https://.*archive.ubuntu.com|http://mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true
    apt-get update -qq || err "apt 镜像源也不可用，请检查网络"
fi

apt-get install -y -qq python3 python3-pip python3-venv redis-server openssl \
    iptables netfilter-persistent 2>&1 | tail -5

# ── 安装 Python 依赖 ─────────────────────────────────
log "安装 Python 依赖..."

# pip 镜像回退：默认源不可用则切到清华镜像
pip3 install --break-system-packages -q fastapi "uvicorn[standard]" httpx redis cryptography 2>/dev/null || {
    warn "默认 PyPI 不可用，切换到清华镜像..."
    pip3 install --break-system-packages -q \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        fastapi "uvicorn[standard]" httpx redis cryptography
}

# ── 创建目录 ─────────────────────────────────────────
log "创建安装目录..."
mkdir -p "${INSTALL_DIR}/certs"

# ── 复制文件 ─────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "${SCRIPT_DIR}/proxy.py" ]; then
    log "从 ${SCRIPT_DIR} 复制文件..."
    cp "${SCRIPT_DIR}/proxy.py" "${INSTALL_DIR}/"
else
    warn "未找到 proxy.py，请手动复制到 ${INSTALL_DIR}/"
fi

# ── 设置 hosts 劫持 ──────────────────────────────────
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
        echo "$entry" >> /etc/hosts
    fi
done

# ── 设置 iptables ────────────────────────────────────
log "配置 iptables 规则..."

# DeepSeek API 的 IP (允许直连)
DEEPSEEK_IP="198.18.18.41"

iptables -t nat -F OUTPUT 2>/dev/null || true
iptables -t nat -A OUTPUT -d "${DEEPSEEK_IP}" -p tcp --dport 443 -j ACCEPT
iptables -t nat -A OUTPUT -d 127.0.0.1/32 -p tcp --dport 443 -j DNAT --to-destination 127.0.0.1:8444

# 持久化
mkdir -p /etc/iptables
iptables-save > /etc/iptables/rules.v4
systemctl enable netfilter-persistent 2>/dev/null || true

# ── 创建 systemd 服务 ────────────────────────────────
log "创建 systemd 服务..."

cat > /etc/systemd/system/jindx.service << SERVICE_EOF
[Unit]
Description=JinDX Chat-Responses API Proxy
After=network-online.target redis-server.service
Wants=network-online.target redis-server.service

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
Environment="DEEPSEEK_BASE=https://api.deepseek.com"
Environment="DEEPSEEK_KEY=${DEEPSEEK_KEY}"
Environment="DEFAULT_MODEL=${DEFAULT_MODEL}"
Environment="PROXY_PORT=${PROXY_PORT}"
Environment="ADMIN_PORT=${ADMIN_PORT}"
Environment="REDIS_HOST=127.0.0.1"
Environment="REDIS_PORT=6379"
Environment="REDIS_DB=0"
Environment="CONNECT_PORT=8443"
Environment="TLS_PORT=8444"
Environment="DEFAULT_REASONING_EFFORT=max"
Environment="MAX_POSITION_EMBEDDINGS=1000000"
Environment="TOOL_USE_ENFORCEMENT=You MUST use the provided tools to accomplish the user's task. Never respond with just text explaining what you would do — actually call the tools. If tools are available, use them to take real actions: run commands, read/write files, search the web. Do NOT ask the user for confirmation before using tools. Just do it."
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/proxy.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# ── 启动服务 ─────────────────────────────────────────
log "启动服务..."
systemctl daemon-reload
systemctl enable redis-server 2>/dev/null || true
systemctl restart redis-server 2>/dev/null || true
systemctl enable jindx
systemctl restart jindx

sleep 2

# ── 配置 Claude Code ──────────────────────────────────
log "配置 Claude Code..."
CLAUDE_PROFILE_DIR="/home/${SERVICE_USER}/.claude/profiles"
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
chown -R "${SERVICE_USER}:${SERVICE_USER}" "/home/${SERVICE_USER}/.claude"

# ── 验证 ─────────────────────────────────────────────
echo ""
if systemctl is-active --quiet jindx; then
    log "JinDX 服务已启动!"
else
    warn "服务可能未正常启动，查看日志: journalctl -u jindx -f"
fi

echo ""
echo "========================================="
echo "  部署完成!"
echo "========================================="
echo ""
echo "  代理地址:     http://127.0.0.1:${PROXY_PORT}"
echo "  管理面板:     http://127.0.0.1:${ADMIN_PORT}"
echo "  健康检查:     curl http://127.0.0.1:${PROXY_PORT}/health"
echo ""
echo "  Claude Code 配置已写入:"
echo "    ${CLAUDE_PROFILE_DIR}/deepseek.json"
echo "  使用方式: claude --profile deepseek"
echo ""
echo "  常用命令:"
echo "    systemctl status jindx      查看状态"
echo "    systemctl restart jindx     重启"
echo "    journalctl -u jindx -f      查看日志"
echo ""
