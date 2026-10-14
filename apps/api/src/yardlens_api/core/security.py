"""Passwords, tokens and the signing secret.

- Passwords are hashed with Argon2id (memory-hard, so offline guessing is slow even on GPUs).
- Access tokens are short-lived JWTs (HS256) carrying the user and the refresh-token family they
  were issued from, so a revoked family stops them at once.
- Refresh tokens are 256 random bits. Only their SHA-256 is stored: a leaked database cannot be
  replayed into sessions.
"""

import hashlib
import hmac
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_ISSUER = "yardlens"
_LEEWAY_SECONDS = 10


def password_hasher(*, fast: bool = False) -> PasswordHasher:
    """Argon2id with the library's recommended cost; ``fast`` for tests only."""
    if fast:
        return PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    return PasswordHasher()


def verify_password(hasher: PasswordHasher, password_hash: str, password: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except VerifyMismatchError, VerificationError, InvalidHashError:
        return False


def new_token() -> str:
    """A 256-bit random token, URL-safe."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def same(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def load_secret(configured: str | None, path: Path) -> str:
    """The configured secret, or one generated on first use and kept in ``path`` (owner-only)."""
    if configured:
        return configured
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        return existing
    secret = secrets.token_urlsafe(64)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(secret)
    logger.info("Generated a new auth secret", extra={"path": str(path)})
    return secret


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: int
    family_id: str
    expires_at: datetime


def encode_access(
    secret: str, *, user_id: int, family_id: str, ttl: timedelta, now: datetime | None = None
) -> tuple[str, datetime]:
    issued = now or datetime.now(UTC)
    expires = issued + ttl
    token = jwt.encode(  # pyright: ignore[reportUnknownMemberType]
        {
            "sub": str(user_id),
            "fam": family_id,
            "typ": "access",
            "iss": _ISSUER,
            "iat": issued,
            "exp": expires,
            "jti": uuid.uuid4().hex,
        },
        secret,
        algorithm=_ALGORITHM,
    )
    return token, expires


def decode_access(secret: str, token: str) -> AccessClaims | None:
    """The claims of a valid, unexpired access token; ``None`` for anything else."""
    try:
        claims = jwt.decode(  # pyright: ignore[reportUnknownMemberType]
            token,
            secret,
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
            leeway=_LEEWAY_SECONDS,
            options={"require": ["sub", "fam", "typ", "iat", "exp"]},
        )
    except jwt.PyJWTError:
        return None
    if claims.get("typ") != "access":
        return None
    try:
        return AccessClaims(
            user_id=int(claims["sub"]),
            family_id=str(claims["fam"]),
            expires_at=datetime.fromtimestamp(int(claims["exp"]), UTC),
        )
    except KeyError, TypeError, ValueError:
        return None
