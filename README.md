# Chat-to-Responses Proxy 说明

将 OpenAI Responses API / Anthropic Messages API 翻译为 DeepSeek Chat Completions API，支持 Codex CLI 和 Claude Code。

## 快速开始

### Linux

```bash
git clone https://github.com/daxian10086/JinDX.git
cd JinDX
sudo ./deploy.sh
```

### macOS

```bash
git clone https://github.com/daxian10086/JinDX.git
cd JinDX
bash install-macos.sh
```

### Windows

```powershell
git clone https://github.com/daxian10086/JinDX.git
cd JinDX
.\start.ps1
```

### 开发环境（所有平台通用）

```bash
pip install fastapi "uvicorn[standard]" httpx redis cryptography
./start.sh          # Linux/macOS
# 或
start.bat           # Windows CMD
```

脚本交互式询问 DeepSeek API Key（其余配置回车默认即可），自动完成：

1. 安装系统依赖 / Python 包
2. 配置 /etc/hosts DNS 劫持（Linux/macOS）
3. 创建后台服务并启动（systemd / launchd）
4. 验证服务健康状态

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
Codex CLI ─── OPENAI_BASE_URL ───▶ proxy (8080) ───▶ api.deepseek.com
                                     ▲
                                     │  /v1/responses → /v1/chat/completions
Claude Code ─ ANTHROPIC_BASE_URL ────┘
                                      ▼
HTTPS 流量 ─── hosts 劫持 → 127.0.0.1:443 ─── proxy TLS (8444)
                                                  ▲
管理面板 ─── http://127.0.0.1:8090 ────────────────┘
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
