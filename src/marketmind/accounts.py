"""Local dashboard identities, password hashes and signed sessions.

Credentials and the signing key live in the persistent, untracked data directory.
The original paper.sqlite is always the owner's ledger.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time

from .config import DATA_DIR
from .paper import state

USERS_FILE = DATA_DIR / "secrets" / "dashboard_users.json"
KEY_FILE = DATA_DIR / "secrets" / "dashboard_session.key"
ITERATIONS = 310_000
SESSION_SECONDS = 12 * 60 * 60


def _read_users() -> list[dict]:
    if not USERS_FILE.exists():
        return []
    return json.loads(USERS_FILE.read_text(encoding="utf-8"))["users"]


def _save_users(users: list[dict]) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(dir=USERS_FILE.parent, prefix=".users-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"users": users}, out, ensure_ascii=False, indent=2)
        os.chmod(path, 0o600)
        os.replace(path, USERS_FILE)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def set_user(username: str, password: str, *, owner: bool = False,
             initial_cash: float = 200.0, auto_trade: bool = False) -> dict:
    """Create/update an identity without resetting any existing ledger."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", username) or not password:
        raise ValueError("invalid username or empty password")
    users = _read_users()
    user = next((u for u in users if u["username"] == username), None)
    if user is None:
        if owner and any(u["account_id"] == "owner" for u in users):
            raise ValueError("owner already exists")
        user = {"username": username, "account_id": "owner" if owner else secrets.token_hex(16)}
        users.append(user)
        with state.use_account(user["account_id"]):
            state.init_account(cash=initial_cash)
            if auto_trade:
                conn = state.connect()
                state.set_setting(conn, "auto_trade", "1")
                conn.close()
    elif owner != (user["account_id"] == "owner"):
        raise ValueError("cannot change account ownership")
    salt = secrets.token_hex(16)
    user.update({"salt": salt,
                 "password_hash": hashlib.pbkdf2_hmac("sha256", password.encode(),
                                                       bytes.fromhex(salt), ITERATIONS).hex(),
                 "version": secrets.token_hex(16)})
    _save_users(users)
    return {"username": user["username"], "account_id": user["account_id"]}


def users() -> list[dict]:
    return [{"username": u["username"], "account_id": u["account_id"]} for u in _read_users()]


def authenticate(username: str, password: str) -> dict | None:
    user = next((u for u in _read_users() if u["username"] == username), None)
    # Always perform the expensive hash to reduce username enumeration timing.
    salt = user["salt"] if user else "0" * 32
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    if user and hmac.compare_digest(digest, user["password_hash"]):
        return user
    return None


def migrate_legacy_owner() -> bool:
    """Import the old single-user credentials once, without changing the ledger."""
    if USERS_FILE.exists():
        return False
    legacy = DATA_DIR / "secrets" / "dashboard_login.json"
    if legacy.exists():
        config = json.loads(legacy.read_text(encoding="utf-8"))
        username, password = config.get("username"), config.get("password")
    else:
        username, password = os.environ.get("DASHBOARD_USER"), os.environ.get("DASHBOARD_PASSWORD")
    if username and password:
        set_user(str(username), str(password), owner=True)
        return True
    return False


def _key() -> bytes:
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(KEY_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as output:
            output.write(secrets.token_bytes(32))
    return KEY_FILE.read_bytes()


def make_session(user: dict) -> str:
    payload = json.dumps({"u": user["username"], "v": user["version"],
                          "exp": int(time.time()) + SESSION_SECONDS}, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    signature = hmac.new(_key(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def session_user(token: str | None) -> dict | None:
    if not token or len(token) > 2048:
        return None
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(_key(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if data["exp"] <= time.time():
            return None
        user = next((u for u in _read_users() if u["username"] == data["u"]), None)
        if user and hmac.compare_digest(user["version"], data["v"]):
            return {"username": user["username"], "account_id": user["account_id"]}
    except (ValueError, KeyError, TypeError, UnicodeError):
        pass
    return None


def main() -> None:
    """Manage local dashboard accounts without putting passwords in shell history."""
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description="Manage paper dashboard accounts")
    parser.add_argument("action", choices=("set-owner", "set-user"))
    parser.add_argument("username")
    parser.add_argument("--auto-trade", action="store_true",
                        help="enable automated paper cycles for a new account")
    parser.add_argument("--initial-cash", type=float, default=200.0)
    args = parser.parse_args()
    if args.initial_cash <= 0:
        parser.error("initial cash must be positive")
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        parser.error("passwords do not match")
    result = set_user(args.username, password, owner=args.action == "set-owner",
                      initial_cash=args.initial_cash, auto_trade=args.auto_trade)
    print(f"Account ready: {result['username']}")


if __name__ == "__main__":
    main()
