# ============================================================================
# dsh-netdisk v1.0.0 一键部署脚本
# 将网盘下载引擎部署到目标工作区的 .dsh-netdisk/ 目录
# 用法:
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-Workspace "D:\path\to\workspace"]
# ============================================================================
param(
    [string]$Workspace = ""
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if ([string]::IsNullOrWhiteSpace($Workspace)) {
    $Workspace = (Get-Location).Path
}
$Workspace = (Resolve-Path -Path $Workspace -ErrorAction SilentlyContinue).Path
if (-not $Workspace) {
    Write-Host "[install] 目标工作区不存在: $Workspace" -ForegroundColor Red
    exit 1
}

Write-Host "======================================================"
Write-Host " dsh-netdisk v1.0.0 部署"
Write-Host " 目标工作区: $Workspace"
Write-Host "======================================================"

# ---------- 1. 环境检查 ----------
Write-Host "[1/4] 检查运行环境 ..."
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { Write-Host "  [失败] 未找到 python(需要 Python 3.8+)" -ForegroundColor Red; exit 1 }
Write-Host "  [ok] python $((python --version 2>&1))"

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) { Write-Host "  [警告] 未找到 node(浏览器登录功能需要 Node 18+)" -ForegroundColor Yellow }
else { Write-Host "  [ok] node $((node --version 2>&1))" }

$edge = Test-Path "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (-not $edge) { Write-Host "  [警告] 未找到 Edge(浏览器登录功能需要 Microsoft Edge)" -ForegroundColor Yellow }
else { Write-Host "  [ok] Edge 浏览器" }

# ---------- 2. 部署核心文件 ----------
Write-Host "[2/4] 部署核心文件到 $Workspace\.dsh-netdisk ..."
$target = Join-Path $Workspace ".dsh-netdisk"
New-Item -ItemType Directory -Force -Path $target | Out-Null

Copy-Item "$scriptDir\netdisk_helper.py" "$target\netdisk_helper.py" -Force
Copy-Item "$scriptDir\browser_login.js"  "$target\browser_login.js"  -Force
Copy-Item "$scriptDir\credentials.example.json" "$target\credentials.example.json" -Force -ErrorAction SilentlyContinue
if (-not (Test-Path "$target\credentials.json")) {
    Copy-Item "$scriptDir\credentials.example.json" "$target\credentials.json" -Force
}
Write-Host "  [ok] netdisk_helper.py / browser_login.js / credentials.json"

# ---------- 3. BaiduPCS-Go 二进制 ----------
Write-Host "[3/4] 部署 BaiduPCS-Go(百度高速通道) ..."
$binDir = Join-Path $target "bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$bpc = Join-Path $binDir "BaiduPCS-Go.exe"

if (Test-Path $bpc) {
    Write-Host "  [ok] 已存在: $bpc"
} elseif (Test-Path "$scriptDir\bin\BaiduPCS-Go.exe") {
    Copy-Item "$scriptDir\bin\BaiduPCS-Go.exe" $bpc -Force
    Write-Host "  [ok] 从发布包拷贝: BaiduPCS-Go.exe"
} else {
    Write-Host "  [..] 从 GitHub 下载 BaiduPCS-Go v4.0.1 (约 5MB) ..."
    $zip = Join-Path $env:TEMP "baidupcs-v4.0.1-win.zip"
    try {
        Invoke-WebRequest -Uri "https://github.com/qjfoidnh/BaiduPCS-Go/releases/download/v4.0.1/BaiduPCS-Go-v4.0.1-windows-x64.zip" -OutFile $zip -TimeoutSec 300
        Expand-Archive -Path $zip -DestinationPath $env:TEMP\baidupcs-v4.0.1 -Force
        $exe = Get-ChildItem "$env:TEMP\baidupcs-v4.0.1" -Recurse -Filter "BaiduPCS-Go.exe" | Select-Object -First 1
        if (-not $exe) { throw "压缩包中未找到 BaiduPCS-Go.exe" }
        Copy-Item $exe.FullName $bpc -Force
        Write-Host "  [ok] BaiduPCS-Go.exe 下载完成"
    } catch {
        Write-Host "  [警告] BaiduPCS-Go 下载失败: $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "         百度下载将回退到 Web 直链通道(速度受限), 可稍后手动放置 BaiduPCS-Go.exe 到 $binDir" -ForegroundColor Yellow
    }
}

# ---------- 4. 校验 ----------
Write-Host "[4/4] 校验部署结果 ..."
& python -m py_compile "$target\netdisk_helper.py" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "  [失败] netdisk_helper.py 语法错误" -ForegroundColor Red; exit 1 }
Write-Host "  [ok] netdisk_helper.py 语法检查通过"

Write-Host ""
Write-Host "======================================================"
Write-Host " 部署完成!"
Write-Host ""
Write-Host " 下一步 — 在 DSH 会话中激活插件:"
Write-Host "   1. cordis_define: kind=new, idPrefix=netdk,"
Write-Host "      code.host = 发布包中 plugin-host.js 的全部内容"
Write-Host "   2. cordis_run: mode=run"
Write-Host ""
Write-Host " 使用:"
Write-Host "   对模型说「登录百度/夸克/迅雷」→ 弹窗登录自动抓 Cookie"
Write-Host "   或「下载 <网盘分享链接>」→ 登录态高速下载"
Write-Host "======================================================"
