#!/bin/bash
# One-time setup for Windows Git Bash: creates the virtual environment and installs dependencies.
set -e
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
echo "Setup finished. Open .env, paste your GEMINI_API_KEY, then run: bash run.sh"
