#Requires -Version 5.1
param(
    [string]$TargetExe = "",
    [string]$TargetBat = "",
    [switch]$PreferExe
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Find-SourceLauncher {
    $vbs = Get-ChildItem -LiteralPath $Root -Filter "*.vbs" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*MultiAgentRoom*" } |
        Select-Object -First 1
    if ($vbs) { return $vbs.FullName }
    $bat = Get-ChildItem -LiteralPath $Root -Filter "*.bat" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*MultiAgentRoom*" -and $_.Name -notlike "*exe*" -and $_.Name -notlike "*UI*" } |
        Select-Object -First 1
    if ($bat) { return $bat.FullName }
    return $null
}

if (-not $TargetExe -and $PreferExe) {
    $cand = Join-Path $Root "dist\MultiAgentRoom\MultiAgentRoom.exe"
    if (Test-Path -LiteralPath $cand) {
        $TargetExe = $cand
    }
}
if (-not $TargetBat -and -not $TargetExe) {
    $TargetBat = Find-SourceLauncher
}

$target = $null
if ($TargetExe -and (Test-Path -LiteralPath $TargetExe)) {
    $target = $TargetExe
} elseif ($TargetBat -and (Test-Path -LiteralPath $TargetBat)) {
    $target = $TargetBat
}

if (-not $target) {
    Write-Error "No launch target under $Root (expected *MultiAgentRoom*.vbs)."
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "MultiAgentRoom.lnk"

$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut($lnkPath)
$s.TargetPath = $target
if ($TargetExe -and (Test-Path -LiteralPath $TargetExe)) {
    $s.WorkingDirectory = Split-Path -Parent $TargetExe
} else {
    $s.WorkingDirectory = $Root
}
$s.WindowStyle = 1
$s.Description = "MultiAgentRoom (source-first launcher)"
$s.Save()

Write-Host "Desktop shortcut created: $lnkPath"
Write-Host "Target: $target"
Write-Host "Note: shortcuts that still point at dist\...\exe show the OLD UI until rebuilt."
