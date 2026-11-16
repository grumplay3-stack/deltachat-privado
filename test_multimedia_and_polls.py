import io
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import auth_database
import database
import github_backup
import media_service
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_delta.db"
    test_auth_db = tmp_path / "test_auth.db"
    test_uploads = tmp_path / "test_uploads"
    test_uploads.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(database, "DB_PATH", str(test_db))
    monkeypatch.setattr(auth_database, "AUTH_DB_PATH", str(test_auth_db))
    monkeypatch.setattr(media_service, "UPLOAD_DIR", test_uploads)

    auth_database.init_auth_db()
    database.init_db()


def get_admin_token():
    auth_database.init_auth_db()
    admin = auth_database.get_user_by_username("dabo777")
    return auth_database.create_session(admin["id"], "127.0.0.1", "test-agent")


def create_normal_user(username="charlie"):
    user = auth_database.create_user(
        username=username,
        alias=username.capitalize(),
        password="password123",
        security_question="Animal favorito?",
        security_answer="gato",
        avatar_color="#81a2be",
        ip_address="127.0.0.1",
        user_agent="test-agent",
    )
    token = auth_database.create_session(user["id"], "127.0.0.1", "test-agent")
    return user, token


def test_poll_lifecycle_and_voting():
    admin_tok = get_admin_token()
    user, user_tok = create_normal_user("pepe")

    # 1. Create Poll
    resp = client.post(
        "/api/polls",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={
            "channel_id": 1,
            "question": "¿Qué música ponemos en la radio?",
            "options": ["Rock Clásico", "Synthwave", "Electrónica"],
        },
    )
    assert resp.status_code == 200, resp.text
    poll = resp.json()["poll"]
    assert poll["question"] == "¿Qué música ponemos en la radio?"
    assert len(poll["options"]) == 3
    assert poll["total_votes"] == 0
    poll_id = poll["id"]
    opt1_id = poll["options"][0]["id"]
    opt2_id = poll["options"][1]["id"]

    # 2. Vote as user
    v_resp = client.post(
        f"/api/polls/{poll_id}/vote",
        headers={"Authorization": f"Bearer {user_tok}"},
        json={"option_id": opt1_id},
    )
    assert v_resp.status_code == 200
    p_after = v_resp.json()["poll"]
    assert p_after["total_votes"] == 1
    assert p_after["options"][0]["votes"] == 1
    assert p_after["options"][0]["percentage"] == 100.0
    assert p_after["user_voted_option_id"] == opt1_id

    # 3. Change vote
    v_resp2 = client.post(
        f"/api/polls/{poll_id}/vote",
        headers={"Authorization": f"Bearer {user_tok}"},
        json={"option_id": opt2_id},
    )
    assert v_resp2.status_code == 200
    p_after2 = v_resp2.json()["poll"]
    assert p_after2["total_votes"] == 1
    assert p_after2["options"][0]["votes"] == 0
    assert p_after2["options"][1]["votes"] == 1
    assert p_after2["options"][1]["percentage"] == 100.0

    # 4. Close poll
    c_resp = client.post(
        f"/api/polls/{poll_id}/close",
        headers={"Authorization": f"Bearer {admin_tok}"},
    )
    assert c_resp.status_code == 200
    assert c_resp.json()["poll"]["is_closed"] is True

    # 5. Cannot vote when closed
    v_closed = client.post(
        f"/api/polls/{poll_id}/vote",
        headers={"Authorization": f"Bearer {user_tok}"},
        json={"option_id": opt1_id},
    )
    assert v_closed.status_code == 400
    assert "cerrada" in v_closed.json()["detail"].lower()


def test_poll_included_in_chat_backup():
    admin_tok = get_admin_token()
    resp = client.post(
        "/api/polls",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={
            "channel_id": 1,
            "question": "¿Cuál es la mejor consola?",
            "options": ["PlayStation", "Xbox", "Nintendo"],
        },
    )
    assert resp.status_code == 200
    poll_id = resp.json()["poll"]["id"]
    opt_id = resp.json()["poll"]["options"][0]["id"]

    # Vote
    client.post(
        f"/api/polls/{poll_id}/vote",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={"option_id": opt_id},
    )

    # Format backup text
    backup_text = github_backup.format_backup_text("11-16-2026")
    assert "[ENCUESTA] ¿Cuál es la mejor consola?" in backup_text
    assert "PlayStation" in backup_text
    assert "Total votos: 1" in backup_text


