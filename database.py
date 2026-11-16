import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional

import auth_database

DB_PATH = os.environ.get("DELTACHAT_DB", "deltachat.db")

DEFAULT_CHANNELS = [
    ("gen", "General", "Charla libre del grupo"),
    ("memes", "Memes", "Memes, humor e imágenes random"),
    ("v", "Videojuegos", "Partidas, torneos y gaming"),
    ("mu", "Música", "Rolitas, playlists y recomendaciones"),
]

ALLOWED_EMOJIS = ["👍", "❤️", "😂", "🔥", "😮"]


@contextmanager
def get_conn():
    """Provide a SQLite connection to deltachat.db."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
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
    """Initialize chat tables in deltachat.db and ensure auth_users.db is initialized first."""
    # Ensure auth_users.db initializes and migrates legacy users BEFORE touching legacy tables
    auth_database.init_auth_db()

    with get_conn() as conn:
        # Check if migrating from legacy single-DB schema with FK to users
        has_legacy_users = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()

        if has_legacy_users:
            _migrate_remove_legacy_user_fks(conn)

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                access_type TEXT NOT NULL DEFAULT 'public',
                is_announcement INTEGER NOT NULL DEFAULT 0,
                pinned_message_id INTEGER DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS channel_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                permission_type TEXT NOT NULL CHECK(permission_type IN ('allow', 'deny')),
                created_at REAL NOT NULL,
                UNIQUE(channel_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                user_id INTEGER,
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
                is_nsfw INTEGER NOT NULL DEFAULT 0,
                reply_to_id INTEGER,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS direct_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                image_url TEXT,
                image_name TEXT,
                image_size_kb INTEGER,
                image_dims TEXT,
                is_hd INTEGER NOT NULL DEFAULT 0,
                expires_at REAL,
                is_expired INTEGER NOT NULL DEFAULT 0,
                is_nsfw INTEGER NOT NULL DEFAULT 0,
                reply_to_id INTEGER,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT NOT NULL CHECK(target_type IN ('channel', 'dm')),
                target_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                UNIQUE(target_type, target_id, user_id, emoji)
            );

            CREATE TABLE IF NOT EXISTS friendships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_a_id INTEGER NOT NULL,
                user_b_id INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'rejected', 'unfriended', 'blocked')),
                action_user_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(user_a_id, user_b_id)
            );

            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                creator_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                is_closed INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS poll_options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id INTEGER NOT NULL,
                option_text TEXT NOT NULL,
                votes_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS poll_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                option_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(poll_id, user_id)
            );
            """
        )

        # Ensure columns exist on pre-existing direct_messages table
        dm_cols = {r["name"] for r in conn.execute("PRAGMA table_info(direct_messages)").fetchall()}
        if "is_read" not in dm_cols:
            conn.execute("ALTER TABLE direct_messages ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0")
        if "is_nsfw" not in dm_cols:
            conn.execute("ALTER TABLE direct_messages ADD COLUMN is_nsfw INTEGER NOT NULL DEFAULT 0")
        if "media_type" not in dm_cols:
            conn.execute("ALTER TABLE direct_messages ADD COLUMN media_type TEXT NOT NULL DEFAULT 'text'")
        if "media_duration" not in dm_cols:
            conn.execute("ALTER TABLE direct_messages ADD COLUMN media_duration REAL DEFAULT NULL")
        if "is_gif_mode" not in dm_cols:
            conn.execute("ALTER TABLE direct_messages ADD COLUMN is_gif_mode INTEGER NOT NULL DEFAULT 0")
        if "poll_id" not in dm_cols:
            conn.execute("ALTER TABLE direct_messages ADD COLUMN poll_id INTEGER DEFAULT NULL")

        # Migrate channels table schema if missing access_type
        ch_cols = {r["name"] for r in conn.execute("PRAGMA table_info(channels)").fetchall()}
        if "access_type" not in ch_cols:
            conn.execute("ALTER TABLE channels ADD COLUMN access_type TEXT NOT NULL DEFAULT 'public'")
        if "is_announcement" not in ch_cols:
            conn.execute("ALTER TABLE channels ADD COLUMN is_announcement INTEGER NOT NULL DEFAULT 0")
        if "pinned_message_id" not in ch_cols:
            conn.execute("ALTER TABLE channels ADD COLUMN pinned_message_id INTEGER DEFAULT NULL")

        # Migrate messages table schema
        msg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "is_nsfw" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN is_nsfw INTEGER NOT NULL DEFAULT 0")
        if "media_type" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN media_type TEXT NOT NULL DEFAULT 'text'")
        if "media_duration" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN media_duration REAL DEFAULT NULL")
        if "is_gif_mode" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN is_gif_mode INTEGER NOT NULL DEFAULT 0")
        if "poll_id" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN poll_id INTEGER DEFAULT NULL")

        for slug, name, desc in DEFAULT_CHANNELS:
            conn.execute(
                "INSERT OR IGNORE INTO channels (slug, name, description) VALUES (?, ?, ?)",
                (slug, name, desc),
            )

        current_ym = datetime.now().strftime("%Y-%m")
        conn.execute(
            "INSERT OR IGNORE INTO system_settings (key, value) VALUES ('last_backup_ym', ?)",
            (current_ym,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO system_settings (key, value) VALUES ('radio_stream_title', 'ConectFm')",
        )
        conn.execute(
            "INSERT OR IGNORE INTO system_settings (key, value) VALUES ('radio_stream_url', '')",
        )
        conn.execute(
            "INSERT OR IGNORE INTO system_settings (key, value) VALUES ('radio_active', '0')",
        )


def _migrate_remove_legacy_user_fks(conn: sqlite3.Connection) -> None:
    """Rebuild tables without cross-DB FK constraints to old users table and drop legacy auth tables."""
    conn.execute("PRAGMA foreign_keys = OFF;")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            user_id INTEGER,
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
        INSERT INTO messages_new SELECT id, channel_id, user_id, author_alias_snapshot, is_system, content,
            image_url, image_name, image_size_kb, image_dims, is_hd, expires_at, is_expired, reply_to_id, created_at FROM messages;
        DROP TABLE messages;
        ALTER TABLE messages_new RENAME TO messages;

        CREATE TABLE IF NOT EXISTS direct_messages_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            image_url TEXT,
            image_name TEXT,
            image_size_kb INTEGER,
            image_dims TEXT,
            is_hd INTEGER NOT NULL DEFAULT 0,
            expires_at REAL,
            is_expired INTEGER NOT NULL DEFAULT 0,
            reply_to_id INTEGER,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        INSERT INTO direct_messages_new (id, sender_id, receiver_id, content, image_url, image_name,
            image_size_kb, image_dims, is_hd, expires_at, is_expired, reply_to_id, created_at)
        SELECT id, sender_id, receiver_id, content, image_url, image_name,
            image_size_kb, image_dims, is_hd, expires_at, is_expired, reply_to_id, created_at FROM direct_messages;
        DROP TABLE direct_messages;
        ALTER TABLE direct_messages_new RENAME TO direct_messages;

        CREATE TABLE IF NOT EXISTS reactions_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL CHECK(target_type IN ('channel', 'dm')),
            target_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            emoji TEXT NOT NULL,
            UNIQUE(target_type, target_id, user_id, emoji)
        );
        INSERT INTO reactions_new SELECT id, target_type, target_id, user_id, emoji FROM reactions;
        DROP TABLE reactions;
        ALTER TABLE reactions_new RENAME TO reactions;

        DROP TABLE IF EXISTS sessions;
        DROP TABLE IF EXISTS bans;
        DROP TABLE IF EXISTS users;
        """
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


def list_channels_for_user(user_id: int, is_admin: bool = False) -> list[dict[str, Any]]:
    with get_conn() as conn:
        channels = [dict(r) for r in conn.execute("SELECT * FROM channels ORDER BY id ASC").fetchall()]
        if is_admin:
            return channels

        # Check permissions for regular users
        allowed: list[dict[str, Any]] = []
        for ch in channels:
            atype = ch.get("access_type", "public")
            cid = ch["id"]
            if atype == "public":
                # Check blacklist
                perm = conn.execute(
                    "SELECT permission_type FROM channel_permissions WHERE channel_id = ? AND user_id = ?",
                    (cid, user_id),
                ).fetchone()
                if not perm or perm["permission_type"] != "deny":
                    allowed.append(ch)
            elif atype == "whitelist":
                # Must be explicitly allowed
                perm = conn.execute(
                    "SELECT permission_type FROM channel_permissions WHERE channel_id = ? AND user_id = ?",
                    (cid, user_id),
                ).fetchone()
                if perm and perm["permission_type"] == "allow":
                    allowed.append(ch)
            elif atype == "blacklist":
                perm = conn.execute(
                    "SELECT permission_type FROM channel_permissions WHERE channel_id = ? AND user_id = ?",
                    (cid, user_id),
                ).fetchone()
                if not perm or perm["permission_type"] != "deny":
                    allowed.append(ch)
        return allowed


def can_user_access_channel(user_id: int, channel_id: int, is_admin: bool = False) -> bool:
    if is_admin:
        return True
    visible = list_channels_for_user(user_id, is_admin=False)
    return any(c["id"] == channel_id for c in visible)


def can_user_post_in_channel(user_id: int, channel_id: int, is_admin: bool = False) -> tuple[bool, str]:
    if not can_user_access_channel(user_id, channel_id, is_admin):
        return False, "No tienes acceso a este tablón."
    ch = get_channel(channel_id)
    if not ch:
        return False, "Tablón no encontrado."
    if ch.get("is_announcement") and not is_admin:
        return False, "Este tablón es de solo lectura (anuncios)."
    return True, ""


def admin_set_channel_visibility(
    channel_id: int,
    access_type: str,
    user_ids: list[int],
    permission_type: str = "allow",
    is_announcement: Optional[bool] = None,
) -> dict[str, Any]:
    with get_conn() as conn:
        ch = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if not ch:
            raise ValueError("Tablón no encontrado.")

        updates = ["access_type = ?"]
        params: list[Any] = [access_type]
        if is_announcement is not None:
            updates.append("is_announcement = ?")
            params.append(1 if is_announcement else 0)
        params.append(channel_id)

        conn.execute(f"UPDATE channels SET {', '.join(updates)} WHERE id = ?", params)
        conn.execute("DELETE FROM channel_permissions WHERE channel_id = ?", (channel_id,))

        now = time.time()
        for uid in user_ids:
            conn.execute(
                "INSERT INTO channel_permissions (channel_id, user_id, permission_type, created_at) VALUES (?, ?, ?, ?)",
                (channel_id, uid, permission_type, now),
            )

        updated = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        return dict(updated)


def admin_pin_message(channel_id: int, message_id: Optional[int]) -> dict[str, Any]:
    with get_conn() as conn:
        conn.execute("UPDATE channels SET pinned_message_id = ? WHERE id = ?", (message_id, channel_id))
        ch = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        return dict(ch) if ch else {}


def get_channel_permissions(channel_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, permission_type FROM channel_permissions WHERE channel_id = ?",
            (channel_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_channel(channel_id: int) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        return dict(row) if row else None


def _attach_reactions(
    conn: sqlite3.Connection, target_type: str, target_ids: list[int], viewer_id: int
) -> dict[int, list[dict[str, Any]]]:
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
    users_map: dict[int, dict[str, Any]],
    reactions: list[dict[str, Any]],
    reply_preview: Optional[dict[str, Any]],
    for_admin: bool,
    poll_data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    u = users_map.get(r["user_id"]) if r["user_id"] is not None else None
    author_alias = u["alias"] if u else (r["author_alias_snapshot"] or "Usuario Eliminado")
    media_type = r["media_type"] if ("media_type" in r.keys() and r["media_type"]) else ("image" if r["image_url"] else "text")
    msg: dict[str, Any] = {
        "id": r["id"],
        "channel_id": r["channel_id"],
        "user_id": r["user_id"],
        "alias": author_alias,
        "avatar_color": u["avatar_color"] if u else "#81a2be",
        "author_role": u["role"] if u else "member",
        "is_system": bool(r["is_system"]),
        "content": r["content"],
        "image_url": None if r["is_expired"] else r["image_url"],
        "image_name": r["image_name"],
        "image_size_kb": r["image_size_kb"],
        "image_dims": r["image_dims"],
        "is_hd": bool(r["is_hd"]),
        "expires_at": r["expires_at"],
        "is_expired": bool(r["is_expired"]),
        "is_nsfw": bool(r["is_nsfw"]) if "is_nsfw" in r.keys() else False,
        "media_type": media_type,
        "media_duration": r["media_duration"] if "media_duration" in r.keys() else None,
        "is_gif_mode": bool(r["is_gif_mode"]) if "is_gif_mode" in r.keys() else False,
        "poll_id": r["poll_id"] if "poll_id" in r.keys() else None,
        "poll": poll_data,
        "reply_to_id": r["reply_to_id"],
        "reply_preview": reply_preview,
        "reactions": reactions,
        "created_at": r["created_at"],
    }
    if for_admin and u is not None:
        msg["username"] = u["username"]
        msg["user_ip"] = u["last_ip"]
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
    is_nsfw: bool = False,
    reply_to_id: Optional[int] = None,
    media_type: str = "text",
    media_duration: Optional[float] = None,
    is_gif_mode: bool = False,
    poll_id: Optional[int] = None,
) -> int:
    now = time.time()
    if user_id is not None:
        u = auth_database.get_user_by_id(user_id)
        if u:
            alias_snapshot = u["alias"]
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO messages (
                channel_id, user_id, author_alias_snapshot, is_system, content,
                image_url, image_name, image_size_kb, image_dims,
                is_hd, expires_at, is_expired, is_nsfw, reply_to_id, created_at,
                media_type, media_duration, is_gif_mode, poll_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
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
                1 if is_nsfw else 0,
                reply_to_id,
                now,
                media_type,
                media_duration,
                1 if is_gif_mode else 0,
                poll_id,
            ),
        )
        return cur.lastrowid


def get_channel_message_by_id(
    msg_id: int, viewer_id: int, for_admin: bool = False
) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            return None
        user_ids = [row["user_id"]] if row["user_id"] else []
        reply_preview = None
        if row["reply_to_id"]:
            rp = conn.execute(
                "SELECT id, user_id, author_alias_snapshot, content, image_name FROM messages WHERE id = ?",
                (row["reply_to_id"],),
            ).fetchone()
            if rp:
                if rp["user_id"]:
                    user_ids.append(rp["user_id"])
                reply_preview = dict(rp)

        users_map = auth_database.get_users_map(user_ids)
        if reply_preview:
            ru = users_map.get(reply_preview.get("user_id"))
            reply_preview = {
                "id": reply_preview["id"],
                "alias": ru["alias"] if ru else reply_preview["author_alias_snapshot"],
                "content": reply_preview["content"],
                "image_name": reply_preview["image_name"],
            }
        reactions_map = _attach_reactions(conn, "channel", [msg_id], viewer_id)
        poll_data = None
        if "poll_id" in row.keys() and row["poll_id"]:
            poll_data = get_poll_by_id(row["poll_id"], viewer_id=viewer_id)
        return _format_channel_row(
            row, users_map, reactions_map.get(msg_id, []), reply_preview, for_admin, poll_data=poll_data
        )


def get_channel_messages(
    channel_id: int,
    viewer_id: int,
    for_admin: bool = False,
    limit: int = 150,
    search: Optional[str] = None,
) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE channel_id = ? ORDER BY id DESC LIMIT ?",
            (channel_id, limit if not search else 500),
        ).fetchall()
        rows.reverse()

        user_ids = [r["user_id"] for r in rows if r["user_id"] is not None]
        reply_ids = list({r["reply_to_id"] for r in rows if r["reply_to_id"]})
        reply_raw_map: dict[int, dict[str, Any]] = {}
        if reply_ids:
            placeholders = ",".join("?" for _ in reply_ids)
            for rr in conn.execute(
                f"SELECT id, user_id, author_alias_snapshot, content, image_name FROM messages WHERE id IN ({placeholders})",
                reply_ids,
            ).fetchall():
                reply_raw_map[rr["id"]] = dict(rr)
                if rr["user_id"]:
                    user_ids.append(rr["user_id"])

        users_map = auth_database.get_users_map(user_ids)
        ids = [r["id"] for r in rows]
        reactions_map = _attach_reactions(conn, "channel", ids, viewer_id)

        poll_ids = [r["poll_id"] for r in rows if "poll_id" in r.keys() and r["poll_id"]]
        poll_map: dict[int, dict[str, Any]] = {}
        for pid in set(poll_ids):
            p = get_poll_by_id(pid, viewer_id=viewer_id)
            if p:
                poll_map[pid] = p

        results: list[dict[str, Any]] = []
        term = search.strip().lower() if search and search.strip() else None
        for r in rows:
            rp_raw = reply_raw_map.get(r["reply_to_id"]) if r["reply_to_id"] else None
            rp = None
            if rp_raw:
                ru = users_map.get(rp_raw.get("user_id"))
                rp = {
                    "id": rp_raw["id"],
                    "alias": ru["alias"] if ru else rp_raw["author_alias_snapshot"],
                    "content": rp_raw["content"],
                    "image_name": rp_raw["image_name"],
                }
            p_data = poll_map.get(r["poll_id"]) if "poll_id" in r.keys() and r["poll_id"] else None
            formatted = _format_channel_row(
                r, users_map, reactions_map.get(r["id"], []), rp, for_admin, poll_data=p_data
            )
            if term:
                if term not in (formatted["content"] or "").lower() and term not in formatted["alias"].lower():
                    continue
            results.append(formatted)
        return results[-limit:]


def delete_channel_message(
    msg_id: int, requester_id: int, is_admin: bool
) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            return None
        if not is_admin and row["user_id"] != requester_id:
            raise PermissionError("No tienes permiso para borrar este mensaje.")
        image_url = row["image_url"]
        channel_id = row["channel_id"]
        conn.execute(
            "DELETE FROM reactions WHERE target_type = 'channel' AND target_id = ?",
            (msg_id,),
        )
        conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        return {"id": msg_id, "channel_id": channel_id, "image_url": image_url}


# --- FRIENDSHIPS & DIRECT MESSAGES ---

def _pair(u1: int, u2: int) -> tuple[int, int]:
    return (min(u1, u2), max(u1, u2))


def get_friendship_between(u1: int, u2: int) -> Optional[dict[str, Any]]:
    a, b = _pair(u1, u2)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM friendships WHERE user_a_id = ? AND user_b_id = ?", (a, b)
        ).fetchone()
        return dict(row) if row else None


def send_friend_request(from_id: int, to_id: int) -> dict[str, Any]:
    if from_id == to_id:
        raise ValueError("No puedes enviarte solicitud a ti mismo.")
    if not auth_database.get_user_by_id(to_id):
        raise ValueError("Usuario no encontrado.")
    a, b = _pair(from_id, to_id)
    now = time.time()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM friendships WHERE user_a_id = ? AND user_b_id = ?", (a, b)
        ).fetchone()
        if existing:
            if existing["status"] == "blocked":
                raise PermissionError("No puedes enviar solicitud a este usuario.")
            if existing["status"] == "accepted":
                return dict(existing)
            if existing["status"] == "pending" and existing["action_user_id"] == to_id:
                # Mutual request -> auto-accept!
                conn.execute(
                    "UPDATE friendships SET status = 'accepted', action_user_id = ?, updated_at = ? WHERE id = ?",
                    (from_id, now, existing["id"]),
                )
            else:
                conn.execute(
                    "UPDATE friendships SET status = 'pending', action_user_id = ?, updated_at = ? WHERE id = ?",
                    (from_id, now, existing["id"]),
                )
        else:
            conn.execute(
                """
                INSERT INTO friendships (user_a_id, user_b_id, status, action_user_id, created_at, updated_at)
                VALUES (?, ?, 'pending', ?, ?, ?)
                """,
                (a, b, from_id, now, now),
            )
        row = conn.execute(
            "SELECT * FROM friendships WHERE user_a_id = ? AND user_b_id = ?", (a, b)
        ).fetchone()
        return dict(row)


def respond_friendship(viewer_id: int, other_id: int, action: str) -> dict[str, Any]:
    a, b = _pair(viewer_id, other_id)
    now = time.time()
    status_map = {
        "accept": "accepted",
        "reject": "rejected",
        "block": "blocked",
        "unfriend": "unfriended",
    }
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM friendships WHERE user_a_id = ? AND user_b_id = ?", (a, b)
        ).fetchone()

        if action == "unblock":
            if not existing or existing["status"] != "blocked":
                raise ValueError("Este usuario no está bloqueado.")
            if existing["action_user_id"] != viewer_id:
                raise PermissionError("Solo quien bloqueó puede desbloquear.")
            conn.execute("DELETE FROM friendships WHERE id = ?", (existing["id"],))
            return {"status": "none"}

        if action not in status_map:
            raise ValueError("Acción inválida.")

        new_status = status_map[action]
        if existing:
            if existing["status"] == "blocked" and existing["action_user_id"] != viewer_id:
                raise PermissionError("No tienes permiso para modificar este bloqueo.")
            conn.execute(
                "UPDATE friendships SET status = ?, action_user_id = ?, updated_at = ? WHERE id = ?",
                (new_status, viewer_id, now, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO friendships (user_a_id, user_b_id, status, action_user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (a, b, new_status, viewer_id, now, now),
            )
        row = conn.execute(
            "SELECT * FROM friendships WHERE user_a_id = ? AND user_b_id = ?", (a, b)
        ).fetchone()
        return dict(row)


def can_send_dm(sender_id: int, receiver_id: int) -> tuple[bool, str]:
    if sender_id == receiver_id:
        return False, "No puedes enviarte DMs a ti mismo."
    rel = get_friendship_between(sender_id, receiver_id)
    if not rel:
        return True, ""
    st = rel["status"]
    if st == "blocked":
        return False, "No puedes enviar mensajes privados a este usuario."
    if st == "unfriended":
        return False, "Ya no son amigos. Envía una nueva solicitud de amistad para volver a chatear."
    if st == "pending" and rel["action_user_id"] != sender_id:
        return False, "Acepta o rechaza la solicitud de conversación primero."
    if st == "rejected" and rel["action_user_id"] == receiver_id:
        return False, "El usuario rechazó tu solicitud de conversación."
    return True, ""


def ensure_pending_dm_friendship(sender_id: int, receiver_id: int) -> None:
    """Create a pending request if none exists yet when initiating a DM."""
    a, b = _pair(sender_id, receiver_id)
    now = time.time()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM friendships WHERE user_a_id = ? AND user_b_id = ?", (a, b)
        ).fetchone()
        if not existing:
            conn.execute(
                """
                INSERT INTO friendships (user_a_id, user_b_id, status, action_user_id, created_at, updated_at)
                VALUES (?, ?, 'pending', ?, ?, ?)
                """,
                (a, b, sender_id, now, now),
            )


def get_user_relationships(user_id: int, for_admin: bool = False) -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM friendships WHERE user_a_id = ? OR user_b_id = ?",
            (user_id, user_id),
        ).fetchall()

        other_ids = [
            r["user_b_id"] if r["user_a_id"] == user_id else r["user_a_id"] for r in rows
        ]
        users_map = auth_database.get_users_map(other_ids)

        friends: list[dict[str, Any]] = []
        incoming_requests: list[dict[str, Any]] = []
        outgoing_requests: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        blocked_by_ids: list[int] = []

        for r in rows:
            other_id = r["user_b_id"] if r["user_a_id"] == user_id else r["user_a_id"]
            u = users_map.get(other_id)
            if not u:
                continue
            entry: dict[str, Any] = {
                "user_id": other_id,
                "alias": u["alias"],
                "avatar_color": u["avatar_color"],
                "status": r["status"],
                "updated_at": r["updated_at"],
            }
            if for_admin:
                entry["username"] = u["username"]

            if r["status"] == "accepted":
                friends.append(entry)
            elif r["status"] == "pending":
                # Fetch last DM preview if any
                last_dm = conn.execute(
                    """
                    SELECT content, image_name, created_at FROM direct_messages
                    WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)
                    ORDER BY id DESC LIMIT 1
                    """,
                    (user_id, other_id, other_id, user_id),
                ).fetchone()
                if last_dm:
                    entry["dm_preview"] = last_dm["content"] or f"[Imagen: {last_dm['image_name']}]"
                if r["action_user_id"] != user_id:
                    incoming_requests.append(entry)
                else:
                    outgoing_requests.append(entry)
            elif r["status"] == "blocked":
                if r["action_user_id"] == user_id:
                    blocked.append(entry)
                else:
                    blocked_by_ids.append(other_id)

        return {
            "friends": friends,
            "incoming_requests": incoming_requests,
            "outgoing_requests": outgoing_requests,
            "blocked": blocked,
            "blocked_by_ids": blocked_by_ids,
        }


def get_dms_conversations_summary(viewer_id: int, for_admin: bool = False) -> list[dict[str, Any]]:
    """Return DM peers ordered by most recent message activity, with unread counts and friendship status."""
    with get_conn() as conn:
        dm_rows = conn.execute(
            """
            SELECT * FROM direct_messages
            WHERE sender_id = ? OR receiver_id = ?
            ORDER BY id DESC
            """,
            (viewer_id, viewer_id),
        ).fetchall()

        f_rows = conn.execute(
            "SELECT * FROM friendships WHERE user_a_id = ? OR user_b_id = ?",
            (viewer_id, viewer_id),
        ).fetchall()

        f_map: dict[int, dict[str, Any]] = {}
        for f in f_rows:
            oid = f["user_b_id"] if f["user_a_id"] == viewer_id else f["user_a_id"]
            f_map[oid] = dict(f)

        # Aggregate per peer
        conv_map: dict[int, dict[str, Any]] = {}
        for d in dm_rows:
            peer_id = d["receiver_id"] if d["sender_id"] == viewer_id else d["sender_id"]
            if peer_id not in conv_map:
                conv_map[peer_id] = {
                    "user_id": peer_id,
                    "last_message": d["content"] or (f"[{d['image_name']}]" if d["image_name"] else ""),
                    "last_message_at": d["created_at"],
                    "unread_count": 0,
                }
            if d["receiver_id"] == viewer_id and d["is_read"] == 0:
                conv_map[peer_id]["unread_count"] += 1

        # Include accepted friends even if no messages sent yet
        for oid, f in f_map.items():
            if f["status"] == "accepted" and oid not in conv_map:
                conv_map[oid] = {
                    "user_id": oid,
                    "last_message": "",
                    "last_message_at": f["updated_at"],
                    "unread_count": 0,
                }

        users_map = auth_database.get_users_map(list(conv_map.keys()))
        result: list[dict[str, Any]] = []
        for peer_id, item in conv_map.items():
            f = f_map.get(peer_id)
            if f and f["status"] == "blocked":
                continue
            if f and f["status"] == "rejected" and f["action_user_id"] == viewer_id:
                continue
            u = users_map.get(peer_id)
            if not u:
                continue
            is_incoming_pending = bool(f and f["status"] == "pending" and f["action_user_id"] != viewer_id)
            out: dict[str, Any] = {
                "user_id": peer_id,
                "alias": u["alias"],
                "avatar_color": u["avatar_color"],
                "last_message": item["last_message"][:45],
                "last_message_at": item["last_message_at"],
                "unread_count": item["unread_count"],
                "friendship_status": f["status"] if f else "none",
                "is_incoming_request": is_incoming_pending,
            }
            if for_admin:
                out["username"] = u["username"]
            result.append(out)

        result.sort(key=lambda x: x["last_message_at"], reverse=True)
        return result


def mark_dm_as_read(viewer_id: int, friend_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE direct_messages SET is_read = 1 WHERE sender_id = ? AND receiver_id = ? AND is_read = 0",
            (friend_id, viewer_id),
        )


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
    is_nsfw: bool = False,
    reply_to_id: Optional[int] = None,
    media_type: str = "text",
    media_duration: Optional[float] = None,
    is_gif_mode: bool = False,
    poll_id: Optional[int] = None,
) -> int:
    ensure_pending_dm_friendship(sender_id, receiver_id)
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO direct_messages (
                sender_id, receiver_id, content, image_url, image_name,
                image_size_kb, image_dims, is_hd, expires_at, is_expired, is_nsfw,
                reply_to_id, is_read, created_at, media_type, media_duration, is_gif_mode, poll_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 0, ?, ?, ?, ?, ?)
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
                1 if is_nsfw else 0,
                reply_to_id,
                now,
                media_type,
                media_duration,
                1 if is_gif_mode else 0,
                poll_id,
            ),
        )
        return cur.lastrowid


def _format_dm_row(
    r: sqlite3.Row,
    users_map: dict[int, dict[str, Any]],
    reactions: list[dict[str, Any]],
    reply_preview: Optional[dict[str, Any]],
    for_admin: bool,
    poll_data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    u = users_map.get(r["sender_id"])
    media_type = r["media_type"] if ("media_type" in r.keys() and r["media_type"]) else ("image" if r["image_url"] else "text")
    msg: dict[str, Any] = {
        "id": r["id"],
        "sender_id": r["sender_id"],
        "receiver_id": r["receiver_id"],
        "user_id": r["sender_id"],
        "alias": u["alias"] if u else "Usuario Eliminado",
        "avatar_color": u["avatar_color"] if u else "#81a2be",
        "author_role": u["role"] if u else "member",
        "content": r["content"],
        "image_url": None if r["is_expired"] else r["image_url"],
        "image_name": r["image_name"],
        "image_size_kb": r["image_size_kb"],
        "image_dims": r["image_dims"],
        "is_hd": bool(r["is_hd"]),
        "expires_at": r["expires_at"],
        "is_expired": bool(r["is_expired"]),
        "is_nsfw": bool(r["is_nsfw"]) if "is_nsfw" in r.keys() else False,
        "media_type": media_type,
        "media_duration": r["media_duration"] if "media_duration" in r.keys() else None,
        "is_gif_mode": bool(r["is_gif_mode"]) if "is_gif_mode" in r.keys() else False,
        "poll_id": r["poll_id"] if "poll_id" in r.keys() else None,
        "poll": poll_data,
        "reply_to_id": r["reply_to_id"],
        "reply_preview": reply_preview,
        "reactions": reactions,
        "created_at": r["created_at"],
    }
    if for_admin and u is not None:
        msg["username"] = u["username"]
    return msg


def get_dm_by_id(
    dm_id: int, viewer_id: int, for_admin: bool = False
) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM direct_messages WHERE id = ?", (dm_id,)).fetchone()
        if not row:
            return None
        user_ids = [row["sender_id"], row["receiver_id"]]
        reply_preview = None
        if row["reply_to_id"]:
            rp = conn.execute(
                "SELECT id, sender_id, content, image_name FROM direct_messages WHERE id = ?",
                (row["reply_to_id"],),
            ).fetchone()
            if rp:
                user_ids.append(rp["sender_id"])
                reply_preview = dict(rp)

        users_map = auth_database.get_users_map(user_ids)
        if reply_preview:
            ru = users_map.get(reply_preview["sender_id"])
            reply_preview = {
                "id": reply_preview["id"],
                "alias": ru["alias"] if ru else "Usuario",
                "content": reply_preview["content"],
                "image_name": reply_preview["image_name"],
            }
        reactions_map = _attach_reactions(conn, "dm", [dm_id], viewer_id)
        poll_data = None
        if "poll_id" in row.keys() and row["poll_id"]:
            poll_data = get_poll_by_id(row["poll_id"], viewer_id=viewer_id)
        return _format_dm_row(
            row, users_map, reactions_map.get(dm_id, []), reply_preview, for_admin, poll_data=poll_data
        )


def get_direct_messages(
    user_a: int,
    user_b: int,
    viewer_id: int,
    for_admin: bool = False,
    limit: int = 150,
    search: Optional[str] = None,
) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM direct_messages
            WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)
            ORDER BY id DESC LIMIT ?
            """,
            (user_a, user_b, user_b, user_a, limit if not search else 500),
        ).fetchall()
        rows.reverse()

        user_ids = [user_a, user_b]
        reply_ids = list({r["reply_to_id"] for r in rows if r["reply_to_id"]})
        reply_raw_map: dict[int, dict[str, Any]] = {}
        if reply_ids:
            placeholders = ",".join("?" for _ in reply_ids)
            for rr in conn.execute(
                f"SELECT id, sender_id, content, image_name FROM direct_messages WHERE id IN ({placeholders})",
                reply_ids,
            ).fetchall():
                reply_raw_map[rr["id"]] = dict(rr)
                if rr["sender_id"]:
                    user_ids.append(rr["sender_id"])

        users_map = auth_database.get_users_map(user_ids)
        ids = [r["id"] for r in rows]
        reactions_map = _attach_reactions(conn, "dm", ids, viewer_id)

        poll_ids = [r["poll_id"] for r in rows if "poll_id" in r.keys() and r["poll_id"]]
        poll_map: dict[int, dict[str, Any]] = {}
        for pid in set(poll_ids):
            p = get_poll_by_id(pid, viewer_id=viewer_id)
            if p:
                poll_map[pid] = p

        results: list[dict[str, Any]] = []
        term = search.strip().lower() if search and search.strip() else None
        for r in rows:
            rp_raw = reply_raw_map.get(r["reply_to_id"]) if r["reply_to_id"] else None
            rp = None
            if rp_raw:
                ru = users_map.get(rp_raw.get("sender_id"))
                rp = {
                    "id": rp_raw["id"],
                    "alias": ru["alias"] if ru else "Usuario",
                    "content": rp_raw["content"],
                    "image_name": rp_raw["image_name"],
                }
            p_data = poll_map.get(r["poll_id"]) if "poll_id" in r.keys() and r["poll_id"] else None
            formatted = _format_dm_row(
                r, users_map, reactions_map.get(r["id"], []), rp, for_admin, poll_data=p_data
            )
            if term:
                if term not in (formatted["content"] or "").lower() and term not in formatted["alias"].lower():
                    continue
            results.append(formatted)
        return results[-limit:]


