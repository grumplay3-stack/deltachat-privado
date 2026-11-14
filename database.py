import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional

DB_PATH = os.environ.get("DELTACHAT_DB", "deltachat.db")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "dabo777")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "52952416")

DEFAULT_CHANNELS = [
    ("gen", "General", "Charla libre del grupo"),
    ("memes", "Memes", "Memes, humor e imágenes random"),
    ("v", "Videojuegos", "Partidas, torneos y gaming"),
    ("mu", "Música", "Rolitas, playlists y recomendaciones"),
]

ALLOWED_EMOJIS = ["👍", "❤️", "😂", "🔥", "😮"]


def hash_secret(secret: str) -> str:
    """Hash a password or security answer using PBKDF2-HMAC-SHA256 with a random salt."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${dk.hex()}"


def verify_secret(secret: str, stored_hash: str) -> bool:
    """Verify a password or security answer against its stored salted hash."""
    try:
        salt, expected_hex = stored_hash.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return hmac.compare_digest(dk.hex(), expected_hex)


@contextmanager
def get_conn():
    """Provide a SQLite connection with row factory and foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15.0)
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


def init_db() -> None:
    """Initialize all tables, default channels, monthly backup marker, and seed Admin."""
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                alias TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                security_question TEXT NOT NULL,
                security_answer_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                avatar_color TEXT NOT NULL DEFAULT '#b5bd68',
                last_ip TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                ip_address TEXT,
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

            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                author_alias_snapshot TEXT NOT NULL DEFAULT 'Sistema',
                is_system INTEGER NOT NULL DEFAULT 0,
                content TEXT NOT NULL DEFAULT '',
                image_url TEXT,
                image_name TEXT,
                image_size_kb INTEGER,
                image_dims TEXT,
                is_hd INTEGER NOT NULL DEFAULT 0,
                expires_at REAL,
                is_expired INTEGER NOT NULL DEFAULT 0,
                reply_to_id INTEGER,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS direct_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                receiver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                content TEXT NOT NULL DEFAULT '',
                image_url TEXT,
                image_name TEXT,
                image_size_kb INTEGER,
                image_dims TEXT,
                is_hd INTEGER NOT NULL DEFAULT 0,
                expires_at REAL,
                is_expired INTEGER NOT NULL DEFAULT 0,
                reply_to_id INTEGER,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT NOT NULL CHECK(target_type IN ('channel', 'dm')),
                target_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                emoji TEXT NOT NULL,
                UNIQUE(target_type, target_id, user_id, emoji)
            );

            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        # Seed default channels
        for slug, name, desc in DEFAULT_CHANNELS:
            conn.execute(
                "INSERT OR IGNORE INTO channels (slug, name, description) VALUES (?, ?, ?)",
                (slug, name, desc),
            )

        # Seed monthly backup marker if missing
        current_ym = datetime.now().strftime("%Y-%m")
        conn.execute(
            "INSERT OR IGNORE INTO system_settings (key, value) VALUES ('last_backup_ym', ?)",
            (current_ym,),
        )

        # Seed preconfigured Admin account (dabo777)
        existing_admin = conn.execute(
            "SELECT id FROM users WHERE username = ?", (ADMIN_USERNAME,)
        ).fetchone()
        if not existing_admin:
            conn.execute(
                """
                INSERT INTO users (
                    username, alias, password_hash, security_question,
                    security_answer_hash, role, avatar_color, created_at
                ) VALUES (?, ?, ?, ?, ?, 'admin', '#cc6666', ?)
                """,
                (
                    ADMIN_USERNAME,
                    "AnonAdmin",
                    hash_secret(ADMIN_PASSWORD),
                    "Cuenta administrativa protegida",
                    hash_secret(ADMIN_PASSWORD),
                    time.time(),
                ),
            )


