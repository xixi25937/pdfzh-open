@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
if not exist "%~dp0.venv-next\Scripts\python.exe" (
    echo [错误] 未找到工程运行环境，请先运行 setup.ps1。
    exit /b 1
)
"%~dp0.venv-next\Scripts\python.exe" "%~dp0pdfzh_nosource.py" %*
exit /b %errorlevel%
