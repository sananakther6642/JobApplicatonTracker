#!/usr/bin/env bash
# macOS / Linux auto-setup and launch script for Job Application Tracker (JAT)
# Fast non-blocking startup: server and browser launch immediately (<2s).

set -euo pipefail
cd "$(dirname "$0")"

echo "==================================================="
echo "  Job Application Tracker (JAT) - Startup (Mac/Linux)"
echo "==================================================="

# 1. Locate Python 3 interpreter
PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

if [ -z "$PYTHON_CMD" ]; then
    echo "[ERROR] Python 3 is not installed or not in PATH."
    echo "Please install Python 3.8+ from https://www.python.org/ or using package manager."
    exit 1
fi

# 2. Create and activate virtual environment (.venv)
if [ ! -f ".venv/bin/python" ]; then
    echo "Creating Python virtual environment..."
    $PYTHON_CMD -m venv .venv || true
fi

if [ -f ".venv/bin/python" ]; then
    VENV_PYTHON=".venv/bin/python"
else
    VENV_PYTHON="$PYTHON_CMD"
fi

# 3. Fast dependency check
echo "Ensuring dependencies are up to date..."
"$VENV_PYTHON" -m pip install -q --disable-pip-version-check -r requirements.txt || true

export AUTO_OPEN_BROWSER=1

# 4. Asynchronous non-blocking Ollama background setup
(
    AI_MODEL="qwen2.5:0.5b"
    if ! command -v ollama >/dev/null 2>&1; then
        if command -v brew >/dev/null 2>&1; then
            brew install ollama >/dev/null 2>&1 || true
        fi
    fi
    if command -v ollama >/dev/null 2>&1; then
        if ! curl -s -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
            nohup ollama serve >/tmp/jat_ollama.log 2>&1 &
            sleep 2
        fi
        if ! ollama list 2>/dev/null | grep -q "^${AI_MODEL}"; then
            ollama pull "$AI_MODEL" >/dev/null 2>&1 || true
        fi
    fi
) >/dev/null 2>&1 &

# 5. Clear port 5050 safely
PID=$(lsof -ti:5050 2>/dev/null || true)
if [ -n "$PID" ]; then
    kill -9 $PID 2>/dev/null || true
fi

echo "Launching JAT application server on http://localhost:5050 ..."
if command -v open >/dev/null 2>&1; then
    open "http://localhost:5050" 2>/dev/null || true
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:5050" 2>/dev/null || true
fi

exec "$VENV_PYTHON" app.py
