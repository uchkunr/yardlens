from fastapi import APIRouter, Request, Response, status

from yardlens_api.api.deps import ContainerDep
from yardlens_api.api.security import (
    REFRESH_COOKIE,
    UserDep,
    clear_auth_cookies,
    client_address,
    set_auth_cookies,
)
from yardlens_api.schemas.auth import AuthState, LoginRequest, PasswordChange, UserRead
from yardlens_api.services.auth import AuthError, Issued

router = APIRouter(prefix="/auth", tags=["auth"])


def _state(issued: Issued) -> AuthState:
    return AuthState(
        user=UserRead(username=issued.user.username, last_login_at=issued.user.last_login_at),
        access_expires_at=issued.access_expires_at,
    )


@router.post("/login", response_model=AuthState)
async def login(
    payload: LoginRequest, request: Request, response: Response, container: ContainerDep
) -> AuthState:
    issued = await container.auth.login(
        payload.username,
        payload.password,
        address=client_address(request),
        user_agent=request.headers.get("user-agent"),
    )
    set_auth_cookies(request, response, issued)
    return _state(issued)


@router.post("/refresh", response_model=AuthState)
async def refresh(request: Request, response: Response, container: ContainerDep) -> AuthState:
    """Swap the refresh cookie for a new one and a fresh access token. Called on every visit."""
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise AuthError("Not signed in")
    try:
        issued = await container.auth.refresh(
            token, address=client_address(request), user_agent=request.headers.get("user-agent")
        )
    except AuthError:
        clear_auth_cookies(request, response)
        raise
    set_auth_cookies(request, response, issued)
    return _state(issued)


@router.get("/me", response_model=AuthState)
async def me(user: UserDep, container: ContainerDep) -> AuthState:
    found = await container.auth.user(user.user_id)
    if found is None:
        raise AuthError("Not signed in")
    return AuthState(
        user=UserRead(username=found.username, last_login_at=found.last_login_at),
        access_expires_at=user.expires_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: UserDep, request: Request, container: ContainerDep) -> Response:
    await container.auth.logout(user.family_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(request, response)
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: UserDep, request: Request, container: ContainerDep) -> Response:
    """Sign out on every device, this one included."""
    await container.auth.logout_all(user.user_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(request, response)
    return response


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChange, user: UserDep, container: ContainerDep
) -> Response:
    """Every other device is signed out; this one stays signed in."""
    await container.auth.change_password(
        user.user_id, payload.current_password, payload.new_password, keep_family=user.family_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
