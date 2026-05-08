# Chat-to-Responses Proxy 说明

将 OpenAI Responses API / Anthropic Messages API 翻译为 DeepSeek Chat Completions API，支持 Codex CLI 和 Claude Code。

## 快速开始

### Linux

```bash
git clone https://github.com/daxian10086/JinDX.git
cd JinDX
sudo ./deploy.sh
```

脚本交互式询问 DeepSeek API Key，自动完成 systemd 服务部署、hosts 劫持、iptables 规则。详见下方"Linux 部署"。

### macOS

**前置要求**：Python 3.10+，可选 `brew install redis` 启用推理缓存。

```bash
git clone https://github.com/daxian10086/JinDX.git
cd JinDX

# 方式一：一键安装（含 launchd 后台服务）
bash install-macos.sh

# 方式二：手动启动（开发/测试）
pip3 install fastapi "uvicorn[standard]" httpx redis cryptography
./start.sh
```

install-macos.sh 会：安装 Python 依赖 → 创建 launchd 服务 → 配置 Claude Code profile → 启动。配置文件自动存入 `~/Library/Application Support/proxy-config.json`。

**launchd 服务管理**：

```bash
launchctl list com.jindx.proxy              # 查看状态
launchctl stop com.jindx.proxy              # 停止
launchctl start com.jindx.proxy             # 启动
launchctl unload ~/Library/LaunchAgents/com.jindx.proxy.plist  # 卸载
```

日志位置：`/opt/jindx/logs/stdout.log` 和 `stderr.log`。

### Windows

**前置要求**：Python 3.10+（安装时勾选"Add Python to PATH"），可选安装 Redis for Windows 启用推理缓存。

```powershell
git clone https://github.com/daxian10086/JinDX.git
cd JinDX

# PowerShell（推荐）
.\start.ps1

# 或 CMD
start.bat
```

首次运行自动安装依赖（fastapi, uvicorn, httpx, redis, cryptography）。脚本已配置好所有默认环境变量，可直接用。

**自定义参数**：

```powershell
# PowerShell 设置环境变量后启动
$env:DEEPSEEK_KEY="sk-xxx"
$env:PROXY_PORT="9000"
.\start.ps1
```

```cmd
REM CMD 方式
set DEEPSEEK_KEY=sk-xxx
set PROXY_PORT=9000
start.bat
```

配置文件位置：`%APPDATA%\proxy-config.json`（通常为 `C:\Users\<用户名>\AppData\Roaming\proxy-config.json`）。

**设置开机自启**（可选）：创建 `start.ps1` 的快捷方式放入 `shell:startup` 文件夹，或使用任务计划程序。

### 开发环境（所有平台通用）

```bash
pip install fastapi "uvicorn[standard]" httpx redis cryptography
./start.sh          # Linux/macOS
# 或 Windows
start.bat / start.ps1
```

部署完成后访问 `http://127.0.0.1:8090` 进入管理面板，所有参数即时调整即时生效。

## 平台差异说明

| 功能 | Linux | macOS | Windows |
|------|-------|-------|---------|
| 后台服务 | systemd | launchd | N/A（手动启动） |
| DNS 劫持 | /etc/hosts + iptables | /etc/hosts | hosts 文件 |
| TLS 证书 | openssl/cryptography | cryptography | cryptography |
| 安装脚本 | deploy.sh | install-macos.sh | start.ps1 |
| 开发启动 | start.sh | start.sh | start.bat / start.ps1 |

## 架构

