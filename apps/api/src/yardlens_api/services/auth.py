"""Signing in, staying signed in, signing out.

A sign-in starts a *family* of refresh tokens. Every refresh replaces the token with a new one in
the same family and extends it, so a user who keeps coming back is never signed out. A token that
was already replaced and is presented again (outside a short grace window for tabs refreshing at
the same moment) means it was copied: the whole family is revoked, signing out both the thief and
the device it came from. Access tokens name their family, and revoked families are kept in memory
for as long as their access tokens could live, so revocation takes effect at once.

Guessing is slowed rather than locked out (locking out the only user would hand an attacker a
denial of service): each address waits longer after every failure, and a wave of failures from
everywhere slows every attempt.
"""

import asyncio
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yardlens_api.core.errors import AppError
from yardlens_api.core.security import (
    AccessClaims,
    decode_access,
    encode_access,
    new_token,
    token_hash,
    verify_password,
)
from yardlens_api.db.models import RefreshToken, User

logger = logging.getLogger(__name__)

# Tabs refreshing at the same moment present the same token; within this window that is not theft.
REUSE_GRACE = timedelta(seconds=60)
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256
# Replaced and revoked tokens are kept this long to recognise reuse, then deleted.
TOKEN_HISTORY = timedelta(days=30)
ALERT_AFTER_FAILURES = 10


class AuthError(AppError):
    status_code = 401


class TooManyAttemptsError(AppError):
    status_code = 429

    def __init__(self, wait_seconds: float) -> None:
        super().__init__(f"Too many attempts; try again in {max(1, round(wait_seconds))} s")
        self.wait_seconds = wait_seconds


class InvalidPasswordError(AppError):
    status_code = 422


@dataclass(frozen=True, slots=True)
class Issued:
    user: User
    family_id: str
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


class LoginThrottle:
    """Per-address exponential delay after a few free failures, plus a global slowdown."""

    FREE_FAILURES = 3
    MAX_WAIT = 300.0
    GLOBAL_WINDOW = 60.0
    GLOBAL_LIMIT = 20

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[str, tuple[int, float]] = {}
        self._recent: deque[float] = deque()

    def wait(self, address: str) -> float:
        """Seconds ``address`` must still wait before trying again."""
        count, last = self._failures.get(address, (0, 0.0))
        if count < self.FREE_FAILURES:
            return 0.0
        delay = min(self.MAX_WAIT, 2.0 ** min(count - self.FREE_FAILURES, 16))
        return max(0.0, last + delay - self._clock())

    def global_delay(self) -> float:
        now = self._clock()
        while self._recent and now - self._recent[0] > self.GLOBAL_WINDOW:
            self._recent.popleft()
        return 1.0 if len(self._recent) >= self.GLOBAL_LIMIT else 0.0

    def failed(self, address: str) -> int:
        now = self._clock()
        count, last = self._failures.get(address, (0, 0.0))
        if now - last > 3600:
            count = 0
        if len(self._failures) >= 10_000:
            # Addresses that gave up an hour ago are forgotten, so the table cannot grow forever.
            self._failures = {a: v for a, v in self._failures.items() if now - v[1] <= 3600}
        self._failures[address] = (count + 1, now)
        self._recent.append(now)
        return count + 1

    def succeeded(self, address: str) -> None:
        self._failures.pop(address, None)