def delete_direct_message(
    dm_id: int, requester_id: int, is_admin: bool
) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM direct_messages WHERE id = ?", (dm_id,)).fetchone()
        if not row:
            return None
        if not is_admin and row["sender_id"] != requester_id:
            raise PermissionError("No tienes permiso para borrar este mensaje.")
        image_url = row["image_url"]
        sender_id = row["sender_id"]
        receiver_id = row["receiver_id"]
        conn.execute(
            "DELETE FROM reactions WHERE target_type = 'dm' AND target_id = ?", (dm_id,)
        )
        conn.execute("DELETE FROM direct_messages WHERE id = ?", (dm_id,))
        return {
            "id": dm_id,
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "image_url": image_url,
        }


def admin_list_all_dm_threads() -> list[dict[str, Any]]:
    """Return overview of all private DM pairs for Admin moderation."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT MIN(sender_id, receiver_id) as u1,
                   MAX(sender_id, receiver_id) as u2,
                   COUNT(*) as msg_count,
                   MAX(created_at) as last_at
            FROM direct_messages
            GROUP BY u1, u2
            ORDER BY last_at DESC
            """
        ).fetchall()
        user_ids: list[int] = []
        for r in rows:
            user_ids.extend([r["u1"], r["u2"]])
        users_map = auth_database.get_users_map(user_ids)
        threads = []
        for r in rows:
            ua = users_map.get(r["u1"])
            ub = users_map.get(r["u2"])
            if not ua or not ub:
                continue
            threads.append(
                {
                    "user_a_id": r["u1"],
                    "user_a_alias": ua["alias"],
                    "user_a_username": ua["username"],
                    "user_b_id": r["u2"],
                    "user_b_alias": ub["alias"],
                    "user_b_username": ub["username"],
                    "msg_count": r["msg_count"],
                    "last_at": r["last_at"],
                }
            )
        return threads