def get_setting(key: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO system_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def create_user(
    username: str,
    alias: str,
    password: str,
    security_question: str,
    security_answer: str,
    avatar_color: str,
    ip_address: Optional[str],
) -> dict[str, Any]:
    """Register a normal member account."""
    clean_user = username.strip()
    clean_alias = alias.strip() or clean_user
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (
                username, alias, password_hash, security_question,
                security_answer_hash, role, avatar_color, last_ip, created_at
            ) VALUES (?, ?, ?, ?, ?, 'member', ?, ?, ?)
            """,
            (
                clean_user,
                clean_alias,
                hash_secret(password),
                security_question.strip(),
                hash_secret(security_answer.strip().lower()),
                avatar_color or "#b5bd68",
                ip_address,
                now,
            ),
        )
        user_id = cur.lastrowid
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row)


def authenticate_user(username: str, password: str, ip_address: Optional[str]) -> Optional[dict[str, Any]]:
    """Verify credentials and update last known IP."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()
        if not row or not verify_secret(password, row["password_hash"]):
            return None
        if ip_address:
            conn.execute("UPDATE users SET last_ip = ? WHERE id = ?", (ip_address, row["id"]))
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        return dict(updated)


def get_security_question(username: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT security_question, role FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()
        if not row or row["role"] == "admin":
            return None
        return row["security_question"]


def recover_password(username: str, security_answer: str, new_password: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, role, security_answer_hash FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
        if not row or row["role"] == "admin":
            return False
        if not verify_secret(security_answer.strip().lower(), row["security_answer_hash"]):
            return False
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_secret(new_password), row["id"]),
        )
        return True


def admin_reset_password(user_id: int, new_password: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_secret(new_password), user_id),
        )
        return cur.rowcount > 0


def create_session(user_id: int, ip_address: Optional[str]) -> str:
    token = secrets.token_urlsafe(32)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, ip_address, created_at) VALUES (?, ?, ?, ?)",
            (token, user_id, ip_address, time.time()),
        )
    return token


