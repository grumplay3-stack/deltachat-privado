import base64
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

import database
import image_service

BACKUP_DIR = Path(os.environ.get("DELTACHAT_BACKUPS", "backups"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def format_backup_text(date_str: str) -> str:
    """Build a plain-text log of all messages (username, date, message text; no attachments)."""
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


def push_to_github(filename: str, content_str: str, date_str: str) -> dict[str, Any]:
    """Upload the backup file to the configured GitHub repository."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    if not token or not repo:
        return {
            "pushed": False,
            "url": None,
            "error": "Variables GITHUB_TOKEN / GITHUB_REPO no configuradas.",
        }

    api_url = f"https://api.github.com/repos/{repo}/contents/backups/{filename}"
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
        "message": f"DeltaChat respaldo mensual {date_str}",
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


def run_backup_and_reset(force: bool = False) -> dict[str, Any]:
    """Execute monthly backup (to local file + GitHub) and wipe/reset chat history."""
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

    gh_result = push_to_github(filename, backup_content, date_str)

    # Clear all messages and delete uploaded media files
    old_images = database.reset_all_messages_for_new_month(current_ym)
    for img_url in old_images:
        image_service.delete_upload_file(img_url)

    return {
        "executed": True,
        "filename": filename,
        "local_path": str(local_path),
        "github": gh_result,
    }


def list_local_backups() -> list[str]:
    files = sorted(BACKUP_DIR.glob("backup_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [f.name for f in files]
