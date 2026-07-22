#!/bin/bash
cd "$(dirname "$0")"
lsof -ti:5050 | xargs kill -9 2>/dev/null
sleep 0.5
python3 app.py