def delete_session(token: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def get_user_by_token(token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT u.* FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def update_user_alias(user_id: int, new_alias: str, avatar_color: Optional[str] = None) -> Optional[dict[str, Any]]:
    clean_alias = new_alias.strip()[:32]
    if not clean_alias:
        return None
    with get_conn() as conn:
        if avatar_color:
            conn.execute(
                "UPDATE users SET alias = ?, avatar_color = ? WHERE id = ?",
                (clean_alias, avatar_color, user_id),
            )
        else:
            conn.execute("UPDATE users SET alias = ? WHERE id = ?", (clean_alias, user_id))
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def update_user_ip(user_id: int, ip_address: Optional[str]) -> None:
    if not ip_address:
        return
    with get_conn() as conn:
        conn.execute("UPDATE users SET last_ip = ? WHERE id = ?", (ip_address, user_id))


# --- BAN MANAGEMENT ---

def check_active_ban(user_id: Optional[int] = None, ip_address: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Return active ban if user_id or ip_address is banned; auto-deactivate expired temp bans."""
    now = time.time()
    with get_conn() as conn:
        # Expire elapsed temporary bans
        conn.execute(
            "UPDATE bans SET active = 0 WHERE active = 1 AND ban_type = 'temp' AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        clauses = []
        params: list[Any] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if ip_address:
            clauses.append("ip_address = ?")
            params.append(ip_address)
        if not clauses:
            return None
        sql = f"SELECT * FROM bans WHERE active = 1 AND ({' OR '.join(clauses)}) ORDER BY id DESC LIMIT 1"
        row = conn.execute(sql, params).fetchone()
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
    with get_conn() as conn:
        if target_user_id is not None:
            u = conn.execute("SELECT username, last_ip, role FROM users WHERE id = ?", (target_user_id,)).fetchone()
            if u:
                if u["role"] == "admin":
                    raise ValueError("No se puede banear al administrador.")
                target_username = u["username"]
                if not resolved_ip:
                    resolved_ip = u["last_ip"]
            # Invalidate user sessions immediately
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
            (
                target_user_id,
                target_username,
                resolved_ip,
                ban_type,
                reason.strip(),
                expires_at,
                admin_id,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM bans WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_bans(include_inactive: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "UPDATE bans SET active = 0 WHERE active = 1 AND ban_type = 'temp' AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        if include_inactive:
            rows = conn.execute("SELECT * FROM bans ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM bans WHERE active = 1 ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def remove_ban(ban_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE bans SET active = 0 WHERE id = ?", (ban_id,))
        return cur.rowcount > 0


# --- USERS, CHANNELS & MESSAGES ---

def list_users(for_admin: bool = False) -> list[dict[str, Any]]:
    with get_conn() as conn:
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
                item["last_ip"] = r["last_ip"]
                item["created_at"] = r["created_at"]
            result.append(item)
        return result


def list_channels() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM channels ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def get_channel(channel_id: int) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        return dict(row) if row else None


def _attach_reactions(conn: sqlite3.Connection, target_type: str, target_ids: list[int], viewer_id: int) -> dict[int, list[dict[str, Any]]]:
    if not target_ids:
        return {}
    placeholders = ",".join("?" for _ in target_ids)
    rows = conn.execute(
        f"""
        SELECT target_id, emoji, COUNT(*) as cnt,
               MAX(CASE WHEN user_id = ? THEN 1 ELSE 0 END) as reacted
        FROM reactions
        WHERE target_type = ? AND target_id IN ({placeholders})
        GROUP BY target_id, emoji
        """,
        [viewer_id, target_type, *target_ids],
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {tid: [] for tid in target_ids}
    for r in rows:
        grouped[r["target_id"]].append(
            {"emoji": r["emoji"], "count": r["cnt"], "me": bool(r["reacted"])}
        )
    return grouped


def _format_channel_row(
    r: sqlite3.Row,
    reactions: list[dict[str, Any]],
    reply_preview: Optional[dict[str, Any]],
    for_admin: bool,
) -> dict[str, Any]:
    author_alias = r["live_alias"] if r["live_alias"] else r["author_alias_snapshot"]
    msg: dict[str, Any] = {
        "id": r["id"],
        "channel_id": r["channel_id"],
        "user_id": r["user_id"],
        "alias": author_alias,
        "avatar_color": r["avatar_color"] or "#81a2be",
        "author_role": r["author_role"] or "member",
        "is_system": bool(r["is_system"]),
        "content": r["content"],
        "image_url": None if r["is_expired"] else r["image_url"],
        "image_name": r["image_name"],
        "image_size_kb": r["image_size_kb"],
        "image_dims": r["image_dims"],
        "is_hd": bool(r["is_hd"]),
        "expires_at": r["expires_at"],
        "is_expired": bool(r["is_expired"]),
        "reply_to_id": r["reply_to_id"],
        "reply_preview": reply_preview,
        "reactions": reactions,
        "created_at": r["created_at"],
    }
    if for_admin and r["user_id"] is not None:
        msg["username"] = r["live_username"]
        msg["user_ip"] = r["live_ip"]
    return msg


def create_channel_message(
    channel_id: int,
    user_id: Optional[int],
    content: str,
    is_system: bool = False,
    alias_snapshot: str = "Sistema",
    image_url: Optional[str] = None,
    image_name: Optional[str] = None,
    image_size_kb: Optional[int] = None,
    image_dims: Optional[str] = None,
    is_hd: bool = False,
    expires_at: Optional[float] = None,
    reply_to_id: Optional[int] = None,
) -> int:
    now = time.time()
    with get_conn() as conn:
        if user_id is not None:
            u = conn.execute("SELECT alias FROM users WHERE id = ?", (user_id,)).fetchone()
            if u:
                alias_snapshot = u["alias"]
        cur = conn.execute(
            """
            INSERT INTO messages (
                channel_id, user_id, author_alias_snapshot, is_system, content,
                image_url, image_name, image_size_kb, image_dims,
                is_hd, expires_at, is_expired, reply_to_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                channel_id,
                user_id,
                alias_snapshot,
                1 if is_system else 0,
                content,
                image_url,
                image_name,
                image_size_kb,
                image_dims,
                1 if is_hd else 0,
                expires_at,
                reply_to_id,
                now,
            ),
        )
        return cur.lastrowid


def get_channel_message_by_id(msg_id: int, viewer_id: int, for_admin: bool = False) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT m.*, u.alias as live_alias, u.username as live_username,
                   u.avatar_color, u.role as author_role, u.last_ip as live_ip
            FROM messages m
            LEFT JOIN users u ON u.id = m.user_id
            WHERE m.id = ?
            """,
            (msg_id,),
        ).fetchone()
        if not row:
            return None
        reactions_map = _attach_reactions(conn, "channel", [msg_id], viewer_id)
        reply_preview = None
        if row["reply_to_id"]:
            rp = conn.execute(
                """
                SELECT m.id, COALESCE(u.alias, m.author_alias_snapshot) as alias, m.content, m.image_name
                FROM messages m LEFT JOIN users u ON u.id = m.user_id WHERE m.id = ?
                """,
                (row["reply_to_id"],),
            ).fetchone()
            if rp:
                reply_preview = dict(rp)
        return _format_channel_row(row, reactions_map.get(msg_id, []), reply_preview, for_admin)


def get_channel_messages(
    channel_id: int,
    viewer_id: int,
    for_admin: bool = False,
    limit: int = 100,
    search: Optional[str] = None,
) -> list[dict[str, Any]]:
    with get_conn() as conn:
        query = """
            SELECT m.*, u.alias as live_alias, u.username as live_username,
                   u.avatar_color, u.role as author_role, u.last_ip as live_ip
            FROM messages m
            LEFT JOIN users u ON u.id = m.user_id
            WHERE m.channel_id = ?
        """
        params: list[Any] = [channel_id]
        if search and search.strip():
            like = f"%{search.strip()}%"
            query += " AND (m.content LIKE ? OR COALESCE(u.alias, m.author_alias_snapshot) LIKE ?)"
            params.extend([like, like])
        query += " ORDER BY m.id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        rows.reverse()

        ids = [r["id"] for r in rows]
        reactions_map = _attach_reactions(conn, "channel", ids, viewer_id)
        reply_ids = list({r["reply_to_id"] for r in rows if r["reply_to_id"]})
        reply_map: dict[int, dict[str, Any]] = {}
        if reply_ids:
            placeholders = ",".join("?" for _ in reply_ids)
            r_rows = conn.execute(
                f"""
                SELECT m.id, COALESCE(u.alias, m.author_alias_snapshot) as alias, m.content, m.image_name
                FROM messages m LEFT JOIN users u ON u.id = m.user_id
                WHERE m.id IN ({placeholders})
                """,
                reply_ids,
            ).fetchall()
            for rr in r_rows:
                reply_map[rr["id"]] = dict(rr)

        return [
            _format_channel_row(
                r,
                reactions_map.get(r["id"], []),
                reply_map.get(r["reply_to_id"]) if r["reply_to_id"] else None,
                for_admin,
            )
            for r in rows
        ]


def delete_channel_message(msg_id: int, requester_id: int, is_admin: bool) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            return None
        if not is_admin and row["user_id"] != requester_id:
            raise PermissionError("No tienes permiso para borrar este mensaje.")
        image_url = row["image_url"]
        channel_id = row["channel_id"]
        conn.execute("DELETE FROM reactions WHERE target_type = 'channel' AND target_id = ?", (msg_id,))
        conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        return {"id": msg_id, "channel_id": channel_id, "image_url": image_url}


# --- DIRECT MESSAGES ---

def create_direct_message(
    sender_id: int,
    receiver_id: int,
    content: str,
    image_url: Optional[str] = None,
    image_name: Optional[str] = None,
    image_size_kb: Optional[int] = None,
    image_dims: Optional[str] = None,
    is_hd: bool = False,
    expires_at: Optional[float] = None,
    reply_to_id: Optional[int] = None,
) -> int:
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO direct_messages (
                sender_id, receiver_id, content, image_url, image_name,
                image_size_kb, image_dims, is_hd, expires_at, is_expired, reply_to_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                sender_id,
                receiver_id,
                content,
                image_url,
                image_name,
                image_size_kb,
                image_dims,
                1 if is_hd else 0,
                expires_at,
                reply_to_id,
                now,
            ),
        )
        return cur.lastrowid


def _format_dm_row(
    r: sqlite3.Row,
    reactions: list[dict[str, Any]],
    reply_preview: Optional[dict[str, Any]],
    for_admin: bool,
) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "id": r["id"],
        "sender_id": r["sender_id"],
        "receiver_id": r["receiver_id"],
        "user_id": r["sender_id"],
        "alias": r["sender_alias"],
        "avatar_color": r["avatar_color"] or "#81a2be",
        "author_role": r["sender_role"] or "member",
        "content": r["content"],
        "image_url": None if r["is_expired"] else r["image_url"],
        "image_name": r["image_name"],
        "image_size_kb": r["image_size_kb"],
        "image_dims": r["image_dims"],
        "is_hd": bool(r["is_hd"]),
        "expires_at": r["expires_at"],
        "is_expired": bool(r["is_expired"]),
        "reply_to_id": r["reply_to_id"],
        "reply_preview": reply_preview,
        "reactions": reactions,
        "created_at": r["created_at"],
    }
    if for_admin:
        msg["username"] = r["sender_username"]
    return msg


def get_dm_by_id(dm_id: int, viewer_id: int, for_admin: bool = False) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT d.*, u.alias as sender_alias, u.username as sender_username,
                   u.avatar_color, u.role as sender_role
            FROM direct_messages d
            JOIN users u ON u.id = d.sender_id
            WHERE d.id = ?
            """,
            (dm_id,),
        ).fetchone()
        if not row:
            return None
        reactions_map = _attach_reactions(conn, "dm", [dm_id], viewer_id)
        reply_preview = None
        if row["reply_to_id"]:
            rp = conn.execute(
                """
                SELECT d.id, u.alias, d.content, d.image_name
                FROM direct_messages d JOIN users u ON u.id = d.sender_id WHERE d.id = ?
                """,
                (row["reply_to_id"],),
            ).fetchone()
            if rp:
                reply_preview = dict(rp)
        return _format_dm_row(row, reactions_map.get(dm_id, []), reply_preview, for_admin)


def get_direct_messages(
    user_a: int,
    user_b: int,
    viewer_id: int,
    for_admin: bool = False,
    limit: int = 100,
    search: Optional[str] = None,
) -> list[dict[str, Any]]:
    with get_conn() as conn:
        query = """
            SELECT d.*, u.alias as sender_alias, u.username as sender_username,
                   u.avatar_color, u.role as sender_role
            FROM direct_messages d
            JOIN users u ON u.id = d.sender_id
            WHERE ((d.sender_id = ? AND d.receiver_id = ?) OR (d.sender_id = ? AND d.receiver_id = ?))
        """
        params: list[Any] = [user_a, user_b, user_b, user_a]
        if search and search.strip():
            like = f"%{search.strip()}%"
            query += " AND (d.content LIKE ? OR u.alias LIKE ?)"
            params.extend([like, like])
        query += " ORDER BY d.id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        rows.reverse()

        ids = [r["id"] for r in rows]
        reactions_map = _attach_reactions(conn, "dm", ids, viewer_id)
        reply_ids = list({r["reply_to_id"] for r in rows if r["reply_to_id"]})
        reply_map: dict[int, dict[str, Any]] = {}
        if reply_ids:
            placeholders = ",".join("?" for _ in reply_ids)
            r_rows = conn.execute(
                f"""
                SELECT d.id, u.alias, d.content, d.image_name
                FROM direct_messages d JOIN users u ON u.id = d.sender_id
                WHERE d.id IN ({placeholders})
                """,
                reply_ids,
            ).fetchall()
            for rr in r_rows:
                reply_map[rr["id"]] = dict(rr)

        return [
            _format_dm_row(
                r,
                reactions_map.get(r["id"], []),
                reply_map.get(r["reply_to_id"]) if r["reply_to_id"] else None,
                for_admin,
            )
            for r in rows
        ]


def delete_direct_message(dm_id: int, requester_id: int, is_admin: bool) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM direct_messages WHERE id = ?", (dm_id,)).fetchone()
        if not row:
            return None
        if not is_admin and row["sender_id"] != requester_id:
            raise PermissionError("No tienes permiso para borrar este mensaje.")
        image_url = row["image_url"]
        sender_id = row["sender_id"]
        receiver_id = row["receiver_id"]
        conn.execute("DELETE FROM reactions WHERE target_type = 'dm' AND target_id = ?", (dm_id,))
        conn.execute("DELETE FROM direct_messages WHERE id = ?", (dm_id,))
        return {
            "id": dm_id,
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "image_url": image_url,
        }


def toggle_reaction(target_type: str, target_id: int, user_id: int, emoji: str) -> list[dict[str, Any]]:
    if emoji not in ALLOWED_EMOJIS:
        raise ValueError("Emoji no permitido.")
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM reactions WHERE target_type = ? AND target_id = ? AND user_id = ? AND emoji = ?",
            (target_type, target_id, user_id, emoji),
        ).fetchone()
        if existing:
            conn.execute("DELETE FROM reactions WHERE id = ?", (existing["id"],))
        else:
            conn.execute(
                "INSERT INTO reactions (target_type, target_id, user_id, emoji) VALUES (?, ?, ?, ?)",
                (target_type, target_id, user_id, emoji),
            )
        rows = conn.execute(
            "SELECT emoji, COUNT(*) as cnt FROM reactions WHERE target_type = ? AND target_id = ? GROUP BY emoji",
            (target_type, target_id),
        ).fetchall()
        return [{"emoji": r["emoji"], "count": r["cnt"]} for r in rows]


