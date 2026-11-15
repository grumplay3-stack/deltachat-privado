import pytest
from fastapi.testclient import TestClient
import main
import auth_database
import database


@pytest.fixture()
def new_features_env(tmp_path, monkeypatch):
    db_file = tmp_path / "test_chat.db"
    auth_db_file = tmp_path / "test_auth.db"
    uploads_dir = tmp_path / "uploads"
    backups_dir = tmp_path / "backups"
    uploads_dir.mkdir()
    backups_dir.mkdir()

    monkeypatch.setenv("DELTACHAT_DB", str(db_file))
    monkeypatch.setenv("DELTACHAT_AUTH_DB", str(auth_db_file))
    monkeypatch.setenv("DELTACHAT_UPLOADS", str(uploads_dir))
    monkeypatch.setenv("DELTACHAT_BACKUPS", str(backups_dir))

    auth_database.AUTH_DB_PATH = str(auth_db_file)
    auth_database.LEGACY_CHAT_DB_PATH = str(db_file)
    database.DB_PATH = str(db_file)

    with TestClient(main.app) as c:
        yield c


def _login(c, u, p):
    return c.post("/api/login", json={"username": u, "password": p}).json()["token"]


def _reg(c, u, a, p):
    return c.post(
        "/api/register",
        json={"username": u, "alias": a, "password": p, "security_question": "¿Mascota?", "security_answer": "Cat"},
    ).json()


def test_channel_visibility_whitelist_and_blacklist(new_features_env):
    c = new_features_env
    adm_tok = _login(c, "dabo777", "52952416")

    user_a = _reg(c, "member_a", "Alice", "pass123")
    user_b = _reg(c, "member_b", "Bob", "pass123")
    tok_a = user_a["token"]
    id_a = user_a["user"]["id"]
    tok_b = user_b["token"]
    id_b = user_b["user"]["id"]

    # Initial channels visible for both
    chs_a = c.get("/api/channels", headers={"Authorization": f"Bearer {tok_a}"}).json()["channels"]
    assert any(ch["slug"] == "gen" for ch in chs_a)

    # 1. Admin configures /mu/ as whitelist: ONLY member_a can see it
    mu_ch = next(ch for ch in chs_a if ch["slug"] == "mu")
    r_perm = c.put(
        f"/api/admin/channels/{mu_ch['id']}/visibility",
        json={"access_type": "whitelist", "user_ids": [id_a], "permission_type": "allow"},
        headers={"Authorization": f"Bearer {adm_tok}"},
    )
    assert r_perm.status_code == 200

    # User A CAN see /mu/
    visible_a = c.get("/api/channels", headers={"Authorization": f"Bearer {tok_a}"}).json()["channels"]
    assert any(ch["id"] == mu_ch["id"] for ch in visible_a)

    # User B CANNOT see /mu/
    visible_b = c.get("/api/channels", headers={"Authorization": f"Bearer {tok_b}"}).json()["channels"]
    assert not any(ch["id"] == mu_ch["id"] for ch in visible_b)

    # User B receives 403 Forbidden trying to access /mu/ messages directly
    r_forbidden = c.get(f"/api/messages/{mu_ch['id']}", headers={"Authorization": f"Bearer {tok_b}"})
    assert r_forbidden.status_code == 403

    # User A CAN read /mu/
    r_allowed = c.get(f"/api/messages/{mu_ch['id']}", headers={"Authorization": f"Bearer {tok_a}"})
    assert r_allowed.status_code == 200

    # 2. Pin message in channel
    r_pin = c.post(
        f"/api/admin/channels/{mu_ch['id']}/pin",
        json={"message_id": 999},
        headers={"Authorization": f"Bearer {adm_tok}"},
    )
    assert r_pin.status_code == 200
    assert r_pin.json()["channel"]["pinned_message_id"] == 999


def test_nsfw_flag_persistence(new_features_env):
    c = new_features_env
    adm_tok = _login(c, "dabo777", "52952416")

    # Generate a real valid PNG via PIL
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color=(200, 50, 50)).save(buf, format="PNG")
    png_bytes = buf.getvalue()

    # Upload with is_nsfw=True
    r_up = c.post(
        "/api/upload",
        files={"file": ("test.png", png_bytes, "image/png")},
        data={"is_hd": "false", "is_nsfw": "true"},
        headers={"Authorization": f"Bearer {adm_tok}"},
    )
    assert r_up.status_code == 200
    meta = r_up.json()
    assert meta["is_nsfw"] is True

    # Check database creation directly with is_nsfw
    msg_id = database.create_channel_message(
        channel_id=1,
        user_id=1,
        content="Contenido con filtro",
        image_url=meta["image_url"],
        image_name="test.png",
        is_nsfw=True,
    )
    msg = database.get_channel_message_by_id(msg_id, viewer_id=1)
    assert msg["is_nsfw"] is True
