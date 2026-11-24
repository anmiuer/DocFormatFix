@echo off
setlocal
cd /d "%~dp0"

set "PY="
if exist ".venv\Scripts\pythonw.exe" set "PY=.venv\Scripts\pythonw.exe"

if not defined PY (
    where pythonw.exe >nul 2>nul
    if not errorlevel 1 set "PY=pythonw.exe"
)
if not defined PY (
    where pyw.exe >nul 2>nul
    if not errorlevel 1 set "PY=pyw.exe -3"
)
if not defined PY (
    where python.exe >nul 2>nul
    if not errorlevel 1 set "PY=python.exe"
)
if not defined PY (
    where py.exe >nul 2>nul
    if not errorlevel 1 set "PY=py.exe -3"
)

if not defined PY (
    echo.
    echo [DocFormatFix] Python 3.9+ was not found.
    echo Install Python from https://www.python.org/downloads/
    echo and enable "Add python.exe to PATH", then try again.
    echo.
    echo You can also run: python launcher.py --check
    echo.
    pause
    exit /b 1
)

echo [DocFormatFix] Starting with: %PY%
start "" %PY% "launcher.py"
exit /b 0
