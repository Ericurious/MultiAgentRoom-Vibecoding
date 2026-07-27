#Requires -Version 5.1
<#
.SYNOPSIS
  将 MultiAgentRoom 打包为可双击的 Windows 程序（无 PowerShell / 无控制台）。

.EXAMPLE
  .\scripts\build_exe.ps1
  .\scripts\build_exe.ps1 -CreateDesktopShortcut
#>
param(
    [switch]$CreateDesktopShortcut,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Error "未找到 python。请先安装 Python 3.11+ 并加入 PATH。"
}

Write-Host "==> 项目根: $Root"

if (-not $SkipInstall) {
    Write-Host "==> 安装/升级 PyInstaller（仅构建依赖）"
    python -m pip install -U pip pyinstaller
}

$spec = Join-Path $Root "packaging\MultiAgentRoom.spec"
if (-not (Test-Path $spec)) {
    Write-Error "缺少 spec: $spec"
}

Write-Host "==> 开始打包（windowed / onedir）…"
$env:PYTHONPATH = Join-Path $Root "src"
python -m PyInstaller --noconfirm --clean $spec

$exe = Join-Path $Root "dist\MultiAgentRoom\MultiAgentRoom.exe"
if (-not (Test-Path $exe)) {
    Write-Error "打包失败：未找到 $exe"
}

Write-Host ""
Write-Host "完成: $exe"
Write-Host "双击该 exe 即可启动（自带 tkinter UI，无需 PowerShell）。"

# 根目录启动脚本保持「源码优先」；打包版另见「启动 打包版(旧UI可能).bat」
# （不再用 exe-优先 覆盖 启动 MultiAgentRoom.bat，避免改前端后双击仍进旧壳）

if ($CreateDesktopShortcut) {
    & (Join-Path $PSScriptRoot "create_desktop_shortcut.ps1")
}

Write-Host ""
Write-Host "日常开发：双击「启动 MultiAgentRoom.vbs」（源码，最新 UI）"
Write-Host "仅跑刚打的包：双击 dist\MultiAgentRoom\MultiAgentRoom.exe 或「启动 打包版(旧UI可能).bat」"
