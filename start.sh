#!/bin/bash
cd "$(dirname "$0")"
pip3 install -q -r requirements.txt

# Best-effort: bring up the local AI gap-filler (Ollama, offline, free).
# The app works fine on regex extraction alone if any of this is skipped or
# fails — it just makes the optional AI-assisted extraction available without
# a manual setup step. See gen_job.py's ai_extract_fields()/ensure_ai_ready().
AI_MODEL="qwen2.5:0.5b"
if ! command -v ollama >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        echo "Ollama not found — installing via Homebrew (one-time, free, fully offline afterwards)..."
        brew install ollama \
            || echo "  [warn] 'brew install ollama' failed — AI-assisted extraction will be unavailable; regex extraction still works normally."
    else
        echo "[info] Ollama not found and Homebrew isn't available — skipping local AI setup (regex-only extraction will be used)."
        echo "       Install Homebrew (https://brew.sh) or Ollama (https://ollama.com) manually to enable AI-assisted extraction."
    fi
fi

if command -v ollama >/dev/null 2>&1; then
    if ! curl -s -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
        echo "Starting Ollama (local AI model server)..."
        nohup ollama serve >/tmp/jat_ollama.log 2>&1 &
        disown
        sleep 2
    fi
    if ! ollama list 2>/dev/null | grep -q "^${AI_MODEL}"; then
        echo "Pulling local AI model ${AI_MODEL} (one-time download, ~400MB)..."
        ollama pull "$AI_MODEL" \
            || echo "  [warn] Could not pull ${AI_MODEL} — AI-assisted extraction will be unavailable; regex extraction still works normally."
    fi
fi

lsof -ti:5050 | xargs kill -9 2>/dev/null
sleep 0.5
python3 app.py
