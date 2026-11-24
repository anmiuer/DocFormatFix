@echo off
setlocal
cd /d "%~dp0"

echo ===== DocFormatFix environment check =====
echo Folder: %CD%
echo.

echo --- Python candidates ---
where pythonw.exe 2>nul
where pyw.exe 2>nul
where python.exe 2>nul
where py.exe 2>nul
echo.

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
    where python.exe >nul 2>nul
    if not errorlevel 1 set "PY=python.exe"
)
if not defined PY (
    where py.exe >nul 2>nul
    if not errorlevel 1 set "PY=py.exe -3"
)

if not defined PY (
    echo [DocFormatFix] Python 3.9+ was not found.
    echo Install Python from https://www.python.org/downloads/
    echo and enable "Add python.exe to PATH".
    echo.
    if /i not "%~1"=="--no-pause" pause
    exit /b 1
)

echo --- Python version ---
%PY% --version
echo.
echo --- launcher.py --check ---
%PY% "launcher.py" --check
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%
if /i not "%~1"=="--no-pause" pause
exit /b %RC%
