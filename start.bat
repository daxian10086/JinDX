@echo off
chcp 65001 >NUL
REM JinDX Windows CMD 启动脚本
REM
REM 用法:
REM   start.bat
REM   set DEEPSEEK_KEY=sk-xxx && start.bat
REM   set PROXY_PORT=9000 && start.bat
REM
REM 功能:
REM   1. 自动检查/配置 DeepSeek API Key（无 key 时交互式输入）
REM   2. 自动安装 Python 依赖（清华镜像回退）
REM   3. 启动 JinDX 代理服务
REM   4. 输出 Codex CLI 环境变量配置
REM
REM 注意：hosts 劫持和端口转发需要管理员权限，请先以管理员身份运行：
REM   .\start.ps1  （推荐，带 hosts 劫持和端口转发）

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

REM ── 检查/配置 DeepSeek API Key ─────────────────

set CONFIG_FILE=%APPDATA%\proxy-config.json
set NEED_KEY=0
if "%DEEPSEEK_KEY%"=="sk-your-deepseek-api-key" set NEED_KEY=1
if "%DEEPSEEK_KEY%"=="" set NEED_KEY=1

if "%NEED_KEY%"=="1" (
    if exist "%CONFIG_FILE%" (
        powershell -NoProfile -Command "$k=(Get-Content '%CONFIG_FILE%' -Raw -Encoding UTF8|ConvertFrom-Json|Select-Object -ExpandProperty deepseek_key -ErrorAction SilentlyContinue); if($k -and $k -ne 'sk-your-deepseek-api-key'){Write-Output $k}" > "%TEMP%\jindx_saved_key.txt" 2>NUL
        set /p SAVED_KEY=<"%TEMP%\jindx_saved_key.txt" 2>NUL
        del "%TEMP%\jindx_saved_key.txt" 2>NUL
        if not "%SAVED_KEY%"=="" (
            set DEEPSEEK_KEY=%SAVED_KEY%
            set NEED_KEY=0
            echo  [+] 从配置文件加载 API Key
        )
    )
)

if "%NEED_KEY%"=="1" (
    echo.
    echo ==========================================
    echo   首次运行 - 请配置 DeepSeek API Key
    echo ==========================================
    echo.
    echo   获取 Key: https://platform.deepseek.com/api_keys
    echo.
    set /p DEEPSEEK_KEY="  请输入你的 DeepSeek API Key (sk-...): "
    if "%DEEPSEEK_KEY%"=="" (
        echo.
        echo  [X] 未输入 API Key，无法启动
        echo      可通过环境变量设置: set DEEPSEEK_KEY=sk-xxx ^&^& start.bat
        pause
        exit /b 1
    )
    REM 持久化到配置文件
    powershell -NoProfile -Command "$f='%CONFIG_FILE%';$d=Split-Path $f -Parent;if(-not(Test-Path $d)){New-Item -ItemType Directory $d -Force|Out-Null};$c=@{};if(Test-Path $f){try{$e=Get-Content $f -Raw -Encoding UTF8|ConvertFrom-Json;foreach($p in $e.PSObject.Properties){$c[$p.Name]=$p.Value}}catch{}}$c['deepseek_key']='%DEEPSEEK_KEY%';$c|ConvertTo-Json -Depth 5|Set-Content $f -Encoding UTF8"
    if %ERRORLEVEL% equ 0 (
        echo  [+] API Key 已保存到 %CONFIG_FILE%
    ) else (
        echo  [!] 配置文件保存失败，Key 仅对本次运行生效
    )
)

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
echo     set OPENAI_API_KEY=%%DEEPSEEK_KEY%%
echo     codex
echo.
echo   推荐以管理员身份运行 start.ps1（自动配置 hosts 劫持 + 端口转发）
echo.

python proxy.py
pause
