import io
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test_deltachat.db"
    uploads_dir = tmp_path / "uploads"
    backups_dir = tmp_path / "backups"
    uploads_dir.mkdir()
    backups_dir.mkdir()

    monkeypatch.setenv("DELTACHAT_DB", str(db_file))
    monkeypatch.setenv("DELTACHAT_UPLOADS", str(uploads_dir))
    monkeypatch.setenv("DELTACHAT_BACKUPS", str(backups_dir))

    import database
    import github_backup
    import image_service
    import main

    database.DB_PATH = str(db_file)
    image_service.UPLOAD_DIR = uploads_dir
    github_backup.BACKUP_DIR = backups_dir

    with TestClient(main.app) as c:
        yield c, uploads_dir, backups_dir


def _make_sample_png(width=1600, height=1200) -> bytes:
    img = Image.new("RGB", (width, height), color=(120, 180, 90))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_admin_login_and_alias_privacy(client):
    c, _, _ = client
    # Reserved admin username cannot be registered
    r_dup = c.post(
        "/api/register",
        json={
            "username": "dabo777",
            "alias": "Fake",
            "password": "test",
            "security_question": "pregunta?",
            "security_answer": "respuesta",
        },
    )
    assert r_dup.status_code == 409

    # Real admin logs in
    r_admin = c.post("/api/login", json={"username": "dabo777", "password": "52952416"})
    assert r_admin.status_code == 200
    admin_token = r_admin.json()["token"]
    assert r_admin.json()["user"]["role"] == "admin"

    # Admin updates alias
    r_alias = c.put(
        "/api/profile/alias",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alias": "AnonLider"},
    )
    assert r_alias.status_code == 200

    # Register normal friend
    r_friend = c.post(
        "/api/register",
        json={
            "username": "carlos_real",
            "alias": "ElCarlos",
            "password": "secret",
            "security_question": "mascota?",
            "security_answer": "firulais",
        },
    )
    assert r_friend.status_code == 200
    friend_token = r_friend.json()["token"]

    # Member sees ONLY aliases, NEVER usernames or IPs
    r_users_member = c.get("/api/users", headers={"Authorization": f"Bearer {friend_token}"})
    assert r_users_member.status_code == 200
    for u in r_users_member.json()["users"]:
        assert "username" not in u
        assert "last_ip" not in u

    # Admin sees BOTH alias and real username
    r_users_admin = c.get("/api/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert r_users_admin.status_code == 200
    usernames = {u.get("username") for u in r_users_admin.json()["users"]}
    assert "carlos_real" in usernames
    assert "dabo777" in usernames


def test_bans_permaban_temp_and_unban(client):
    c, _, _ = client
    admin_token = c.post("/api/login", json={"username": "dabo777", "password": "52952416"}).json()["token"]
    r_friend = c.post(
        "/api/register",
        json={
            "username": "troll1",
            "alias": "Troll",
            "password": "pass",
            "security_question": "pregunta?",
            "security_answer": "respuesta",
        },
        headers={"X-Forwarded-For": "203.0.113.55"},
    )
    assert r_friend.status_code == 200
    friend_id = r_friend.json()["user"]["id"]
    friend_token = r_friend.json()["token"]

    # Apply temporary suspension (15 min)
    r_ban = c.post(
        "/api/admin/bans",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "target_user_id": friend_id,
            "ban_type": "temp",
            "duration_minutes": 15,
            "reason": "Flood en /gen/",
        },
    )
    assert r_ban.status_code == 200
    ban_id = r_ban.json()["ban"]["id"]

    # Banned user cannot login or register from that IP
    r_login_blocked = c.post(
        "/api/login",
        json={"username": "troll1", "password": "pass"},
        headers={"X-Forwarded-For": "203.0.113.55"},
    )
    assert r_login_blocked.status_code == 403
    assert "Suspensión temporal" in r_login_blocked.json()["detail"]

    # Admin removes ban
    r_unban = c.delete(
        f"/api/admin/bans/{ban_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r_unban.status_code == 200

    # User can login again
    r_login_ok = c.post(
        "/api/login",
        json={"username": "troll1", "password": "pass"},
        headers={"X-Forwarded-For": "203.0.113.55"},
    )
    assert r_login_ok.status_code == 200


def test_image_compression_and_hd_expiration(client):
    c, uploads_dir, _ = client
    token = c.post("/api/login", json={"username": "dabo777", "password": "52952416"}).json()["token"]

    # Standard compressed image
    png_bytes = _make_sample_png()
    r_std = c.post(
        "/api/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("foto.png", png_bytes, "image/png")},
        data={"is_hd": "false"},
    )
    assert r_std.status_code == 200
    meta_std = r_std.json()
    assert meta_std["is_hd"] is False
    assert meta_std["image_size_kb"] <= 300

    # HD ephemeral image
    r_hd = c.post(
        "/api/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("hd_foto.png", png_bytes, "image/png")},
        data={"is_hd": "true"},
    )
    assert r_hd.status_code == 200
    meta_hd = r_hd.json()
    assert meta_hd["is_hd"] is True
    assert meta_hd["expires_at"] is not None

    # Post HD image via WebSocket
    with c.websocket_connect(f"/ws?token={token}") as ws:
        _ = ws.receive_json()  # presence
        _ = ws.receive_json()  # system notice
        ws.send_json(
            {
                "action": "send_channel_message",
                "channel_id": 1,
                "content": ">mira esta foto HD",
                "image": meta_hd,
            }
        )
        msg_event = ws.receive_json()
        assert msg_event["type"] == "new_channel_message"
        msg_id = msg_event["message"]["id"]

    hd_filename = Path(meta_hd["image_url"]).name
    assert (uploads_dir / hd_filename).exists()

    # Fast-forward 11 minutes to trigger HD expiration cleanup
    import database
    import image_service

    expired = database.fetch_and_expire_hd_images(now=time.time() + 700)
    assert len(expired["channels"]) == 1
    image_service.delete_upload_file(expired["channels"][0]["image_url"])
    assert not (uploads_dir / hd_filename).exists()

    r_msgs = c.get("/api/messages/1", headers={"Authorization": f"Bearer {token}"})
    target = [m for m in r_msgs.json()["messages"] if m["id"] == msg_id][0]
    assert target["is_expired"] is True
    assert target["image_url"] is None


def test_monthly_backup_and_reset(client):
    c, _, backups_dir = client
    admin_token = c.post("/api/login", json={"username": "dabo777", "password": "52952416"}).json()["token"]
    r_friend = c.post(
        "/api/register",
        json={
            "username": "amigu1",
            "alias": "Pepe",
            "password": "pass",
            "security_question": "pregunta?",
            "security_answer": "respuesta",
        },
    )
    friend_token = r_friend.json()["token"]

    with c.websocket_connect(f"/ws?token={friend_token}") as ws:
        _ = ws.receive_json()
        _ = ws.receive_json()
        ws.send_json({"action": "send_channel_message", "channel_id": 1, "content": "Hola amigus del mes!"})
        _ = ws.receive_json()

    r_backup = c.post("/api/admin/backup-now", headers={"Authorization": f"Bearer {admin_token}"})
    assert r_backup.status_code == 200
    data = r_backup.json()
    assert data["executed"] is True
    assert data["filename"].startswith("backup_")

    backup_text = (backups_dir / data["filename"]).read_text(encoding="utf-8")
    assert "amigu1" in backup_text
    assert "Hola amigus del mes!" in backup_text

    # Chat should be reset with welcome system message
    r_msgs = c.get("/api/messages/1", headers={"Authorization": f"Bearer {admin_token}"})
    msgs = r_msgs.json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["is_system"] is True
    assert "Nuevo mes iniciado" in msgs[0]["content"]
