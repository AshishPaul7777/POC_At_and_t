"""Password hashing.

``hashlib.scrypt`` from the standard library rather than passlib or bcrypt.
scrypt is a memory-hard KDF, it has been in Python since 3.6, and using it means
this app takes on no new dependency to hold its credentials -- which is exactly
the kind of dependency worth not having.

Parameters follow the interactive-login profile from the scrypt paper: n=2^14
costs roughly 16 MB and ~50 ms per verification, slow enough to make offline
guessing expensive and fast enough that a login does not feel broken.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N = 1 << 14          # CPU/memory cost
_R = 8                # block size
_P = 1                # parallelism
_DKLEN = 32
_SALT_BYTES = 16
_MAXMEM = 96 * 1024 * 1024   # headroom over the ~16 MB n=2^14 actually needs


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """Return a self-describing hash: ``scrypt$n$r$p$salt$key``.

    The parameters travel with the hash so they can be raised later without
    invalidating existing passwords -- verification reads whatever each row was
    written with.
    """
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R,
                         p=_P, dklen=_DKLEN, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. Never raises -- a malformed hash is just a failure."""
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"), salt=_unb64(salt),
            n=int(n), r=int(r), p=int(p), dklen=len(_unb64(key)),
            maxmem=_MAXMEM,
        )
        # compare_digest, not ==: an early-exit comparison leaks how much of the
        # hash matched, which is enough to reconstruct it a byte at a time.
        return hmac.compare_digest(candidate, _unb64(key))
    except Exception:
        return False
