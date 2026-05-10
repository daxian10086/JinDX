# JinDX — DeepSeek API Proxy

将 OpenAI Responses API / Anthropic Messages API 翻译为 DeepSeek Chat Completions API，支持 Codex CLI 和 Claude Code。

## 快速开始

### Linux

```bash
git clone https://github.com/daxian10086/JinDX.git
cd JinDX
sudo bash platform/deploy.sh
```

交互式输入 DeepSeek API Key，自动完成 systemd 服务部署、hosts 劫持、iptables 规则。默认源不可用时自动切到阿里云/清华镜像。

### macOS

**前置要求**：Python 3.10+，可选 `brew install redis` 启用推理缓存。

```bash
git clone https://github.com/daxian10086/JinDX.git
cd JinDX

# 方式一：一键安装（含 launchd 后台服务、hosts 劫持、pfctl 端口转发）
bash platform/install-macos.sh

# 方式二：手动启动（开发/测试）
pip3 install fastapi "uvicorn[standard]" httpx redis cryptography
bash platform/start.sh
```

install-macos.sh 会：安装 Python 依赖 → 创建 launchd 服务 → 配置 /etc/hosts 劫持 → 配置 pfctl 端口转发(443→8444) → 配置 Claude Code profile → 输出 Codex CLI 环境变量。配置文件存入 `~/Library/Application Support/proxy-config.json`。默认源不可用时自动切到清华镜像。

**launchd 服务管理**：

```bash
launchctl list com.jindx.proxy              # 查看状态
launchctl stop com.jindx.proxy              # 停止
launchctl start com.jindx.proxy             # 启动
launchctl unload ~/Library/LaunchAgents/com.jindx.proxy.plist  # 卸载
```

日志：`/opt/jindx/logs/stdout.log`、`stderr.log`。

### Windows

**前置要求**：Python 3.10+（安装时勾选"Add Python to PATH"），可选安装 Redis for Windows。

```powershell
git clone https://github.com/daxian10086/JinDX.git
cd JinDX

# 右键 PowerShell → 以管理员身份运行
.\platform\start.ps1
```

start.ps1 以管理员运行时自动配置 hosts 劫持（5 个域名 → 127.0.0.1）和 netsh 端口转发（127.0.0.1:443 → 8444），启动后输出 Codex CLI / Claude Code 环境变量。非管理员也可运行（缺少劫持功能）。

**自定义参数**：

```powershell
$env:DEEPSEEK_KEY="sk-xxx"
$env:PROXY_PORT="9000"
.\platform\start.ps1
```

配置文件：`%APPDATA%\proxy-config.json`。

### 开发环境（所有平台通用）

```bash
pip install fastapi "uvicorn[standard]" httpx redis cryptography
bash platform/start.sh          # Linux/macOS
platform\start.bat           # Windows
```

部署完成后访问 `http://127.0.0.1:8090` 进入管理面板，所有参数即时调整即时生效。

### 客户端配置

代理启动后，各客户端的连接方式：

```bash
# Codex CLI
export OPENAI_BASE_URL=http://127.0.0.1:8080
export OPENAI_API_KEY=你的DeepSeek-Key
codex

# Claude Code（Linux/macOS）
export ANTHROPIC_BASE_URL=http://127.0.0.1:8080
export ANTHROPIC_API_KEY=你的DeepSeek-Key
claude
```

也可以通过 hosts 劫持 + 端口转发让 Codex 无感知走代理（见各平台的安装脚本）。

## 平台差异说明

| 功能 | Linux | macOS | Windows |
|------|-------|-------|---------|
| 后台服务 | systemd | launchd | 手动启动（可加入 startup） |
| hosts 劫持 | /etc/hosts | /etc/hosts | `C:\Windows\System32\drivers\etc\hosts` |
| 端口转发 (443→8444) | iptables DNAT | pfctl rdr | netsh portproxy |
| TLS 证书 | cryptography / openssl | cryptography | cryptography |
| 安装脚本 | deploy.sh | install-macos.sh | start.ps1（管理员） |
| 开发启动 | start.sh | start.sh | platform\start.bat / start.ps1 |
| 配置文件 | `~/.config/proxy-config.json` | `~/Library/Application Support/` | `%APPDATA%\` |
| 镜像回退 | apt→阿里云, pip→清华 | pip→清华, brew提示 | pip→清华 |

## 环境变量

所有环境变量均有默认值，仅 `DEEPSEEK_KEY` 必须修改。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEEPSEEK_KEY` | — | **必填**，DeepSeek API Key |
| `DEEPSEEK_BASE` | `https://api.deepseek.com` | 上游 API 地址 |
| `DEFAULT_MODEL` | `deepseek-v4-pro` | 默认模型 |
| `PROXY_PORT` | `8080` | HTTP/WS 代理端口 |
| `TLS_PORT` | `8444` | 直接 TLS 端口（配合 hosts 劫持） |
| `CONNECT_PORT` | `8443` | CONNECT 隧道端口 |
| `ADMIN_PORT` | `8090` | Web 管理面板端口 |
| `REDIS_HOST` | `127.0.0.1` | Redis 地址 |
| `REDIS_PORT` | `6379` | Redis 端口 |
| `REDIS_DB` | `0` | Redis 数据库编号 |
| `DEFAULT_REASONING_EFFORT` | — | 推理强度 (min/low/medium/high/max) |
| `MAX_POSITION_EMBEDDINGS` | `1000000` | 最大上下文长度 |
| `REASONING_CACHE_MAX` | `10` | 每会话最多缓存的推理条数 |
| `REASONING_CACHE_TTL` | `600` | 推理缓存有效期（秒） |
| `PROXY_CONFIG_FILE` | 平台自适应 | 配置文件路径 |

