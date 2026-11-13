"""Login for the web UI: password check, session tokens and a brute-force limit.

Everything is held in memory, so restarting the UI logs everyone out. Nothing here ever
logs or returns a password or a full token.
"""

import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from web_automation.settings import SettingsError

COOKIE_NAME = "wa_session"
MIN_PASSWORD_LENGTH = 8
MAX_USERNAME_LENGTH = 100
MAX_PASSWORD_LENGTH = 200
SESSION_LIFETIME_S = 12 * 3600  # a login lasts 12 hours, then the page asks again
MAX_SESSIONS = 20               # the oldest login is dropped beyond this
MAX_FAILURES = 5                # wrong attempts allowed ...
FAILURE_WINDOW_S = 300          # ... within this many seconds
LOCKOUT_S = 300                 # then every login attempt is refused for this long

Clock = Callable[[], float]


def _digest(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8")).digest()


def tokens_equal(first: str, second: str) -> bool:
    """Constant-time comparison that also accepts non-ASCII text (hmac.compare_digest on
    plain strings raises TypeError for it, which would turn a bad header into a server error)."""
    return hmac.compare_digest(_digest(first), _digest(second))


def validate_login_settings(username: str, password: str) -> bool:
    """True when the UI login is switched on. Raises SettingsError for a half-set or weak one.

    Error messages never contain the values.
    """
    if not username and not password:
        return False
    if not username or not password:
        raise SettingsError("WA_UI_USERNAME and WA_UI_PASSWORD must both be set, or both left empty")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SettingsError(f"WA_UI_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters long")
    if len(username) > MAX_USERNAME_LENGTH or len(password) > MAX_PASSWORD_LENGTH:
        raise SettingsError(
            f"WA_UI_USERNAME may be up to {MAX_USERNAME_LENGTH} and WA_UI_PASSWORD up to "
            f"{MAX_PASSWORD_LENGTH} characters"
        )
    return True


def credentials_match(expected_user: str, expected_password: str, user: str, password: str) -> bool:
    """True only if both match. Both are always compared (no early exit), in constant time,
    so the response time does not reveal which of the two was wrong."""
    user_ok = hmac.compare_digest(_digest(user), _digest(expected_user))
    password_ok = hmac.compare_digest(_digest(password), _digest(expected_password))
    return user_ok & password_ok


class SessionStore:
    """Random login tokens that expire. Only a hash of each token is kept."""

    def __init__(self, lifetime_s: float = SESSION_LIFETIME_S, clock: Clock = time.monotonic) -> None:
        self.lifetime_s = lifetime_s
        self._clock = clock
        self._lock = threading.Lock()
        self._expires: OrderedDict[bytes, float] = OrderedDict()

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._drop_expired()
            self._expires[_digest(token)] = self._clock() + self.lifetime_s
            while len(self._expires) > MAX_SESSIONS:
                self._expires.popitem(last=False)
        return token

    def is_valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            self._drop_expired()
            return _digest(token) in self._expires

    def revoke(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._expires.pop(_digest(token), None)

    def _drop_expired(self) -> None:
        now = self._clock()
        for key in [k for k, until in self._expires.items() if until <= now]:
            del self._expires[key]


class LoginThrottle:
    """After too many wrong attempts, refuse every attempt (even a correct one) for a while."""

    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        window_s: float = FAILURE_WINDOW_S,
        lockout_s: float = LOCKOUT_S,
        clock: Clock = time.monotonic,
    ) -> None:
        self._max, self._window, self._lockout, self._clock = max_failures, window_s, lockout_s, clock
        self._lock = threading.Lock()
        self._failures: deque[float] = deque()
        self._locked_until = 0.0

    def retry_after(self) -> int:
        """Seconds until a login attempt is allowed again; 0 when it is allowed now."""
        with self._lock:
            return max(0, math.ceil(self._locked_until - self._clock()))

    def record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            self._failures.append(now)
            while self._failures and self._failures[0] <= now - self._window:
                self._failures.popleft()
            if len(self._failures) >= self._max:
                self._locked_until = now + self._lockout
                self._failures.clear()

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
