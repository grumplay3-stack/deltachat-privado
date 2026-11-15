import json
import os
import re
import secrets
import shutil
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

AUTH_DB_PATH = os.environ.get("DELTACHAT_AUTH_DB", "auth_users.db")
LEGACY_CHAT_DB_PATH = os.environ.get("DELTACHAT_DB", "deltachat.db")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "dabo777")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "52952416")

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60  # 15 minutes


def normalize_text(text: str) -> str:
    """Normalize answers/questions: lowercase, strip accents, punctuation, extra whitespace."""
    nfkd = unicodedata.normalize("NFD", (text or "").strip().lower())
    no_accents = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    cleaned = re.sub(r"[¿?¡!.,;:]+", "", no_accents)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_user_agent(ua: str) -> str:
    """Convert raw User-Agent into a clean browser + OS summary."""
    raw = ua or ""
    low = raw.lower()

    if "torbrowser" in low or ("firefox" in low and "rv:" in low and "windows nt 10.0" in low and "x64" in low and "tor" in low):
        browser = "Tor Browser"
    elif "edg/" in low or "edge/" in low:
        browser = "Edge"
    elif "brave" in low:
        browser = "Brave"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    elif "chrome/" in low or "crios/" in low:
        browser = "Chrome"
    elif "firefox/" in low or "fxios/" in low:
        browser = "Firefox"
    elif "safari/" in low:
        browser = "Safari"
    else:
        browser = "Navegador Web"

    if "android" in low:
        os_name = "Android"
    elif "iphone" in low or "ipad" in low or "ipod" in low:
        os_name = "iOS"
    elif "windows nt 10" in low or "windows nt 11" in low:
        os_name = "Windows"
    elif "windows" in low:
        os_name = "Windows"
    elif "mac os x" in low or "macintosh" in low:
        os_name = "macOS"
    elif "linux" in low:
        os_name = "Linux"
    else:
        os_name = "Desconocido"

    return f"{browser} en {os_name}"


