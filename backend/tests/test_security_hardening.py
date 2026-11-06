"""Security hardening: brute-force protection (G6), token revocation, retention purge (B-03)."""
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import AuditLog, IdentityReviewItem, ImportBatch, ImportStagingRow, Tenant, User
from app.security import bruteforce
from app.security.auth import hash_password
from app.services.retention import NON_PERSONAL_MAPPED_KEYS, purge
from tests.conftest import PASSWORD

LOGIN = "/api/v1/auth/login"


class _Clock:
    def __init__(self) -> None:
        self.t = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


@pytest.fixture()
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(bruteforce, "now", c)
    return c


@pytest.fixture(autouse=True)
def isolated_limiter(monkeypatch):
    """Force the in-process window and leave no failures behind for other test modules."""
    monkeypatch.setattr(bruteforce.login_limiter, "_redis", None)
    monkeypatch.setattr(bruteforce.login_limiter, "_redis_checked", True)
    bruteforce.login_limiter.reset()
    yield
    bruteforce.login_limiter.reset()


def _limits(monkeypatch, **kw):
    cfg = bruteforce.BruteForceSettings(**{"lockout_threshold": 5, "lockout_minutes": 15, "ip_max_failures": 1000,
                                           "ip_window_seconds": 300, **kw})
    monkeypatch.setattr(bruteforce, "settings", lambda: cfg)
    return cfg


def _new_user(db) -> User:
    tid = db.execute(select(Tenant.id).where(Tenant.code == "EXACT")).scalar_one()
    u = User(tenant_id=tid, email=f"sec-{uuid.uuid4().hex[:10]}@exact.local", full_name="Security Test",
             password_hash=hash_password(PASSWORD), locale="en")
    db.add(u)
    db.commit()
    return u


def _login(client, email, password=PASSWORD):
    return client.post(LOGIN, json={"email": email, "password": password})


def _audits(db, action, since_id, user_id=None):
    q = select(AuditLog).where(AuditLog.action == action, AuditLog.id > since_id).order_by(AuditLog.id)
    if user_id is not None:
        q = q.where(AuditLog.user_id == user_id)
    return db.execute(q).scalars().all()


def _max_audit(db) -> int:
    return db.execute(select(func.coalesce(func.max(AuditLog.id), 0))).scalar_one()


# ------------------------------------------------------------------ account lockout


def test_lockout_after_threshold_and_unlock_after_expiry(client, db, clock, monkeypatch):
    _limits(monkeypatch, lockout_threshold=5, lockout_minutes=15)
    u = _new_user(db)
    start = _max_audit(db)
    for _ in range(5):
        r = _login(client, u.email, "wrong-password")
        assert r.status_code == 401 and r.json() == {"detail": "Invalid credentials"}
    db.expire_all()
    locked = db.get(User, u.id)
    assert locked.locked_until is not None and locked.locked_until > clock.t
    # correct password while locked: same 401 body as a wrong password (no enumeration via the lock)
    r = _login(client, u.email)
    assert r.status_code == 401 and r.json() == {"detail": "Invalid credentials"}
    assert len(_audits(db, "auth.login_failed", start, u.id)) == 5
    reasons = [a.details["reason"] for a in _audits(db, "auth.locked", start, u.id)]
    assert reasons == ["threshold_reached", "attempt_while_locked"]
    # still locked just before expiry, open after it
    clock.advance(minutes=14, seconds=59)
    assert _login(client, u.email).status_code == 401
    clock.advance(seconds=2)
    r = _login(client, u.email)
    assert r.status_code == 200, r.text
    db.expire_all()
    u2 = db.get(User, u.id)
    assert u2.failed_login_count == 0 and u2.locked_until is None


def test_successful_login_resets_failure_counter(client, db, clock, monkeypatch):
    _limits(monkeypatch, lockout_threshold=5)
    u = _new_user(db)
    for _ in range(4):
        assert _login(client, u.email, "nope").status_code == 401
    db.expire_all()
    assert db.get(User, u.id).failed_login_count == 4
    assert _login(client, u.email).status_code == 200
    db.expire_all()
    assert db.get(User, u.id).failed_login_count == 0
    for _ in range(4):
        assert _login(client, u.email, "nope").status_code == 401
    assert _login(client, u.email).status_code == 200  # 8 failures in total but never 5 consecutive


# ------------------------------------------------------------------ IP rate limit


