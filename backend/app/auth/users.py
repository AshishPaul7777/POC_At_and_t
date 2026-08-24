"""User records, and the superuser that guarantees access after a deploy.

The bootstrap is the important part. A freshly deployed VM has an empty
database and no way in, so the account named by AUTH_SUPERUSER_EMAIL is created
on startup from the environment. It is reconciled on every boot -- password
resets to whatever the env says -- because the recovery path for a forgotten
password on a single-tenant deployment is "change the env and restart", and
that has to actually work.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import text

from app.auth.passwords import hash_password, verify_password
from app.config import get_settings
from app.db.session import session_scope

log = structlog.get_logger()

ROLES = ("admin", "user")

_COLUMNS = """id, email, role, is_active, is_superuser,
              created_at, created_by, last_login_at"""


async def bootstrap_superuser() -> None:
    """Create or reconcile the env-provisioned admin. Called at startup."""
    s = get_settings()
    email = (s.auth_superuser_email or "").strip().lower()
    password = s.auth_superuser_password.get_secret_value() if s.auth_superuser_password else ""

    if not email:
        log.warning("auth_superuser_unset",
                    detail="AUTH_SUPERUSER_EMAIL is empty; nobody can log in "
                           "until a user exists.")
        return
    if not password:
        log.warning("auth_superuser_no_password", email=email,
                    detail="AUTH_SUPERUSER_PASSWORD is empty; the account will "
                           "not be created.")
        return

    async with session_scope() as sess:
        prior = (await sess.execute(text(
            "SELECT bootstrap_fingerprint FROM app_user WHERE lower(email) = :e"
        ), {"e": email})).scalar_one_or_none()

        # Only overwrite the stored password when the environment's value has
        # actually changed. Reconciling unconditionally would revert a password
        # the superuser had rotated in the UI, at the next restart, with no
        # message anywhere -- and the account would still open with a secret
        # sitting in a file on the host.
        unchanged = bool(prior) and verify_password(password, prior)
        if unchanged:
            await sess.execute(text("""
                UPDATE app_user SET role = 'admin', is_active = TRUE,
                                    is_superuser = TRUE
                 WHERE lower(email) = :e
            """), {"e": email})
            log.info("auth_superuser_ready", email=email, password="unchanged")
            return

        digest = hash_password(password)
        await sess.execute(text("""
            INSERT INTO app_user (email, password_hash, role, is_active,
                                  is_superuser, created_by,
                                  bootstrap_fingerprint)
            VALUES (:e, :h, 'admin', TRUE, TRUE, 'bootstrap', :f)
            ON CONFLICT (lower(email)) DO UPDATE SET
                password_hash         = EXCLUDED.password_hash,
                role                  = 'admin',
                is_active             = TRUE,
                is_superuser          = TRUE,
                bootstrap_fingerprint = EXCLUDED.bootstrap_fingerprint
        """), {"e": email, "h": digest, "f": hash_password(password)})
    log.info("auth_superuser_ready", email=email,
             password="applied from environment")


async def authenticate(email: str, password: str) -> dict[str, Any] | None:
    """Verify a credential pair. Returns the user, or None for any failure."""
    async with session_scope() as sess:
        row = (await sess.execute(text(f"""
            SELECT {_COLUMNS}, password_hash FROM app_user
             WHERE lower(email) = lower(:e)
        """), {"e": email})).mappings().first()

    if not row or not row["is_active"]:
        # Hash anyway on a missing user so the response time does not reveal
        # which addresses have accounts.
        verify_password(password, "scrypt$16384$8$1$AAAA$AAAA")
        return None
    if not verify_password(password, row["password_hash"]):
        return None

    async with session_scope() as sess:
        await sess.execute(
            text("UPDATE app_user SET last_login_at = now() WHERE id = :i"),
            {"i": row["id"]})
    return {k: v for k, v in row.items() if k != "password_hash"}


async def get_by_email(email: str) -> dict[str, Any] | None:
    async with session_scope() as sess:
        row = (await sess.execute(text(f"""
            SELECT {_COLUMNS} FROM app_user WHERE lower(email) = lower(:e)
        """), {"e": email})).mappings().first()
    return dict(row) if row else None


async def get_active(user_id: int) -> dict[str, Any] | None:
    """The live row for an id, or None if it is gone or disabled.

    Every guarded request calls this. The cookie already carries the email and
    role, so this lookup exists purely so that disabling or deleting an account
    takes effect now rather than whenever the token happens to expire -- an
    admin who clicks "disable" reasonably expects the session to end with it.
    """
    async with session_scope() as s:
        row = (await s.execute(text(f"""
            SELECT {_COLUMNS} FROM app_user WHERE id = :i AND is_active
        """), {"i": user_id})).mappings().first()
        return dict(row) if row else None


async def list_users() -> list[dict[str, Any]]:
    async with session_scope() as sess:
        rows = (await sess.execute(text(f"""
            SELECT {_COLUMNS} FROM app_user
             ORDER BY is_superuser DESC, role, lower(email)
        """))).mappings().all()
    return [dict(r) for r in rows]


async def create_user(email: str, password: str, role: str,
                      created_by: str) -> dict[str, Any]:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 5:
        raise ValueError("a valid email address is required")
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    # Short passwords are the most common way a deployment gets breached, and
    # this is the only place to stop them.
    if len(password or "") < 12:
        raise ValueError("password must be at least 12 characters")

    async with session_scope() as sess:
        exists = await sess.scalar(
            text("SELECT 1 FROM app_user WHERE lower(email) = :e"), {"e": email})
        if exists:
            raise ValueError(f"{email} already has an account")
        row = (await sess.execute(text(f"""
            INSERT INTO app_user (email, password_hash, role, created_by)
            VALUES (:e, :h, :r, :b)
            RETURNING {_COLUMNS}
        """), {"e": email, "h": hash_password(password), "r": role,
               "b": created_by})).mappings().first()
    log.info("auth_user_created", email=email, role=role, by=created_by)
    return dict(row)


async def update_user(user_id: int, *, role: str | None = None,
                      is_active: bool | None = None,
                      password: str | None = None) -> dict[str, Any]:
    async with session_scope() as sess:
        target = (await sess.execute(
            text("SELECT id, email, is_superuser FROM app_user WHERE id = :i"),
            {"i": user_id})).mappings().first()
        if not target:
            raise ValueError("no such user")
        # The superuser is the account guaranteed to work after a fresh deploy.
        # Letting an admin demote or disable it is how a deployment locks
        # everyone out with no recovery short of editing the database.
        if target["is_superuser"] and (role == "user" or is_active is False):
            raise ValueError("the superuser cannot be demoted or deactivated")

        sets, params = [], {"i": user_id}
        if role is not None:
            if role not in ROLES:
                raise ValueError(f"role must be one of {', '.join(ROLES)}")
            sets.append("role = :r"); params["r"] = role
        if is_active is not None:
            sets.append("is_active = :a"); params["a"] = is_active
        if password is not None:
            if len(password) < 12:
                raise ValueError("password must be at least 12 characters")
            sets.append("password_hash = :h"); params["h"] = hash_password(password)
        if not sets:
            raise ValueError("nothing to update")

        row = (await sess.execute(text(f"""
            UPDATE app_user SET {', '.join(sets)} WHERE id = :i
            RETURNING {_COLUMNS}
        """), params)).mappings().first()
    log.info("auth_user_updated", user_id=user_id, role=role, active=is_active)
    return dict(row)


async def delete_user(user_id: int) -> None:
    async with session_scope() as sess:
        row = (await sess.execute(
            text("SELECT is_superuser, email FROM app_user WHERE id = :i"),
            {"i": user_id})).mappings().first()
        if not row:
            raise ValueError("no such user")
        if row["is_superuser"]:
            raise ValueError("the superuser cannot be deleted")
        await sess.execute(text("DELETE FROM app_user WHERE id = :i"), {"i": user_id})
    log.info("auth_user_deleted", email=row["email"])
