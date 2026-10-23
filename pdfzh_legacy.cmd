@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [错误] 未找到旧版 Python 环境: %PY%
    exit /b 1
)
"%PY%" "%ROOT%pdfzh.py" %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