def test_ip_rate_limit_returns_429_with_retry_after(client, db, clock, monkeypatch):
    _limits(monkeypatch, ip_max_failures=3, ip_window_seconds=60, lockout_threshold=100)
    u = _new_user(db)
    start = _max_audit(db)
    for i in range(3):
        assert _login(client, f"nobody-{i}@nowhere.invalid", "x").status_code == 401
    r = _login(client, u.email)  # even valid credentials are refused once the IP is over the limit
    assert r.status_code == 429
    assert r.json() == {"detail": "Too many login attempts. Try again later."}
    retry = int(r.headers["Retry-After"])
    assert 1 <= retry <= 60
    rl = _audits(db, "auth.rate_limited", start)
    assert len(rl) == 1 and rl[0].outcome == "denied" and rl[0].details["retry_after_seconds"] == retry
    # rate-limited attempts do not extend the window
    assert _login(client, u.email).status_code == 429
    clock.advance(seconds=61)
    assert _login(client, u.email).status_code == 200


def test_successful_logins_do_not_consume_ip_budget(client, db, clock, monkeypatch):
    _limits(monkeypatch, ip_max_failures=2, ip_window_seconds=60)
    u = _new_user(db)
    for _ in range(5):
        assert _login(client, u.email).status_code == 200


# ------------------------------------------------------------------ no user enumeration


def test_unknown_email_and_wrong_password_are_indistinguishable(client, db, clock, monkeypatch):
    _limits(monkeypatch, lockout_threshold=1000)
    u = _new_user(db)
    unknown = f"ghost-{uuid.uuid4().hex[:8]}@exact.local"
    inactive = _new_user(db)
    inactive.is_active = False
    db.commit()

    def timed(email):
        t0 = time.perf_counter()
        r = _login(client, email, "Wrong!2345")
        return r, time.perf_counter() - t0

    samples: dict[str, list[float]] = {"unknown": [], "wrong": [], "inactive": []}
    responses = {}
    for _ in range(5):
        for k, email in (("unknown", unknown), ("wrong", u.email), ("inactive", inactive.email)):
            r, dt = timed(email)
            samples[k].append(dt)
            responses[k] = r
    bodies = {k: (r.status_code, r.json(), r.headers.get("www-authenticate")) for k, r in responses.items()}
    assert bodies["unknown"] == bodies["wrong"] == bodies["inactive"] == (401, {"detail": "Invalid credentials"}, None)
    med = {k: statistics.median(v) for k, v in samples.items()}
    # every path runs exactly one scrypt derivation; allow generous jitter for shared CI machines
    assert max(med.values()) / min(med.values()) < 2.0, med


# ------------------------------------------------------------------ token revocation


def test_logout_all_revokes_old_tokens_but_new_login_works(client, db, monkeypatch):
    _limits(monkeypatch)
    u = _new_user(db)
    t1 = _login(client, u.email).json()["access_token"]
    t2 = _login(client, u.email).json()["access_token"]  # a second device
    h1, h2 = {"Authorization": f"Bearer {t1}"}, {"Authorization": f"Bearer {t2}"}
    assert client.get("/api/v1/auth/me", headers=h1).status_code == 200
    start = _max_audit(db)
    r = client.post("/api/v1/auth/logout-all", headers=h1)
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert len(_audits(db, "auth.logout_all", start, u.id)) == 1
    assert client.get("/api/v1/auth/me", headers=h1).status_code == 401
    assert client.get("/api/v1/auth/me", headers=h2).status_code == 401
    assert client.post("/api/v1/auth/logout-all", headers=h1).status_code == 401
    t3 = _login(client, u.email).json()["access_token"]
    assert jwt.decode(t3, options={"verify_signature": False})["tv"] == 1
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t3}"}).status_code == 200


def _forge(claims: dict) -> str:
    now = datetime.now(UTC)
    base = {"iat": now, "exp": now + timedelta(minutes=5), "iss": "hrswi", "jti": uuid.uuid4().hex}
    return jwt.encode({**base, **claims}, get_settings().jwt_secret, algorithm="HS256")


def test_token_without_or_with_stale_tv_rejected(client, db):
    u = _new_user(db)
    me = "/api/v1/auth/me"
    ok = _forge({"sub": str(u.id), "tid": u.tenant_id, "tv": 0})
    assert client.get(me, headers={"Authorization": f"Bearer {ok}"}).status_code == 200
    no_tv = _forge({"sub": str(u.id), "tid": u.tenant_id})
    assert client.get(me, headers={"Authorization": f"Bearer {no_tv}"}).status_code == 401
    for bad in (1, "0", None, True):
        tok = _forge({"sub": str(u.id), "tid": u.tenant_id, "tv": bad})
        assert client.get(me, headers={"Authorization": f"Bearer {tok}"}).status_code == 401, bad
    junk_sub = _forge({"sub": "abc", "tid": u.tenant_id, "tv": 0})
    assert client.get(me, headers={"Authorization": f"Bearer {junk_sub}"}).status_code == 401


