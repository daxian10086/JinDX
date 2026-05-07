# Chat-to-Responses Proxy 说明

将 OpenAI Responses API / Anthropic Messages API 翻译为 DeepSeek Chat Completions API，支持 Codex CLI 和 Claude Code。

## 快速开始

在新服务器上三步完成部署：

```bash
git clone https://github.com/daxian10086/JinDX.git
cd JinDX
sudo ./deploy.sh
```

脚本交互式询问 DeepSeek API Key（其余配置回车默认即可），自动完成：

1. 安装系统依赖（python3、redis、iptables-persistent 等）
2. 安装 Python 包（fastapi、uvicorn、httpx、redis）
3. 配置 `/etc/hosts` DNS 劫持 + iptables NAT 规则 + 规则持久化
4. 创建 systemd 服务并启动
5. 验证服务健康状态

部署完成后访问 `http://<服务器IP>:8090` 进入管理面板，所有参数即时调整即时生效。

## 架构

```
Codex CLI ─── OPENAI_BASE_URL ───▶ nyro (19530) ───▶ proxy (8080) ───▶ api.deepseek.com (198.18.18.41)
                                    ▲                        │
                                    │  Anthropic Messages     │  /v1/responses → /v1/chat/completions
Claude Code ─ ANTHROPIC_BASE_URL ───┘                        │
                                                             ▼
HTTPS 流量 ─── /etc/hosts 劫持 → 127.0.0.1:443 ─── iptables DNAT ───▶ proxy TLS (8444) ───▶ proxy (8080)
                                                                        ▲
管理面板 ─── http://127.0.0.1:8090 ──────────────────────────────────────┘
```

### 网络配置

- **iptables (NAT OUTPUT)**:
  ```
  规则1: 放行 DeepSeek API (198.18.18.41:443) → 直连，代理需要访问上游
  规则2: DNAT 127.0.0.1:443 → 127.0.0.1:8444   → 仅拦截本地劫持流量
  ```
  其他所有 HTTPS 流量直连，不再受拦截影响。

- **DNS 劫持**: `/etc/hosts` 将 `api.openai.com`、`chatgpt.com`、`auth.openai.com`、`chat.openai.com` 指向 `127.0.0.1`

- **规则持久化**: `netfilter-persistent` 服务，规则保存在 `/etc/iptables/rules.v4`，重启自动恢复

- **TLS 证书**: 自签名，SAN 包含 localhost、api.openai.com、api.deepseek.com、chatgpt.com（首次启动自动生成）

### 三个 systemd 服务

| 服务 | 端口 | 说明 |
|------|------|------|
| `nyro.service` | 19530 (proxy) / 19531 (admin) | AI Gateway，路由 gpt-5.5 → deepseek-v4-pro |
| `connect-to-nyro.service` | 8443 | HTTP CONNECT 隧道，TLS 终止后转发到 nyro |
| `chat-responses-proxy.service` (jindx) | 8080 / 8444 / 8090 | 核心代理 + TLS 直连 + Web 管理界面 |

## 文件清单

```
chat-to-responses-proxy/
├── proxy.py                         # 主程序
├── start.sh                         # 开发环境快速启动脚本
├── deploy.sh                        # 新服务器一键部署脚本
├── git-push.sh                      # 通过 API 推送提交（绕过网络限制）
├── requirements.txt                 # Python 依赖
├── chat-responses-proxy.service     # systemd 服务模板
├── .gitignore
├── certs/
│   ├── tls.key                      # TLS 私钥（自动生成）
│   └── tls.crt                      # 自签名证书（自动生成）
└── README.md

~/.config/
├── proxy-config.json                # 运行时参数（Web UI 修改后自动保存）
└── systemd/user/
    ├── nyro.service
    ├── connect-to-nyro.service
    └── chat-responses-proxy.service

~/.claude/
└── profiles/deepseek.json           # Claude Code 配置，指向 nyro

~/.config/environment.d/
└── codex-proxy.conf                 # Codex 环境变量 OPENAI_BASE_URL
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/chat/completions` | 直接透传 DeepSeek |
| POST | `/v1/responses` | Responses→Chat 翻译 |
| WS | `/v1/responses` | WebSocket 翻译 |
| POST/WS | `/backend-api/codex/responses` | Codex 专用路由 |
| POST | `/v1/messages` | Anthropic Messages 透传 |
| GET | `/v1/models` | 模型列表 |
| GET | `/health` | 健康检查 |
| GET | `/stats` | 请求统计（管理面板用） |

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
- **请求统计**: 总请求、活跃流、错误率、缓存命中率，实时刷新

## 关键实现细节

### 多工具调用修复

DeepSeek 要求同一轮的所有 tool_calls 必须在**一个** assistant 消息中：
```json
{"role": "assistant", "content": null, "tool_calls": [A, B, C]}
```
代理会将 Codex 发送的多个独立 function_call 合并到一条消息。

### 网页抓取

发现用户消息中的 URL 后，代理**预先抓取**网页内容注入到对话上下文，模型直接使用内容而无需发起 web_fetch 工具调用。绕过 Codex 沙箱的网络限制。

### 推理缓存

DeepSeek 思考模式要求所有 assistant 消息都携带 `reasoning_content`。代理缓存每轮对话的推理内容（Redis 优先，内存兜底），下一轮注入到所有 assistant 消息中保证格式正确。

### Anthropic 透传

直接转发 Claude Code 的 Anthropic Messages API 请求到 DeepSeek 的 `/anthropic/v1/messages` 端点，不做翻译。

## 一键部署

```bash
# 在新服务器上
git clone https://github.com/daxian10086/JinDX.git
cd JinDX
sudo ./deploy.sh
# 交互式输入 DeepSeek API Key，其余配置默认即可
```

部署脚本自动完成：系统依赖安装 → Python 包安装 → hosts 劫持 → iptables 规则 → systemd 服务 → 启动验证。

## 常用命令

```bash
# 查看服务状态
systemctl status jindx          # 部署版（system 级）
systemctl --user status nyro chat-responses-proxy connect-to-nyro  # 开发版（user 级）

# 重启所有服务
systemctl restart jindx
systemctl --user restart nyro chat-responses-proxy connect-to-nyro

# 查看日志
journalctl -u jindx -f
journalctl --user -u chat-responses-proxy -f

# 测试代理
curl -s http://127.0.0.1:8080/health

# 调整参数
curl -X POST http://127.0.0.1:8090/config \
  -H "Content-Type: application/json" \
  -d '{"temperature": 0.7, "reasoning_effort": "high"}'

# 推送代码（正常网络）
git push origin master

# 推送代码（网络受限时通过 API）
./git-push.sh
```
