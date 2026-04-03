#!/usr/bin/env bash
# Load dev environment and start uvicorn with hot-reload.
# Usage: bash dev.sh  (or source dev.sh)

# Auth0
export AUTH0_DOMAIN="dev-aqqzoidgnfq307l8.us.auth0.com"
export AUTH0_CLIENT_ID="REDACTED_AUTH0_CLIENT_ID"
export AUTH0_CLIENT_SECRET="REDACTED_AUTH0_CLIENT_SECRET"

# Xero Custom Connection
export XERO_CLIENT_ID="REDACTED_XERO_CLIENT_ID"
export XERO_CLIENT_SECRET="REDACTED_XERO_CLIENT_SECRET"

# Local dev overrides
export SECURE_COOKIES="0"

py -3 -m uvicorn app.main:app --reload --app-dir src
