@echo off
REM Windows auto-setup and launch script for Job Application Tracker (JAT)
REM Instant non-blocking startup: server and browser launch immediately (<2s).

cd /d "%~dp0"

echo ===================================================
echo   Job Application Tracker (JAT) - Startup
echo ===================================================

REM 1. Locate Python interpreter
set "PYTHON_CMD="
where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=python"
) else (
    where py >nul 2>nul
    if %errorlevel% equ 0 (
        set "PYTHON_CMD=py -3"
    ) else (
        where python3 >nul 2>nul
        if %errorlevel% equ 0 (
            set "PYTHON_CMD=python3"
        )
    )
)

if not defined PYTHON_CMD (
    echo [ERROR] Python is not installed or not in system PATH.
    echo Please install Python 3.8+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 2. Setup virtual environment (.venv)
if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    %PYTHON_CMD% -m venv .venv >nul 2>&1
    if %errorlevel% neq 0 (
        set "VENV_PYTHON=%PYTHON_CMD%"
    ) else (
        set "VENV_PYTHON=.venv\Scripts\python.exe"
    )
) else (
    set "VENV_PYTHON=.venv\Scripts\python.exe"
)

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat >nul 2>&1
)

REM 3. Fast dependency check
echo Ensuring dependencies are up to date...
"%VENV_PYTHON%" -m pip install -q --disable-pip-version-check -r requirements.txt >nul 2>&1

set "AUTO_OPEN_BROWSER=1"

REM 4. Launch optional local AI (Ollama) service in background WITHOUT blocking startup
start /B cmd /c "(where ollama >nul 2>nul && ollama serve >nul 2>&1) || (if exist \"%LOCALAPPDATA%\Ollama\ollama.exe\" \"%LOCALAPPDATA%\Ollama\ollama.exe\" serve >nul 2>&1)" >nul 2>&1

REM 5. Clear port 5050 and launch web application immediately
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5050" ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)

echo Starting JAT application on http://localhost:5050 ...
start http://localhost:5050
"%VENV_PYTHON%" app.py
pause
