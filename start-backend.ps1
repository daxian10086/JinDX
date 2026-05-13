# JinDX Windows 后台版启动脚本（免 Python，免 pip）
#
# 用法:
#   .\start-backend.ps1
#   $env:DEEPSEEK_KEY="sk-xxx"; .\start-backend.ps1
#   $env:PROXY_PORT=9000; .\start-backend.ps1
#
# 功能：
#   1. 自动检查/配置 DeepSeek API Key（无 key 时交互式输入）
#   2. 自动配置 hosts 劫持（将 api.openai.com 等指向 127.0.0.1）
#   3. 自动配置端口转发（netsh 将 443 -> 8444）
#   4. 启动 JinDX 代理服务（proxy-backend.exe，已内嵌全部依赖）
#   5. 输出 Codex CLI / Claude Code 环境变量配置

param()

Set-Location $PSScriptRoot

# -- 环境变量默认值 ------------------------------------

$env:PROXY_PORT              = if ($env:PROXY_PORT)              { $env:PROXY_PORT }              else { "8080" }
$env:ADMIN_PORT              = if ($env:ADMIN_PORT)              { $env:ADMIN_PORT }              else { "8090" }
$env:DEEPSEEK_KEY            = if ($env:DEEPSEEK_KEY)            { $env:DEEPSEEK_KEY }            else { "sk-your-deepseek-api-key" }
$env:DEEPSEEK_BASE           = if ($env:DEEPSEEK_BASE)           { $env:DEEPSEEK_BASE }           else { "https://api.deepseek.com" }
$env:DEFAULT_MODEL           = if ($env:DEFAULT_MODEL)           { $env:DEFAULT_MODEL }           else { "deepseek-v4-pro" }
$env:DEFAULT_REASONING_EFFORT = if ($env:DEFAULT_REASONING_EFFORT) { $env:DEFAULT_REASONING_EFFORT } else { "max" }
$env:MAX_POSITION_EMBEDDINGS = if ($env:MAX_POSITION_EMBEDDINGS) { $env:MAX_POSITION_EMBEDDINGS } else { "1000000" }
$env:CONNECT_PORT            = if ($env:CONNECT_PORT)            { $env:CONNECT_PORT }            else { "8443" }
$env:TLS_PORT                = if ($env:TLS_PORT)                { $env:TLS_PORT }                else { "8444" }

# -- 步骤 0：检查/配置 DeepSeek API Key -----------------

$configFile = if ($env:PROXY_CONFIG_FILE) { $env:PROXY_CONFIG_FILE } else { "$env:APPDATA\proxy-config.json" }

# 先从配置文件读取已有 key（如果环境变量还是默认值）
$savedKey = ""
if (Test-Path $configFile) {
    try {
        $cfg = Get-Content $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $savedKey = if ($cfg.claude_deepseek_key) { $cfg.claude_deepseek_key }
                    elseif ($cfg.deepseek_key)     { $cfg.deepseek_key }
                    else { "" }
    } catch { }
}

$needPrompt = $false
if ($env:DEEPSEEK_KEY -eq "sk-your-deepseek-api-key" -or -not $env:DEEPSEEK_KEY) {
    if ($savedKey -and $savedKey -ne "sk-your-deepseek-api-key") {
        # 配置文件中有有效 key，同步到环境变量
        $env:DEEPSEEK_KEY = $savedKey
        Write-Host "  [+] 从配置文件加载 API Key" -ForegroundColor Green
    } else {
        $needPrompt = $true
    }
}

