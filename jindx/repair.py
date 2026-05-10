"""
服务自动诊断与修复模块。

诊断模式 (ServiceFailure):
  1. CHDIR_ERROR      — WorkingDirectory 不存在 → 修正为脚本所在目录
  2. PORT_CONFLICT    — 端口被残留进程占用 → fuser -k 释放
  3. SCRIPT_MISSING   — proxy.py 路径不正确 → 修正 ExecStart
  4. UNKNOWN          — 未知原因，输出最近日志供排查

工作模式:
  - CLI: python3 -m jindx.repair          诊断并自动修复
  - CLI: python3 -m jindx.repair --dry-run  仅诊断，不修复
  - API: from jindx.repair import repair_service 可在 admin.py 中暴露
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────

INSTALL_DIR = Path("/home/wdmms123/jindx")
SERVICE_FILE = Path.home() / ".config/systemd/user/chat-responses-proxy.service"
SYSTEM_SERVICE_FILE = Path("/etc/systemd/system/jindx.service")
SERVICE_NAME = "chat-responses-proxy"
PORTS = [8080, 8444, 8090]


class FailureKind(Enum):
    CHDIR_ERROR = auto()
    PORT_CONFLICT = auto()
    SCRIPT_MISSING = auto()
    HEALTHY = auto()
    UNKNOWN = auto()


@dataclass
class Diagnosis:
    kind: FailureKind
    detail: str = ""
    fixed: bool = False
    journal_lines: list = field(default_factory=list)


# ── 诊断 ───────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """运行命令，返回 (returncode, stdout, stderr)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"


def _get_service_unit() -> tuple[str, str]:
    """检测应当使用的 systemd 单元名称和 scope。"""
    if SERVICE_FILE.exists():
        return "user", SERVICE_NAME
    if SYSTEM_SERVICE_FILE.exists():
        return "system", "jindx"
    return "user", SERVICE_NAME


def _journal_recent(scope: str, name: str, lines: int = 30) -> str:
    """获取最近日志。"""
    if scope == "user":
        cmd = ["journalctl", "--user", "-u", name, "--no-pager", "-n", str(lines)]
    else:
        cmd = ["journalctl", "-u", name, "--no-pager", "-n", str(lines)]
    _, stdout, _ = _run(cmd)
    return stdout


def diagnose() -> Diagnosis:
    """诊断服务状态，返回 Diagnosis 结构。"""
    scope, name = _get_service_unit()

    # 先检查服务是否在运行
    if scope == "user":
        rc, _, _ = _run(["systemctl", "--user", "is-active", "--quiet", name])
    else:
        rc, _, _ = _run(["systemctl", "is-active", "--quiet", name])

    if rc == 0:
        return Diagnosis(kind=FailureKind.HEALTHY, detail="服务正在运行")

    journal = _journal_recent(scope, name)

    # Pattern 1: CHDIR 错误
    if "Changing to the requested working directory failed" in journal:
        m = re.search(r"WorkingDirectory=(\S+)", journal)
        bad_dir = m.group(1) if m else "(unknown)"
        return Diagnosis(
            kind=FailureKind.CHDIR_ERROR,
            detail=f"WorkingDirectory 不可访问: {bad_dir}",
            journal_lines=journal.splitlines(),
        )

    # Pattern 2: 端口冲突
    if "address already in use" in journal:
        port_matches = re.findall(r"attempting to bind on address.*?:(\d+)", journal)
        ports = port_matches if port_matches else [str(p) for p in PORTS]
        return Diagnosis(
            kind=FailureKind.PORT_CONFLICT,
            detail=f"端口已被占用: {', '.join(ports)}",
            journal_lines=journal.splitlines(),
        )

    # Pattern 3: 脚本路径错误
    if "No such file or directory" in journal and "proxy.py" in journal:
        return Diagnosis(
            kind=FailureKind.SCRIPT_MISSING,
            detail="proxy.py 路径不对或不存在",
            journal_lines=journal.splitlines(),
        )

    return Diagnosis(
        kind=FailureKind.UNKNOWN,
        detail="journal 中未匹配已知错误模式",
        journal_lines=journal.splitlines(),
    )


# ── 修复 ───────────────────────────────────────────────────

