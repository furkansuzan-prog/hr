"""FastAPI dependencies for authentication, authorization and audit."""
from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, get_db
from app.core.logging import request_id_var
from app.models import AuditLog, User
from app.security.access import AccessContext, build_access_context
from app.security.auth import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def write_audit(
    *,
    tenant_id: int | None,
    user_id: int | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    outcome: str = "allowed",
    details: dict | None = None,
    ip: str | None = None,
) -> None:
    """Audit entries are written in their own transaction so that denials survive request rollback."""
    with SessionLocal() as s:
        s.add(
            AuditLog(
                tenant_id=tenant_id,
                user_id=user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                outcome=outcome,
                details=details,
                ip=ip,
                request_id=request_id_var.get(),
            )
        )
        s.commit()


def get_context(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> AccessContext:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = decode_access_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from None
    try:
        user = db.get(User, int(claims["sub"]))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from None
    if user is None or not user.is_active or user.tenant_id != claims.get("tid"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid user")
    # revocation: logout-all (and any future credential reset) increments users.token_version
    tv = claims.get("tv")
    if not isinstance(tv, int) or isinstance(tv, bool) or tv != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    ctx = build_access_context(db, user)
    # UI language may differ from the stored profile locale; only the display language is affected
    hdr = (request.headers.get("X-Locale") or "").lower()[:2]
    if hdr in ("tr", "en", "ru"):
        ctx.locale = hdr
    request.state.ctx = ctx
    return ctx


def require(permission: str) -> Callable[..., AccessContext]:
    def dep(request: Request, ctx: AccessContext = Depends(get_context)) -> AccessContext:
        if not ctx.has(permission):
            write_audit(
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                action="authz.denied",
                entity_type="permission",
                entity_id=permission,
                outcome="denied",
                details={"path": request.url.path},
                ip=request.client.host if request.client else None,
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")
        return ctx

    return dep


def deny_entity(ctx: AccessContext, request: Request, perm: str, entity_ids: set[int]) -> None:
    write_audit(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="authz.scope_denied",
        entity_type="legal_entity",
        entity_id=",".join(map(str, sorted(entity_ids))),
        outcome="denied",
        details={"path": request.url.path, "permission": perm},
    )
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Requested legal entity is outside your access scope")