def password_problem(password: str, username: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters"
    if password.strip().lower() == username.strip().lower():
        return "Password must differ from the username"
    return None


class AuthService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        secret: str,
        hasher: PasswordHasher,
        access_ttl: timedelta,
        refresh_ttl: timedelta,
        alert: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._secret = secret
        self._hasher = hasher
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl
        self._alert = alert
        self.throttle = LoginThrottle()
        self._login_locks: dict[str, asyncio.Lock] = {}
        self._login_waiting: dict[str, int] = {}
        # Verified against when the username is unknown, so both paths take the same time.
        self._dummy_hash = hasher.hash(new_token())
        self._revoked: dict[str, datetime] = {}

    # -- setup -----------------------------------------------------------------------------------

    async def load_revocations(self) -> None:
        """Families revoked recently enough for their access tokens to still be alive."""
        since = datetime.now(UTC) - self.access_ttl - timedelta(minutes=1)
        async with self._session_factory() as session:
            rows = await session.execute(
                select(RefreshToken.family_id, func.max(RefreshToken.revoked_at))
                .where(RefreshToken.revoked_at >= since)
                .group_by(RefreshToken.family_id)
            )
            for family_id, revoked_at in rows:
                if revoked_at is not None:
                    self._revoked[family_id] = revoked_at

    async def has_user(self) -> bool:
        async with self._session_factory() as session:
            return bool(await session.scalar(select(func.count()).select_from(User)))

    async def create_admin(self, username: str, password: str) -> None:
        """The one administrator. Refuses when one exists already."""
        name = username.strip().lower()
        if not name:
            raise InvalidPasswordError("Username is required")
        problem = password_problem(password, name)
        if problem:
            raise InvalidPasswordError(problem)
        password_hash = await asyncio.to_thread(self._hasher.hash, password)
        async with self._session_factory() as session:
            if await session.scalar(select(func.count()).select_from(User)):
                raise InvalidPasswordError("An administrator exists already")
            session.add(User(username=name, password_hash=password_hash))
            await session.commit()
        logger.info("Administrator created", extra={"username": name})

    async def set_password(self, username: str, password: str) -> None:
        """Reset a password from the command line; signs the user out everywhere."""
        name = username.strip().lower()
        problem = password_problem(password, name)
        if problem:
            raise InvalidPasswordError(problem)
        password_hash = await asyncio.to_thread(self._hasher.hash, password)
        async with self._session_factory() as session:
            user = await session.scalar(select(User).where(User.username == name))
            if user is None:
                raise AuthError(f"No user named {name}")
            user.password_hash = password_hash
            user.password_changed_at = datetime.now(UTC)
            await self._revoke(session, RefreshToken.user_id == user.id)
            await session.commit()

    # -- sign-in and refresh -------------------------------------------------------------------

    async def login(
        self, username: str, password: str, *, address: str, user_agent: str | None
    ) -> Issued:
        """Attempts from one address run one at a time, so a burst of parallel guesses meets
        the throttle instead of slipping past it while the first attempt is still checked."""
        lock = self._login_locks.setdefault(address, asyncio.Lock())
        self._login_waiting[address] = self._login_waiting.get(address, 0) + 1
        try:
            async with lock:
                return await self._login(username, password, address=address, user_agent=user_agent)
        finally:
            self._login_waiting[address] -= 1
            if self._login_waiting[address] == 0:
                del self._login_waiting[address]
                del self._login_locks[address]

    async def _login(
        self, username: str, password: str, *, address: str, user_agent: str | None
    ) -> Issued:
        wait = self.throttle.wait(address)
        if wait > 0:
            raise TooManyAttemptsError(wait)
        await asyncio.sleep(self.throttle.global_delay())
        name = username.strip().lower()
        async with self._session_factory() as session:
            user = await session.scalar(select(User).where(User.username == name))
            known_hash = user.password_hash if user is not None else self._dummy_hash
            valid = await asyncio.to_thread(verify_password, self._hasher, known_hash, password)
            if user is None or not valid:
                failures = self.throttle.failed(address)
                logger.warning("Failed sign-in", extra={"address": address, "failures": failures})
                if failures == ALERT_AFTER_FAILURES and self._alert is not None:
                    await self._alert(address)
                raise AuthError("Wrong username or password")
            self.throttle.succeeded(address)
            if self._hasher.check_needs_rehash(user.password_hash):
                user.password_hash = await asyncio.to_thread(self._hasher.hash, password)
            user.last_login_at = datetime.now(UTC)
            issued = self._issue(session, user, uuid.uuid4().hex, address, user_agent)
            await session.commit()
        logger.info("Signed in", extra={"username": name, "address": address})
        return issued

    async def refresh(self, token: str, *, address: str, user_agent: str | None) -> Issued:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            row = await session.scalar(
                select(RefreshToken).where(RefreshToken.token_hash == token_hash(token))
            )
            if row is None or row.revoked_at is not None or row.expires_at <= now:
                raise AuthError("Session expired")
            if row.rotated_at is not None and now - row.rotated_at > REUSE_GRACE:
                await self._revoke(session, RefreshToken.family_id == row.family_id)
                await session.commit()
                logger.warning(
                    "A replaced refresh token was used again; signed that device out",
                    extra={"address": address, "family": row.family_id},
                )
                raise AuthError("Session expired")
            row.rotated_at = row.rotated_at or now
            row.last_used_at = now
            user = await session.get(User, row.user_id)
            if user is None:
                raise AuthError("Session expired")
            issued = self._issue(session, user, row.family_id, address, user_agent)
            await session.commit()
        return issued

    def _issue(
        self,
        session: AsyncSession,
        user: User,
        family_id: str,
        address: str,
        user_agent: str | None,
    ) -> Issued:
        now = datetime.now(UTC)
        refresh = new_token()
        refresh_expires = now + self.refresh_ttl
        session.add(
            RefreshToken(
                id=uuid.uuid4().hex,
                user_id=user.id,
                family_id=family_id,
                token_hash=token_hash(refresh),
                created_at=now,
                expires_at=refresh_expires,
                user_agent=(user_agent or "")[:256] or None,
                ip=address[:64],
            )
        )
        access, access_expires = encode_access(
            self._secret, user_id=user.id, family_id=family_id, ttl=self.access_ttl, now=now
        )
        return Issued(user, family_id, access, access_expires, refresh, refresh_expires)

    # -- checking and signing out ----------------------------------------------------------------

    def verify(self, access_token: str) -> AccessClaims | None:
        claims = decode_access(self._secret, access_token)
        if claims is None or self.is_revoked(claims.family_id):
            return None
        return claims

    def is_revoked(self, family_id: str) -> bool:
        return family_id in self._revoked

    async def user(self, user_id: int) -> User | None:
        async with self._session_factory() as session:
            return await session.get(User, user_id)

    async def logout(self, family_id: str) -> None:
        async with self._session_factory() as session:
            await self._revoke(session, RefreshToken.family_id == family_id)
            await session.commit()

    async def logout_all(self, user_id: int, *, keep_family: str | None = None) -> None:
        async with self._session_factory() as session:
            condition = RefreshToken.user_id == user_id
            if keep_family is not None:
                condition = condition & (RefreshToken.family_id != keep_family)
            await self._revoke(session, condition)
            await session.commit()

    async def change_password(
        self, user_id: int, current: str, new: str, *, keep_family: str
    ) -> None:
        """Signs every other device out; the one making the change stays signed in."""
        async with self._session_factory() as session:
            user = await session.get(User, user_id)
            if user is None:
                raise AuthError("Session expired")
            if not await asyncio.to_thread(
                verify_password, self._hasher, user.password_hash, current
            ):
                raise InvalidPasswordError("Current password is wrong")
            problem = password_problem(new, user.username)
            if problem:
                raise InvalidPasswordError(problem)
            user.password_hash = await asyncio.to_thread(self._hasher.hash, new)
            user.password_changed_at = datetime.now(UTC)
            await self._revoke(
                session,
                (RefreshToken.user_id == user_id) & (RefreshToken.family_id != keep_family),
            )
            await session.commit()

    async def _revoke(self, session: AsyncSession, condition: Any) -> None:
        now = datetime.now(UTC)
        families = (
            await session.scalars(
                select(RefreshToken.family_id).where(condition, RefreshToken.revoked_at.is_(None))
            )
        ).all()
        await session.execute(
            update(RefreshToken)
            .where(condition, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        for family_id in set(families):
            self._revoked[family_id] = now

    async def purge(self) -> int:
        """Delete expired tokens and old history; forget revocations no access token outlives."""
        now = datetime.now(UTC)
        cutoff = now - TOKEN_HISTORY
        async with self._session_factory() as session:
            result = await session.execute(
                delete(RefreshToken).where(
                    or_(
                        RefreshToken.expires_at < now,
                        RefreshToken.revoked_at < cutoff,
                        RefreshToken.rotated_at < cutoff,
                    )
                )
            )
            await session.commit()
        horizon = now - self.access_ttl - timedelta(minutes=1)
        self._revoked = {family: at for family, at in self._revoked.items() if at >= horizon}
        deleted: int = getattr(result, "rowcount", 0) or 0
        return deleted
