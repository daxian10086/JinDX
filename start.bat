@echo off
REM Chat <-> Responses API Proxy for DeepSeek V4 Pro — Windows CMD 启动脚本
REM
REM 用法:
REM   start.bat
REM   set PROXY_PORT=9000 && start.bat
REM   set DEEPSEEK_KEY=sk-xxx && start.bat

cd /d "%~dp0"

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

echo === Chat-Responses Proxy ===
echo Target:  %DEEPSEEK_BASE%/v1/chat/completions
echo Model:   %DEFAULT_MODEL%
echo Port:    %PROXY_PORT% (admin: %ADMIN_PORT%)
echo Redis:   %REDIS_HOST%:%REDIS_PORT%
echo.

REM 检查并安装依赖（默认源不可用则切到清华镜像）
python -c "import fastapi,uvicorn,httpx,redis,cryptography" 2>NUL
if errorlevel 1 (
    echo 安装 Python 依赖...
    pip install -q fastapi "uvicorn[standard]" httpx redis cryptography 2>NUL
    if errorlevel 1 (
        echo 默认 PyPI 不可用，切换到清华镜像...
        pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi "uvicorn[standard]" httpx redis cryptography
    )
)

python proxy.py
pause
