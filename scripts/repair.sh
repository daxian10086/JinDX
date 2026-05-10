#!/bin/bash
# JinDX 自动诊断修复脚本
# 用法: ./repair.sh [--dry-run] [--daemon]
#
#   ./repair.sh           一次性诊断修复
#   ./repair.sh --dry-run  仅诊断，不修复
#   ./repair.sh --daemon   守护模式：轮询检测，发现故障自动修复
#
# 可配合 crontab: */5 * * * * /home/wdmms123/jindx/repair.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="/tmp/jindx-repair.log"

log()  { echo "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG_FILE"; }
warn() { echo "[$(date '+%H:%M:%S')] ⚠ $1" | tee -a "$LOG_FILE"; }
err() { echo "[$(date '+%H:%M:%S')] ✗ $1" | tee -a "$LOG_FILE"; }

# ── 检测系统环境 ───────────────────────────────────────

SYSTEMCTL="systemctl"
if [ -f "$HOME/.config/systemd/user/chat-responses-proxy.service" ]; then
    SYSTEMCTL="systemctl --user"
    SERVICE="chat-responses-proxy"
elif [ -f "/etc/systemd/system/jindx.service" ]; then
    SERVICE="jindx"
else
    err "未找到 systemd service 文件"
    exit 1
fi

INSTALL_DIR="/home/wdmms123/jindx"
PORTS=(8080 8444 8090)

# ── 诊断函数 ────────────────────────────────────────────

check_service() {
    if $SYSTEMCTL is-active --quiet "$SERVICE" 2>/dev/null; then
        return 0
    fi
    return 1
}

get_journal_recent() {
    $SYSTEMCTL status "$SERVICE" --no-pager -n 30 2>/dev/null || true
    echo "---"
    journalctl ${SYSTEMCTL#systemctl } -u "$SERVICE" --no-pager -n 5 2>/dev/null || true
}

diagnose_failure() {
    local journal
    journal=$(journalctl ${SYSTEMCTL#systemctl } -u "$SERVICE" --no-pager -n 50 2>/dev/null || true)

    # Pattern 1: CHDIR 错误
    if echo "$journal" | grep -q "Changing to the requested working directory failed"; then
        echo "CHDIR_ERROR"
        return
    fi

    # Pattern 2: 端口冲突
    if echo "$journal" | grep -q "address already in use"; then
        echo "PORT_CONFLICT"
        return
    fi

    # Pattern 3: 脚本路径错误
    if echo "$journal" | grep -q "No such file or directory" | grep -q "proxy.py"; then
        echo "SCRIPT_MISSING"
        return
    fi

    echo "UNKNOWN"
}

# ── 修复函数 ────────────────────────────────────────────

fix_chdir() {
    local svc_file=""
    if [ -f "$HOME/.config/systemd/user/chat-responses-proxy.service" ]; then
        svc_file="$HOME/.config/systemd/user/chat-responses-proxy.service"
    elif [ -f "/etc/systemd/system/jindx.service" ]; then
        svc_file="/etc/systemd/system/jindx.service"
    fi

    if [ -z "$svc_file" ]; then
        err "找不到 service 文件"
        return 1
    fi

    sed -i "s|^WorkingDirectory=.*|WorkingDirectory=${INSTALL_DIR}|" "$svc_file"
    log "已修复 WorkingDirectory → ${INSTALL_DIR}"
    return 0
}

fix_port_conflict() {
    local freed=0
    for port in "${PORTS[@]}"; do
        if command -v fuser &>/dev/null; then
            fuser -k "${port}/tcp" 2>/dev/null && { log "已释放端口 ${port}"; freed=1; }
        fi
    done
    # 备用：kill 所有残留 proxy.py 进程
    pkill -f "proxy.py" 2>/dev/null && { log "已终止残留 proxy 进程"; freed=1; }
    sleep 1
    return 0
}

fix_script_path() {
    local svc_file=""
    if [ -f "$HOME/.config/systemd/user/chat-responses-proxy.service" ]; then
        svc_file="$HOME/.config/systemd/user/chat-responses-proxy.service"
    elif [ -f "/etc/systemd/system/jindx.service" ]; then
        svc_file="/etc/systemd/system/jindx.service"
    fi

    if [ -z "$svc_file" ]; then
        err "找不到 service 文件"
        return 1
    fi

    sed -i "s|^ExecStart=.*|ExecStart=/usr/bin/python3 ${INSTALL_DIR}/proxy.py|" "$svc_file"
    log "已修复 ExecStart → ${INSTALL_DIR}/proxy.py"
    return 0
}

reload_and_restart() {
    $SYSTEMCTL daemon-reload 2>/dev/null || true
    $SYSTEMCTL restart "$SERVICE" 2>/dev/null || true
    sleep 2
    if $SYSTEMCTL is-active --quiet "$SERVICE" 2>/dev/null; then
        log "服务已成功重启"
        return 0
    fi
    err "服务重启后仍未 active"
    return 1
}

# ── 主流程 ──────────────────────────────────────────────

DRY_RUN=false
DAEMON_MODE=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --daemon)  DAEMON_MODE=true ;;
        --help|-h)
            echo "用法: $0 [--dry-run] [--daemon]"
            echo "  --dry-run  仅诊断，不修复"
            echo "  --daemon   守护模式：持续监控自动修复"
            exit 0
            ;;
    esac
done

run_once() {
    if check_service; then
        log "服务正常运行"
        return 0
    fi

    local failure
    failure=$(diagnose_failure)
    warn "检测到故障: ${failure}"

    if [ "$DRY_RUN" = true ]; then
        warn "[dry-run] 跳过修复"
        get_journal_recent
        return 1
    fi

    local repaired=false
    case "$failure" in
        CHDIR_ERROR)
            fix_chdir && repaired=true
            ;;
        PORT_CONFLICT)
            fix_port_conflict && repaired=true
            ;;
        SCRIPT_MISSING)
            fix_script_path && repaired=true
            ;;
        UNKNOWN|*)
            err "未知故障，尝试通用恢复..."
            fix_port_conflict
            ;;
    esac

    if reload_and_restart; then
        log "修复完成"
    else
        err "修复失败，查看日志: journalctl ${SYSTEMCTL#systemctl } -u $SERVICE -f"
    fi
}

if [ "$DAEMON_MODE" = true ]; then
    log "进入守护模式 (每 30 秒检查一次)..."
    while true; do
        if ! check_service; then
            run_once
        fi
        sleep 30
    done
else
    run_once
fi
