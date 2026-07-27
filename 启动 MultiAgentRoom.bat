@echo off
REM 源码启动：默认打开柔和 Web UI（浏览器）
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" python -m multi_agent_room
  exit /b 0
)

where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" pythonw -m multi_agent_room
  exit /b 0
)

msg * "未找到 Python。请安装 Python 3.11+ 后重试。"
exit /b 1
