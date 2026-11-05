"""Password login with brute-force protection and session revocation (SECURITY_PRIVACY G6, token revocation).

Response contract (no user enumeration):
  - unknown e-mail, wrong password, inactive account, locked account -> 401 {"detail": "Invalid credentials"}
    Every one of these paths performs exactly one scrypt derivation, so response times are comparable.
  - too many failures from one client IP -> 429 {"detail": "Too many login attempts. Try again later."}
    with a Retry-After header (seconds). This depends only on the IP, never on the account.
Audit actions: auth.login, auth.login_failed, auth.locked, auth.rate_limited, auth.logout_all.
"""
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import User
from app.security import bruteforce
from app.security.auth import verify_password
from app.security.deps import write_audit

INVALID_CREDENTIALS = "Invalid credentials"
RATE_LIMITED = "Too many login attempts. Try again later."


def _invalid() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)


def authenticate(db: Session, email: str, password: str, ip: str | None) -> User:
    """Return the authenticated user or raise the uniform 401 / IP-based 429."""
    cfg = bruteforce.settings()
    retry = bruteforce.login_limiter.retry_after(ip)
    if retry is not None:
        write_audit(tenant_id=None, user_id=None, action="auth.rate_limited", outcome="denied", ip=ip,
                    details={"retry_after_seconds": retry})
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, RATE_LIMITED, headers={"Retry-After": str(retry)})

    user = db.execute(select(User).where(func.lower(User.email) == email.strip().lower())).scalar()
    at = bruteforce.now()
    # always exactly one scrypt run (verify_password burns the same time when there is no stored hash)
    password_ok = verify_password(password, user.password_hash if user is not None else None)

    if user is None or not user.is_active:
        bruteforce.login_limiter.record_failure(ip)
        write_audit(tenant_id=user.tenant_id if user else None, user_id=user.id if user else None,
                    action="auth.login_failed", outcome="denied", ip=ip,
                    details={"reason": "inactive"} if user is not None else None)
        raise _invalid()

    if cfg.enabled and bruteforce.is_locked(user.locked_until, at):
        bruteforce.login_limiter.record_failure(ip)
        write_audit(tenant_id=user.tenant_id, user_id=user.id, action="auth.locked", outcome="denied", ip=ip,
                    details={"reason": "attempt_while_locked", "locked_until": user.locked_until.isoformat()})
        raise _invalid()

    if not password_ok:
        bruteforce.login_limiter.record_failure(ip)
        # atomic increment: concurrent failures cannot be lost
        count = db.execute(update(User).where(User.id == user.id)
                           .values(failed_login_count=User.failed_login_count + 1)
                           .returning(User.failed_login_count)).scalar_one()
        locked_until = None
        if cfg.enabled and count >= cfg.lockout_threshold:
            locked_until = at + timedelta(minutes=cfg.lockout_minutes)
            db.execute(update(User).where(User.id == user.id)
                       .values(locked_until=locked_until, failed_login_count=0))
        db.commit()
        write_audit(tenant_id=user.tenant_id, user_id=user.id, action="auth.login_failed", outcome="denied", ip=ip,
                    details={"consecutive_failures": count})
        if locked_until is not None:
            write_audit(tenant_id=user.tenant_id, user_id=user.id, action="auth.locked", outcome="denied", ip=ip,
                        details={"reason": "threshold_reached", "failures": count,
                                 "locked_until": locked_until.isoformat()})
        raise _invalid()

    db.execute(update(User).where(User.id == user.id)
               .values(failed_login_count=0, locked_until=None, last_login_at=at))
    db.commit()
    db.refresh(user)
    write_audit(tenant_id=user.tenant_id, user_id=user.id, action="auth.login", ip=ip)
    return user


def revoke_all_tokens(db: Session, user_id: int) -> int:
    """Invalidate every token issued so far for the user; returns the new token version."""
    tv = db.execute(update(User).where(User.id == user_id)
                    .values(token_version=User.token_version + 1)
                    .returning(User.token_version)).scalar_one()
    db.commit()
    return tv