def toggle_reaction(
    target_type: str, target_id: int, user_id: int, emoji: str
) -> list[dict[str, Any]]:
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
    """Return all text messages across channels and DMs using live users_map from auth_users.db."""
    with get_conn() as conn:
        channels = conn.execute("SELECT * FROM channels ORDER BY id ASC").fetchall()
        raw_msgs = conn.execute("SELECT * FROM messages ORDER BY id ASC").fetchall()
        raw_dms = conn.execute("SELECT * FROM direct_messages ORDER BY id ASC").fetchall()

    user_ids: list[int] = []
    for m in raw_msgs:
        if m["user_id"]:
            user_ids.append(m["user_id"])
    for d in raw_dms:
        user_ids.extend([d["sender_id"], d["receiver_id"]])
    users_map = auth_database.get_users_map(user_ids)

    msgs_by_ch: dict[int, list[dict[str, Any]]] = {ch["id"]: [] for ch in channels}
    for m in raw_msgs:
        u = users_map.get(m["user_id"]) if m["user_id"] else None
        p_id = m["poll_id"] if "poll_id" in m.keys() else None
        p_data = get_poll_by_id(p_id) if p_id else None
        msgs_by_ch.setdefault(m["channel_id"], []).append(
            {
                "id": m["id"],
                "content": m["content"],
                "created_at": m["created_at"],
                "is_system": m["is_system"],
                "media_type": m["media_type"] if "media_type" in m.keys() else "text",
                "poll": p_data,
                "username": u["username"] if u else m["author_alias_snapshot"],
                "alias": u["alias"] if u else m["author_alias_snapshot"],
            }
        )

    channel_logs = [
        {"channel": dict(ch), "messages": msgs_by_ch.get(ch["id"], [])} for ch in channels
    ]

    dm_logs = []
    for d in raw_dms:
        su = users_map.get(d["sender_id"])
        ru = users_map.get(d["receiver_id"])
        p_id = d["poll_id"] if "poll_id" in d.keys() else None
        p_data = get_poll_by_id(p_id) if p_id else None
        dm_logs.append(
            {
                "id": d["id"],
                "content": d["content"],
                "created_at": d["created_at"],
                "media_type": d["media_type"] if "media_type" in d.keys() else "text",
                "poll": p_data,
                "sender_username": su["username"] if su else f"user_{d['sender_id']}",
                "sender_alias": su["alias"] if su else f"user_{d['sender_id']}",
                "receiver_username": ru["username"] if ru else f"user_{d['receiver_id']}",
                "receiver_alias": ru["alias"] if ru else f"user_{d['receiver_id']}",
            }
        )

    return {"channels": channel_logs, "dms": dm_logs}


