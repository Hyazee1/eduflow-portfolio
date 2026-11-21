@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 项目虚拟环境不存在，请先在 PowerShell 执行 .\setup.ps1
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "run.py" %*
if errorlevel 1 pause
