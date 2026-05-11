# JinDX Windows PowerShell 启动脚本
#
# 用法:
#   .\start.ps1
#   $env:DEEPSEEK_KEY="sk-xxx"; .\start.ps1
#   $env:PROXY_PORT=9000; .\start.ps1
#
# 功能：
#   1. 自动安装 Python 依赖（清华镜像回退）
#   2. 自动配置 hosts 劫持（将 api.openai.com 等指向 127.0.0.1）
#   3. 自动配置端口转发（netsh 将 443 -> 8444）
#   4. 启动 JinDX 代理服务
#   5. 输出 Codex CLI / Claude Code 环境变量配置

param()

Set-Location $PSScriptRoot

# -- 自动查找 Python -----------------------------------
# 优先使用 WorkBuddy 管理的 Python，其次用 PATH 中的，最后搜索常见位置

$pythonExe = $null
$managedPython = "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
$userPythonDir = "C:\Users\Administrator\AppData\Local\Programs\Python\Python313"
$userPython = "$userPythonDir\python.exe"

if (Test-Path $managedPython) {
    $pythonExe = $managedPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonExe = (Get-Command python).Source
} elseif (Test-Path $userPython) {
    $pythonExe = $userPython
}

if (-not $pythonExe) {
    Write-Host "  [*] 未找到 Python，正在自动安装 Python 3.13.12..." -ForegroundColor Yellow

    $pythonVersion = "3.13.12"
    $installerUrl = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-amd64.exe"
    $installerPath = "$env:TEMP\python-$pythonVersion-amd64.exe"

    try {
        # 下载安装包
        Write-Host "  [*] 下载中: $installerUrl" -ForegroundColor Gray
        Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing -ErrorAction Stop
        Write-Host "  [+] 下载完成" -ForegroundColor Green

        # 静默安装（仅当前用户，加入 PATH）
        Write-Host "  [*] 安装中..." -ForegroundColor Gray
        $installArgs = @(
            "/quiet",
            "InstallAllUsers=0",
            "PrependPath=1",
            "Include_test=0",
            "DefaultJustForMeTargetDir=$userPythonDir",
            "TargetDir=$userPythonDir"
        )
        Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait
        Remove-Item $installerPath -Force

        # 验证安装
        if (Test-Path $userPython) {
            $pythonExe = $userPython
            Write-Host "  [+] Python 3.13.12 安装成功" -ForegroundColor Green
        } else {
            Write-Host "  [X] Python 安装失败，请手动安装" -ForegroundColor Red
            Write-Host "      下载: https://www.python.org/downloads/" -ForegroundColor Yellow
            exit 1
        }
    } catch {
        Write-Host "  [X] 下载失败: $_" -ForegroundColor Red
        Write-Host "      请手动下载安装: https://www.python.org/downloads/" -ForegroundColor Yellow
        if (Test-Path $installerPath) { Remove-Item $installerPath -Force }
        exit 1
    }
}

Write-Host "  [+] Python: $pythonExe" -ForegroundColor Green

# pip 调用辅助函数（用 python -m pip，兼容没有 pip.exe 的环境）
function Invoke-Pip {
    & $pythonExe -m pip @args
}

# -- 环境变量默认值 ------------------------------------

$env:PROXY_PORT              = if ($env:PROXY_PORT)              { $env:PROXY_PORT }              else { "8080" }
$env:ADMIN_PORT              = if ($env:ADMIN_PORT)              { $env:ADMIN_PORT }              else { "8090" }
$env:DEEPSEEK_KEY            = if ($env:DEEPSEEK_KEY)            { $env:DEEPSEEK_KEY }            else { "sk-your-deepseek-api-key" }
$env:DEEPSEEK_BASE           = if ($env:DEEPSEEK_BASE)           { $env:DEEPSEEK_BASE }           else { "https://api.deepseek.com" }
$env:DEFAULT_MODEL           = if ($env:DEFAULT_MODEL)           { $env:DEFAULT_MODEL }           else { "deepseek-v4-pro" }
$env:DEFAULT_REASONING_EFFORT = if ($env:DEFAULT_REASONING_EFFORT) { $env:DEFAULT_REASONING_EFFORT } else { "max" }
$env:MAX_POSITION_EMBEDDINGS = if ($env:MAX_POSITION_EMBEDDINGS) { $env:MAX_POSITION_EMBEDDINGS } else { "1000000" }
$env:REDIS_HOST              = if ($env:REDIS_HOST)              { $env:REDIS_HOST }              else { "127.0.0.1" }
$env:REDIS_PORT              = if ($env:REDIS_PORT)              { $env:REDIS_PORT }              else { "6379" }
$env:REDIS_DB                = if ($env:REDIS_DB)                { $env:REDIS_DB }                else { "0" }
$env:CONNECT_PORT            = if ($env:CONNECT_PORT)            { $env:CONNECT_PORT }            else { "8443" }
$env:TLS_PORT                = if ($env:TLS_PORT)                { $env:TLS_PORT }                else { "8444" }