# --- HD EXPIRATION & BACKUP QUERIES ---

def fetch_and_expire_hd_images(now: Optional[float] = None) -> dict[str, list[dict[str, Any]]]:
    """Find expired HD images in both channels and DMs, mark them expired, and return info for cleanup."""
    ts = now if now is not None else time.time()
    expired_channels: list[dict[str, Any]] = []
    expired_dms: list[dict[str, Any]] = []
    with get_conn() as conn:
        c_rows = conn.execute(
            "SELECT id, channel_id, image_url FROM messages WHERE is_hd = 1 AND is_expired = 0 AND expires_at IS NOT NULL AND expires_at <= ?",
            (ts,),
        ).fetchall()
        for r in c_rows:
            conn.execute("UPDATE messages SET is_expired = 1, image_url = NULL WHERE id = ?", (r["id"],))
            expired_channels.append(dict(r))

        d_rows = conn.execute(
            "SELECT id, sender_id, receiver_id, image_url FROM direct_messages WHERE is_hd = 1 AND is_expired = 0 AND expires_at IS NOT NULL AND expires_at <= ?",
            (ts,),
        ).fetchall()
        for r in d_rows:
            conn.execute("UPDATE direct_messages SET is_expired = 1, image_url = NULL WHERE id = ?", (r["id"],))
            expired_dms.append(dict(r))

    return {"channels": expired_channels, "dms": expired_dms}