def _fix_chdir() -> bool:
    """修复 WorkingDirectory 指向脚本所在目录。"""
    scope, _ = _get_service_unit()
    svc_file = SERVICE_FILE if scope == "user" else SYSTEM_SERVICE_FILE

    if not svc_file.exists():
        logger.error(f"service 文件不存在: {svc_file}")
        return False

    content = svc_file.read_text()
    new_content = re.sub(
        r"^WorkingDirectory=.*$",
        f"WorkingDirectory={INSTALL_DIR}",
        content,
        flags=re.MULTILINE,
    )
    svc_file.write_text(new_content)
    logger.info(f"已修复 WorkingDirectory → {INSTALL_DIR}")
    return True


def _fix_port_conflict() -> bool:
    """释放被占用的端口。"""
    if not shutil.which("fuser"):
        logger.error("fuser 命令不可用")
        return False

    success = False
    for port in PORTS:
        rc, out, _ = _run(["fuser", "-k", f"{port}/tcp"])
        if rc == 0:
            logger.info(f"已释放端口 {port}")
            success = True
    return success


def _fix_script_path() -> bool:
    """修复 ExecStart 路径。"""
    scope, _ = _get_service_unit()
    svc_file = SERVICE_FILE if scope == "user" else SYSTEM_SERVICE_FILE

    if not svc_file.exists():
        logger.error(f"service 文件不存在: {svc_file}")
        return False

    content = svc_file.read_text()
    new_content = re.sub(
        r"^ExecStart=.*$",
        f"ExecStart=/usr/bin/python3 {INSTALL_DIR}/proxy.py",
        content,
        flags=re.MULTILINE,
    )
    svc_file.write_text(new_content)
    logger.info(f"已修复 ExecStart → {INSTALL_DIR}/proxy.py")
    return True


def _reload_and_restart() -> tuple[bool, str]:
    """daemon-reload 并重启服务。"""
    scope, name = _get_service_unit()
    prefix = ["systemctl", "--user"] if scope == "user" else ["systemctl"]

    rc, _, err = _run(prefix + ["daemon-reload"])
    if rc != 0:
        return False, f"daemon-reload 失败: {err}"

    rc, _, err = _run(prefix + ["restart", name])
    if rc != 0:
        return False, f"restart 失败: {err}"

    time.sleep(2)
    rc, _, _ = _run(prefix + ["is-active", "--quiet", name])
    if rc == 0:
        return True, "服务已成功重启"
    return False, "服务重启后仍未 active"


def repair_service(dry_run: bool = False) -> Diagnosis:
    """诊断并自动修复服务。返回修复后的诊断结果。"""
    diag = diagnose()

    if diag.kind == FailureKind.HEALTHY:
        return diag

    logger.warning(f"检测到故障 [{diag.kind.name}]: {diag.detail}")

    if dry_run:
        return diag

    fixes = {
        FailureKind.CHDIR_ERROR: (_fix_chdir, "WorkingDirectory"),
        FailureKind.PORT_CONFLICT: (_fix_port_conflict, "端口"),
        FailureKind.SCRIPT_MISSING: (_fix_script_path, "ExecStart"),
    }

    fix_fn, label = fixes.get(diag.kind, (None, None))
    if fix_fn is None:
        logger.error(f"无法自动修复: {diag.kind.name}")
        return diag

    if not fix_fn():
        logger.error(f"修复失败: {label}")
        return diag

    ok, msg = _reload_and_restart()
    diag.fixed = ok
    diag.detail = msg
    return diag


# ── CLI 入口 ─────────────────────────────────────────────────

def _cli():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    dry_run = "--dry-run" in sys.argv

    diag = repair_service(dry_run=dry_run)

    print(f"\n诊断结果: {diag.kind.name}")
    print(f"详情:     {diag.detail}")
    if diag.fixed:
        print("修复:     成功")
    elif diag.kind == FailureKind.HEALTHY:
        print("状态:     正常")
    elif dry_run:
        print("修复:     跳过 (dry-run)")
    else:
        print("修复:     失败")

    if diag.journal_lines:
        print(f"\n最近日志 ({len(diag.journal_lines)} 行):")
        for line in diag.journal_lines[-8:]:
            print(f"  {line[:200]}")


if __name__ == "__main__":
    _cli()