def reset_all_messages_for_new_month(new_ym: str) -> list[str]:
    """Delete all messages, DMs, reactions, keeping friendships and auth_users.db 100% intact."""
    image_paths: list[str] = []
    with get_conn() as conn:
        for r in conn.execute("SELECT image_url FROM messages WHERE image_url IS NOT NULL").fetchall():
            image_paths.append(r["image_url"])
        for r in conn.execute("SELECT image_url FROM direct_messages WHERE image_url IS NOT NULL").fetchall():
            image_paths.append(r["image_url"])

        conn.execute("DELETE FROM reactions")
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM direct_messages")
        conn.execute("DELETE FROM poll_votes")
        conn.execute("DELETE FROM poll_options")
        conn.execute("DELETE FROM polls")
        conn.execute(
            "INSERT INTO system_settings (key, value) VALUES ('last_backup_ym', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (new_ym,),
        )
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


# --- RADIO STREAM SETTINGS ---

def get_radio_stream() -> dict[str, Any]:
    """Retrieve current shared radio stream configuration."""
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM system_settings WHERE key LIKE 'radio_%'").fetchall()
        data = {r["key"]: r["value"] for r in rows}
        return {
            "title": data.get("radio_stream_title", "ConectFm"),
            "url": data.get("radio_stream_url", ""),
            "active": data.get("radio_active", "0") in ("1", "true", "True"),
        }