def test_deactivated_user_token_rejected(client, db, monkeypatch):
    _limits(monkeypatch)
    u = _new_user(db)
    h = {"Authorization": f"Bearer {_login(client, u.email).json()['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    u.is_active = False
    db.commit()
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


# ------------------------------------------------------------------ retention (B-03)


def _batch(db, tid, status, updated_at):
    b = ImportBatch(tenant_id=tid, entity_type="employment", file_name="hr.csv", file_sha256=uuid.uuid4().hex * 2,
                    status=status, updated_at=updated_at, created_at=updated_at)
    db.add(b)
    db.flush()
    row = ImportStagingRow(
        batch_id=b.id, row_number=2,
        raw={"Ad": "Ayşe", "Soyad": "Yılmaz", "TCKN": "hmac:abc", "Sicil": "E-77"},
        mapped={"external_id": "X-1", "first_name": "Ayşe", "last_name": "Yılmaz", "national_id_hash": "abc",
                "employee_number": "E-77", "birth_year": 1990, "gender": "F", "legal_entity_code": "A",
                "start_date": "2024-01-01", "end_date": None, "contract_type": "permanent",
                "termination_category": None, "project_code": "P1"},
        errors=[{"rule": "future_start", "severity": "warning", "message": "start_date is in the future"}],
        committed=status == "committed",
    )
    db.add(row)
    db.flush()
    return b, row


def test_retention_dry_run_then_apply(db):
    tid = db.execute(select(Tenant.id).where(Tenant.code == "EXACT")).scalar_one()
    now = datetime.now(UTC)
    old = now - timedelta(days=120)
    old_b, old_row = _batch(db, tid, "committed", old)
    failed_b, failed_row = _batch(db, tid, "failed", old)
    _new_b, new_row = _batch(db, tid, "committed", now - timedelta(days=10))
    _open_b, open_row = _batch(db, tid, "validated", old)  # not finished: never purged
    rv_old = IdentityReviewItem(tenant_id=tid, reason="nid mismatch", payload={"first_name": "Ayşe"},
                                status="merged", updated_at=old, created_at=old)
    rv_open = IdentityReviewItem(tenant_id=tid, reason="nid mismatch", payload={"first_name": "Ali"},
                                 status="open", updated_at=old, created_at=old)
    db.add_all([rv_old, rv_open])
    db.commit()
    start = _max_audit(db)

    dry = purge(db, now=now, staging_days=90, review_days=90, dry_run=True)
    assert dry["dry_run"] is True
    mine = dry["per_tenant"][str(tid)]
    assert mine["staging_rows"] >= 2 and mine["failed_batches_expired"] >= 1 and mine["review_items"] >= 1
    db.expire_all()
    assert db.get(ImportStagingRow, old_row.id).raw["Ad"] == "Ayşe"
    assert db.get(IdentityReviewItem, rv_old.id).payload == {"first_name": "Ayşe"}
    assert _audits(db, "retention.purge", start) == []

    res = purge(db, now=now, staging_days=90, review_days=90, dry_run=False)
    assert res["dry_run"] is False and res["staging_rows"] == dry["staging_rows"]
    db.expire_all()
    for rid in (old_row.id, failed_row.id):
        r = db.get(ImportStagingRow, rid)
        assert r.raw == {}
        assert set(r.mapped) <= set(NON_PERSONAL_MAPPED_KEYS)
        assert r.mapped["legal_entity_code"] == "A" and r.mapped["start_date"] == "2024-01-01"
        assert "first_name" not in r.mapped and "national_id_hash" not in r.mapped and "birth_year" not in r.mapped
        assert r.errors and r.errors[0]["rule"] == "future_start"
    assert db.get(ImportBatch, failed_b.id).status == "expired"
    assert db.get(ImportBatch, old_b.id).status == "committed"
    assert db.get(ImportStagingRow, new_row.id).raw["Ad"] == "Ayşe"  # inside retention window
    assert db.get(ImportStagingRow, open_row.id).mapped["first_name"] == "Ayşe"  # batch not finished
    assert db.get(IdentityReviewItem, rv_old.id).payload == {}
    assert db.get(IdentityReviewItem, rv_open.id).payload == {"first_name": "Ali"}
    audits = _audits(db, "retention.purge", start)
    tenant_row = [a for a in audits if a.tenant_id == tid]
    summary = [a for a in audits if a.tenant_id is None]
    assert len(tenant_row) == 1 and tenant_row[0].details["staging_rows"] == mine["staging_rows"]
    assert len(summary) == 1 and summary[0].details["staging_rows"] == res["staging_rows"]

    # idempotent: a second run finds nothing more but still leaves an audit trail
    again = purge(db, now=now, dry_run=False)
    assert again["staging_rows"] == 0 and again["review_items"] == 0 and again["failed_batches_expired"] == 0
    assert len(_audits(db, "retention.purge", start)) == 3


def test_retention_rejects_nonsense_periods(db):
    with pytest.raises(ValueError):
        purge(db, now=datetime.now(UTC), staging_days=0)


def test_retention_cli_dry_run(capsys):
    from app.services.retention import main

    assert main(["--staging-days", "90"]) == 0
    assert '"dry_run": true' in capsys.readouterr().out
