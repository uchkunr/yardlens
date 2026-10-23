"""Signing in, staying signed in, and the ways in that must stay shut."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    ORIGIN,
    FakeDetector,
    browser,
    login,
)
from yardlens_api.api.deps import Container
from yardlens_api.core.config import Settings
from yardlens_api.db.models import Camera, RefreshToken
from yardlens_api.main import create_app


def _cookie(client: httpx.AsyncClient, name: str) -> str | None:
    for cookie in client.cookies.jar:
        if cookie.name == name:
            return cookie.value
    return None


def _set_refresh(client: httpx.AsyncClient, value: str) -> None:
    client.cookies.set("yl_refresh", value, domain="testserver.local", path="/api/auth")
    for cookie in list(client.cookies.jar):
        if cookie.name == "yl_refresh" and cookie.value != value:
            client.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)


async def test_everything_but_signing_in_needs_a_session(anon_client: httpx.AsyncClient) -> None:
    for path in ("/api/cameras", "/api/settings", "/api/events", "/api/system/resources"):
        assert (await anon_client.get(path)).status_code == 401, path
    assert (await anon_client.get("/media/snapshots/2026/01/01/x.jpg")).status_code == 401
    assert (await anon_client.get("/api/ping")).json() == {"status": "ok"}


async def test_wrong_credentials_get_the_same_answer(anon_client: httpx.AsyncClient) -> None:
    wrong_password = await anon_client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": "not-the-password"}
    )
    unknown_user = await anon_client.post(
        "/api/auth/login", json={"username": "nobody", "password": ADMIN_PASSWORD}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json() == {"detail": "Wrong username or password"}


async def test_signing_in_sets_private_cookies(anon_client: httpx.AsyncClient) -> None:
    response = await login(anon_client)
    cookies = {
        header.split("=", 1)[0]: header for header in response.headers.get_list("set-cookie")
    }
    assert set(cookies) == {"yl_access", "yl_refresh", "yl_session"}
    for header in cookies.values():
        assert "HttpOnly" in header
    assert "SameSite=strict" in cookies["yl_access"]
    assert "Path=/api/auth" in cookies["yl_refresh"]
    me = await anon_client.get("/api/auth/me")
    assert me.json()["user"]["username"] == ADMIN_USERNAME


async def test_state_changes_need_the_csrf_header_and_an_allowed_origin(
    client: httpx.AsyncClient,
) -> None:
    body = {"name": "Gate", "source_type": "rtsp", "url": "rtsp://10.0.0.5/s", "enabled": False}
    no_header = await client.post("/api/cameras", json=body, headers={"X-YardLens-Request": ""})
    assert no_header.status_code == 403
    other_site = await client.post(
        "/api/cameras", json=body, headers={"Origin": "https://evil.example"}
    )
    assert other_site.status_code == 403
    assert (await client.post("/api/cameras", json=body)).status_code == 201


async def test_refresh_rotates_and_a_reused_token_signs_that_device_out(
    client: httpx.AsyncClient, container: Container
) -> None:
    first = _cookie(client, "yl_refresh")
    assert first
    rotated = await client.post("/api/auth/refresh")
    assert rotated.status_code == 200
    second = _cookie(client, "yl_refresh")
    assert second
    assert second != first

    # Tabs refreshing together present the old token within the grace window: still fine.
    _set_refresh(client, first)
    assert (await client.post("/api/auth/refresh")).status_code == 200

    # Long after it was replaced, the old token is a copy: the whole family is revoked.
    async with container.session_factory() as session:
        await session.execute(
            update(RefreshToken).values(rotated_at=datetime.now(UTC) - timedelta(minutes=5))
        )
        await session.commit()
    _set_refresh(client, first)
    assert (await client.post("/api/auth/refresh")).status_code == 401
    assert (await client.get("/api/cameras")).status_code == 401, "access revoked at once"
    async with container.session_factory() as session:
        tokens = (await session.scalars(select(RefreshToken))).all()
    assert all(token.revoked_at is not None for token in tokens)


async def test_signing_out_everywhere_stops_existing_access_tokens(
    client: httpx.AsyncClient, anon_client: httpx.AsyncClient
) -> None:
    await login(anon_client)  # a second device
    assert (await anon_client.get("/api/cameras")).status_code == 200
    assert (await client.post("/api/auth/logout-all")).status_code == 204
    assert (await anon_client.get("/api/cameras")).status_code == 401
    assert (await anon_client.post("/api/auth/refresh")).status_code == 401


async def test_password_change_keeps_this_device_and_signs_out_the_rest(
    client: httpx.AsyncClient, anon_client: httpx.AsyncClient
) -> None:
    await login(anon_client)
    wrong = await client.post(
        "/api/auth/password",
        json={"current_password": "nope", "new_password": "another-long-password"},
    )
    assert wrong.status_code == 422
    short = await client.post(
        "/api/auth/password", json={"current_password": ADMIN_PASSWORD, "new_password": "short"}
    )
    assert short.status_code == 422
    changed = await client.post(
        "/api/auth/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "another-long-password"},
    )
    assert changed.status_code == 204
    assert (await client.get("/api/cameras")).status_code == 200
    assert (await anon_client.get("/api/cameras")).status_code == 401
    await login(anon_client, "another-long-password")


async def test_repeated_failures_slow_the_address_down(anon_client: httpx.AsyncClient) -> None:
    wrong = {"username": ADMIN_USERNAME, "password": "guess"}
    codes = [(await anon_client.post("/api/auth/login", json=wrong)).status_code for _ in range(4)]
    assert codes[:3] == [401, 401, 401], "three free attempts"
    assert codes[3] == 429
    # Even the right password waits until the delay is over.
    right = await anon_client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    assert right.status_code == 429


@pytest.mark.usefixtures("video_file")
async def test_camera_passwords_never_leave_the_server(
    client: httpx.AsyncClient, container: Container
) -> None:
    secret_url = "rtsp://admin:s3cret@10.0.0.5:554/Streaming/Channels/102"
    created = await client.post(
        "/api/cameras",
        json={"name": "Bay", "source_type": "rtsp", "url": secret_url, "enabled": False},
    )
    camera = created.json()
    assert camera["url"] == "rtsp://admin:••••@10.0.0.5:554/Streaming/Channels/102"
    assert "s3cret" not in (await client.get("/api/cameras")).text

    # Saving the form without touching the address keeps the stored password.
    renamed = await client.patch(
        f"/api/cameras/{camera['id']}", json={"name": "Bay 1", "url": camera["url"]}
    )
    assert renamed.status_code == 200
    async with container.session_factory() as session:
        stored = await session.get(Camera, camera["id"])
        assert stored is not None
        assert stored.url == secret_url


async def test_cors_allows_the_dashboard_with_credentials(anon_client: httpx.AsyncClient) -> None:
    preflight = await anon_client.options(
        "/api/auth/login",
        headers={"Access-Control-Request-Method": "POST", "Origin": ORIGIN},
    )
    assert preflight.headers["access-control-allow-origin"] == ORIGIN
    assert preflight.headers["access-control-allow-credentials"] == "true"


async def test_cookies_are_secure_behind_https(settings: Settings) -> None:
    secure_settings = settings.model_copy(
        update={"public_url": "https://yardlens.local", "cors_origins": [ORIGIN]}
    )
    app = create_app(secure_settings, detector=FakeDetector())
    async with app.router.lifespan_context(app), browser(app) as http:
        response = await login(http)
        for header in response.headers.get_list("set-cookie"):
            assert "Secure" in header, header


async def test_parallel_guesses_from_one_address_meet_the_throttle(
    anon_client: httpx.AsyncClient,
) -> None:
    """Attempts from one address are checked one at a time, so a burst cannot slip past the delay
    while the first attempts are still being verified."""
    wrong = {"username": ADMIN_USERNAME, "password": "guess"}
    responses = await asyncio.gather(
        *(anon_client.post("/api/auth/login", json=wrong) for _ in range(8))
    )
    codes = sorted(response.status_code for response in responses)
    assert codes == [401, 401, 401, 429, 429, 429, 429, 429]
