"""Authentication: hashing, session tokens, and the guarantees the UI relies on.

These run against the real database, like the rest of the suite. Each test
creates accounts under a unique address and removes them afterwards, so the
superuser provisioned from the environment is left exactly as it was found --
deleting it would lock the developer out of their own running instance.

The load-bearing cases are the negative ones: a disabled account's existing
session must stop working, the superuser must survive a hostile admin, and a
wrong password must be indistinguishable from an unknown address.
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.auth import passwords, tokens, users
from app.auth.deps import COOKIE_NAME, current_user, require_admin
from app.db.session import session_scope


def _email() -> str:
    return f"test-{uuid.uuid4().hex[:10]}@example.invalid"


@pytest.fixture
async def account():
    """A throwaway active user, removed however the test ends."""
    created: list[int] = []

    async def make(role: str = "user", password: str = "correct-horse-battery"):
        row = await users.create_user(_email(), password, role, created_by="tests")
        created.append(row["id"])
        return row, password

    yield make

    async with session_scope() as s:
        for uid in created:
            await s.execute(text("DELETE FROM app_user WHERE id = :i"), {"i": uid})


class _Req:
    """The one thing the dependencies read off a Request."""

    def __init__(self, token: str | None):
        self.cookies = {COOKIE_NAME: token} if token else {}


# -- password hashing --------------------------------------------------------

def test_hash_is_salted_and_verifies():
    a = passwords.hash_password("same-password-twice")
    b = passwords.hash_password("same-password-twice")
    assert a != b, "identical passwords must not produce identical hashes"
    assert passwords.verify_password("same-password-twice", a)
    assert passwords.verify_password("same-password-twice", b)


def test_hash_does_not_contain_the_password():
    digest = passwords.hash_password("hunter2-hunter2")
    assert "hunter2" not in digest


def test_wrong_password_rejected():
    assert not passwords.verify_password("nope", passwords.hash_password("yes"))


def test_malformed_hash_is_rejected_not_raised():
    # A truncated or hand-edited row must fail closed rather than 500.
    for bad in ("", "not-a-hash", "scrypt$16384$8", "scrypt$x$y$z$w"):
        assert not passwords.verify_password("anything", bad)


# -- session tokens ----------------------------------------------------------

def test_token_round_trip():
    token = tokens.issue({"sub": "a@b.c", "role": "admin", "uid": 7}, 60)
    payload = tokens.verify(token)
    assert payload is not None
    assert payload["sub"] == "a@b.c" and payload["uid"] == 7


def test_tampered_token_rejected():
    token = tokens.issue({"sub": "a@b.c", "role": "user", "uid": 7}, 60)
    body, sig = token.rsplit(".", 1)
    forged = tokens.issue({"sub": "a@b.c", "role": "admin", "uid": 7}, 60)
    # Swap in an admin payload while keeping the signature of the user payload.
    assert tokens.verify(forged.rsplit(".", 1)[0] + "." + sig) is None
    assert tokens.verify(body + ".AAAA") is None
    assert tokens.verify("garbage") is None
    assert tokens.verify(None) is None


def test_expired_token_rejected():
    assert tokens.verify(tokens.issue({"sub": "a", "uid": 1}, -1)) is None
    assert int(time.time()) > 0  # sanity: the clock is real, not frozen


# -- authenticate ------------------------------------------------------------

async def test_authenticate_accepts_correct_password(account):
    row, pw = await account()
    assert await users.authenticate(row["email"], pw) is not None


async def test_authenticate_is_case_insensitive_on_email(account):
    row, pw = await account()
    assert await users.authenticate(row["email"].upper(), pw) is not None


async def test_authenticate_rejects_wrong_password(account):
    row, _ = await account()
    assert await users.authenticate(row["email"], "wrong-password") is None


async def test_authenticate_rejects_unknown_email():
    assert await users.authenticate(_email(), "whatever-password") is None


async def test_authenticate_rejects_disabled_account(account):
    row, pw = await account()
    await users.update_user(row["id"], is_active=False)
    assert await users.authenticate(row["email"], pw) is None


# -- the guarantees the admin panel makes ------------------------------------

async def test_duplicate_email_rejected_regardless_of_case(account):
    row, _ = await account()
    with pytest.raises(ValueError):
        await users.create_user(row["email"].upper(), "another-long-password",
                                "user", created_by="tests")


async def test_short_password_rejected():
    with pytest.raises(ValueError):
        await users.create_user(_email(), "short", "user", created_by="tests")


async def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        await users.create_user(_email(), "a-long-enough-password", "superadmin",
                                created_by="tests")


async def test_superuser_cannot_be_demoted_disabled_or_deleted():
    """The one account guaranteed to work after a deploy stays that way."""
    su = await users.get_by_email(_superuser_email())
    if su is None:
        pytest.skip("no superuser provisioned in this environment")
    for kwargs in ({"role": "user"}, {"is_active": False}):
        with pytest.raises(ValueError):
            await users.update_user(su["id"], **kwargs)
    with pytest.raises(ValueError):
        await users.delete_user(su["id"])
    # Still intact.
    again = await users.get_by_email(su["email"])
    assert again["role"] == "admin" and again["is_active"]


def _superuser_email() -> str:
    from app.config import get_settings
    return (get_settings().auth_superuser_email or "").strip().lower()


# -- request-level guards ----------------------------------------------------

async def test_current_user_rejects_missing_and_bad_cookies():
    for req in (_Req(None), _Req("garbage"), _Req(tokens.issue({"uid": 1}, -1))):
        with pytest.raises(HTTPException) as e:
            await current_user(req)
        assert e.value.status_code == 401


async def test_current_user_accepts_a_live_session(account):
    row, _ = await account()
    token = tokens.issue({"sub": row["email"], "role": "user", "uid": row["id"]}, 60)
    user = await current_user(_Req(token))
    assert user["email"] == row["email"] and user["role"] == "user"


async def test_disabling_an_account_ends_its_existing_session(account):
    """The admin panel's disable button is worthless if the open tab keeps working."""
    row, _ = await account()
    token = tokens.issue({"sub": row["email"], "role": "user", "uid": row["id"]}, 3600)
    await current_user(_Req(token))            # works while active

    await users.update_user(row["id"], is_active=False)
    with pytest.raises(HTTPException) as e:
        await current_user(_Req(token))
    assert e.value.status_code == 401


async def test_deleting_an_account_ends_its_existing_session(account):
    row, _ = await account()
    token = tokens.issue({"sub": row["email"], "role": "user", "uid": row["id"]}, 3600)
    await users.delete_user(row["id"])
    with pytest.raises(HTTPException):
        await current_user(_Req(token))


async def test_role_is_read_from_the_database_not_the_cookie(account):
    """A cookie minted as admin must not confer admin after a demotion."""
    row, _ = await account(role="admin")
    token = tokens.issue({"sub": row["email"], "role": "admin", "uid": row["id"]}, 3600)
    assert (await require_admin(await current_user(_Req(token))))["role"] == "admin"

    await users.update_user(row["id"], role="user")
    with pytest.raises(HTTPException) as e:
        await require_admin(await current_user(_Req(token)))
    assert e.value.status_code == 403


async def test_require_admin_refuses_a_plain_user(account):
    row, _ = await account()
    token = tokens.issue({"sub": row["email"], "role": "user", "uid": row["id"]}, 3600)
    with pytest.raises(HTTPException) as e:
        await require_admin(await current_user(_Req(token)))
    assert e.value.status_code == 403
