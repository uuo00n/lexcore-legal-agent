from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.auth import admin_auth_enabled, require_admin


@pytest.mark.asyncio
async def test_require_admin_is_noop_without_key(monkeypatch):
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)

    await require_admin(None)

    assert admin_auth_enabled() is False


@pytest.mark.asyncio
async def test_require_admin_rejects_wrong_key(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "secret")

    with pytest.raises(HTTPException) as exc:
        await require_admin("wrong")

    assert exc.value.status_code == 401
    await require_admin("secret")
