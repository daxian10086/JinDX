"""运行时配置管理，线程安全。"""

import json
import os
import sys
import logging
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)


# ── 跨平台默认配置路径 ─────────────────────────────────────────────

def _default_config_path() -> str:
    if "PROXY_CONFIG_FILE" in os.environ:
        return os.environ["PROXY_CONFIG_FILE"]
    system = os.name  # "posix" (Linux/macOS) or "nt" (Windows)
    if system == "nt":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return str(Path(appdata) / "proxy-config.json")
    # macOS
    if system == "posix" and os.uname().sysname == "Darwin":
        return str(Path.home() / "Library" / "Application Support" / "proxy-config.json")
    # Linux / other POSIX
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return str(Path(xdg) / "proxy-config.json")


# ── 环境变量默认值（集中定义，与原始 proxy.py 完全一致）──────────────

DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE", "https://api.deepseek.com")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY", "sk-your-deepseek-api-key")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8080"))
CONNECT_PORT = int(os.environ.get("CONNECT_PORT", "8443"))
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "deepseek-v4-pro")
TLS_PORT = int(os.environ.get("TLS_PORT", "8444"))
ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "8090"))
REASONING_CACHE_MAX = int(os.environ.get("REASONING_CACHE_MAX", "10"))
REASONING_CACHE_TTL = int(os.environ.get("REASONING_CACHE_TTL", "600"))
DEFAULT_REASONING_EFFORT = os.environ.get("DEFAULT_REASONING_EFFORT", None)
MAX_POSITION_EMBEDDINGS = int(os.environ.get("MAX_POSITION_EMBEDDINGS", "1000000"))

TOOL_USE_ENFORCEMENT = os.environ.get(
    "TOOL_USE_ENFORCEMENT",
    "You MUST use the provided tools to accomplish the user's task. "
    "Never respond with just text explaining what you would do — actually call the tools. "
    "If tools are available, use them to take real actions: run commands, read/write files, search the web. "
    "Do NOT ask the user for confirmation before using tools. Just do it.",
)

CONFIG_FILE = Path(_default_config_path())


# ── TLS 证书路径 ───────────────────────────────────────────────────────

# 打包为 exe (PyInstaller) 时，证书目录放在 exe 同目录下以保证可写
if getattr(sys, 'frozen', False):
    CERT_DIR = Path(sys.executable).parent / "certs"
else:
    CERT_DIR = Path(__file__).parent.parent / "certs"
CERT_FILE = CERT_DIR / "tls.crt"
KEY_FILE = CERT_DIR / "tls.key"


# ── 默认配置 ──────────────────────────────────────────────────────────

_DEFAULT_CONFIG = {
    "deepseek_key": DEEPSEEK_KEY,
    "deepseek_base": DEEPSEEK_BASE,
    "default_model": DEFAULT_MODEL,
    "model_mapping": {"gpt-5.5": "deepseek-v4-pro", "gpt-5": "deepseek-v4-pro"},
    "reasoning_effort": None,
    "max_position_embeddings": MAX_POSITION_EMBEDDINGS,
    "max_output_tokens": 16384,
    "temperature": None,
    "top_p": None,
    "tool_use_enforcement": True,
    "tool_use_prompt": TOOL_USE_ENFORCEMENT,
    "web_fetch_max_urls": 5,
    "web_fetch_timeout": 10,
    "web_fetch_max_body": 80000,
    "enable_reasoning_cache": True,
    "reasoning_cache_ttl": 600,
    "claude_deepseek_key": "",
    "claude_deepseek_base": "https://api.deepseek.com",
    "claude_default_model": "deepseek-v4-pro",
    "claude_reasoning_effort": None,
    "claude_max_position_embeddings": 1000000,
    "claude_max_output_tokens": 16384,
    "claude_temperature": None,
    "claude_top_p": None,
    "claude_strip_thinking": True,
    "claude_skip_dangerous_mode": True,
    "claude_deepseek_thinking_enabled": False,
}


