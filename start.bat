@echo off
REM Windows auto-setup and launch script for Job Application Tracker (JAT)
REM Fast non-blocking startup with optional background & auto-start on boot.

cd /d "%~dp0"

set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTUP_VBS=%STARTUP_FOLDER%\launch_jat_autostart.vbs"

REM Check if running in background mode (e.g. launched via VBScript or Startup)
set "BG_MODE=0"
if /i "%1"=="background" set "BG_MODE=1"
if /i "%1"=="bg" set "BG_MODE=1"
if /i "%1"=="silent" set "BG_MODE=1"
if defined BACKGROUND set "BG_MODE=1"

if "%BG_MODE%"=="0" (
    echo ===================================================
    echo   Job Application Tracker (JAT) - Startup
    echo ===================================================
)

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
    if "%BG_MODE%"=="0" pause
    exit /b 1
)

REM 2. Setup virtual environment (.venv)
if not exist ".venv\Scripts\python.exe" (
    if "%BG_MODE%"=="0" echo Creating Python virtual environment...
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
if "%BG_MODE%"=="0" echo Ensuring dependencies are up to date...
"%VENV_PYTHON%" -m pip install -q --disable-pip-version-check -r requirements.txt >nul 2>&1

set "AUTO_OPEN_BROWSER=1"

REM 4. Interactive Configuration Options (only if run interactively in terminal)
if "%BG_MODE%"=="0" (
    echo.
    echo ---------------------------------------------------
    echo   Startup Options
    echo ---------------------------------------------------

    rem Option 1: Auto-start on Windows reboot
    if exist "%STARTUP_VBS%" (
        echo [Status] System boot auto-start is currently ENABLED.
        choice /C YN /M "[Option 1] Do you want to DISABLE system boot auto-start"
        if errorlevel 2 (
            echo [OK] Keeping system boot auto-start ENABLED.
        ) else (
            del /f /q "%STARTUP_VBS%" >nul 2>&1
            echo [OK] System boot auto-start disabled.
        )
    ) else (
        echo [Status] System boot auto-start is currently DISABLED.
        choice /C YN /M "[Option 1] Enable JAT auto-start every time Windows boots up"
        if errorlevel 2 (
            echo [OK] Keeping system boot auto-start DISABLED.
        ) else (
            (
                echo Set WshShell = CreateObject^("WScript.Shell"^)^
                echo WshShell.Run "cmd /c """"%~dp0start.bat"""" background", 0, False
            ) > "%STARTUP_VBS%"
            echo [OK] System boot auto-start enabled!
        )
    )

    echo.
    rem Option 2: Run background vs foreground
    choice /C YN /M "[Option 2] Run JAT hidden in background (no console window)"
    if errorlevel 2 (
        echo [OK] Continuing in foreground terminal window...
    ) else (
        echo [OK] Launching JAT in background...
        start "" wscript.exe "%~dp0launch_jat.vbs"
        exit /b 0
    )
    echo.
)

REM 5. Launch optional local AI (Ollama) service in background WITHOUT blocking startup
start /B cmd /c "(where ollama >nul 2>nul && ollama serve >nul 2>&1) || (if exist \"%LOCALAPPDATA%\Ollama\ollama.exe\" \"%LOCALAPPDATA%\Ollama\ollama.exe\" serve >nul 2>&1)" >nul 2>&1

REM 6. Clear port 5050 and launch web application
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5050" ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)

if "%BG_MODE%"=="0" echo Starting JAT application on http://localhost:5050 ...
"%VENV_PYTHON%" app.py
if "%BG_MODE%"=="0" pause

