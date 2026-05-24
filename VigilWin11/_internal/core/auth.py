import time, hashlib, secrets, threading
from core.config import LOGIN_MAX_FAILURES, LOGIN_LOCKOUT_SECS, PBKDF2_ITERATIONS

SESSION_EXPIRY = 8 * 3600

_sessions: dict = {}
_sessions_lock = threading.Lock()

_login_failures: dict = {}
_login_failures_lock = threading.Lock()

def hash_pin(pin: str, salt: bytes | None = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.strip().encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2:{salt.hex()}:{h.hex()}"

def verify_pin(pin: str, stored: str) -> bool:
    if stored.startswith("pbkdf2:"):
        parts = stored.split(":")
        if len(parts) != 3:
            return False
        salt = bytes.fromhex(parts[1])
        expected = f"pbkdf2:{parts[1]}:{parts[2]}"
        return secrets.compare_digest(hash_pin(pin, salt), expected)
    return secrets.compare_digest(
        hashlib.sha256(pin.strip().encode()).hexdigest(), stored)

def is_login_locked(ip: str) -> tuple[bool, int]:
    with _login_failures_lock:
        record = _login_failures.get(ip)
        if not record:
            return False, 0
        failures, locked_until = record
        if failures >= LOGIN_MAX_FAILURES and time.time() < locked_until:
            return True, int(locked_until - time.time())
        if time.time() >= locked_until:
            _login_failures.pop(ip, None)
        return False, 0

def record_login_failure(ip: str) -> None:
    with _login_failures_lock:
        record = _login_failures.get(ip, (0, 0))
        failures = record[0] + 1
        locked_until = time.time() + LOGIN_LOCKOUT_SECS if failures >= LOGIN_MAX_FAILURES else 0
        _login_failures[ip] = (failures, locked_until)

def clear_login_failures(ip: str) -> None:
    with _login_failures_lock:
        _login_failures.pop(ip, None)

def check_session(token: str) -> bool:
    with _sessions_lock:
        exp = _sessions.get(token)
        if exp and time.time() < exp:
            return True
        _sessions.pop(token, None)
        return False

def create_session() -> str:
    token = secrets.token_hex(32)
    now = time.time()
    with _sessions_lock:
        expired = [k for k, v in _sessions.items() if now >= v]
        for k in expired:
            del _sessions[k]
        _sessions[token] = now + SESSION_EXPIRY
    return token

def destroy_session(token: str) -> None:
    with _sessions_lock:
        _sessions.pop(token, None)

def destroy_all_sessions() -> None:
    with _sessions_lock:
        _sessions.clear()