Write-Host ""
Write-Host "=========================================="  -ForegroundColor Cyan
Write-Host "  JinDX Proxy for Windows"                    -ForegroundColor Cyan
Write-Host "=========================================="  -ForegroundColor Cyan
Write-Host ""
Write-Host "  Target:   $env:DEEPSEEK_BASE/v1/chat/completions"
Write-Host "  Model:    $env:DEFAULT_MODEL"
Write-Host "  HTTP/WS:  http://127.0.0.1:$env:PROXY_PORT"
Write-Host "  TLS:      127.0.0.1:$env:TLS_PORT (for port forward)"
Write-Host "  CONNECT:  127.0.0.1:$env:CONNECT_PORT"
Write-Host "  Admin:    http://127.0.0.1:$env:ADMIN_PORT"
Write-Host ""

# -- 检查管理员权限（hosts 和 netsh 需要）----------------

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")

if (-not $isAdmin) {
    Write-Host "[!] 未以管理员身份运行" -ForegroundColor Yellow
    Write-Host "    hosts 劫持和端口转发需要管理员权限，将跳过这些步骤" -ForegroundColor Yellow
    Write-Host "    推荐：右键 PowerShell -> 以管理员身份运行，然后重新执行 .\start.ps1" -ForegroundColor Yellow
    Write-Host ""
}

# -- 步骤 1：配置 hosts 劫持 ----------------------------

if ($isAdmin) {
    $hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
    $hostsEntries = @(
        "127.0.0.1 api.openai.com",
        "127.0.0.1 chatgpt.com",
        "127.0.0.1 auth.openai.com",
        "127.0.0.1 chat.openai.com",
        "127.0.0.1 ab.chatgpt.com"
    )

    $hostsContent = Get-Content $hostsPath -Raw -ErrorAction SilentlyContinue
    $changed = $false
    foreach ($entry in $hostsEntries) {
        if ($hostsContent -notmatch [regex]::Escape($entry)) {
            Add-Content -Path $hostsPath -Value $entry
            Write-Host "  [+] hosts: $entry" -ForegroundColor Green
            $changed = $true
        }
    }
    if (-not $changed) {
        Write-Host "  [=] hosts 劫持已配置" -ForegroundColor Gray
    }
    # 刷新 DNS 缓存
    ipconfig /flushdns | Out-Null

    # -- 步骤 2：配置端口转发 (443 -> 8444) ------------------

    $fwRuleName = "JinDX Port Forward 443 to 8444"
    $existingRule = netsh interface portproxy show v4tov4 | Select-String "443"
    if (-not $existingRule) {
        netsh interface portproxy add v4tov4 listenport=443 listenaddress=127.0.0.1 connectport=$env:TLS_PORT connectaddress=127.0.0.1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [+] netsh: 127.0.0.1:443 -> 127.0.0.1:$env:TLS_PORT" -ForegroundColor Green
        }
    } else {
        Write-Host "  [=] 端口转发已配置" -ForegroundColor Gray
    }
}

# -- 步骤 3：安装 Python 依赖 ----------------------------

& $pythonExe -c "import fastapi,uvicorn,httpx,redis,cryptography" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [*] 安装 Python 依赖..." -ForegroundColor Yellow
    Invoke-Pip install -q fastapi "uvicorn[standard]" httpx redis cryptography 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [*] 默认 PyPI 不可用，切换到清华镜像..." -ForegroundColor Yellow
        Invoke-Pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi "uvicorn[standard]" httpx redis cryptography
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [X] 依赖安装失败，请手动安装" -ForegroundColor Red
            exit 1
        }
    }
    Write-Host "  [+] 依赖安装完成" -ForegroundColor Green
}

# -- 步骤 4：启动代理 ------------------------------------

Write-Host ""
Write-Host "=========================================="  -ForegroundColor Cyan
Write-Host "  Codex CLI 配置（在新的终端执行）"         -ForegroundColor Cyan
Write-Host "=========================================="  -ForegroundColor Cyan
Write-Host ""
Write-Host "  # PowerShell:"                               -ForegroundColor White
Write-Host '  $env:OPENAI_BASE_URL="http://127.0.0.1:'"$env:PROXY_PORT"'"' -ForegroundColor Green
Write-Host '  $env:OPENAI_API_KEY="<你的 DeepSeek Key>"'     -ForegroundColor Green
Write-Host "  codex"                                        -ForegroundColor Green
Write-Host ""
Write-Host "  # 或一次性执行："                             -ForegroundColor White
Write-Host '  $env:OPENAI_BASE_URL="http://127.0.0.1:'"$env:PROXY_PORT"'"; $env:OPENAI_API_KEY="<key>"; codex' -ForegroundColor Green
Write-Host ""
Write-Host "=========================================="  -ForegroundColor Cyan
Write-Host ""

# 运行代理
& $pythonExe proxy.py
