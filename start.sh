#!/usr/bin/env bash
# macOS / Linux auto-setup and launch script for Job Application Tracker (JAT)
# Fast non-blocking startup with optional background & auto-start on boot.

set -euo pipefail
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_PATH"

PLIST_PATH="$HOME/Library/LaunchAgents/com.jat.tracker.plist"

# Check if running in background mode (e.g. launched via launchd, nohup, or flag)
BG_MODE=0
if [ "${1:-}" = "background" ] || [ "${1:-}" = "bg" ] || [ "${1:-}" = "silent" ] || [ -n "${BACKGROUND:-}" ]; then
    BG_MODE=1
fi

if [ "$BG_MODE" -eq 0 ]; then
    echo "==================================================="
    echo "  Job Application Tracker (JAT) - Startup (Mac/Linux)"
    echo "==================================================="
fi

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
    if [ "$BG_MODE" -eq 0 ]; then echo "Creating Python virtual environment..."; fi
    $PYTHON_CMD -m venv .venv || true
fi

if [ -f ".venv/bin/python" ]; then
    VENV_PYTHON=".venv/bin/python"
else
    VENV_PYTHON="$PYTHON_CMD"
fi

# 3. Fast dependency check
if [ "$BG_MODE" -eq 0 ]; then echo "Ensuring dependencies are up to date..."; fi
"$VENV_PYTHON" -m pip install -q --disable-pip-version-check -r requirements.txt || true

export AUTO_OPEN_BROWSER=1

# 4. Interactive Configuration Options (only if run interactively in terminal)
if [ "$BG_MODE" -eq 0 ] && [ -t 0 ]; then
    echo ""
    echo "---------------------------------------------------"
    echo "  Startup Options"
    echo "---------------------------------------------------"

    # Option 1: Auto-start on macOS login/reboot
    if [ -f "$PLIST_PATH" ]; then
        echo "[Status] System boot auto-start is currently ENABLED."
        read -r -p "[Option 1] Do you want to DISABLE system boot auto-start? [y/N]: " choice1
        case "$choice1" in
            [Yy]*)
                launchctl unload "$PLIST_PATH" 2>/dev/null || true
                rm -f "$PLIST_PATH"
                echo "[OK] macOS startup auto-run disabled."
                ;;
            *)
                echo "[OK] Keeping system boot auto-start ENABLED."
                ;;
        esac
    else
        echo "[Status] System boot auto-start is currently DISABLED."
        read -r -p "[Option 1] Enable JAT auto-start every time macOS logs in? [y/N]: " choice1
        case "$choice1" in
            [Yy]*)
                mkdir -p "$HOME/Library/LaunchAgents"
                cat <<EOF > "$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.jat.tracker</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${SCRIPT_PATH}/start.sh</string>
        <string>background</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
EOF
                launchctl load "$PLIST_PATH" 2>/dev/null || true
                echo "[OK] macOS startup auto-run enabled!"
                ;;
            *)
                echo "[OK] Keeping system boot auto-start DISABLED."
                ;;
        esac
    fi

    echo ""
    # Option 2: Run background vs foreground
    read -r -p "[Option 2] Run JAT hidden in background (no terminal window)? [y/N]: " choice2
    case "$choice2" in
        [Yy]*)
            echo "[OK] Launching JAT in background..."
            nohup "${SCRIPT_PATH}/start.sh" background >/tmp/jat_app.log 2>&1 &
            echo "[OK] JAT is running in the background. Opening browser..."
            exit 0
            ;;
        *)
            echo "[OK] Continuing in foreground terminal window..."
            ;;
    esac
    echo ""
fi

# 5. Asynchronous non-blocking Ollama background setup
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

# 6. Clear port 5050 safely
PID=$(lsof -ti:5050 2>/dev/null || true)
if [ -n "$PID" ]; then
    kill -9 $PID 2>/dev/null || true
fi

if [ "$BG_MODE" -eq 0 ]; then
    echo "Launching JAT application server on http://localhost:5050 ..."
fi

exec "$VENV_PYTHON" app.py