def test_radio_stream_settings():
    admin_tok = get_admin_token()
    _, user_tok = create_normal_user("radiofan")

    # Get initial radio
    r_get = client.get("/api/radio")
    assert r_get.status_code == 200
    assert r_get.json()["title"] == "ConectFm"

    # Non-admin cannot set radio
    r_forbidden = client.post(
        "/api/admin/radio",
        headers={"Authorization": f"Bearer {user_tok}"},
        json={"url": "https://stream.example.com/live", "title": "Radio Retro", "active": True},
    )
    assert r_forbidden.status_code == 403

    # Admin sets radio
    r_set = client.post(
        "/api/admin/radio",
        headers={"Authorization": f"Bearer {admin_tok}"},
        json={"url": "https://stream.example.com/live", "title": "Radio Delta FM", "active": True},
    )
    assert r_set.status_code == 200
    assert r_set.json()["title"] == "Radio Delta FM"
    assert r_set.json()["url"] == "https://stream.example.com/live"
    assert r_set.json()["active"] is True

    # Check updated GET
    r_check = client.get("/api/radio")
    assert r_check.json()["title"] == "Radio Delta FM"


def test_media_upload_video_and_audio():
    admin_tok = get_admin_token()

    # Generate small dummy video bytes and thumbnail
    dummy_video = b"RIFF....AVI " + (b"\x00" * 1024)  # ~1 KB

    # Generate dummy WebP thumbnail image
    img = Image.new("RGB", (100, 100), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    thumb_bytes = buf.getvalue()

    resp = client.post(
        "/api/upload-media",
        headers={"Authorization": f"Bearer {admin_tok}"},
        files={
            "file": ("clip.mp4", dummy_video, "video/mp4"),
            "thumbnail": ("thumb.webp", thumb_bytes, "image/webp"),
        },
        data={
            "media_type": "video",
            "duration": "14.5",
            "is_gif_mode": "false",
            "is_nsfw": "true",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["media_type"] == "video"
    assert data["thumbnail_url"] is not None
    assert data["media_duration"] == 14.5
    assert data["is_nsfw"] is True


def test_media_upload_25mb_limit():
    admin_tok = get_admin_token()
    # 26 MB video should be rejected
    oversized_video = b"A" * (26 * 1024 * 1024)
    resp = client.post(
        "/api/upload-media",
        headers={"Authorization": f"Bearer {admin_tok}"},
        files={"file": ("huge.mp4", oversized_video, "video/mp4")},
        data={"media_type": "video"},
    )
    assert resp.status_code == 400
    assert "25 mb" in resp.json()["detail"].lower()


def test_media_cleanup_preserves_pinned():
    admin_tok = get_admin_token()
    admin = auth_database.get_user_by_username("dabo777")

    # Create dummy media files
    old_time = time.time() - (40 * 86400) # 40 days old

    # Normal old message
    msg1_id = database.create_channel_message(
        channel_id=1,
        user_id=admin["id"],
        content="Antiguo video",
        image_url="/uploads/vid_normal_old.mp4",
        media_type="video",
    )
    with database.get_conn() as conn:
        conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (old_time, msg1_id))

    # Pinned old message
    msg2_id = database.create_channel_message(
        channel_id=1,
        user_id=admin["id"],
        content="Antiguo video fijado",
        image_url="/uploads/vid_pinned_old.mp4",
        media_type="video",
    )
    with database.get_conn() as conn:
        conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (old_time, msg2_id))
        conn.execute("UPDATE channels SET pinned_message_id = ? WHERE id = 1", (msg2_id,))

    # Run cleanup
    res = database.cleanup_expired_media(max_age_days=30)
    assert res["cleaned_count"] == 1

    # Verify msg1 is expired, msg2 remains active
    m1 = database.get_channel_message_by_id(msg1_id, admin["id"])
    assert m1["is_expired"] is True
    assert m1["image_url"] is None

    m2 = database.get_channel_message_by_id(msg2_id, admin["id"])
    assert m2["is_expired"] is False
    assert m2["image_url"] == "/uploads/vid_pinned_old.mp4"