def set_radio_stream(url: str, title: str = "ConectFm", active: bool = True) -> dict[str, Any]:
    """Set the radio stream URL, station title, and active state (Admin only)."""
    clean_title = (title or "ConectFm").strip()
    clean_url = (url or "").strip()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO system_settings (key, value) VALUES ('radio_stream_title', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (clean_title,),
        )
        conn.execute(
            "INSERT INTO system_settings (key, value) VALUES ('radio_stream_url', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (clean_url,),
        )
        conn.execute(
            "INSERT INTO system_settings (key, value) VALUES ('radio_active', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("1" if active else "0",),
        )
    return {
        "title": clean_title,
        "url": clean_url,
        "active": bool(active),
    }


# --- POLLS (ENCUESTAS) ---

def create_poll(
    channel_id: int,
    creator_id: int,
    question: str,
    options: list[str],
) -> dict[str, Any]:
    """Create a new poll in a channel and publish the corresponding message."""
    q = (question or "").strip()
    if not q:
        raise ValueError("La pregunta de la encuesta no puede estar vacía.")
    clean_options = [opt.strip() for opt in options if opt and opt.strip()]
    if len(clean_options) < 2:
        raise ValueError("La encuesta debe tener al menos 2 opciones.")
    if len(clean_options) > 5:
        raise ValueError("La encuesta puede tener un máximo de 5 opciones.")

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO polls (channel_id, creator_id, question, is_closed, created_at) VALUES (?, ?, ?, 0, ?)",
            (channel_id, creator_id, q, now),
        )
        poll_id = cur.lastrowid
        for opt_text in clean_options:
            conn.execute(
                "INSERT INTO poll_options (poll_id, option_text, votes_count) VALUES (?, ?, 0)",
                (poll_id, opt_text),
            )

        u = auth_database.get_user_by_id(creator_id)
        alias = u["alias"] if u else "Sistema"
        m_cur = conn.execute(
            """
            INSERT INTO messages (
                channel_id, user_id, author_alias_snapshot, is_system, content,
                media_type, poll_id, created_at
            ) VALUES (?, ?, ?, 0, ?, 'poll', ?, ?)
            """,
            (channel_id, creator_id, alias, f"📊 [Encuesta] {q}", poll_id, now),
        )

    res = get_poll_by_id(poll_id, viewer_id=creator_id)
    if not res:
        raise RuntimeError("Error al recuperar la encuesta recién creada.")
    return res


