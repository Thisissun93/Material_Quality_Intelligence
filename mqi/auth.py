"""Users, roles and API tokens.

Roles follow the people who touch a release:
  qc        enters retests, opens OOS investigations, closes phase 1 (laboratory)
  qa        everything qc does, closes phase 2 (manufacturing), reviews a lot for release
  approver  approves or rejects a lot that QA reviewed (cannot approve a lot they reviewed themselves)
  viewer    reads only
  service   machine account of the equipment collector: posts equipment runs, reads lots
  admin     manages users and the demo; cannot sign releases

Passwords and tokens are stored only as salted PBKDF2 / SHA-256 hashes. Sign-ins, failed sign-ins and user
changes go to the audit trail.

    python -m mqi.auth add-user qa.choi QA "Choi QA"       # asks for the password (or MQI_NEW_PASSWORD)
    python -m mqi.auth list
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
from datetime import datetime, timedelta

from . import audit, db

ROLES = ("viewer", "qc", "qa", "approver", "service", "admin")
ROLE_KO = {"viewer": "조회", "qc": "QC", "qa": "QA", "approver": "출하 승인자", "service": "설비 수집기",
           "admin": "관리자"}

PERMS = {
    "read": set(ROLES),
    "test.write": {"qc", "qa"},          # retest, open OOS, phase 1
    "oos.phase2": {"qa"},
    "release.review": {"qa"},
    "release.approve": {"approver"},
    "equipment.write": {"service", "admin"},
    "audit.read": {"qa", "approver", "admin"},
    "user.admin": {"admin"},
    "demo.rebuild": {"admin"},
}

TOKEN_HOURS = 12
_ITER = 200_000

# demo plant only (created by mqi.demo.build); a plant store gets its users from `python -m mqi.auth add-user`
DEMO_PASSWORD = "demo1234"
DEMO_USERS = [
    ("qc.kim", "김QC", "qc"),
    ("qa.lee", "이QA", "qa"),
    ("qa.choi", "최QA", "qa"),
    ("approver.park", "박승인", "approver"),
    ("viewer", "조회 전용", "viewer"),
    ("collector", "설비 수집기", "service"),
    ("admin", "관리자", "admin"),
]


def _now() -> datetime:
    return datetime.now()


def _pw_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITER).hex()


def can(user: dict | None, perm: str) -> bool:
    return bool(user) and user.get("role") in PERMS.get(perm, set())


def add_user(username: str, name: str, role: str, password: str, by: str = "system", dbpath=None) -> None:
    username = username.strip()
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if not username or len(password) < 8:
        raise ValueError("사용자 ID와 8자 이상 비밀번호가 필요합니다 / username and a password of 8+ characters are required")
    salt = secrets.token_hex(16)
    with db.connect(dbpath) as con:
        if con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError(f"이미 있는 사용자입니다 / user {username} exists")
        con.execute("INSERT INTO users (username, name, role, salt, pw_hash, active, created) VALUES (?,?,?,?,?,?,?)",
                    (username, name or username, role, salt, _pw_hash(password, salt), 1, _now().isoformat("T", "seconds")))
        audit.record(con, by, "user.add", f"user/{username}", {"role": role, "name": name})


def get_user(username: str, dbpath=None) -> dict | None:
    r = db.read("SELECT username, name, role, active FROM users WHERE username=?", (username,), dbpath)
    return r.iloc[0].to_dict() if len(r) else None


def users(dbpath=None):
    return db.read("SELECT username, name, role, active, created FROM users ORDER BY username", (), dbpath)


def check_password(username: str, password: str, purpose: str = "login", dbpath=None) -> dict | None:
    """The user if the password is right and the account active, else None. Both outcomes are audited."""
    r = db.read("SELECT username, name, role, salt, pw_hash, active FROM users WHERE username=?",
                (username or "",), dbpath)
    ok = len(r) and int(r.iloc[0]["active"]) == 1 and hmac.compare_digest(
        _pw_hash(password or "", r.iloc[0]["salt"]), r.iloc[0]["pw_hash"])
    user = {k: r.iloc[0][k] for k in ("username", "name", "role")} if ok else None
    audit.log(username or "", f"{purpose}.{'ok' if ok else 'fail'}", f"user/{username}",
              role=user["role"] if user else None, dbpath=dbpath)
    return user


def issue_token(user: dict, hours: int = TOKEN_HOURS, dbpath=None) -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    exp = (_now() + timedelta(hours=hours)).isoformat("T", "seconds")
    with db.connect(dbpath) as con:
        con.execute("DELETE FROM tokens WHERE expires < ?", (_now().isoformat("T", "seconds"),))
        con.execute("INSERT INTO tokens (token_hash, username, issued, expires) VALUES (?,?,?,?)",
                    (hashlib.sha256(raw.encode()).hexdigest(), user["username"], _now().isoformat("T", "seconds"), exp))
    return raw, exp


def user_for_token(raw: str, dbpath=None) -> dict | None:
    if not raw:
        return None
    r = db.read("SELECT u.username, u.name, u.role, u.active, t.expires FROM tokens t JOIN users u "
                "ON t.username=u.username WHERE t.token_hash=?", (hashlib.sha256(raw.encode()).hexdigest(),), dbpath)
    if r.empty or int(r.iloc[0]["active"]) != 1 or r.iloc[0]["expires"] < _now().isoformat("T", "seconds"):
        return None
    return {k: r.iloc[0][k] for k in ("username", "name", "role")}


def revoke_token(raw: str, dbpath=None) -> None:
    with db.connect(dbpath) as con:
        con.execute("DELETE FROM tokens WHERE token_hash=?", (hashlib.sha256(raw.encode()).hexdigest(),))


def seed_demo_users(dbpath=None) -> None:
    for u, n, r in DEMO_USERS:
        if not get_user(u, dbpath):
            add_user(u, n, r, os.environ.get("MQI_DEMO_PASSWORD", DEMO_PASSWORD), by="demo", dbpath=dbpath)


def main(argv: list[str]) -> int:
    if argv[:1] == ["list"]:
        print(users().to_string(index=False))
        return 0
    if argv[:1] == ["add-user"] and len(argv) >= 3:
        import getpass

        pw = os.environ.get("MQI_NEW_PASSWORD") or getpass.getpass("password: ")
        add_user(argv[1], argv[3] if len(argv) > 3 else argv[1], argv[2].lower(), pw, by="cli")
        print(f"added {argv[1]} ({argv[2].lower()})")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
