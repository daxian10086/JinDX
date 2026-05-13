# JinDX Windows 两阶段打包脚本
#   阶段 1: PyInstaller 打包 Python 代理 -> proxy-backend.exe
#   阶段 2: Wails 构建 Go GUI，嵌入 proxy-backend.exe -> jindx.exe
#
# 前置要求:
#   - Python 3.10+
#   - Go 1.22+ (winget install GoLang.Go -e)
#   - Node.js 18+ (winget install OpenJS.NodeJS.LTS -e)
#   - Wails CLI (go install github.com/wailsapp/wails/v3/cmd/wails3@latest)
#
# 用法:
#   .\build-exe.ps1                  # 完整打包（Python + GUI）
#   .\build-exe.ps1 -PythonOnly      # 仅打包 Python 代理
#   .\build-exe.ps1 -GuiOnly         # 仅打包 GUI（需先有 proxy-backend.exe）

param(
    [switch] $PythonOnly,
    [switch] $GuiOnly
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  JinDX Windows EXE 打包脚本"              -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

$distDir = "$PSScriptRoot\dist"
$buildDir = "$PSScriptRoot\build"

# ═══════════════════════════════════════════════════════════
# 阶段 1: PyInstaller 打包 Python 代理
# ═══════════════════════════════════════════════════════════

if (-not $GuiOnly) {
    # -- 查找 Python -----------------------------------

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
        Write-Host "  [X] 未找到 Python，请先安装 Python 3.10+" -ForegroundColor Red
        Write-Host "      winget install Python.Python.3.11 -e"  -ForegroundColor Yellow
        exit 1
    }

    Write-Host "  [+] Python: $pythonExe" -ForegroundColor Green

    function Invoke-Pip { & $pythonExe -m pip @args }

    # -- 安装 PyInstaller ------------------------------

    Write-Host "  [*] 检查 PyInstaller..." -ForegroundColor Yellow
    & $pythonExe -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [*] 安装 PyInstaller..." -ForegroundColor Yellow
        Invoke-Pip install -q pyinstaller 2>$null
        if ($LASTEXITCODE -ne 0) {
            Invoke-Pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  [X] PyInstaller 安装失败" -ForegroundColor Red
                exit 1
            }
        }
        Write-Host "  [+] PyInstaller 安装完成" -ForegroundColor Green
    }

    # -- 确保项目依赖 ----------------------------------

    Write-Host "  [*] 检查项目依赖..." -ForegroundColor Yellow
    & $pythonExe -c "import fastapi,uvicorn,httpx,cryptography" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [*] 安装项目依赖..." -ForegroundColor Yellow
        Invoke-Pip install -q fastapi "uvicorn[standard]" httpx cryptography 2>$null
        if ($LASTEXITCODE -ne 0) {
            Invoke-Pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi "uvicorn[standard]" httpx cryptography
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  [X] 依赖安装失败" -ForegroundColor Red
                exit 1
            }
        }
        Write-Host "  [+] 依赖安装完成" -ForegroundColor Green
    }

    # -- 清理旧构建 ------------------------------------

    if (Test-Path $distDir) { Remove-Item -Recurse -Force $distDir -ErrorAction SilentlyContinue }
    if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir -ErrorAction SilentlyContinue }
    if (Test-Path "$PSScriptRoot\jindx.spec") { Remove-Item -Force "$PSScriptRoot\jindx.spec" }

    # -- PyInstaller 打包 ------------------------------

    Write-Host ""
    Write-Host "  [*] 阶段 1/2: PyInstaller 打包 Python 代理..." -ForegroundColor Yellow
    Write-Host ""

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
        "--icon", "NONE",
        "proxy.py"
    )

    & $pythonExe -m PyInstaller @pyinstallerArgs

    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "  [X] Python 代理打包失败" -ForegroundColor Red
        exit 1
    }

    Write-Host "  [+] proxy-backend.exe 打包完成" -ForegroundColor Green

    # 复制 proxy-backend.exe 到 gui/ 目录供 Wails embed
    $proxyExeDist = "$distDir\proxy-backend.exe"
    $guiEmbedTarget = "$PSScriptRoot\gui\proxy-backend.exe"
    if (Test-Path $proxyExeDist) {
        Copy-Item $proxyExeDist $guiEmbedTarget -Force
        Write-Host "  [+] proxy-backend.exe -> gui/proxy-backend.exe (待 Wails 嵌入)" -ForegroundColor Green
    }
}