def fetch_all_messages_for_backup() -> dict[str, Any]:
    """Return all text messages grouped by channel and DM pairs for monthly backup (no attachments)."""
    with get_conn() as conn:
        channels = conn.execute("SELECT * FROM channels ORDER BY id ASC").fetchall()
        channel_logs = []
        for ch in channels:
            msgs = conn.execute(
                """
                SELECT m.id, m.content, m.created_at, m.is_system,
                       COALESCE(u.username, m.author_alias_snapshot) as username,
                       COALESCE(u.alias, m.author_alias_snapshot) as alias
                FROM messages m
                LEFT JOIN users u ON u.id = m.user_id
                WHERE m.channel_id = ?
                ORDER BY m.id ASC
                """,
                (ch["id"],),
            ).fetchall()
            channel_logs.append({"channel": dict(ch), "messages": [dict(m) for m in msgs]})

        dms = conn.execute(
            """
            SELECT d.id, d.content, d.created_at,
                   s.username as sender_username, s.alias as sender_alias,
                   r.username as receiver_username, r.alias as receiver_alias
            FROM direct_messages d
            JOIN users s ON s.id = d.sender_id
            JOIN users r ON r.id = d.receiver_id
            ORDER BY d.id ASC
            """
        ).fetchall()
        return {"channels": channel_logs, "dms": [dict(d) for d in dms]}


