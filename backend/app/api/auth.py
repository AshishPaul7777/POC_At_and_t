"""Login, session, and user administration.

This router is mounted WITHOUT the app-wide auth dependency, because /login and
/me have to be reachable while signed out. Every route that changes anything
therefore carries its own guard -- there is no ambient protection here to lean
on, which is worth remembering when adding one.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth import tokens, users
from app.auth.deps import COOKIE_NAME, current_user, require_admin
from app.config import get_settings

log = structlog.get_logger()
router = APIRouter(prefix="/api/auth", tags=["auth"])

def _ttl() -> int:
    return max(1, get_settings().auth_session_hours) * 3600


def _set_cookie(response: Response, token: str, request: Request) -> None:
    # Secure is set only when the request actually arrived over TLS. Setting it
    # unconditionally would silently drop the cookie on a plain-HTTP VM, and
    # the symptom -- login appears to succeed, next request is 401 -- gives no
    # hint why. X-Forwarded-Proto is honoured because uvicorn runs with
    # --proxy-headers behind nginx.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=_ttl(),
        httponly=True,
        samesite="lax",
        secure=(proto == "https"),
        path="/",
    )


@router.post("/login")
async def login(payload: dict, request: Request, response: Response) -> dict:
    email = str((payload or {}).get("email", "")).strip()
    password = str((payload or {}).get("password", ""))
    if not email or not password:
        raise HTTPException(400, "email and password are required")

    user = await users.authenticate(email, password)
    if not user:
        log.info("auth_login_failed", email=email[:64],
                 ip=request.client.host if request.client else None)
        # A fixed delay on failure. Not a rate limiter, but enough that an
        # online guessing attempt is slow, and it costs a real user nothing.
        await asyncio.sleep(0.6)
        # One message for both wrong-password and no-such-account, so the
        # response cannot be used to enumerate who has access.
        raise HTTPException(401, "incorrect email or password")

    token = tokens.issue(
        {"sub": user["email"], "role": user["role"], "uid": user["id"]},
        _ttl())
    _set_cookie(response, token, request)
    log.info("auth_login", email=user["email"], role=user["role"])
    return {"email": user["email"], "role": user["role"],
            "is_superuser": user["is_superuser"]}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(request: Request) -> dict:
    """Who am I? Answers 200 with authenticated=false rather than 401.

    The client calls this on every load to decide between the app and the login
    page; a 401 here would be indistinguishable from a real failure and would
    pollute the console on every visit.
    """
    from app.auth.deps import _payload

    payload = _payload(request)
    if not payload:
        return {"authenticated": False}
    # The live row, not the cookie's copy of it: a disabled account must land on
    # the login page rather than on an app shell whose every request 401s, and a
    # role change must reach the nav without making the user sign in again.
    record = await users.get_active(int(payload["uid"]))
    if record is None:
        return {"authenticated": False}
    return {"authenticated": True, "email": record["email"],
            "role": record["role"], "is_superuser": record["is_superuser"]}


@router.post("/password")
async def change_own_password(payload: dict,
                              user: dict = Depends(current_user)) -> dict:
    """Change your own password. Requires the current one."""
    current = str((payload or {}).get("current_password", ""))
    new = str((payload or {}).get("new_password", ""))
    if not await users.authenticate(str(user["sub"]), current):
        await asyncio.sleep(0.6)
        raise HTTPException(401, "current password is incorrect")
    record = await users.get_by_email(str(user["sub"]))
    if not record:
        raise HTTPException(404, "account no longer exists")
    try:
        await users.update_user(record["id"], password=new)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


# -- administration ------------------------------------------------------------

@router.get("/users")
async def list_users(_: dict = Depends(require_admin)) -> list[dict]:
    return await users.list_users()


@router.post("/users")
async def create_user(payload: dict, admin: dict = Depends(require_admin)) -> dict:
    try:
        return await users.create_user(
            email=str((payload or {}).get("email", "")),
            password=str((payload or {}).get("password", "")),
            role=str((payload or {}).get("role", "user")),
            created_by=str(admin.get("sub", "")),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.patch("/users/{user_id}")
async def update_user(user_id: int, payload: dict,
                      _: dict = Depends(require_admin)) -> dict:
    body = payload or {}
    try:
        return await users.update_user(
            user_id,
            role=body.get("role"),
            is_active=body.get("is_active"),
            password=body.get("password"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, admin: dict = Depends(require_admin)) -> dict:
    # Removing your own account mid-session leaves a valid cookie for a user
    # that no longer exists; refusing is simpler than reasoning about it.
    if int(admin.get("uid", 0)) == user_id:
        raise HTTPException(400, "you cannot delete your own account")
    try:
        await users.delete_user(user_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@router.get("/config")
async def auth_config() -> dict:
    """Whether the deployment is usable at all, for the login page to warn on."""
    s = get_settings()
    return {"superuser_configured": bool(s.auth_superuser_email
                                         and s.auth_superuser_password)}
