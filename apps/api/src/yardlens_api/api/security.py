"""Auth at the HTTP edge: cookies, the signed-in dependency, CSRF and private snapshots.

Tokens live in HttpOnly cookies, so page scripts (and anything injected into them) never see
them. ``SameSite=Strict`` keeps them off cross-site requests, and every state-changing request
must also carry a custom header a cross-site form cannot set, from an allowed origin.
"""

from typing import Annotated, Any, cast
from urllib.parse import urlsplit

from fastapi import Depends, Request, Response
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from yardlens_api.core.config import Settings
from yardlens_api.core.security import AccessClaims
from yardlens_api.services.auth import AuthError, AuthService, Issued

ACCESS_COOKIE = "yl_access"
REFRESH_COOKIE = "yl_refresh"
# Not a secret: tells the dashboard's server-side routing that a sign-in exists. Lax, so the
# first click on a link (from Telegram, say) still lands on the page rather than the login.
SESSION_COOKIE = "yl_session"
REFRESH_PATH = "/api/auth"
CSRF_HEADER = "x-yardlens-request"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _auth(connection: HTTPConnection) -> AuthService:
    container = connection.app.state.container
    return cast("AuthService", container.auth)


def _settings(connection: HTTPConnection) -> Settings:
    container = connection.app.state.container
    return cast("Settings", container.settings)


def client_address(connection: HTTPConnection) -> str:
    """The caller's address; behind Caddy uvicorn takes it from trusted forwarding headers."""
    return connection.client.host if connection.client else "unknown"


def claims_from(connection: HTTPConnection) -> AccessClaims | None:
    token = connection.cookies.get(ACCESS_COOKIE)
    if not token:
        header = connection.headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    return _auth(connection).verify(token) if token else None


async def require_user(request: Request) -> AccessClaims:
    claims = claims_from(request)
    if claims is None:
        raise AuthError("Not signed in")
    return claims


UserDep = Annotated[AccessClaims, Depends(require_user)]


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or _settings(request).public_url.startswith("https://")


def set_auth_cookies(request: Request, response: Response, issued: Issued) -> None:
    secure = _secure(request)
    auth = _auth(request)
    response.set_cookie(
        ACCESS_COOKIE,
        issued.access_token,
        max_age=int(auth.access_ttl.total_seconds()),
        path="/",
        httponly=True,
        secure=secure,
        samesite="strict",
    )
    refresh_age = int(auth.refresh_ttl.total_seconds())
    response.set_cookie(
        REFRESH_COOKIE,
        issued.refresh_token,
        max_age=refresh_age,
        path=REFRESH_PATH,
        httponly=True,
        secure=secure,
        samesite="strict",
    )
    response.set_cookie(
        SESSION_COOKIE,
        "1",
        max_age=refresh_age,
        path="/",
        httponly=True,
        secure=secure,
        samesite="lax",
    )


def clear_auth_cookies(request: Request, response: Response) -> None:
    secure = _secure(request)
    for name, path, samesite in (
        (ACCESS_COOKIE, "/", "strict"),
        (REFRESH_COOKIE, REFRESH_PATH, "strict"),
        (SESSION_COOKIE, "/", "lax"),
    ):
        response.delete_cookie(
            name, path=path, httponly=True, secure=secure, samesite=cast("Any", samesite)
        )


def allowed_origins(settings: Settings) -> set[str]:
    public = urlsplit(settings.public_url)
    origins = {origin.rstrip("/") for origin in settings.cors_origins}
    if public.scheme and public.netloc:
        origins.add(f"{public.scheme}://{public.netloc}")
    return origins


class CsrfMiddleware:
    """Refuses state-changing API requests without the custom header or from another origin."""

    def __init__(self, app: ASGIApp, *, origins: set[str]) -> None:
        self.app = app
        self.origins = origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in _UNSAFE_METHODS:
            path: str = scope["path"]
            if path.startswith("/api/"):
                headers = Headers(scope=scope)
                origin = headers.get("origin")
                if headers.get(CSRF_HEADER) != "1" or (
                    origin is not None and origin.rstrip("/") not in self.origins
                ):
                    response = JSONResponse(
                        {"detail": "Cross-site request refused"}, status_code=403
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class PrivateStaticFiles(StaticFiles):
    """Snapshots for signed-in users only."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and claims_from(HTTPConnection(scope)) is None:
            response = JSONResponse({"detail": "Not signed in"}, status_code=401)
            await response(scope, receive, send)
            return
        await super().__call__(scope, receive, send)
