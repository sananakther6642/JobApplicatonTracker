@echo off
REM Windows auto-setup script for Job Application Tracker (JAT)
REM Configures local environment, dependency management, and AI services.

cd /d "%~dp0"

echo Installing Python dependencies...
REM Use module invocation to ensure pip matches the active python interpreter context
python -m pip install -q -r requirements.txt

REM Define default parameters for local model integration
set "AI_MODEL=qwen2.5:0.5b"
set "OLLAMA_EXE="

REM Verify system PATH for existing Ollama executable
where ollama >nul 2>nul
if %errorlevel% equ 0 (
    set "OLLAMA_EXE=ollama"
    goto :check_server
)

REM Inspect default installation paths if executable is omitted from PATH
if exist "%LOCALAPPDATA%\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Ollama\ollama.exe"
if exist "%PROGRAMFILES%\Ollama\ollama.exe" set "OLLAMA_EXE=%PROGRAMFILES%\Ollama\ollama.exe"
if exist "%USERPROFILE%\.ollama\ollama.exe" set "OLLAMA_EXE=%USERPROFILE%\.ollama\ollama.exe"

if defined OLLAMA_EXE goto :check_server

REM ---- Install Ollama ----
echo Ollama executable not found. Initiating automated setup...

where winget >nul 2>nul
if %errorlevel% equ 0 (
    echo Installing Ollama via Windows Package Manager (winget)...
    winget install Ollama.Ollama --accept-package-agreements --accept-source-agreements
    echo Waiting for system environment registration...
    timeout /t 5 /nobreak >nul
)

REM Re-evaluating executable availability following package installation
where ollama >nul 2>nul
if %errorlevel% equ 0 (
    set "OLLAMA_EXE=ollama"
) else (
    if exist "%LOCALAPPDATA%\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Ollama\ollama.exe"
    if exist "%PROGRAMFILES%\Ollama\ollama.exe" set "OLLAMA_EXE=%PROGRAMFILES%\Ollama\ollama.exe"
    if exist "%USERPROFILE%\.ollama\ollama.exe" set "OLLAMA_EXE=%USERPROFILE%\.ollama\ollama.exe"
)

if defined OLLAMA_EXE goto :check_server

REM Fallback mechanism for manual binary acquisition
echo Downloading Ollama setup package...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile '%TEMP%\OllamaSetup.exe'" >nul 2>&1

if exist "%TEMP%\OllamaSetup.exe" (
    echo Launching installer. Completing setup enables local model features.
    start "" "%TEMP%\OllamaSetup.exe"
    echo Pausing execution while installer initializes...
    timeout /t 10 /nobreak >nul
) else (
    echo [warn] Download failed. Falling back to pattern-based regex extraction.
)

echo [info] Continuing startup with deterministic regex extraction engine.
goto :start_server

REM ---- Verify Server Status ----
:check_server
netstat -ano | findstr ":11434" | findstr "LISTENING" >nul 2>nul
if %errorlevel% equ 0 goto :pull_model

echo Spawning Ollama model service in background...
start /B "" "%OLLAMA_EXE%" serve

REM Polling loop to wait until port 11434 active state is confirmed (up to 10 seconds)
set /a RETRIES=0
:poll_server
timeout /t 2 /nobreak >nul
netstat -ano | findstr ":11434" | findstr "LISTENING" >nul 2>nul
if %errorlevel% equ 0 goto :pull_model
set /a RETRIES+=1
if %RETRIES% lss 5 goto :poll_server

echo [warn] Server initialization timed out. Proceeding without model inference.
goto :start_server

REM ---- Model Retrieval ----
:pull_model
"%OLLAMA_EXE%" list 2>nul | findstr /i "%AI_MODEL%" >nul
if %errorlevel% equ 0 goto :start_server

echo Downloading language model %AI_MODEL%...
"%OLLAMA_EXE%" pull %AI_MODEL%
if %errorlevel% neq 0 (
    echo [warn] Model pull failed. Regex fallback remains operational.
)

REM ---- Web Server Execution ----
:start_server
echo Terminating any active processes on port 5050...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5050" ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo Launching JAT application server on http://localhost:5050 ...
python app.py
pause
