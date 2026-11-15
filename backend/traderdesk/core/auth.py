"""Sign-in, sessions and roles. Argon2 passwords, optional TOTP, 12-hour idle sessions,
re-authentication before the first live order of the day."""
from __future__ import annotations

import secrets as _secrets
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

IDLE = timedelta(hours=12)
_ph = PasswordHasher()

ROLES: dict[str, set[str]] = {
    "owner": {"*"},
    "trader": {"portfolio:read", "orders:read", "orders:ticket", "orders:send", "orders:cancel",
               "orders:flatten", "orders:kill", "rules:read", "brokers:read"},
    "viewer": {"portfolio:read", "orders:read", "brokers:read"},
    "accountant": {"portfolio:read", "orders:read", "tax:read", "exports:run"},
    "family": {"money:read"},
}
# owner only: users:manage, brokers:manage, rules:edit, mode:live, audit:read, kill:release


def hash_password(pw: str) -> str:
    if len(pw) < 10:
        raise ValueError("passwords need at least 10 characters")
    return _ph.hash(pw)


def verify_password(hashed: str, pw: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def new_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str | None, code: str | None) -> bool:
    if not secret:
        return True
    return bool(code) and pyotp.TOTP(secret).verify(str(code).strip(), valid_window=1)


def allowed(role: str, perm: str) -> bool:
    p = ROLES.get(role, set())
    return "*" in p or perm in p


@dataclass
class SessionInfo:
    username: str
    role: str
    last_seen: datetime
    reauth_on: date | None = None
    csrf: str = field(default_factory=lambda: _secrets.token_urlsafe(24))


class SessionStore:
    def __init__(self, clock=datetime.now):
        self._s: dict[str, SessionInfo] = {}
        self._lock = threading.Lock()
        self.clock = clock

    def create(self, username: str, role: str) -> tuple[str, SessionInfo]:
        tok = _secrets.token_urlsafe(32)
        info = SessionInfo(username, role, self.clock(), reauth_on=self.clock().date())
        with self._lock:
            self._s[tok] = info
        return tok, info

    def get(self, tok: str | None) -> SessionInfo | None:
        if not tok:
            return None
        with self._lock:
            info = self._s.get(tok)
            if info is None:
                return None
            if self.clock() - info.last_seen > IDLE:
                del self._s[tok]
                return None
            info.last_seen = self.clock()
            return info

    def drop(self, tok: str | None) -> None:
        with self._lock:
            self._s.pop(tok or "", None)

    def all(self) -> list[tuple[str, SessionInfo]]:
        with self._lock:
            return list(self._s.items())

    def drop_user(self, username: str) -> None:
        with self._lock:
            for k in [k for k, v in self._s.items() if v.username == username]:
                del self._s[k]
