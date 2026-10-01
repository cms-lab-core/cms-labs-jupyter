from __future__ import annotations

import base64
import json
from typing import Any

from jupyter_server.auth import IdentityProvider, User


IDENTITY_HEADER = "X-CMS-Identity"


def _decode_identity(encoded: str) -> dict[str, Any]:
    if not encoded or len(encoded) > 16_384:
        raise ValueError("missing or oversized workspace identity")
    padding = "=" * (-len(encoded) % 4)
    payload = base64.urlsafe_b64decode(encoded + padding)
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("workspace identity must be an object")
    return value


def _initials(name: str) -> str | None:
    parts = [part for part in name.split() if part]
    if not parts:
        return None
    return "".join(part[0].upper() for part in parts[:2])


class ProxyIdentityProvider(IdentityProvider):
    """Build a Jupyter identity from a header verified by Clabgate/nginx."""

    @property
    def login_available(self) -> bool:
        return False

    async def get_user(self, handler: Any) -> User | None:
        encoded = handler.request.headers.get(IDENTITY_HEADER, "")
        try:
            identity = _decode_identity(encoded)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

        subject = str(identity.get("sub", "")).strip()
        username = str(identity.get("username", "")).strip()
        display_name = str(identity.get("name", "")).strip() or username
        if not subject or not username:
            return None
        return User(
            username=subject,
            name=username,
            display_name=display_name,
            initials=_initials(display_name),
        )