def get_poll_by_id(poll_id: int, viewer_id: Optional[int] = None) -> Optional[dict[str, Any]]:
    """Retrieve full details of a poll including percentage calculation and viewer vote status."""
    if not poll_id:
        return None
    with get_conn() as conn:
        p = conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
        if not p:
            return None
        opts = conn.execute(
            "SELECT id, option_text, votes_count FROM poll_options WHERE poll_id = ? ORDER BY id ASC",
            (poll_id,),
        ).fetchall()

        total_votes = sum(o["votes_count"] for o in opts)
        options_data = []
        for o in opts:
            pct = round((o["votes_count"] / total_votes * 100), 1) if total_votes > 0 else 0.0
            options_data.append({
                "id": o["id"],
                "text": o["option_text"],
                "votes": o["votes_count"],
                "percentage": pct,
            })

        user_voted_id = None
        if viewer_id is not None:
            v = conn.execute(
                "SELECT option_id FROM poll_votes WHERE poll_id = ? AND user_id = ?",
                (poll_id, viewer_id),
            ).fetchone()
            if v:
                user_voted_id = v["option_id"]

        u = auth_database.get_user_by_id(p["creator_id"])
        return {
            "id": p["id"],
            "channel_id": p["channel_id"],
            "creator_id": p["creator_id"],
            "creator_alias": u["alias"] if u else "Usuario",
            "question": p["question"],
            "is_closed": bool(p["is_closed"]),
            "created_at": p["created_at"],
            "total_votes": total_votes,
            "options": options_data,
            "user_voted_option_id": user_voted_id,
        }


