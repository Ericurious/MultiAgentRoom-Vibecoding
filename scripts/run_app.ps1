$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$env:PYTHONPATH = (Join-Path $Root "src")
Set-Location $Root
# 默认 Web UI；传 --tk 可开旧桌面壳
python -m multi_agent_room @args
