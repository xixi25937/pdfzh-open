@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "ROOT=%~dp0"
set "PY=%ROOT%.venv-next\Scripts\python.exe"

echo [PDFZH] Launcher started. Checking local environment...
if not exist "%PY%" (
    echo [PDFZH] ERROR: Python environment not found.
    echo [PDFZH] Run setup.ps1 first.
    endlocal & exit /b 1
)

echo [PDFZH] Environment ready. Starting translator...
echo [PDFZH] The first local layout-model load may take several minutes.
echo [PDFZH] Please wait and do not start the same PDF twice.
"%PY%" -u "%ROOT%pdfzh_next.py" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" echo [PDFZH] Finished with error code %RC%.
if "%RC%"=="0" echo [PDFZH] Finished successfully.
endlocal & exit /b %RC%