@contextmanager
def get_auth_conn():
    """Provide a SQLite connection to auth_users.db."""
    conn = sqlite3.connect(AUTH_DB_PATH, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_auth_db() -> None:
    """Initialize auth_users.db tables, migrate any legacy users, and ensure dabo777 exists."""
    with get_auth_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                password TEXT NOT NULL,
                alias TEXT NOT NULL,
                avatar_color TEXT NOT NULL DEFAULT '#b5bd68',
                role TEXT NOT NULL DEFAULT 'member',
                security_question TEXT NOT NULL,
                security_answer TEXT NOT NULL,
                admin_notes TEXT NOT NULL DEFAULT '',
                must_change_password INTEGER NOT NULL DEFAULT 0,
                last_ip TEXT,
                last_device TEXT,
                last_login_at REAL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS login_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username_attempted TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                user_agent TEXT NOT NULL,
                device_summary TEXT NOT NULL,
                success INTEGER NOT NULL,
                failure_reason TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_change_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                field_changed TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                changed_by_user_id INTEGER,
                changed_by_role TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_known_ips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                ip_address TEXT NOT NULL,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                UNIQUE(user_id, ip_address)
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                ip_address TEXT,
                user_agent TEXT,
                is_impersonated INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                username TEXT,
                ip_address TEXT,
                ban_type TEXT NOT NULL CHECK(ban_type IN ('permaban', 'temp')),
                reason TEXT NOT NULL DEFAULT '',
                expires_at REAL,
                banned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS failures_tracker (
                scope TEXT NOT NULL,
                identifier TEXT NOT NULL,
                failures INTEGER NOT NULL DEFAULT 0,
                last_failure REAL NOT NULL,
                blocked_until REAL,
                PRIMARY KEY (scope, identifier)
            );

            CREATE TABLE IF NOT EXISTS admin_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                username TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        # Migrate legacy users from deltachat.db if auth_users.db is empty and legacy db exists
        count_row = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
        if count_row["cnt"] == 0 and Path(LEGACY_CHAT_DB_PATH).is_file():
            _migrate_legacy_users(conn)

        # Ensure preconfigured Admin account (dabo777) exists
        existing_admin = conn.execute(
            "SELECT id FROM users WHERE username = ?", (ADMIN_USERNAME,)
        ).fetchone()
        if not existing_admin:
            conn.execute(
                """
                INSERT INTO users (
                    username, password, alias, avatar_color, role,
                    security_question, security_answer, admin_notes,
                    must_change_password, created_at
                ) VALUES (?, ?, ?, '#cc6666', 'admin', ?, ?, 'Administrador Principal', 0, ?)
                """,
                (
                    ADMIN_USERNAME,
                    ADMIN_PASSWORD,
                    "AnonAdmin",
                    "Cuenta administrativa protegida",
                    ADMIN_PASSWORD,
                    time.time(),
                ),
            )


def _migrate_legacy_users(auth_conn: sqlite3.Connection) -> None:
    """One-time migration of existing users/bans/sessions from legacy deltachat.db."""
    try:
        lconn = sqlite3.connect(LEGACY_CHAT_DB_PATH, timeout=5.0)
        lconn.row_factory = sqlite3.Row
        has_users = lconn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if not has_users:
            lconn.close()
            return
        for u in lconn.execute("SELECT * FROM users ORDER BY id ASC").fetchall():
            pw = ADMIN_PASSWORD if u["username"].lower() == ADMIN_USERNAME.lower() else "(clave_migrada)"
            auth_conn.execute(
                """
                INSERT OR IGNORE INTO users (
                    id, username, password, alias, avatar_color, role,
                    security_question, security_answer, last_ip, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    u["id"],
                    u["username"],
                    pw,
                    u["alias"],
                    u["avatar_color"],
                    u["role"],
                    u["security_question"],
                    "",
                    u["last_ip"],
                    u["created_at"],
                ),
            )
            if u["last_ip"]:
                auth_conn.execute(
                    "INSERT OR IGNORE INTO user_known_ips (user_id, ip_address, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                    (u["id"], u["last_ip"], u["created_at"], u["created_at"]),
                )
        has_bans = lconn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bans'"
        ).fetchone()
        if has_bans:
            for b in lconn.execute("SELECT * FROM bans").fetchall():
                auth_conn.execute(
                    """
                    INSERT INTO bans (user_id, username, ip_address, ban_type, reason, expires_at, banned_by, active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        b["user_id"],
                        b["username"],
                        b["ip_address"],
                        b["ban_type"],
                        b["reason"],
                        b["expires_at"],
                        b["banned_by"],
                        b["active"],
                        b["created_at"],
                    ),
                )
        lconn.close()
    except Exception:
        pass


def get_auth_setting(key: str) -> Optional[str]:
    with get_auth_conn() as conn:
        row = conn.execute("SELECT value FROM auth_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_auth_setting(key: str, value: str) -> None:
    with get_auth_conn() as conn:
        conn.execute(
            "INSERT INTO auth_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _record_known_ip(conn: sqlite3.Connection, user_id: int, ip_address: Optional[str]) -> None:
    if not ip_address or ip_address == "unknown":
        return
    now = time.time()
    conn.execute(
        """
        INSERT INTO user_known_ips (user_id, ip_address, first_seen, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, ip_address) DO UPDATE SET last_seen = excluded.last_seen
        """,
        (user_id, ip_address, now, now),
    )


def _log_change(
    conn: sqlite3.Connection,
    user_id: int,
    field: str,
    old_val: Optional[str],
    new_val: Optional[str],
    actor_id: Optional[int],
    actor_role: str,
) -> None:
    if old_val == new_val:
        return
    conn.execute(
        """
        INSERT INTO user_change_history (
            user_id, field_changed, old_value, new_value,
            changed_by_user_id, changed_by_role, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, field, old_val, new_val, actor_id, actor_role, time.time()),
    )


def add_audit_log(event_type: str, username: str, details: str = "") -> None:
    with get_auth_conn() as conn:
        conn.execute(
            "INSERT INTO admin_audit_logs (event_type, username, details, created_at) VALUES (?, ?, ?, ?)",
            (event_type, username, details, time.time()),
        )


def get_audit_logs(limit: int = 50) -> list[dict[str, Any]]:
    with get_auth_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM admin_audit_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# --- RATE LIMITING (5 ATTEMPTS / 15 MIN) ---

def check_rate_limit(scope: str, identifier: str) -> Optional[int]:
    """Return remaining minutes if blocked, else None."""
    now = time.time()
    with get_auth_conn() as conn:
        row = conn.execute(
            "SELECT blocked_until FROM failures_tracker WHERE scope = ? AND identifier = ?",
            (scope, identifier.lower()),
        ).fetchone()
        if not row or not row["blocked_until"]:
            return None
        if row["blocked_until"] > now:
            rem_mins = max(1, int((row["blocked_until"] - now + 59) // 60))
            return rem_mins
        # Lock expired; clear it
        conn.execute(
            "DELETE FROM failures_tracker WHERE scope = ? AND identifier = ?",
            (scope, identifier.lower()),
        )
        return None


def record_attempt(
    scope: str,
    identifier: str,
    success: bool,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[int]:
    """Record success (clears failures) or failure (locks after 5 attempts). Returns lock minutes if locked."""
    key = identifier.lower()
    now = time.time()

    def _apply(c: sqlite3.Connection) -> Optional[int]:
        if success:
            c.execute(
                "DELETE FROM failures_tracker WHERE scope = ? AND identifier = ?",
                (scope, key),
            )
            return None
        row = c.execute(
            "SELECT failures, last_failure FROM failures_tracker WHERE scope = ? AND identifier = ?",
            (scope, key),
        ).fetchone()
        if row and (now - row["last_failure"]) <= LOCKOUT_SECONDS:
            new_failures = row["failures"] + 1
        else:
            new_failures = 1

        blocked_until = now + LOCKOUT_SECONDS if new_failures >= MAX_FAILED_ATTEMPTS else None
        c.execute(
            """
            INSERT INTO failures_tracker (scope, identifier, failures, last_failure, blocked_until)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope, identifier) DO UPDATE SET
                failures = excluded.failures,
                last_failure = excluded.last_failure,
                blocked_until = excluded.blocked_until
            """,
            (scope, key, new_failures, now, blocked_until),
        )
        if blocked_until:
            return LOCKOUT_SECONDS // 60
        return None

    if conn is not None:
        return _apply(conn)
    with get_auth_conn() as own_conn:
        return _apply(own_conn)


# --- AUTHENTICATION & USER CRUD ---

def create_user(
    username: str,
    alias: str,
    password: str,
    security_question: str,
    security_answer: str,
    avatar_color: str,
    ip_address: Optional[str],
    user_agent: str = "",
) -> dict[str, Any]:
    clean_user = username.strip()
    clean_alias = alias.strip() or clean_user
    now = time.time()
    device = parse_user_agent(user_agent)
    with get_auth_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (
                username, password, alias, avatar_color, role,
                security_question, security_answer, admin_notes,
                must_change_password, last_ip, last_device, last_login_at, created_at
            ) VALUES (?, ?, ?, ?, 'member', ?, ?, '', 0, ?, ?, ?, ?)
            """,
            (
                clean_user,
                password,
                clean_alias,
                avatar_color or "#b5bd68",
                security_question.strip(),
                security_answer.strip(),
                ip_address,
                device,
                now,
                now,
            ),
        )
        uid = cur.lastrowid
        _record_known_ip(conn, uid, ip_address)
        conn.execute(
            """
            INSERT INTO login_logs (user_id, username_attempted, ip_address, user_agent, device_summary, success, failure_reason, created_at)
            VALUES (?, ?, ?, ?, ?, 1, 'Registro inicial', ?)
            """,
            (uid, clean_user, ip_address or "unknown", user_agent, device, now),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return dict(row)


def authenticate_user(
    username: str,
    password: str,
    ip_address: Optional[str],
    user_agent: str = "",
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Verify credentials, record login_logs, enforce 5-attempt lockout."""
    clean_user = username.strip()
    ip = ip_address or "unknown"
    device = parse_user_agent(user_agent)
    now = time.time()
    rate_key = f"{clean_user}|{ip}"

    locked_mins = check_rate_limit("login", rate_key)
    if locked_mins:
        with get_auth_conn() as conn:
            conn.execute(
                """
                INSERT INTO login_logs (user_id, username_attempted, ip_address, user_agent, device_summary, success, failure_reason, created_at)
                VALUES (NULL, ?, ?, ?, ?, 0, ?, ?)
                """,
                (clean_user, ip, user_agent, device, f"Bloqueado temporalmente ({locked_mins}m)", now),
            )
        return None, f"Demasiados intentos fallidos. Intenta de nuevo en {locked_mins} minutos."

    with get_auth_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (clean_user,)).fetchone()
        if not row or row["password"] != password:
            uid = row["id"] if row else None
            conn.execute(
                """
                INSERT INTO login_logs (user_id, username_attempted, ip_address, user_agent, device_summary, success, failure_reason, created_at)
                VALUES (?, ?, ?, ?, ?, 0, 'Usuario o clave incorrecta', ?)
                """,
                (uid, clean_user, ip, user_agent, device, now),
            )
            just_locked = record_attempt("login", rate_key, success=False, conn=conn)
            if just_locked:
                return None, f"Demasiados intentos fallidos. Cuenta bloqueada por {just_locked} minutos."
            return None, "Usuario o contraseña incorrectos."

        # Check active ban before logging success
        record_attempt("login", rate_key, success=True, conn=conn)
        conn.execute(
            "UPDATE users SET last_ip = ?, last_device = ?, last_login_at = ? WHERE id = ?",
            (ip, device, now, row["id"]),
        )
        _record_known_ip(conn, row["id"], ip)
        conn.execute(
            """
            INSERT INTO login_logs (user_id, username_attempted, ip_address, user_agent, device_summary, success, failure_reason, created_at)
            VALUES (?, ?, ?, ?, ?, 1, '', ?)
            """,
            (row["id"], clean_user, ip, user_agent, device, now),
        )
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        return dict(updated), None


def get_security_question(username: str) -> Optional[str]:
    with get_auth_conn() as conn:
        row = conn.execute(
            "SELECT security_question, role FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()
        if not row or row["role"] == "admin":
            return None
        return row["security_question"]


def recover_password_step2(
    username: str,
    security_answer: str,
    new_password: str,
    ip_address: Optional[str],
    user_agent: str = "",
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Verify normalized security answer, update password, audit, and return user for auto-login."""
    clean_user = username.strip()
    ip = ip_address or "unknown"
    rate_key = f"{clean_user}|{ip}"

    locked_mins = check_rate_limit("recovery", rate_key)
    if locked_mins:
        return None, f"Demasiados intentos fallidos de recuperación. Espera {locked_mins} minutos."

    with get_auth_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (clean_user,)
        ).fetchone()
        if not row or row["role"] == "admin":
            just_locked = record_attempt("recovery", rate_key, success=False, conn=conn)
            if just_locked:
                return None, f"Demasiados intentos fallidos. Espera {just_locked} minutos."
            return None, "Datos de recuperación incorrectos."

        if normalize_text(security_answer) != normalize_text(row["security_answer"]):
            just_locked = record_attempt("recovery", rate_key, success=False, conn=conn)
            if just_locked:
                return None, f"Demasiados intentos fallidos. Espera {just_locked} minutos."
            return None, "Datos de recuperación incorrectos."

        record_attempt("recovery", rate_key, success=True, conn=conn)
        old_pw = row["password"]
        now = time.time()
        device = parse_user_agent(user_agent)

        conn.execute(
            "UPDATE users SET password = ?, must_change_password = 0, last_ip = ?, last_device = ?, last_login_at = ? WHERE id = ?",
            (new_password, ip, device, now, row["id"]),
        )
        _log_change(conn, row["id"], "password", old_pw, new_password, row["id"], "recovery")
        _record_known_ip(conn, row["id"], ip)
        conn.execute(
            "INSERT INTO admin_audit_logs (event_type, username, details, created_at) VALUES ('account_recovery', ?, ?, ?)",
            (row["username"], f"Recuperó su cuenta desde {ip} ({device})", now),
        )
        # Invalidate previous sessions
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        return dict(updated), None


def update_user_alias(
    user_id: int,
    new_alias: str,
    avatar_color: Optional[str] = None,
    actor_id: Optional[int] = None,
    actor_role: str = "self",
) -> Optional[dict[str, Any]]:
    clean_alias = new_alias.strip()[:32]
    if not clean_alias:
        return None
    with get_auth_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        _log_change(conn, user_id, "alias", row["alias"], clean_alias, actor_id or user_id, actor_role)
        if avatar_color:
            conn.execute(
                "UPDATE users SET alias = ?, avatar_color = ? WHERE id = ?",
                (clean_alias, avatar_color, user_id),
            )
        else:
            conn.execute("UPDATE users SET alias = ? WHERE id = ?", (clean_alias, user_id))
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(updated)


def update_own_security(user_id: int, current_password: str, question: str, answer: str) -> bool:
    with get_auth_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or row["password"] != current_password:
            return False
        _log_change(conn, user_id, "security_question", row["security_question"], question.strip(), user_id, "self")
        _log_change(conn, user_id, "security_answer", row["security_answer"], answer.strip(), user_id, "self")
        conn.execute(
            "UPDATE users SET security_question = ?, security_answer = ? WHERE id = ?",
            (question.strip(), answer.strip(), user_id),
        )
        return True


def change_forced_password(user_id: int, new_password: str) -> Optional[dict[str, Any]]:
    with get_auth_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        _log_change(conn, user_id, "password", row["password"], new_password, user_id, "self")
        conn.execute(
            "UPDATE users SET password = ?, must_change_password = 0 WHERE id = ?",
            (new_password, user_id),
        )
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(updated)


# --- SESSIONS ---

def create_session(
    user_id: int,
    ip_address: Optional[str],
    user_agent: str = "",
    is_impersonated: bool = False,
) -> str:
    token = secrets.token_urlsafe(32)
    with get_auth_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, ip_address, user_agent, is_impersonated, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (token, user_id, ip_address, user_agent, 1 if is_impersonated else 0, time.time()),
        )
    return token


def delete_session(token: str) -> None:
    with get_auth_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def delete_user_sessions(user_id: int) -> None:
    with get_auth_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def get_user_by_token(token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    with get_auth_conn() as conn:
        row = conn.execute(
            """
            SELECT u.*, s.is_impersonated FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict[str, Any]]:
    with get_auth_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username: str) -> Optional[dict[str, Any]]:
    with get_auth_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
        return dict(row) if row else None


def get_users_map(user_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not user_ids:
        return {}
    unique_ids = list({uid for uid in user_ids if uid is not None})
    if not unique_ids:
        return {}
    placeholders = ",".join("?" for _ in unique_ids)
    with get_auth_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM users WHERE id IN ({placeholders})", unique_ids
        ).fetchall()
        return {r["id"]: dict(r) for r in rows}


def update_user_ip(user_id: int, ip_address: Optional[str], user_agent: str = "") -> None:
    if not ip_address or ip_address == "unknown":
        return
    with get_auth_conn() as conn:
        device = parse_user_agent(user_agent) if user_agent else None
        if device:
            conn.execute(
                "UPDATE users SET last_ip = ?, last_device = ? WHERE id = ?",
                (ip_address, device, user_id),
            )
        else:
            conn.execute("UPDATE users SET last_ip = ? WHERE id = ?", (ip_address, user_id))
        _record_known_ip(conn, user_id, ip_address)


def list_users(for_admin: bool = False) -> list[dict[str, Any]]:
    with get_auth_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY alias COLLATE NOCASE ASC").fetchall()
        result = []
        for r in rows:
            item: dict[str, Any] = {
                "id": r["id"],
                "alias": r["alias"],
                "avatar_color": r["avatar_color"],
                "role": r["role"],
            }
            if for_admin:
                item["username"] = r["username"]
                item["password"] = r["password"]
                item["security_question"] = r["security_question"]
                item["security_answer"] = r["security_answer"]
                item["admin_notes"] = r["admin_notes"]
                item["must_change_password"] = bool(r["must_change_password"])
                item["last_ip"] = r["last_ip"]
                item["last_device"] = r["last_device"]
                item["last_login_at"] = r["last_login_at"]
                item["created_at"] = r["created_at"]
            result.append(item)
        return result


# --- ADMIN SUPERPOWERS ---

def admin_update_user(
    admin_id: int,
    user_id: int,
    username: Optional[str] = None,
    password: Optional[str] = None,
    alias: Optional[str] = None,
    avatar_color: Optional[str] = None,
    role: Optional[str] = None,
    security_question: Optional[str] = None,
    security_answer: Optional[str] = None,
    admin_notes: Optional[str] = None,
    must_change_password: Optional[bool] = None,
    invalidate_sessions: bool = False,
) -> dict[str, Any]:
    with get_auth_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("Usuario no encontrado.")

        updates: dict[str, Any] = {}
        if username is not None and username.strip():
            new_u = username.strip()
            _log_change(conn, user_id, "username", row["username"], new_u, admin_id, "admin")
            updates["username"] = new_u
        if password is not None and password != "":
            _log_change(conn, user_id, "password", row["password"], password, admin_id, "admin")
            updates["password"] = password
            invalidate_sessions = True
        if alias is not None and alias.strip():
            new_a = alias.strip()[:32]
            _log_change(conn, user_id, "alias", row["alias"], new_a, admin_id, "admin")
            updates["alias"] = new_a
        if avatar_color is not None:
            updates["avatar_color"] = avatar_color
        if role is not None and role in ("admin", "member"):
            if row["username"].lower() != ADMIN_USERNAME.lower():
                _log_change(conn, user_id, "role", row["role"], role, admin_id, "admin")
                updates["role"] = role
        if security_question is not None:
            _log_change(conn, user_id, "security_question", row["security_question"], security_question.strip(), admin_id, "admin")
            updates["security_question"] = security_question.strip()
        if security_answer is not None:
            _log_change(conn, user_id, "security_answer", row["security_answer"], security_answer.strip(), admin_id, "admin")
            updates["security_answer"] = security_answer.strip()
        if admin_notes is not None:
            updates["admin_notes"] = admin_notes
        if must_change_password is not None:
            updates["must_change_password"] = 1 if must_change_password else 0

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
            conn.execute(
                f"UPDATE users SET {set_clause} WHERE id = ?",
                [*updates.values(), user_id],
            )
        if invalidate_sessions:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

        updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(updated)


def admin_generate_temp_password(admin_id: int, user_id: int) -> str:
    temp_pw = f"Delta-{secrets.randbelow(9000) + 1000}"
    admin_update_user(
        admin_id=admin_id,
        user_id=user_id,
        password=temp_pw,
        must_change_password=True,
        invalidate_sessions=True,
    )
    return temp_pw


def get_user_logs_and_history(user_id: int) -> dict[str, Any]:
    with get_auth_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return {"logins": [], "changes": [], "known_ips": []}
        logins = conn.execute(
            "SELECT * FROM login_logs WHERE user_id = ? OR username_attempted = ? ORDER BY id DESC LIMIT 50",
            (user_id, user["username"]),
        ).fetchall()
        changes = conn.execute(
            "SELECT * FROM user_change_history WHERE user_id = ? ORDER BY id DESC LIMIT 50",
            (user_id,),
        ).fetchall()
        ips = conn.execute(
            "SELECT * FROM user_known_ips WHERE user_id = ? ORDER BY last_seen DESC",
            (user_id,),
        ).fetchall()
        return {
            "logins": [dict(r) for r in logins],
            "changes": [dict(r) for r in changes],
            "known_ips": [dict(r) for r in ips],
        }


# --- BANS ---

def check_active_ban(user_id: Optional[int] = None, ip_address: Optional[str] = None) -> Optional[dict[str, Any]]:
    # Inmunidad absoluta para el administrador: nunca puede ser baneado ni bloqueado por IP
    if user_id is not None:
        u = get_user_by_id(user_id)
        if u and u.get("role") == "admin":
            return None

    now = time.time()
    with get_auth_conn() as conn:
        conn.execute(
            "UPDATE bans SET active = 0 WHERE active = 1 AND ban_type = 'temp' AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        clauses = []
        params: list[Any] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if ip_address and ip_address != "unknown":
            clauses.append("ip_address = ?")
            params.append(ip_address)
        if not clauses:
            return None
        row = conn.execute(
            f"SELECT * FROM bans WHERE active = 1 AND ({' OR '.join(clauses)}) ORDER BY id DESC LIMIT 1",
            params,
        ).fetchone()
        return dict(row) if row else None


def create_ban(
    admin_id: int,
    target_user_id: Optional[int],
    ip_address: Optional[str],
    ban_type: str,
    reason: str,
    duration_minutes: Optional[int],
) -> dict[str, Any]:
    now = time.time()
    expires_at = None
    if ban_type == "temp":
        mins = max(1, int(duration_minutes or 60))
        expires_at = now + (mins * 60)

    target_username = None
    resolved_ip = ip_address.strip() if ip_address else None
    with get_auth_conn() as conn:
        if target_user_id is not None:
            u = conn.execute("SELECT username, last_ip, role FROM users WHERE id = ?", (target_user_id,)).fetchone()
            if u:
                if u["role"] == "admin":
                    raise ValueError("No se puede banear al administrador.")
                target_username = u["username"]
                if not resolved_ip:
                    resolved_ip = u["last_ip"]
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_user_id,))

        if resolved_ip:
            conn.execute("DELETE FROM sessions WHERE ip_address = ?", (resolved_ip,))

        cur = conn.execute(
            """
            INSERT INTO bans (
                user_id, username, ip_address, ban_type, reason,
                expires_at, banned_by, active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (target_user_id, target_username, resolved_ip, ban_type, reason.strip(), expires_at, admin_id, now),
        )
        row = conn.execute("SELECT * FROM bans WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_bans(include_inactive: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    with get_auth_conn() as conn:
        conn.execute(
            "UPDATE bans SET active = 0 WHERE active = 1 AND ban_type = 'temp' AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        sql = "SELECT * FROM bans ORDER BY id DESC" if include_inactive else "SELECT * FROM bans WHERE active = 1 ORDER BY id DESC"
        return [dict(r) for r in conn.execute(sql).fetchall()]


def remove_ban(ban_id: int) -> bool:
    with get_auth_conn() as conn:
        cur = conn.execute("UPDATE bans SET active = 0 WHERE id = ?", (ban_id,))
        return cur.rowcount > 0


# --- EXPORT & RESTORE OF AUTH DB ---

def export_users_db_json() -> str:
    """Export all users, login_logs, change_history, known_ips, and bans as JSON."""
    with get_auth_conn() as conn:
        payload = {
            "exported_at": datetime.now().isoformat(),
            "format_version": 1,
            "users": [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()],
            "user_known_ips": [dict(r) for r in conn.execute("SELECT * FROM user_known_ips ORDER BY id ASC").fetchall()],
            "user_change_history": [dict(r) for r in conn.execute("SELECT * FROM user_change_history ORDER BY id ASC").fetchall()],
            "login_logs": [dict(r) for r in conn.execute("SELECT * FROM login_logs ORDER BY id DESC LIMIT 500").fetchall()],
            "bans": [dict(r) for r in conn.execute("SELECT * FROM bans ORDER BY id ASC").fetchall()],
        }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def restore_users_from_json(json_str: str) -> int:
    """Restore auth_users.db tables from a JSON backup string. Returns restored user count."""
    data = json.loads(json_str)
    if "users" not in data or not isinstance(data["users"], list):
        raise ValueError("El archivo JSON no tiene el formato válido de respaldo de usuarios.")

    with get_auth_conn() as conn:
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM user_known_ips")
        conn.execute("DELETE FROM user_change_history")
        conn.execute("DELETE FROM login_logs")
        conn.execute("DELETE FROM bans")
        conn.execute("DELETE FROM users")

        for u in data["users"]:
            conn.execute(
                """
                INSERT INTO users (
                    id, username, password, alias, avatar_color, role,
                    security_question, security_answer, admin_notes,
                    must_change_password, last_ip, last_device, last_login_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    u["id"],
                    u["username"],
                    u["password"],
                    u["alias"],
                    u.get("avatar_color", "#b5bd68"),
                    u.get("role", "member"),
                    u.get("security_question", ""),
                    u.get("security_answer", ""),
                    u.get("admin_notes", ""),
                    u.get("must_change_password", 0),
                    u.get("last_ip"),
                    u.get("last_device"),
                    u.get("last_login_at"),
                    u.get("created_at", time.time()),
                ),
            )

        for ip_row in data.get("user_known_ips", []):
            conn.execute(
                "INSERT OR IGNORE INTO user_known_ips (id, user_id, ip_address, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
                (ip_row["id"], ip_row["user_id"], ip_row["ip_address"], ip_row["first_seen"], ip_row["last_seen"]),
            )

        for ch_row in data.get("user_change_history", []):
            conn.execute(
                """
                INSERT OR IGNORE INTO user_change_history (
                    id, user_id, field_changed, old_value, new_value,
                    changed_by_user_id, changed_by_role, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ch_row["id"],
                    ch_row["user_id"],
                    ch_row["field_changed"],
                    ch_row.get("old_value"),
                    ch_row.get("new_value"),
                    ch_row.get("changed_by_user_id"),
                    ch_row.get("changed_by_role", "admin"),
                    ch_row.get("created_at", time.time()),
                ),
            )

        for l_row in data.get("login_logs", []):
            conn.execute(
                """
                INSERT OR IGNORE INTO login_logs (
                    id, user_id, username_attempted, ip_address, user_agent,
                    device_summary, success, failure_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    l_row["id"],
                    l_row.get("user_id"),
                    l_row["username_attempted"],
                    l_row["ip_address"],
                    l_row.get("user_agent", ""),
                    l_row.get("device_summary", ""),
                    l_row.get("success", 1),
                    l_row.get("failure_reason", ""),
                    l_row.get("created_at", time.time()),
                ),
            )

        for b_row in data.get("bans", []):
            conn.execute(
                """
                INSERT OR IGNORE INTO bans (
                    id, user_id, username, ip_address, ban_type, reason,
                    expires_at, banned_by, active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    b_row["id"],
                    b_row.get("user_id"),
                    b_row.get("username"),
                    b_row.get("ip_address"),
                    b_row["ban_type"],
                    b_row.get("reason", ""),
                    b_row.get("expires_at"),
                    b_row.get("banned_by"),
                    b_row.get("active", 1),
                    b_row.get("created_at", time.time()),
                ),
            )
        conn.execute("PRAGMA foreign_keys = ON;")

    add_audit_log("backup_restore", ADMIN_USERNAME, f"Restaurados {len(data['users'])} usuarios desde JSON")
    return len(data["users"])


def restore_users_from_sqlite_bytes(raw_bytes: bytes) -> int:
    """Validate and replace auth_users.db with an uploaded SQLite backup file."""
    tmp_path = Path(AUTH_DB_PATH).with_suffix(".restore_tmp")
    tmp_path.write_bytes(raw_bytes)
    try:
        test_conn = sqlite3.connect(str(tmp_path))
        row = test_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if not row:
            test_conn.close()
            raise ValueError("El archivo .db no contiene la tabla 'users'.")
        cnt = test_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        test_conn.close()
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise ValueError(f"Archivo SQLite inválido: {exc}") from exc

    shutil.move(str(tmp_path), AUTH_DB_PATH)
    add_audit_log("backup_restore", ADMIN_USERNAME, f"Restaurado archivo .db ({cnt} usuarios)")
    return cnt
