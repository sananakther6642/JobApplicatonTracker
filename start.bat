@echo off
REM Windows auto-setup for JAT (Job Application Tracker)
REM Equivalent of start.sh for Command Prompt

cd /d "%~dp0"

echo Installing Python dependencies...
pip install -q -r requirements.txt

REM Best-effort: bring up the local AI gap-filler (Ollama, offline, free).
REM The app works fine on regex extraction alone if any of this is skipped or
REM fails. See gen_job.py's ai_extract_fields()/ensure_ai_ready().
set "AI_MODEL=qwen2.5:0.5b"
set "OLLAMA_EXE="

where ollama >nul 2>nul
if %errorlevel% equ 0 (
    set "OLLAMA_EXE=ollama"
    goto :check_server
)

REM Check common install locations for ollama.exe
if exist "%LOCALAPPDATA%\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Ollama\ollama.exe"
if exist "%PROGRAMFILES%\Ollama\ollama.exe" set "OLLAMA_EXE=%PROGRAMFILES%\Ollama\ollama.exe"
if exist "%USERPROFILE%\.ollama\ollama.exe" set "OLLAMA_EXE=%USERPROFILE%\.ollama\ollama.exe"

if defined OLLAMA_EXE goto :check_server

REM ---- Install Ollama ----
echo Ollama not found. Attempting automatic installation...

where winget >nul 2>nul
if %errorlevel% equ 0 (
    echo Installing Ollama via winget...
    winget install Ollama.Ollama --accept-package-agreements --accept-source-agreements >nul 2>&1
)

echo Downloading Ollama installer...
powershell -Command "Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile '%TEMP%\OllamaSetup.exe'" >nul 2>&1

if exist "%TEMP%\OllamaSetup.exe" (
    echo Running Ollama installer...
    start /wait "%TEMP%\OllamaSetup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART >nul 2>&1
    del "%TEMP%\OllamaSetup.exe" >nul 2>&1
) else (
    echo [warn] Could not download Ollama installer.
)

REM Refresh lookup after install
where ollama >nul 2>nul
if %errorlevel% equ 0 (
    set "OLLAMA_EXE=ollama"
) else (
    if exist "%LOCALAPPDATA%\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Ollama\ollama.exe"
    if exist "%PROGRAMFILES%\Ollama\ollama.exe" set "OLLAMA_EXE=%PROGRAMFILES%\Ollama\ollama.exe"
    if exist "%USERPROFILE%\.ollama\ollama.exe" set "OLLAMA_EXE=%USERPROFILE%\.ollama\ollama.exe"
)

if not defined OLLAMA_EXE (
    echo [info] Ollama installation could not be completed -- skipping local AI setup (regex-only extraction will be used).
    echo       Install Ollama manually from https://ollama.com to enable AI-assisted extraction.
    goto :start_server
)

REM ---- Start Ollama server ----
:check_server
netstat -ano | findstr ":11434" | findstr "LISTENING" >nul 2>nul
if %errorlevel% equ 0 goto :pull_model

echo Starting Ollama (local AI model server)...
start /B "" "%OLLAMA_EXE%" serve
timeout /t 3 /nobreak >nul

REM ---- Pull model ----
:pull_model
"%OLLAMA_EXE%" list 2>nul | findstr /i "%AI_MODEL%" >nul
if %errorlevel% equ 0 goto :start_server

echo Pulling local AI model %AI_MODEL% (one-time download, ~400MB^)...
"%OLLAMA_EXE%" pull %AI_MODEL%
if %errorlevel% neq 0 (
    echo   [warn] Could not pull %AI_MODEL% -- AI-assisted extraction will be unavailable; regex extraction still works normally.
)

REM ---- Start Flask ----
:start_server
echo Stopping any existing server on port 5050...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5050" ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>nul
)
timeout /t 0.5 /nobreak >nul

echo Starting JAT server on http://localhost:5050 ...
python app.py
pause
