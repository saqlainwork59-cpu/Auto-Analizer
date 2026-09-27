from __future__ import annotations

import uuid

import httpx


async def register(client: httpx.AsyncClient, email: str | None = None, password: str = "Very-Str0ng-pass") -> dict:
    email = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
    r = await client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return {"email": email, "password": password, "csrf": r.json()["csrf_token"], "id": r.json()["user"]["id"]}


def csrf_headers(client: httpx.AsyncClient) -> dict:
    return {"X-CSRF-Token": client.cookies.get("px_csrf") or ""}
