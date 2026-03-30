"""Pydantic models for Auth0 authentication and role-based access."""

from __future__ import annotations

from pydantic import BaseModel, Field


class User(BaseModel):
    """Authenticated user with role and permissions resolved from config/roles.yaml."""

    email: str
    name: str = ""
    role: str | None = None  # admin / board / staff / None
    permissions: list[str] = Field(default_factory=list)

    # --- convenience helpers ---------------------------------------------------

    def has_permission(self, permission: str) -> bool:
        """Check whether the user holds *permission*."""
        return permission in self.permissions

    @property
    def is_authenticated(self) -> bool:
        return bool(self.email)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"