## 架构

```
                          ┌──────────────────────────────────────────┐
                          │              JinDX Proxy                 │
                          │                                          │
  Codex CLI ──────────────┼──▶ 8444 (TLS 直连) ─┐                    │
  (OPENAI_BASE_URL)       │                      │                   │
                          │  Claude Code ───────▶ 8080 (HTTP/WS)     │
                          │  (ANTHROPIC_BASE_URL)  ▲    ▲             │
                          │                       │    │             │
                          │  127.0.0.1:443 ───────┘  (hosts+端口转发)  │
                          │  (Codex 无感知劫持)                        │
                          │                                          │
                          │  8090 (Admin UI) ─── Web 管理界面          │
                          └──────────────────────────────────────────┘
                                          │
                                          ▼
                                  api.deepseek.com
                               (Chat Completions API)
```

**四个服务端口**：

| 端口 | 协议 | 用途 |
|------|------|------|
| 8080 | HTTP/WS | Responses API 翻译 + SSE 流式 + WebSocket |
| 8443 | TCP/TLS | HTTP CONNECT 隧道（Codex CLI 的 https_proxy） |
| 8444 | HTTPS | 直接 TLS 终止（配合 hosts 劫持拦截 api.openai.com:443） |
| 8090 | HTTP | Web 管理面板 |

**三条流量路径**：

1. **HTTP 直连** — 设置 `OPENAI_BASE_URL`/`ANTHROPIC_BASE_URL=http://127.0.0.1:8080`，直接走 HTTP 代理
2. **HTTPS 劫持** — 无需任何环境变量配置，hosts 将 `api.openai.com` 指向 127.0.0.1，端口转发将 :443 → :8444，Codex 无感知走代理
3. **CONNECT 隧道** — 设置 `HTTPS_PROXY=http://127.0.0.1:8443`，适用于 Codex CLI 的 https_proxy 模式

**请求处理流程**：

```
Responses API 请求
  → 模型名映射（gpt-5.5 → deepseek-v4-pro）
  → 首轮：网页预取 + tool_use 提示注入（后续轮次跳过，保护 prompt cache）
  → 推理缓存注入（上轮 thinking → assistant 消息，thinking 关闭时跳过）
  → 转换为 Chat Completions 格式
  → 发送到 api.deepseek.com
  → SSE 翻译回 Responses 事件流
  → 缓存本轮推理内容
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/messages`、`/messages` | Anthropic Messages→Chat 翻译（Claude Code） |
| GET | `/v1/models/claude` | Claude Code 模型列表 |
| POST | `/v1/chat/completions`、`/chat/completions` | Chat Completions 透传 |
| POST | `/v1/responses`、`/responses` | Responses→Chat 翻译 |
| WS | `/v1/responses`、`/responses` | WebSocket Responses 翻译 |
| POST/WS | `/backend-api/codex/responses` | Codex 专用 Responses 路由 |
| GET | `/v1/models`、`/models` | 模型列表 |
| GET | `/health` | 健康检查 |
| GET | `/backend-api/codex/models` | Codex 模型目录 |
| POST | `/backend-api/codex/analytics-events/events` | Codex 遥测桩 |
| GET | `/backend-api/plugins/featured` | 插件列表（空） |
| POST | `/backend-api/wham/apps` | WHAM 桩 |
| ANY | `/backend-api/{path}` | Codex 后端兜底 |

## 管理界面

浏览器打开 `http://127.0.0.1:8090`，两个标签页：

### Codex 标签

- **上游连接**：API Key（Codex 和 Claude 共用）、Base URL、默认模型
- **模型映射**：OpenAI 模型名 → DeepSeek 模型名
- **生成参数**：推理强度、上下文窗口、最大输出、Temperature、Top P
- **网页抓取**：最大 URL 数、超时、响应体上限
- **推理缓存**：开关 + TTL（Redis 优先，内存兜底）

### Claude 标签

- **上游连接**：独立 API Key（留空则回退到 Codex Key）、Base URL、默认模型
- **生成参数**：推理强度、上下文窗口、最大输出、Temperature、Top P
- **模型选项**：过滤 Thinking、跳过危险模式提示、启用 DeepSeek Thinking

### 右侧面板

- **实时统计**：运行时间、请求数、活跃流、错误率、缓存命中率、活跃会话
- **终端环境变量**：一键复制 Codex CLI / Claude Code 环境变量
- **系统状态**：DeepSeek API 连通性、Redis 状态
- **上游错误 / 最近日志**：错误排查