if ($needPrompt) {
    Write-Host ""
    Write-Host "=========================================="  -ForegroundColor Cyan
    Write-Host "  首次运行 - 请配置 DeepSeek API Key"        -ForegroundColor Yellow
    Write-Host "=========================================="  -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  获取 Key: https://platform.deepseek.com/api_keys" -ForegroundColor Gray
    Write-Host "  之后可在管理面板 http://127.0.0.1:$($env:ADMIN_PORT) 修改" -ForegroundColor Gray
    Write-Host ""

    $inputKey = Read-Host "  请输入你的 DeepSeek API Key (sk-...)"

    if (-not $inputKey -or $inputKey.Trim() -eq "") {
        Write-Host ""
        Write-Host "  [X] 未输入 API Key，无法启动" -ForegroundColor Red
        Write-Host "      可通过环境变量设置: `$env:DEEPSEEK_KEY=`"sk-xxx`"; .\start-backend.ps1" -ForegroundColor Yellow
        exit 1
    }

    $inputKey = $inputKey.Trim()
    $env:DEEPSEEK_KEY = $inputKey

    # 持久化到配置文件
    try {
        $dir = Split-Path $configFile -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

        $cfgObj = @{ deepseek_key = $inputKey }
        if (Test-Path $configFile) {
            try {
                $existing = Get-Content $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
                foreach ($p in $existing.PSObject.Properties) { $cfgObj[$p.Name] = $p.Value }
            } catch { }
        }
        $cfgObj | ConvertTo-Json -Depth 5 | Set-Content $configFile -Encoding UTF8
        Write-Host "  [+] API Key 已保存到 $configFile" -ForegroundColor Green
    } catch {
        Write-Host "  [!] 配置文件保存失败: $_" -ForegroundColor Yellow
        Write-Host "  [!] Key 仅对本次运行生效，下次需要重新输入" -ForegroundColor Yellow
    }
}

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
    Write-Host "    推荐：右键 PowerShell -> 以管理员身份运行，然后重新执行 .\start-backend.ps1" -ForegroundColor Yellow
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

# -- 步骤 3：自动配置 Codex CLI config.toml ------------

$codexConfigDir = "$env:USERPROFILE\.codex"
$codexConfigFile = "$codexConfigDir\config.toml"

# 构建跨平台 projects 信任段
$homePath = $env:USERPROFILE -replace '\\', '/'
$projectsSection = @'

[projects."HOME_PATH_PLACEHOLDER"]
trust_level = "trusted"

[projects."C:/"]
trust_level = "trusted"

[projects."D:/"]
trust_level = "trusted"
'@ -replace 'HOME_PATH_PLACEHOLDER', $homePath

$codexConfigContent = @"
model = "gpt-5.5"
model_reasoning_effort = "xhigh"
model_provider = "openai_http"

[model_providers.openai_http]
name = "JinDx Proxy (DeepSeek)"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = true
base_url = "http://127.0.0.1:$env:PROXY_PORT"
$projectsSection

[tui.model_availability_nux]
"gpt-5.5" = 4

[features]
terminal_resize_reflow = true
"@

$needsUpdate = $false

if (Test-Path $codexConfigFile) {
    $existing = Get-Content $codexConfigFile -Raw
    if ($existing -notmatch "model_provider" -or $existing -notmatch "127\.0\.0\.1") {
        $needsUpdate = $true
        $backupPath = "$codexConfigFile.bak"
        Copy-Item $codexConfigFile $backupPath -Force
        Write-Host "  [=] 已备份原 Codex 配置到 $backupPath" -ForegroundColor Gray
    }
} else {
    $needsUpdate = $true
    New-Item -ItemType Directory -Path $codexConfigDir -Force | Out-Null
}

if ($needsUpdate) {
    Set-Content -Path $codexConfigFile -Value $codexConfigContent -Encoding UTF8
    Write-Host "  [+] Codex config.toml 已自动配置 -> http://127.0.0.1:$($env:PROXY_PORT)" -ForegroundColor Green
} else {
    Write-Host "  [=] Codex config.toml 已是 JinDx 代理配置" -ForegroundColor Gray
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

# 运行代理（proxy-backend.exe 已内嵌全部 Python 依赖）
& .\proxy-backend.exe
