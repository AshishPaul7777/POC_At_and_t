"""Request-level authentication.

The session lives in an httpOnly cookie rather than a header. Two reasons that
matter here: script on the page cannot read it, so an XSS bug cannot exfiltrate
a session; and EventSource sends cookies automatically but cannot set headers,
so the pipeline and assistant streams authenticate with no extra work.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request

from app.auth import tokens

COOKIE_NAME = "sfc_session"


def _payload(request: Request) -> dict[str, Any] | None:
    return tokens.verify(request.cookies.get(COOKIE_NAME))


async def current_user(request: Request) -> dict[str, Any]:
    """Any signed-in, active user. 401 otherwise."""
    payload = _payload(request)
    if not payload:
        # The header tells the browser not to pop its own basic-auth dialog,
        # and tells our client to route to the login page.
        raise HTTPException(status_code=401, detail="not signed in",
                            headers={"WWW-Authenticate": "Cookie"})

    # Re-read the account rather than trusting the cookie's copy of it. A
    # signed token is proof of who signed in, not proof that the account still
    # exists, is still enabled, or still has the role it had at sign-in.
    from app.auth.users import get_active
    user = await get_active(int(payload["uid"]))
    if user is None:
        raise HTTPException(status_code=401, detail="this account is no longer active",
                            headers={"WWW-Authenticate": "Cookie"})
    return {**payload, "email": user["email"], "role": user["role"], "user": user}


async def require_admin(user: dict = Depends(current_user)) -> dict[str, Any]:
    """Admins only -- currently just user management."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="administrator access required")
    return user