# ═══════════════════════════════════════════════════════════
# 阶段 2: Wails 构建 Go GUI
# ═══════════════════════════════════════════════════════════

if (-not $PythonOnly) {
    # -- 检查 Go ---------------------------------------

    $goExe = Get-Command go -ErrorAction SilentlyContinue
    if (-not $goExe) {
        Write-Host "  [X] 未找到 Go，请先安装 Go 1.22+" -ForegroundColor Red
        Write-Host "      winget install GoLang.Go -e"  -ForegroundColor Yellow
        exit 1
    }
    Write-Host "  [+] Go: $($goExe.Source)" -ForegroundColor Green

    # -- 检查 Wails CLI --------------------------------

    $wailsExe = Get-Command wails3 -ErrorAction SilentlyContinue
    if (-not $wailsExe) {
        Write-Host "  [X] 未找到 Wails CLI，请先安装:" -ForegroundColor Red
        Write-Host "      go install github.com/wailsapp/wails/v3/cmd/wails3@latest" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "  [+] Wails CLI: $($wailsExe.Source)" -ForegroundColor Green

    # -- 检查 Node.js ----------------------------------

    $nodeExe = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeExe) {
        Write-Host "  [X] 未找到 Node.js，请先安装 Node.js 18+" -ForegroundColor Red
        Write-Host "      winget install OpenJS.NodeJS.LTS -e"  -ForegroundColor Yellow
        exit 1
    }

    # -- 前端依赖安装 ----------------------------------

    $frontendDir = "$PSScriptRoot\gui\frontend"
    if (Test-Path "$frontendDir\package.json") {
        Write-Host "  [*] 安装前端依赖..." -ForegroundColor Yellow
        Push-Location $frontendDir
        try {
            npm install --silent 2>$null
            if ($LASTEXITCODE -ne 0) {
                npm install  # 重试，显示错误
            }
            Write-Host "  [+] 前端依赖安装完成" -ForegroundColor Green
        } finally {
            Pop-Location
        }
    }

    # -- 检查 proxy-backend.exe ------------------------

    $embedExe = "$PSScriptRoot\gui\proxy-backend.exe"
    if (-not (Test-Path $embedExe)) {
        Write-Host "  [!] gui/proxy-backend.exe 不存在，Wails 将不嵌入代理（开发模式）" -ForegroundColor Yellow
        Write-Host "      先运行: .\build-exe.ps1 -PythonOnly" -ForegroundColor Yellow
    }

    # -- Wails 构建 ------------------------------------

    Write-Host ""
    Write-Host "  [*] 阶段 2/2: Wails 构建 GUI..." -ForegroundColor Yellow
    Write-Host ""

    Push-Location "$PSScriptRoot\gui"
    try {
        & wails3 build -o "$distDir\jindx.exe"

        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "  [X] Wails 构建失败" -ForegroundColor Red
            exit 1
        }
    } finally {
        Pop-Location
    }

    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "  Wails GUI 构建完成!"                     -ForegroundColor Green
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  输出文件: dist\jindx.exe"              -ForegroundColor White
}

# ═══════════════════════════════════════════════════════════
# 最终输出
# ═══════════════════════════════════════════════════════════

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  打包完成!"                                -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  输出目录: dist\"                          -ForegroundColor White
if (Test-Path "$distDir\proxy-backend.exe") {
    Write-Host "    proxy-backend.exe  — Python 代理 (命令行)" -ForegroundColor White
}
if (Test-Path "$distDir\jindx.exe") {
    Write-Host "    jindx.exe          — GUI 桌面应用"       -ForegroundColor White
}
Write-Host ""
Write-Host "  GUI 应用功能:"                             -ForegroundColor Yellow
Write-Host "    - 系统托盘图标 + 后台运行"                 -ForegroundColor White
Write-Host "    - Codex / Claude 配置面板"                -ForegroundColor White
Write-Host "    - 实时统计 + 日志查看"                    -ForegroundColor White
Write-Host "    - 环境变量一键复制"                       -ForegroundColor White
Write-Host "    - 开机自启 (注册表)"                      -ForegroundColor White
Write-Host ""
Write-Host "  运行方式:"                                  -ForegroundColor Yellow
Write-Host "    .\dist\jindx.exe       — 启动 GUI"       -ForegroundColor Green
Write-Host "    .\dist\proxy-backend.exe — 仅启动代理"    -ForegroundColor Green
Write-Host ""