def vote_poll(poll_id: int, user_id: int, option_id: int) -> dict[str, Any]:
    """Cast or update a vote on a poll."""
    with get_conn() as conn:
        p = conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
        if not p:
            raise ValueError("Encuesta no encontrada.")
        if p["is_closed"]:
            raise ValueError("Esta encuesta ya está cerrada y no acepta más votos.")

        opt = conn.execute(
            "SELECT id FROM poll_options WHERE id = ? AND poll_id = ?",
            (option_id, poll_id),
        ).fetchone()
        if not opt:
            raise ValueError("Opción no válida para esta encuesta.")

        now = time.time()
        conn.execute(
            """
            INSERT INTO poll_votes (poll_id, user_id, option_id, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(poll_id, user_id) DO UPDATE SET option_id = excluded.option_id, created_at = excluded.created_at
            """,
            (poll_id, user_id, option_id, now),
        )

        opts = conn.execute("SELECT id FROM poll_options WHERE poll_id = ?", (poll_id,)).fetchall()
        for o in opts:
            cnt = conn.execute(
                "SELECT COUNT(*) as c FROM poll_votes WHERE option_id = ?",
                (o["id"],),
            ).fetchone()["c"]
            conn.execute(
                "UPDATE poll_options SET votes_count = ? WHERE id = ?",
                (cnt, o["id"]),
            )

    res = get_poll_by_id(poll_id, viewer_id=user_id)
    if not res:
        raise RuntimeError("Error al recuperar la encuesta actualizada.")
    return res


