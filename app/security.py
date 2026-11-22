"""密码哈希、会话、凭据加密、登录限速。只用 cryptography + 标准库。

密码：PBKDF2-HMAC-SHA256 260k 轮（与参考项目同口径）。
凭据：AES-GCM，主密钥 32 字节存 data/master.key，丢了无法解密 AK Secret。
会话：32 字节随机 token，内存表 + 过期时间；登录失败 5 次锁定 15 分钟。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import settings

PBKDF2_ROUNDS = 260_000
SESSION_TTL = 14 * 24 * 3600
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60

_sessions: dict[str, float] = {}
_sessions_lock = threading.Lock()
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ROUNDS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, digest_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
        return hmac.compare_digest(expected, actual)
    except Exception:
        return False


def get_master_key() -> bytes:
    path = settings.MASTER_KEY_PATH
    if path.exists():
        raw = path.read_bytes()
        if len(raw) >= 32:
            return raw[:32]
    settings.ensure_dirs()
    key = os.urandom(32)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    nonce = os.urandom(12)
    ct = AESGCM(get_master_key()).encrypt(nonce, plain.encode(), None)
    return "aesgcm$" + base64.b64encode(nonce + ct).decode()


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    if not stored.startswith("aesgcm$"):
        return stored  # 旧明文兼容：读出后由调用方重新加密
    try:
        raw = base64.b64decode(stored[len("aesgcm$"):])
        return AESGCM(get_master_key()).decrypt(raw[:12], raw[12:], None).decode()
    except Exception:
        return ""


def new_session() -> str:
    token = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[token] = time.time() + SESSION_TTL
    return token


def check_session(token: str | None) -> bool:
    if not token:
        return False
    with _sessions_lock:
        exp = _sessions.get(token)
        if exp and exp > time.time():
            return True
        _sessions.pop(token, None)
    return False


def revoke_session(token: str | None) -> None:
    if token:
        with _sessions_lock:
            _sessions.pop(token, None)


def record_login_fail(key: str) -> None:
    now = time.time()
    with _fails_lock:
        lst = [t for t in _fails.get(key, []) if now - t < LOCK_SECONDS]
        lst.append(now)
        _fails[key] = lst


def login_locked(key: str) -> bool:
    now = time.time()
    with _fails_lock:
        lst = [t for t in _fails.get(key, []) if now - t < LOCK_SECONDS]
        _fails[key] = lst
        return len(lst) >= MAX_FAILS


def clear_login_fails(key: str) -> None:
    with _fails_lock:
        _fails.pop(key, None)
