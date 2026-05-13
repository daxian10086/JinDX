# JinDX Windows Release 打包脚本
# 在 Windows 上执行，生成两个 zip 包放到 release/ 目录
#
# 前置要求:
#   - Python 3.10+  (winget install Python.Python.3.11 -e)
#   - Go 1.22+ (winget install GoLang.Go -e) — 仅 GUI 版需要
#   - Node.js 18+ (winget install OpenJS.NodeJS.LTS -e) — 仅 GUI 版需要
#   - Wails CLI (go install github.com/wailsapp/wails/v3/cmd/wails3@latest) — 仅 GUI 版需要
#
# 用法:
#   .\build-release.ps1                # 打包两个版本
#   .\build-release.ps1 -BackendOnly   # 仅后台版
#   .\build-release.ps1 -GuiOnly       # 仅桌面版
#
# 输出:
#   release/jindx-backend-vX.Y.Z.zip    — 后台命令行版（proxy-backend.exe + 启动脚本）
#   release/jindx-gui-vX.Y.Z.zip        — 前台桌面版（Wails GUI exe）

param(
    [string]$Version = "1.0.0",
    [switch]$BackendOnly,
    [switch]$GuiOnly
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  JinDX Windows Release 打包"              -ForegroundColor Cyan
Write-Host "  Version: $Version"                      -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

$releaseDir = "$PSScriptRoot\release"
$tempDir = "$PSScriptRoot\build-temp"

if (Test-Path $tempDir) { Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

# ═══════════════════════════════════════════════════════════
# 共享步骤: PyInstaller 构建 proxy-backend.exe
# 后台版和 GUI 版都需要这个 exe
# ═══════════════════════════════════════════════════════════

$pybackDir = "$tempDir\pyback"
New-Item -ItemType Directory -Path $pybackDir -Force | Out-Null
$proxyExe = "$pybackDir\proxy-backend.exe"

# 查找 Python
$pythonExe = $null
$managedPython = "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.11.12\python.exe"
$userPythonDir = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311"
$userPython = "$userPythonDir\python.exe"

if (Test-Path $managedPython) {
    $pythonExe = $managedPython
} elseif (Test-Path $userPython) {
    $pythonExe = $userPython
} elseif (Test-Path "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe") {
    $pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
} elseif (Test-Path "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe") {
    $pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
}

if (-not $pythonExe) {
    Write-Host "  [X] 未找到 Python" -ForegroundColor Red
    Write-Host "      winget install Python.Python.3.11 -e" -ForegroundColor Yellow
    exit 1
}
Write-Host "  [+] Python: $pythonExe" -ForegroundColor Green

function Invoke-Pip { & $pythonExe -m pip @args }

# 检查/安装 PyInstaller 和项目依赖
Write-Host "  [*] 检查打包依赖..." -ForegroundColor Yellow
& $pythonExe -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Invoke-Pip install -q pyinstaller 2>$null
    if ($LASTEXITCODE -ne 0) {
        Invoke-Pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller
    }
}
& $pythonExe -c "import fastapi,uvicorn,httpx,cryptography" 2>$null
if ($LASTEXITCODE -ne 0) {
    Invoke-Pip install -q fastapi "uvicorn[standard]" httpx cryptography 2>$null
    if ($LASTEXITCODE -ne 0) {
        Invoke-Pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi "uvicorn[standard]" httpx cryptography
    }
}

# PyInstaller 打包 Python 代理 -> proxy-backend.exe
$pybuildWorkPath = "$tempDir\pybuild-work"
$pyinstallerArgs = @(
    "--onefile",
    "--name", "proxy-backend",
    "--add-data", "jindx;jindx",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "fastapi",
    "--hidden-import", "httpx",
    "--hidden-import", "cryptography",
    "--distpath", $pybackDir,
    "--workpath", $pybuildWorkPath,
    "proxy.py"
)

Write-Host "  [*] PyInstaller 打包 Python 代理..." -ForegroundColor Yellow
& $pythonExe -m PyInstaller @pyinstallerArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [X] PyInstaller 打包失败" -ForegroundColor Red
    exit 1
}
Write-Host "  [+] proxy-backend.exe 生成完成" -ForegroundColor Green
Write-Host ""

# ═══════════════════════════════════════════════════════════
# 阶段 1: 后台命令行版 — proxy-backend.exe + 启动脚本
# ═══════════════════════════════════════════════════════════

