import base64
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

import auth_database
import database
import image_service

BACKUP_DIR = Path(os.environ.get("DELTACHAT_BACKUPS", "backups"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
USERS_BACKUP_DIR = BACKUP_DIR / "users"
USERS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)

THREE_DAYS_SECONDS = 3 * 24 * 60 * 60


def format_backup_text(date_str: str) -> str:
    """Build a plain-text log of all messages and DMs (username, date, message text; no attachments)."""
    data = database.fetch_all_messages_for_backup()
    lines = [
        "================================================================",
        "DELTACHAT - RESPALDO MENSUAL DE CONVERSACIONES",
        f"Fecha de respaldo (MM-DD-YYYY): {date_str}",
        "Nota: Solo contiene usuario, fecha y texto (sin archivos adjuntos).",
        "================================================================",
        "",
    ]

    for ch_entry in data["channels"]:
        ch = ch_entry["channel"]
        lines.append(f"--- TABLON: /{ch['slug']}/ ({ch['name']}) ---")
        msgs = ch_entry["messages"]
        if not msgs:
            lines.append("  (Sin mensajes en este tablón)")
        for m in msgs:
            if m["is_system"]:
                continue
            dt = datetime.fromtimestamp(m["created_at"]).strftime("%m-%d-%Y %H:%M:%S")
            text = (m["content"] or "").strip()
            if not text:
                continue
            user_label = m["username"]
            if m["alias"] and m["alias"] != m["username"]:
                user_label = f"{m['username']} (alias: {m['alias']})"
            lines.append(f"[{dt}] {user_label}: {text}")
        lines.append("")

    dms = data["dms"]
    if dms:
        lines.append("--- MENSAJES DIRECTOS PRIVADOS (DMs) ---")
        for d in dms:
            text = (d["content"] or "").strip()
            if not text:
                continue
            dt = datetime.fromtimestamp(d["created_at"]).strftime("%m-%d-%Y %H:%M:%S")
            lines.append(
                f"[{dt}] {d['sender_username']} -> {d['receiver_username']}: {text}"
            )
        lines.append("")

    return "\n".join(lines)


def push_to_github(repo_path: str, content_str: str, commit_message: str) -> dict[str, Any]:
    """Upload a file to the configured GitHub repository under repo_path."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    if not token or not repo:
        return {
            "pushed": False,
            "url": None,
            "error": "Variables GITHUB_TOKEN / GITHUB_REPO no configuradas.",
        }

    api_url = f"https://api.github.com/repos/{repo}/contents/{repo_path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    sha: Optional[str] = None
    try:
        get_resp = requests.get(api_url, headers=headers, timeout=15)
        if get_resp.status_code == 200:
            sha = get_resp.json().get("sha")
    except Exception:
        pass

    encoded = base64.b64encode(content_str.encode("utf-8")).decode("ascii")
    payload: dict[str, Any] = {
        "message": commit_message,
        "content": encoded,
    }
    if sha:
        payload["sha"] = sha

    try:
        put_resp = requests.put(api_url, headers=headers, json=payload, timeout=20)
        if put_resp.status_code in (200, 201):
            html_url = put_resp.json().get("content", {}).get("html_url")
            return {"pushed": True, "url": html_url, "error": None}
        return {
            "pushed": False,
            "url": None,
            "error": f"GitHub API HTTP {put_resp.status_code}: {put_resp.text[:200]}",
        }
    except Exception as exc:
        return {"pushed": False, "url": None, "error": str(exc)}


def run_users_db_backup(force: bool = False) -> dict[str, Any]:
    """Run local backup of auth_users.db every 3 days (or on demand)."""
    USERS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    now_ts = time.time()
    last_ts_str = auth_database.get_auth_setting("last_users_backup_ts")
    if not force and last_ts_str:
        try:
            if (now_ts - float(last_ts_str)) < THREE_DAYS_SECONDS:
                return {"executed": False, "reason": "Aún no se cumplen 3 días desde la última copia."}
        except ValueError:
            pass

    date_str = datetime.now().strftime("%m-%d-%Y")
    json_name = f"users_backup_{date_str}.json"
    json_content = auth_database.export_users_db_json()
    json_path = USERS_BACKUP_DIR / json_name
    json_path.write_text(json_content, encoding="utf-8")

    db_name = f"users_backup_{date_str}.db"
    db_src = Path(auth_database.AUTH_DB_PATH)
    if db_src.is_file():
        shutil.copy2(db_src, USERS_BACKUP_DIR / db_name)

    auth_database.set_auth_setting("last_users_backup_ts", str(now_ts))
    return {
        "executed": True,
        "json_filename": json_name,
        "db_filename": db_name,
        "local_path": str(json_path),
    }


def run_backup_and_reset(force: bool = False) -> dict[str, Any]:
    """Execute monthly backup (messages + users JSON to local + GitHub) and wipe chat history."""
    now = datetime.now()
    current_ym = now.strftime("%Y-%m")
    last_ym = database.get_setting("last_backup_ym")

    if not force and last_ym == current_ym:
        return {"executed": False, "reason": "El mes actual ya está activo."}

    date_str = now.strftime("%m-%d-%Y")
    filename = f"backup_{date_str}.txt"
    backup_content = format_backup_text(date_str)

    local_path = BACKUP_DIR / filename
    local_path.write_text(backup_content, encoding="utf-8")

    gh_result = push_to_github(
        f"backups/{filename}", backup_content, f"DeltaChat respaldo mensual {date_str}"
    )

    # Also backup users JSON locally and to GitHub (auth_users.db is NEVER reset!)
    users_res = run_users_db_backup(force=True)
    users_json = auth_database.export_users_db_json()
    gh_users_result = push_to_github(
        f"backups/users/{users_res['json_filename']}",
        users_json,
        f"DeltaChat respaldo usuarios {date_str}",
    )

    old_images = database.reset_all_messages_for_new_month(current_ym)
    for img_url in old_images:
        image_service.delete_upload_file(img_url)

    return {
        "executed": True,
        "filename": filename,
        "local_path": str(local_path),
        "github": gh_result,
        "users_backup": users_res,
        "github_users": gh_users_result,
    }


def list_local_backups() -> list[str]:
    files = sorted(BACKUP_DIR.glob("backup_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [f.name for f in files]


def list_users_backups() -> list[str]:
    USERS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(
        [p for p in USERS_BACKUP_DIR.iterdir() if p.is_file() and p.suffix in (".json", ".db")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [f.name for f in files]