所有配置保存即时生效，无需重启。

## 文件清单

```
JinDX/
├── proxy.py                         # 主程序入口
├── jindx/                           # Python 模块
│   ├── __init__.py
│   ├── config.py                    # 运行时配置（线程安全 + 跨平台路径）
│   ├── stats.py                     # 统计计数 + 日志缓冲 + 敏感信息脱敏
│   ├── web_fetch.py                 # URL 检测 / 预取 / 抓取
│   ├── cache.py                     # 推理缓存（Redis + 内存双写 + Session 隔离 + 自动重连）
│   ├── protocol.py                  # Responses ↔ Chat 格式翻译
│   ├── routes.py                    # HTTP / SSE / WebSocket 路由 + 共享 httpx 客户端
│   ├── codex.py                     # Codex RPC 模拟 + 模型目录
│   ├── claude.py                    # Anthropic Messages ↔ DeepSeek 协议翻译
│   ├── admin.py                     # Web 管理 API + 内嵌 HTML UI
│   └── tunnel.py                    # TLS CA+Server 证书链生成 + CONNECT 隧道
├── platform/                        # 平台安装和启动脚本
│   ├── deploy.sh                    # Linux 一键部署（systemd + iptables）
│   ├── install-macos.sh             # macOS 安装脚本（launchd + pfctl）
│   ├── start.sh                     # Linux/macOS 开发启动
│   ├── platform\start.bat                    # Windows CMD 启动
│   ├── start.ps1                    # Windows PowerShell 启动
│   ├── com.jindx.proxy.plist        # macOS launchd 服务定义
│   └── chat-responses-proxy.service # Linux systemd 服务模板
├── scripts/                         # 工具脚本
│   ├── repair.sh                    # Shell 诊断修复脚本
│   └── repair.py                    # Python 诊断修复模块
├── requirements.txt                 # Python 依赖
└── README.md
```

## 关键实现细节

### 多工具调用合并

DeepSeek 要求同一轮的所有 tool_calls 必须在一个 assistant 消息中。代理自动将 Codex 发送的多个独立 function_call 合并为一条符合要求的消息。

### 网页预取

发现用户消息中的 URL 后，代理预先抓取网页内容注入对话上下文，模型直接使用内容而无需发起 web_fetch 工具调用，绕过 Codex 沙箱的网络限制。**仅首轮注入**，避免后续轮次修改消息破坏 DeepSeek prompt cache。

### 推理缓存

DeepSeek 思考模式要求所有 assistant 消息都携带 `reasoning_content`。代理缓存每轮对话的推理内容（Redis 优先，内存兜底），下一轮注入到历史 assistant 消息中。

- Codex 与 Claude 的推理缓存完全隔离（Redis key 格式：`reasoning:{codex|claude}:{session_id}`）
- 默认关闭 thinking（避免 `reasoning_content` 400 错误），可在管理面板开启
- 缓存注入仅在 thinking 启用时生效，关闭时保持消息原样以最大化 prompt cache 命中率

### Prompt Cache 保护

为确保 DeepSeek 的 prompt caching 正常工作，代理采取以下策略：

- `reasoning_content` 仅在有缓存数据时才注入，不会填充空字符串
- `tool_use` 强制提示、`web_fetch` 预取仅首轮注入
- Claude 通道在 thinking 关闭时完全跳过缓存注入，保持消息体不变

### TLS 证书管理

首次启动自动生成 CA+Server 证书链（CA: CN=JinDX-CA，Server 由 CA 签发），SAN 覆盖 localhost、api.openai.com 等 7 个域名。有效期 5 年。优先使用 `cryptography` 库（跨平台），回退到 `openssl` CLI。CA 证书单独输出为 `ca.pem` 方便用户导入系统信任。

### 日志安全

全局日志过滤器自动脱敏 API Key 和 Bearer token，写入环形缓冲区和 Python logger 的内容均被过滤。

### Session 隔离

Codex 和 Claude 分别使用 `"codex"` 和 `"claude"` 作为缓存 source 前缀，Redis key 和内存缓存完全隔离。Session ID 使用 SHA256 哈希生成，降低碰撞概率。

## 常用命令

```bash
# 测试代理
curl -s http://127.0.0.1:8080/health

# 调整参数
curl -X POST http://127.0.0.1:8090/config \
  -H "Content-Type: application/json" \
  -d '{"temperature": 0.7, "reasoning_effort": "high"}'

# 推送代码（网络受限时参考 git-push-github-ssh skill）
```

### Linux (systemd)

```bash
sudo systemctl status jindx       # 查看状态
sudo systemctl restart jindx      # 重启
sudo journalctl -u jindx -f       # 查看日志
```

### macOS (launchd)

```bash
launchctl list com.jindx.proxy              # 查看状态
launchctl stop com.jindx.proxy              # 停止
launchctl start com.jindx.proxy             # 启动
```

### Windows

以管理员身份运行 PowerShell：`.\platform\start.ps1`
