@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PDFZH_PY=%~dp0.venv-next\Scripts\python.exe"
if not exist "%PDFZH_PY%" (
    echo [PDFZH] First use: run install.cmd to install Python dependencies.
    exit /b 1
)
"%PDFZH_PY%" -u "%~dp0pdfzh_cli.py" %*
exit /b %errorlevel%