if (-not $GuiOnly) {
    Write-Host "  [*] 阶段 1/2: 打包后台命令行版..." -ForegroundColor Yellow

    $backendDir = "$tempDir\jindx-backend"
    New-Item -ItemType Directory -Path $backendDir -Force | Out-Null

    # 复制 proxy-backend.exe（免 Python、pip，依赖已内嵌）
    Copy-Item $proxyExe "$backendDir\" -Force
    Write-Host "    proxy-backend.exe (已内嵌全部依赖)" -ForegroundColor Gray

    # 复制免 Python 启动脚本
    $backendScripts = @(
        "start-backend.ps1",
        "start-backend.bat",
        "README.md"
    )

    foreach ($item in $backendScripts) {
        $src = "$PSScriptRoot\$item"
        if (Test-Path $src) {
            Copy-Item $src "$backendDir\" -Force
            Write-Host "    $item" -ForegroundColor Gray
        }
    }

    $backendZip = "$releaseDir\jindx-backend-v$Version.zip"
    Compress-Archive -Path "$backendDir\*" -DestinationPath $backendZip
    Write-Host "  [+] 后台版: jindx-backend-v$Version.zip" -ForegroundColor Green
}

# ═══════════════════════════════════════════════════════════
# 阶段 2: 前台桌面版 — Wails GUI exe (嵌入 proxy-backend.exe)
# ═══════════════════════════════════════════════════════════

if (-not $BackendOnly) {
    Write-Host ""
    Write-Host "  [*] 阶段 2/2: 打包前台桌面版 (jindx-gui.exe)..." -ForegroundColor Yellow

    # 检查工具链
    $goExe = Get-Command go -ErrorAction SilentlyContinue
    if (-not $goExe) {
        Write-Host "  [X] 未找到 Go，跳过桌面版打包" -ForegroundColor Red
        Write-Host "      winget install GoLang.Go -e" -ForegroundColor Yellow
        Write-Host "      go install github.com/wailsapp/wails/v3/cmd/wails3@latest" -ForegroundColor Yellow
        exit 1
    }

    $wailsExe = Get-Command wails3 -ErrorAction SilentlyContinue
    if (-not $wailsExe) {
        Write-Host "  [X] 未找到 wails3 CLI" -ForegroundColor Red
        Write-Host "      go install github.com/wailsapp/wails/v3/cmd/wails3@latest" -ForegroundColor Yellow
        exit 1
    }

    # 复制 proxy-backend.exe 到 gui/ 供 Wails embed
    $embedDir = "$PSScriptRoot\gui"
    $embedExePath = "$embedDir\proxy-backend.exe"
    Copy-Item $proxyExe $embedExePath -Force
    Write-Host "  [+] proxy-backend.exe -> gui/ (Wails embed)" -ForegroundColor Green

    # 安装前端依赖
    $frontendDir = "$embedDir\frontend"
    if (Test-Path "$frontendDir\package.json") {
        Write-Host "  [*] 安装前端依赖..." -ForegroundColor Yellow
        Push-Location $frontendDir
        try {
            npm install --silent 2>$null
            if ($LASTEXITCODE -ne 0) { npm install }
            Write-Host "  [+] 前端依赖安装完成" -ForegroundColor Green
        } finally {
            Pop-Location
        }
    }

    # Wails 构建 GUI
    Write-Host "  [*] Wails 构建 GUI..." -ForegroundColor Yellow
    Push-Location $embedDir
    try {
        $guiName = "jindx-gui"
        $guiDir = "$tempDir\jindx-gui"
        New-Item -ItemType Directory -Path $guiDir -Force | Out-Null

        & wails3 build -o "$guiDir\$guiName.exe"

        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [X] Wails 构建失败" -ForegroundColor Red
            exit 1
        }

        Copy-Item "$PSScriptRoot\README.md" "$guiDir\" -Force

        $guiZip = "$releaseDir\jindx-gui-v$Version.zip"
        Compress-Archive -Path "$guiDir\*" -DestinationPath $guiZip
        Write-Host "  [+] 桌面版: jindx-gui-v$Version.zip" -ForegroundColor Green
    } finally {
        Pop-Location
    }
}

# ═══════════════════════════════════════════════════════════
# 清理
# ═══════════════════════════════════════════════════════════

if (Test-Path $tempDir) { Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue }
if (Test-Path "$PSScriptRoot\jindx.spec") { Remove-Item -Force "$PSScriptRoot\jindx.spec" -ErrorAction SilentlyContinue }
# 清理 embed 的 exe (不污染 git)
if (Test-Path "$PSScriptRoot\gui\proxy-backend.exe") { Remove-Item -Force "$PSScriptRoot\gui\proxy-backend.exe" -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Release 打包完成!"                       -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Release 目录: $releaseDir"             -ForegroundColor White

Get-ChildItem $releaseDir | ForEach-Object {
    $size = [math]::Round($_.Length / 1MB, 1)
    Write-Host "    $($_.Name)  ($size MB)" -ForegroundColor White
}

Write-Host ""
Write-Host "  上传到 GitHub Release:"                 -ForegroundColor Yellow
Write-Host "    gh release create v$Version release\*.zip --title 'v$Version'" -ForegroundColor Green
