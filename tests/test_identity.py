from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

from cms_labs_jupyter.identity import ProxyIdentityProvider


def encoded_identity(**overrides: object) -> str:
    identity = {"sub": "42", "username": "student", "name": "Иван Иванов"}
    identity.update(overrides)
    return base64.urlsafe_b64encode(json.dumps(identity).encode()).decode().rstrip("=")


def handler(value: str) -> SimpleNamespace:
    return SimpleNamespace(request=SimpleNamespace(headers={"X-CMS-Identity": value}))


def test_proxy_identity_provider_uses_verified_header() -> None:
    provider = ProxyIdentityProvider()
    user = asyncio.run(provider.get_user(handler(encoded_identity())))
    assert user is not None
    assert user.username == "42"
    assert user.name == "student"
    assert user.display_name == "Иван Иванов"
    assert user.initials == "ИИ"


def test_proxy_identity_provider_rejects_missing_identity() -> None:
    provider = ProxyIdentityProvider()
    assert asyncio.run(provider.get_user(handler(""))) is None