class RuntimeConfig:
    """线程安全的运行时配置，替代全局 _runtime_config dict。"""

    def __init__(self):
        self._lock = Lock()
        self._config: dict = {}
        self._load()

    # ── 私有方法 ──────────────────────────────────────────────────────

    def _load(self):
        """从文件加载配置，合并默认值。"""
        cfg = dict(_DEFAULT_CONFIG)
        try:
            if CONFIG_FILE.exists():
                raw = CONFIG_FILE.read_bytes()
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("gbk")
                saved = json.loads(text)
                cfg.update(saved)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load config: {e}")
        self._config = cfg

    def _save(self) -> None:
        """持久化当前配置到文件。"""
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(self._config, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to save config: {e}")

    # ── 公共接口 ──────────────────────────────────────────────────────

    def get(self, key: str, default=None):
        """读取配置项，线程安全。"""
        with self._lock:
            return self._config.get(key, default)

    def update(self, updates: dict) -> None:
        """批量更新配置项（复制-修改-替换，非原地修改），线程安全。"""
        allowed = {
            "deepseek_key", "deepseek_base", "default_model",
            "model_mapping", "reasoning_effort", "max_position_embeddings",
            "max_output_tokens", "temperature", "top_p",
            "tool_use_enforcement", "tool_use_prompt",
            "web_fetch_max_urls", "web_fetch_timeout", "web_fetch_max_body",
            "enable_reasoning_cache", "reasoning_cache_ttl",
            "claude_deepseek_key", "claude_deepseek_base", "claude_default_model",
            "claude_reasoning_effort", "claude_max_position_embeddings",
            "claude_max_output_tokens", "claude_temperature", "claude_top_p",
            "claude_strip_thinking", "claude_skip_dangerous_mode",
            "claude_deepseek_thinking_enabled",
        }
        with self._lock:
            new_cfg = dict(self._config)
            for key, val in updates.items():
                if key in allowed:
                    new_cfg[key] = val
            self._config = new_cfg
        self._save()
        logger.info(f"Config updated: {json.dumps({k: v for k, v in updates.items() if k in allowed}, ensure_ascii=False)[:500]}")

    def reload(self) -> None:
        """重新从文件加载配置。"""
        self._load()

    @property
    def config_dict(self) -> dict:
        """返回只读快照（用于管理 API 序列化）。"""
        with self._lock:
            return dict(self._config)


# ── 全局单例 ─────────────────────────────────────────────────────────

config = RuntimeConfig()


# ── Codex 配置自动生成 ──────────────────────────────────────────────
# 启动时将 jindx 运行时配置写入 ~/.codex/config.toml，
# 确保 Codex CLI 始终连接到本地代理。API Key 不写入明文，
# 由 Codex 通过 OPENAI_API_KEY 环境变量注入，实现自动脱敏。

CODEX_CONFIG_TOML = """\
# 此文件由 JinDx Proxy 启动时自动生成，请勿手动编辑。
# 如需修改模型或参数，请通过管理面板 http://127.0.0.1:{admin_port} 操作。

model = "gpt-5.5"
model_reasoning_effort = "xhigh"
model_provider = "openai_http"

[model_providers.openai_http]
name = "JinDx Proxy (DeepSeek)"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = true
base_url = "http://127.0.0.1:{proxy_port}"

{projects_section}

[tui.model_availability_nux]
"gpt-5.5" = 4

[features]
terminal_resize_reflow = true
"""


def write_codex_config_toml(*, force: bool = False) -> None:
    """在代理首次启动时写入 Codex CLI 配置文件（脱敏）。

    默认仅当 ~/.codex/config.toml 不存在时才写入（首次初始化），
    已有配置则跳过，避免覆盖用户手动修改或 Codex 自动追加的字段。

    设置 force=True 时强制重新写入（用于管理面板手动启用）。

    API Key 不在 config.toml 中写入，而是由 Codex 通过
    OPENAI_API_KEY 环境变量读取。
    """
    target_dir = Path.home() / ".codex"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "config.toml"

    # 非强制模式：文件已存在则跳过
    if not force and target_file.exists():
        logger.debug(f"Codex config already exists, skip writing: {target_file}")
        return

    # 构建代理需要的核心配置
    home_path = str(Path.home()).replace("\\", "/")
    projects_section = f'[projects."{home_path}"]\ntrust_level = "trusted"'
    if os.name == "nt":
        projects_section += '\n\n[projects."C:/"]\ntrust_level = "trusted"'
        projects_section += '\n\n[projects."D:/"]\ntrust_level = "trusted"'

    content = CODEX_CONFIG_TOML.format(
        admin_port=ADMIN_PORT,
        proxy_port=PROXY_PORT,
        projects_section=projects_section,
    )

    target_file.write_text(content, encoding="utf-8")
    logger.info(f"Codex config initialized at {target_file} (key: via OPENAI_API_KEY env)")


# ── Claude Code 配置自动生成 ────────────────────────────────────────
# 启动时将 jindx 运行时配置写入 ~/.claude/settings.json，
# 确保 Claude Code 始终连接到本地代理。Auth Token 用占位符，
# 实际 API Key 由 ANTHROPIC_API_KEY 环境变量注入，实现自动脱敏。


# ---- Claude hosts ----------
_CLAUDE_HOSTS = [
    ("127.0.0.1", "api.anthropic.com"),
]

def _ensure_claude_hosts_hijack() -> None:
    """Auto-add api.anthropic.com -> 127.0.0.1 to Windows hosts file."""
    if os.name != "nt":
        return
    hosts_path = r"C:\Windows\System32\drivers\etc\hosts"
    try:
        with open(hosts_path, "r", encoding="utf-8") as f:
            existing = f.read()
    except Exception:
        existing = ""
    changed = False
    entries_to_add = []
    for ip, domain in _CLAUDE_HOSTS:
        line = f"{ip} {domain}"
        if line not in existing:
            entries_to_add.append(line)
            changed = True
    if not changed:
        logger.debug("Claude hosts hijack already in place")
        return
    try:
        with open(hosts_path, "a", encoding="utf-8") as f:
            for entry in entries_to_add:
                f.write(f"\n{entry}")
        logger.info(
            "Claude hosts hijack written: "
            + ", ".join(entries_to_add)
        )
    except PermissionError:
        logger.warning(
            "Cannot write hosts file (need admin). "
            "Run start.ps1 as admin or add manually: "
            + ", ".join(entries_to_add)
        )


def write_claude_settings_json(*, force: bool = False) -> None:
    """在代理首次启动时写入 Claude Code settings.json（脱敏）。

    默认仅当 ~/.claude/settings.json 不存在时才写入（首次初始化），
    已有配置则跳过，避免覆盖用户自定义的 settings。

    设置 force=True 时强制重新写入（用于管理面板手动启用）。

    Auth token 使用占位符，Claude Code 通过 ANTHROPIC_API_KEY
    环境变量读取实际的 API Key。用户无需手动设置 settings.json。
    """
    target_dir = Path.home() / ".claude"
    target_dir.mkdir(parents=True, exist_ok=True)
    settings_path = target_dir / "settings.json"

    # 非强制模式：文件已存在则跳过
    if not force and settings_path.exists():
        logger.debug(f"Claude settings already exists, skip writing: {settings_path}")
        return

    settings = {
        "env": {
            "ANTHROPIC_AUTH_TOKEN": "proxy-placeholder",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{PROXY_PORT}",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": config.get("default_model", DEFAULT_MODEL),
            "ANTHROPIC_DEFAULT_OPUS_MODEL": config.get("default_model", DEFAULT_MODEL),
            "ANTHROPIC_DEFAULT_SONNET_MODEL": config.get("default_model", DEFAULT_MODEL),
            "ANTHROPIC_MODEL": config.get("default_model", DEFAULT_MODEL),
        },
        "model": "sonnet",
        "skipDangerousModePermissionPrompt": config.get("claude_skip_dangerous_mode", True),
        "theme": "auto",
    }

    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    logger.info(
        f"Claude settings {'re-' if force and settings_path.exists() else ''}written to {settings_path} "
        f"(auth: via ANTHROPIC_API_KEY env, model: {settings['env']['ANTHROPIC_MODEL']})"
    )
    _ensure_claude_hosts_hijack()


# ── 配置清除 & 状态查询 ─────────────────────────────────────────────

def clear_codex_config_toml() -> None:
    """删除 Codex CLI 配置文件，停用 Codex 代理写入。"""
    target = Path.home() / ".codex" / "config.toml"
    if target.exists():
        target.unlink()
        logger.info(f"Codex config removed: {target}")


def clear_claude_settings_json() -> None:
    """移除 Claude Code settings.json 中的代理配置，保留其他用户设置。

    只清除 env 块中的代理相关字段，不覆盖用户的 tipsHistory、
    projects 等个性化数据。"""
    target = Path.home() / ".claude" / "settings.json"
    if target.exists():
        try:
            data = json.loads(target.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
        data.pop("env", None)
        data.setdefault("model", "sonnet")
        data.setdefault("theme", "auto")
    else:
        data = {"model": "sonnet", "theme": "auto"}
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    logger.info(f"Claude proxy config removed from {target}")


def get_proxy_status() -> dict:
    """查询代理配置文件状态，供管理面板开关使用。

    返回 codex_enabled 和 claude_enabled 两个布尔值。
    """
    codex_config = Path.home() / ".codex" / "config.toml"
    codex_enabled = codex_config.exists()

    claude_settings = Path.home() / ".claude" / "settings.json"
    claude_enabled = False
    if claude_settings.exists():
        try:
            data = json.loads(claude_settings.read_text())
            env = data.get("env", {})
            claude_enabled = env.get("ANTHROPIC_BASE_URL", "").startswith("http://127.0.0.1")
        except (json.JSONDecodeError, OSError):
            pass

    return {"codex_enabled": codex_enabled, "claude_enabled": claude_enabled}
