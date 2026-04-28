#!/usr/bin/env bash
# Load dev environment and start uvicorn with hot-reload.
# Usage: bash dev.sh
# Requires: .env.local (copy from .env.example and fill in values)

set -a
source "$(dirname "$0")/.env.local"
set +a

py -3 -m uvicorn app.main:app --reload --app-dir src
