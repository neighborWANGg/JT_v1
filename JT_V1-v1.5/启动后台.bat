@echo off
chcp 65001 >nul
cd /d "%~dp0"
title JT v1.1 Backend

powershell.exe -NoProfile -Command "try { $result = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8765/health' -TimeoutSec 1; if ($result.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
if not errorlevel 1 (
  echo 后台已经运行，正在打开工作台...
  start "" "http://127.0.0.1:8765/ui"
  exit /b 0
)

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0..\JT_V1\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  echo 未找到 Python 虚拟环境。
  echo 请先在当前目录执行：python -m venv .venv
  echo 然后执行：.venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)

echo 正在启动 JT v1.1 后台...
echo 工作台地址：http://127.0.0.1:8765/ui
echo 按 Ctrl+C 可停止后台。
echo.
start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8765/ui'"
"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8765

echo.
echo 后台已停止。
pause