def close_poll(poll_id: int, user_id: int, is_admin: bool) -> dict[str, Any]:
    """Close a poll so no more votes can be cast."""
    with get_conn() as conn:
        p = conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
        if not p:
            raise ValueError("Encuesta no encontrada.")
        if not is_admin and p["creator_id"] != user_id:
            raise PermissionError("Solo el creador o un administrador pueden cerrar esta encuesta.")
        conn.execute("UPDATE polls SET is_closed = 1 WHERE id = ?", (poll_id,))

    res = get_poll_by_id(poll_id, viewer_id=user_id)
    if not res:
        raise RuntimeError("Error al recuperar la encuesta cerrada.")
    return res


# --- MEDIA CLEANUP (RETENTION 30 DAYS WITH PINNED EXEMPTION) ---

def cleanup_expired_media(max_age_days: int = 30) -> dict[str, Any]:
    """Delete unpinned images, videos, and audios older than max_age_days."""
    cutoff = time.time() - (max_age_days * 86400)
    cleaned = 0
    with get_conn() as conn:
        pinned_rows = conn.execute("SELECT pinned_message_id FROM channels WHERE pinned_message_id IS NOT NULL").fetchall()
        pinned_ids = {r["pinned_message_id"] for r in pinned_rows}

        rows = conn.execute(
            "SELECT id, image_url, media_type FROM messages WHERE created_at < ? AND image_url IS NOT NULL AND is_expired = 0",
            (cutoff,),
        ).fetchall()

        for r in rows:
            if r["id"] in pinned_ids:
                continue
            media_url = r["image_url"]
            if media_url:
                try:
                    import media_service
                    media_service.delete_media_file(media_url)
                except Exception:
                    pass
                conn.execute("UPDATE messages SET is_expired = 1, image_url = NULL WHERE id = ?", (r["id"],))
                cleaned += 1

    return {"cleaned_count": cleaned}

