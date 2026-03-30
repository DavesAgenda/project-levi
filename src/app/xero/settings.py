"""Xero configuration — loaded from environment variables or Docker secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _read_secret(env_var: str, file_env_var: str, default: str = "") -> str:
    """Read a secret from env var or Docker secret file.

    Docker Compose secrets are mounted as files at /run/secrets/<name>.
    The *_FILE env var points to the file path.  If the file exists,
    its contents are used.  Otherwise, falls back to the plain env var.
    """
    # Try Docker secrets file first (more secure)
    file_path = os.environ.get(file_env_var, "")
    if file_path:
        path = Path(file_path)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()

    # Fall back to plain environment variable
    return os.environ.get(env_var, default)


@dataclass(frozen=True)
class XeroSettings:
    """Xero OAuth credentials loaded from environment variables or Docker secrets."""

    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_env(cls) -> XeroSettings:
        """Load settings from environment variables or Docker secret files.

        Supports two modes:
            1. Docker secrets (preferred): XERO_CLIENT_ID_FILE, XERO_CLIENT_SECRET_FILE
               pointing to /run/secrets/<name>
            2. Plain env vars (dev): XERO_CLIENT_ID, XERO_CLIENT_SECRET

        Optional:
            XERO_REDIRECT_URI (defaults to http://localhost:8000/auth/xero/callback)
        """
        client_id = _read_secret("XERO_CLIENT_ID", "XERO_CLIENT_ID_FILE")
        client_secret = _read_secret("XERO_CLIENT_SECRET", "XERO_CLIENT_SECRET_FILE")
        redirect_uri = os.environ.get(
            "XERO_REDIRECT_URI",
            "http://localhost:8000/auth/xero/callback",
        )

        if not client_id or not client_secret:
            # Allow the app to start without credentials (for dev/testing)
            # but API calls will fail with a clear error
            pass

        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )


# Singleton — import this everywhere
xero_settings = XeroSettings.from_env()
