# Chat <-> Responses API Proxy for DeepSeek V4 Pro — Windows PowerShell 启动脚本
#
# 用法:
#   .\start.ps1
#   $env:PROXY_PORT=9000; .\start.ps1
#   $env:DEEPSEEK_KEY="sk-xxx"; .\start.ps1

Set-Location $PSScriptRoot

$env:PROXY_PORT = if ($env:PROXY_PORT) { $env:PROXY_PORT } else { "8080" }
$env:ADMIN_PORT = if ($env:ADMIN_PORT) { $env:ADMIN_PORT } else { "8090" }
$env:DEEPSEEK_KEY = if ($env:DEEPSEEK_KEY) { $env:DEEPSEEK_KEY } else { "sk-your-deepseek-api-key" }
$env:DEEPSEEK_BASE = if ($env:DEEPSEEK_BASE) { $env:DEEPSEEK_BASE } else { "https://api.deepseek.com" }
$env:DEFAULT_MODEL = if ($env:DEFAULT_MODEL) { $env:DEFAULT_MODEL } else { "deepseek-v4-pro" }
$env:DEFAULT_REASONING_EFFORT = if ($env:DEFAULT_REASONING_EFFORT) { $env:DEFAULT_REASONING_EFFORT } else { "max" }
$env:MAX_POSITION_EMBEDDINGS = if ($env:MAX_POSITION_EMBEDDINGS) { $env:MAX_POSITION_EMBEDDINGS } else { "1000000" }
$env:REDIS_HOST = if ($env:REDIS_HOST) { $env:REDIS_HOST } else { "127.0.0.1" }
$env:REDIS_PORT = if ($env:REDIS_PORT) { $env:REDIS_PORT } else { "6379" }
$env:REDIS_DB = if ($env:REDIS_DB) { $env:REDIS_DB } else { "0" }
$env:CONNECT_PORT = if ($env:CONNECT_PORT) { $env:CONNECT_PORT } else { "8443" }
$env:TLS_PORT = if ($env:TLS_PORT) { $env:TLS_PORT } else { "8444" }

Write-Host "=== Chat-Responses Proxy ==="
Write-Host "Target:  $env:DEEPSEEK_BASE/v1/chat/completions"
Write-Host "Model:   $env:DEFAULT_MODEL"
Write-Host "Port:    $env:PROXY_PORT (admin: $env:ADMIN_PORT)"
Write-Host "Redis:   ${env:REDIS_HOST}:$env:REDIS_PORT"
Write-Host ""

# 检查并安装依赖
$depsOk = python -c "import fastapi,uvicorn,httpx,redis,cryptography" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "安装 Python 依赖..."
    pip install -q fastapi "uvicorn[standard]" httpx redis cryptography
}

python proxy.py