def reset_all_messages_for_new_month(new_ym: str) -> list[str]:
    """Delete all messages, DMs, reactions, and return list of image paths to unlink."""
    image_paths: list[str] = []
    with get_conn() as conn:
        for r in conn.execute("SELECT image_url FROM messages WHERE image_url IS NOT NULL").fetchall():
            image_paths.append(r["image_url"])
        for r in conn.execute("SELECT image_url FROM direct_messages WHERE image_url IS NOT NULL").fetchall():
            image_paths.append(r["image_url"])

        conn.execute("DELETE FROM reactions")
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM direct_messages")
        conn.execute(
            "INSERT INTO system_settings (key, value) VALUES ('last_backup_ym', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (new_ym,),
        )
        # Insert welcome message in #general (channel_id = 1)
        gen = conn.execute("SELECT id FROM channels WHERE slug = 'gen'").fetchone()
        if gen:
            conn.execute(
                """
                INSERT INTO messages (channel_id, user_id, author_alias_snapshot, is_system, content, created_at)
                VALUES (?, NULL, 'Sistema', 1, ?, ?)
                """,
                (
                    gen["id"],
                    ">Nuevo mes iniciado. El historial del mes anterior fue respaldado y el chat ha comenzado de cero.",
                    time.time(),
                ),
            )
    return image_paths
