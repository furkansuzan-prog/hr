"""Authentication primitives. IdP-agnostic: an OIDC adapter only needs to produce a user id (ADR-006)."""
import base64
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        # constant-ish time even when the user has no local password
        hashlib.scrypt(password.encode(), salt=b"0" * 16, **_SCRYPT)
        return False
    _, salt_b64, dk_b64 = stored.split("$")
    dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
    return hmac.compare_digest(dk, base64.b64decode(dk_b64))


def create_access_token(user_id: int, tenant_id: int) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tid": tenant_id,
        "iat": now,
        "exp": now + timedelta(minutes=s.jwt_ttl_minutes),
        "jti": uuid.uuid4().hex,
        "iss": "hrswi",
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"], issuer="hrswi",
                      options={"require": ["exp", "iat", "sub", "tid", "iss"]})


def national_id_hash(raw: str) -> str:
    """Keyed pseudonymisation of a national identity number - the raw value is never persisted."""
    norm = "".join(ch for ch in raw if ch.isalnum()).upper()
    return hmac.new(get_settings().identity_hmac_key.encode(), norm.encode(), hashlib.sha256).hexdigest()


def random_password() -> str:
    return secrets.token_urlsafe(12)