```
                          ┌──────────────────────────────────────┐
                          │            JinDX Proxy               │
                          │                                      │
  Codex CLI ──────────────┼──▶ 8444 (TLS 直连) ─┐                │
  (OPENAI_BASE_URL)       │                      │                │
                          │  Claude Code ───────▶ 8080 (HTTP/WS) │
                          │  (ANTHROPIC_BASE_URL)  ▲    ▲         │
                          │                       │    │         │
                          │  HTTPS_PROXY ───▶ 8443 (CONNECT+TLS) │
                          │  (Codex https_proxy)  ─────┘         │
                          │                                      │
                          │  8090 (Admin UI) ─── Web 管理界面     │
                          └──────────────────────────────────────┘
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

**两条流量路径**：

1. **HTTP 直连** — Claude Code 设置 `ANTHROPIC_BASE_URL=http://127.0.0.1:8080`，直接走 HTTP 代理
2. **HTTPS 劫持** — Codex CLI 请求 `https://api.openai.com` → `/etc/hosts` 指向 127.0.0.1 → iptables/loopback 将 :443 转发到 :8444（TLS 代理），或通过 CONNECT 隧道 :8443

**请求处理流程**：

```
Responses API 请求
  → 模型名映射（gpt-5.5 → deepseek-v4-pro）
  → 网页预取（发现 URL 自动抓取注入上下文）
  → 推理缓存注入（上轮 thinking → assistant 消息）
  → 转换为 Chat Completions 格式
  → 发送到 api.deepseek.com
  → SSE 翻译回 Responses 事件流
  → 缓存本轮推理内容
```

## 文件清单

```
chat-to-responses-proxy/
├── proxy.py                         # 主程序入口
├── jindx/                           # 模块
│   ├── config.py                    # 运行时配置（线程安全）
│   ├── stats.py                     # 统计计数
│   ├── web_fetch.py                 # 网页抓取
│   ├── cache.py                     # 推理缓存（Redis + 内存）
│   ├── protocol.py                  # Responses ↔ Chat 翻译
│   ├── routes.py                    # HTTP/SSE/WebSocket 路由
│   ├── codex.py                     # Codex RPC 模拟
│   ├── admin.py                     # 管理 API + Web UI
│   └── tunnel.py                    # TLS 证书 + CONNECT 隧道
├── start.sh                         # Linux/macOS 开发启动
├── start.bat                        # Windows CMD 启动
├── start.ps1                        # Windows PowerShell 启动
├── deploy.sh                        # Linux 一键部署
├── install-macos.sh                 # macOS 安装脚本
├── com.jindx.proxy.plist            # macOS launchd 服务定义
├── chat-responses-proxy.service     # Linux systemd 服务模板
├── requirements.txt                 # Python 依赖
└── README.md
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/chat/completions` | 直接透传 DeepSeek |
| POST | `/v1/responses` | Responses→Chat 翻译 |
| WS | `/v1/responses` | WebSocket 翻译 |
| POST/WS | `/backend-api/codex/responses` | Codex 专用路由 |
| GET | `/v1/models` | 模型列表 |
| GET | `/health` | 健康检查 |

## 管理界面

浏览器打开 `http://127.0.0.1:8090` 可调整以下全部参数，保存即时生效：

- **模型映射**: OpenAI 模型名 → DeepSeek 模型名
- **推理强度**: min/low/medium/high/max
- **最大输出 token 数**
- **最大上下文长度** (max_position_embeddings)：默认 1M tokens
- **Temperature / Top P**
- **Tool Use 强制调用**: 开关 + 自定义提示词
- **网页抓取**: URL 数量上限、超时、响应体上限
- **推理缓存**: 开关 + TTL（Redis 优先，内存兜底）

## 常用命令

```bash
# 测试代理
curl -s http://127.0.0.1:8080/health

# 调整参数
curl -X POST http://127.0.0.1:8090/config \
  -H "Content-Type: application/json" \
  -d '{"temperature": 0.7, "reasoning_effort": "high"}'
```

### Linux (systemd)

```bash
systemctl status jindx       # 查看状态
systemctl restart jindx      # 重启
journalctl -u jindx -f       # 查看日志
```

### macOS (launchd)

```bash
launchctl list com.jindx.proxy              # 查看状态
launchctl stop com.jindx.proxy              # 停止
launchctl start com.jindx.proxy             # 启动
```

### Windows

直接运行 `start.bat` 或 `start.ps1`。
