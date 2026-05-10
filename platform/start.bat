@echo off
REM JinDX Windows CMD 启动脚本
REM
REM 用法:
REM   start.bat
REM   set DEEPSEEK_KEY=sk-xxx && start.bat
REM   set PROXY_PORT=9000 && start.bat
REM
REM 功能:
REM   1. 自动安装 Python 依赖（清华镜像回退）
REM   2. 启动 JinDX 代理服务
REM   3. 输出 Codex CLI 环境变量配置
REM
REM 注意：hosts 劫持和端口转发需要管理员权限，请先以管理员身份运行：
REM   .\start.ps1  （推荐，带 hosts 劫持和端口转发）
REM

cd /d "%~dp0.."

if not defined PROXY_PORT set PROXY_PORT=8080
if not defined ADMIN_PORT set ADMIN_PORT=8090
if not defined DEEPSEEK_KEY set DEEPSEEK_KEY=sk-your-deepseek-api-key
if not defined DEEPSEEK_BASE set DEEPSEEK_BASE=https://api.deepseek.com
if not defined DEFAULT_MODEL set DEFAULT_MODEL=deepseek-v4-pro
if not defined DEFAULT_REASONING_EFFORT set DEFAULT_REASONING_EFFORT=max
if not defined MAX_POSITION_EMBEDDINGS set MAX_POSITION_EMBEDDINGS=1000000
if not defined REDIS_HOST set REDIS_HOST=127.0.0.1
if not defined REDIS_PORT set REDIS_PORT=6379
if not defined REDIS_DB set REDIS_DB=0
if not defined CONNECT_PORT set CONNECT_PORT=8443
if not defined TLS_PORT set TLS_PORT=8444

echo.
echo ==========================================
echo   JinDX Proxy for Windows (CMD)
echo ==========================================
echo.
echo   Target:   %DEEPSEEK_BASE%/v1/chat/completions
echo   Model:    %DEFAULT_MODEL%
echo   HTTP/WS:  http://127.0.0.1:%PROXY_PORT%
echo   Admin:    http://127.0.0.1:%ADMIN_PORT%
echo.

REM ── 安装 Python 依赖 ────────────────────────────

python -c "import fastapi,uvicorn,httpx,redis,cryptography" 2>NUL
if errorlevel 1 (
    echo  [*] 安装 Python 依赖...
    pip install -q fastapi "uvicorn[standard]" httpx redis cryptography 2>NUL
    if errorlevel 1 (
        echo  [*] 默认 PyPI 不可用，切换到清华镜像...
        pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi "uvicorn[standard]" httpx redis cryptography
    )
)

REM ── 提示 Codex CLI 配置 ─────────────────────────

echo   Codex CLI 配置（在新的终端执行）:
echo     set OPENAI_BASE_URL=http://127.0.0.1:%PROXY_PORT%
echo     set OPENAI_API_KEY=你的 DeepSeek Key
echo     codex
echo.
echo   推荐以管理员身份运行 start.ps1（自动配置 hosts 劫持 + 端口转发）
echo.

python proxy.py
pause
