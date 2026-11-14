import io
import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "test_chat.db"
    auth_db_file = tmp_path / "test_auth.db"
    uploads_dir = tmp_path / "uploads"
    backups_dir = tmp_path / "backups"
    users_backups_dir = backups_dir / "users"
    uploads_dir.mkdir()
    backups_dir.mkdir()
    users_backups_dir.mkdir()

    monkeypatch.setenv("DELTACHAT_DB", str(db_file))
    monkeypatch.setenv("DELTACHAT_AUTH_DB", str(auth_db_file))
    monkeypatch.setenv("DELTACHAT_UPLOADS", str(uploads_dir))
    monkeypatch.setenv("DELTACHAT_BACKUPS", str(backups_dir))

    import auth_database
    import database
    import github_backup
    import image_service
    import main

    auth_database.AUTH_DB_PATH = str(auth_db_file)
    auth_database.LEGACY_CHAT_DB_PATH = str(db_file)
    database.DB_PATH = str(db_file)
    image_service.UPLOAD_DIR = uploads_dir
    github_backup.BACKUP_DIR = backups_dir
    github_backup.USERS_BACKUP_DIR = users_backups_dir

    with TestClient(main.app) as c:
        yield c, auth_db_file, db_file, backups_dir, users_backups_dir


def _reg(c, username, alias, password, q="¿Nombre de tu primera mascota?", a="Firulais"):
    return c.post(
        "/api/register",
        json={
            "username": username,
            "alias": alias,
            "password": password,
            "security_question": q,
            "security_answer": a,
        },
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0"},
    )


def _login(c, username, password, ua="Mozilla/5.0 Chrome/124.0"):
    return c.post(
        "/api/login",
        json={"username": username, "password": password},
        headers={"User-Agent": ua},
    )


def test_auth_db_persistence_and_login_logs_and_lockout(env):
    c, auth_db_file, db_file, _, _ = env

    r = _reg(c, "juanito", "Juan", "clave123")
    assert r.status_code == 200
    uid = r.json()["user"]["id"]

    # Monthly reset wipes chat messages but MUST NOT wipe auth_users.db
    import database
    adm = _login(c, "dabo777", "52952416").json()["token"]
    c.post("/api/admin/backup-now", headers={"Authorization": f"Bearer {adm}"})
    database.reset_all_messages_for_new_month("2099-01")

    # juanito still logs in cleanly with same password
    r_ok = _login(c, "juanito", "clave123", ua="Mozilla/5.0 (Linux; Android 13) Firefox/122.0")
    assert r_ok.status_code == 200

    # Check login_logs captured UA summary & plain password in auth_users.db
    adm_users = c.get("/api/admin/database/users", headers={"Authorization": f"Bearer {adm}"}).json()["users"]
    j_row = next(u for u in adm_users if u["id"] == uid)
    assert j_row["password"] == "clave123"
    assert "Android" in j_row["last_device"] or "Firefox" in j_row["last_device"]

    logs = c.get(f"/api/admin/database/users/{uid}/logs", headers={"Authorization": f"Bearer {adm}"}).json()
    assert len(logs["logins"]) >= 2
    assert len(logs["known_ips"]) >= 1

    # Regular member cannot read admin DB endpoints
    tok_j = r_ok.json()["token"]
    assert c.get("/api/admin/database/users", headers={"Authorization": f"Bearer {tok_j}"}).status_code == 403

    # 5 failed login attempts trigger 15-min lockout (429)
    for _ in range(5):
        r_bad = _login(c, "juanito", "wrongpass")
    assert r_bad.status_code == 429
    # Even right password blocked while locked
    assert _login(c, "juanito", "clave123").status_code == 429


def test_two_step_recovery_normalized_and_admin_immunity(env):
    c, _, _, _, _ = env
    _reg(c, "maria99", "Mari", "oldpass", q="¿Ciudad de nacimiento?", a="  Bogotá  ")

    # dabo777 immune to public recovery
    r_adm_q = c.get("/api/recover-question/dabo777")
    assert r_adm_q.status_code == 404
    r_adm_p = c.post(
        "/api/recover-password",
        json={"username": "dabo777", "security_answer": "anything", "new_password": "hacked"},
    )
    assert r_adm_p.status_code in (400, 403, 404)

    # Step 1: get question
    r_q = c.get("/api/recover-question/maria99")
    assert r_q.status_code == 200
    assert r_q.json()["security_question"] == "¿Ciudad de nacimiento?"

    # Step 2: wrong answer rate limit test (up to 4 wrong, then 1 right)
    for _ in range(2):
        r_fail = c.post(
            "/api/recover-password",
            json={"username": "maria99", "security_answer": "Lima", "new_password": "newpass1"},
        )
        assert r_fail.status_code == 400

    # Normalized answer (lowercase, no accents, extra spaces) succeeds & auto-logins
    r_rec = c.post(
        "/api/recover-password",
        json={"username": "maria99", "security_answer": "BOGOTA", "new_password": "newpass1"},
    )
    assert r_rec.status_code == 200
    body = r_rec.json()
    assert body["token"]
    assert body["user"]["username"] == "maria99"

    # Profile security Q&A update
    tok = body["token"]
    r_upd = c.put(
        "/api/profile/security",
        json={"current_password": "newpass1", "security_question": "¿Color favorito?", "security_answer": "Azul Marino"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r_upd.status_code == 200
    assert c.get("/api/recover-question/maria99").json()["security_question"] == "¿Color favorito?"

    # Lockout on 5 bad recovery attempts
    r_p = _reg(c, "pedro", "Pete", "pw12", q="¿Tu color favorito?", a="Verde")
    assert r_p.status_code == 200
    for _ in range(5):
        r_lock = c.post(
            "/api/recover-password",
            json={"username": "pedro", "security_answer": "nope", "new_password": "pw34"},
        )
    assert r_lock.status_code == 429


def test_admin_impersonate_temp_pw_forced_change_and_audit(env):
    c, _, _, _, _ = env
    adm = _login(c, "dabo777", "52952416").json()["token"]

    r_u = _reg(c, "carlos", "Carlitos", "car123")
    uid = r_u.json()["user"]["id"]
    old_tok = r_u.json()["token"]

    # Admin generates random temp password & invalidates old sessions
    r_tmp = c.post(f"/api/admin/generate-temp-password/{uid}", headers={"Authorization": f"Bearer {adm}"})
    assert r_tmp.status_code == 200
    temp_pw = r_tmp.json()["temp_password"]
    assert temp_pw.startswith("Delta-")
    # Old session was invalidated
    assert c.get("/api/me", headers={"Authorization": f"Bearer {old_tok}"}).status_code == 401

    # Login with temp password has must_change_password = True
    r_login_tmp = _login(c, "carlos", temp_pw)
    assert r_login_tmp.status_code == 200
    assert r_login_tmp.json()["user"]["must_change_password"] is True
    tmp_tok = r_login_tmp.json()["token"]

    # User completes forced password change
    r_force = c.post(
        "/api/profile/change-forced-password",
        json={"new_password": "nuevaClave99"},
        headers={"Authorization": f"Bearer {tmp_tok}"},
    )
    assert r_force.status_code == 200
    assert c.get("/api/me", headers={"Authorization": f"Bearer {tmp_tok}"}).json()["must_change_password"] is False

    # Admin edits user fields (alias, admin_notes) & impersonates
    r_edit = c.put(
        f"/api/admin/database/users/{uid}",
        json={"alias": "Don Carlos", "admin_notes": "VIP confiable"},
        headers={"Authorization": f"Bearer {adm}"},
    )
    assert r_edit.status_code == 200
    assert r_edit.json()["user"]["admin_notes"] == "VIP confiable"

    r_imp = c.post(f"/api/admin/impersonate/{uid}", headers={"Authorization": f"Bearer {adm}"})
    assert r_imp.status_code == 200
    imp_tok = r_imp.json()["token"]
    assert c.get("/api/me", headers={"Authorization": f"Bearer {imp_tok}"}).json()["username"] == "carlos"
    # Admin token remains valid and still dabo777
    assert c.get("/api/me", headers={"Authorization": f"Bearer {adm}"}).json()["username"] == "dabo777"

    # Change history & audit logs recorded
    u_logs = c.get(f"/api/admin/database/users/{uid}/logs", headers={"Authorization": f"Bearer {adm}"}).json()
    assert len(u_logs["changes"]) >= 2
    audits = c.get("/api/admin/audit-logs", headers={"Authorization": f"Bearer {adm}"}).json()["logs"]
    actions = {a["event_type"] for a in audits}
    assert "admin_impersonate" in actions and "admin_temp_password" in actions


def test_friendships_dm_requests_blocks_and_admin_inspect(env):
    c, _, _, _, _ = env
    adm = _login(c, "dabo777", "52952416").json()["token"]

    u1 = _reg(c, "ana", "Anita", "1234").json()
    u2 = _reg(c, "beto", "Betin", "1234").json()
    u3 = _reg(c, "clara", "Clarita", "1234").json()
    tok1, id1 = u1["token"], u1["user"]["id"]
    tok2, id2 = u2["token"], u2["user"]["id"]
    tok3, id3 = u3["token"], u3["user"]["id"]

    # Send DM without being friends -> arrives as pending message request
    with c.websocket_connect(f"/ws?token={tok1}") as ws1, c.websocket_connect(f"/ws?token={tok2}") as ws2:
        ws1.send_json({"action": "send_dm", "receiver_id": id2, "content": "Hola Beto, aceptas DM?"})
        # Beto receives new_dm
        got_dm = False
        for _ in range(10):
            ev = ws2.receive_json()
            if ev.get("type") == "new_dm":
                got_dm = True
                break
        assert got_dm

    # Summary for Beto shows pending incoming request & unread_count = 1
    sum2 = c.get("/api/dms/summary", headers={"Authorization": f"Bearer {tok2}"}).json()["conversations"]
    req_from_ana = next(x for x in sum2 if x["user_id"] == id1)
    assert req_from_ana["is_incoming_request"] is True
    assert req_from_ana["unread_count"] == 1
    assert "Hola Beto" in req_from_ana["last_message"]
    # Privacy: Beto does NOT see Ana's login username in summary or /api/friends
    assert "username" not in req_from_ana

    # While pending, Beto cannot reply until accepting
    dm_thread = c.get(f"/api/dms/{id1}", headers={"Authorization": f"Bearer {tok2}"}).json()
    assert dm_thread["friend"]["can_send"] is False

    # Beto accepts friend/DM request
    r_acc = c.post(
        "/api/friends/respond",
        json={"target_user_id": id1, "action": "accept"},
        headers={"Authorization": f"Bearer {tok2}"},
    )
    assert r_acc.status_code == 200
    assert r_acc.json()["friendship"]["status"] == "accepted"

    # Beto marks read -> unread_count becomes 0 (silent, no read receipt pushed to Ana)
    c.post(f"/api/dms/{id1}/read", headers={"Authorization": f"Bearer {tok2}"})
    sum2_after = c.get("/api/dms/summary", headers={"Authorization": f"Bearer {tok2}"}).json()["conversations"]
    assert next(x for x in sum2_after if x["user_id"] == id1)["unread_count"] == 0

    # Clara sends friend request by alias to Ana, then Ana blocks Clara
    r_freq = c.post(
        "/api/friends/request",
        json={"query": "Anita"},
        headers={"Authorization": f"Bearer {tok3}"},
    )
    assert r_freq.status_code == 200
    c.post(
        "/api/friends/respond",
        json={"target_user_id": id3, "action": "block"},
        headers={"Authorization": f"Bearer {tok1}"},
    )

    # Blocked user cannot send DM to Ana
    with c.websocket_connect(f"/ws?token={tok3}") as ws3:
        ws3.send_json({"action": "send_dm", "receiver_id": id1, "content": "Intentando bypass"})
        got_err = False
        for _ in range(10):
            ev = ws3.receive_json()
            if ev.get("type") == "error":
                got_err = True
                break
        assert got_err

    # Unfriend closes future DMs while preserving history
    c.post(
        "/api/friends/respond",
        json={"target_user_id": id1, "action": "unfriend"},
        headers={"Authorization": f"Bearer {tok2}"},
    )
    dm_after_unfriend = c.get(f"/api/dms/{id1}", headers={"Authorization": f"Bearer {tok2}"}).json()
    assert len(dm_after_unfriend["messages"]) == 1
    assert dm_after_unfriend["friend"]["can_send"] is False

    # Admin DM inspect
    threads = c.get("/api/admin/dms/inspect", headers={"Authorization": f"Bearer {adm}"}).json()["threads"]
    assert len(threads) >= 1
    pair = c.get(f"/api/admin/dms/inspect/{id1}/{id2}", headers={"Authorization": f"Bearer {adm}"}).json()["messages"]
    assert len(pair) == 1


def test_users_backup_every_3_days_and_restore(env):
    c, auth_db_file, _, backups_dir, users_backups_dir = env
    adm = _login(c, "dabo777", "52952416").json()["token"]

    _reg(c, "lucas", "Luquitas", "pass999")

    # Force 3-day users backup
    r_bk = c.post("/api/admin/database/backup-now", headers={"Authorization": f"Bearer {adm}"})
    assert r_bk.status_code == 200
    bk_list = c.get("/api/admin/database/backups", headers={"Authorization": f"Bearer {adm}"}).json()["files"]
    assert any(b.endswith(".json") for b in bk_list)
    assert any(b.endswith(".db") for b in bk_list)

    # Export JSON, create another user, then restore JSON snapshot -> new user removed, lucas preserved
    r_exp = c.get("/api/admin/database/export?format=json", headers={"Authorization": f"Bearer {adm}"})
    assert r_exp.status_code == 200
    snapshot_bytes = r_exp.content

    _reg(c, "temporal", "Temp", "pass000")
    assert _login(c, "temporal", "pass000").status_code == 200

    r_rest = c.post(
        "/api/admin/database/restore",
        files={"file": ("users_export.json", io.BytesIO(snapshot_bytes), "application/json")},
        headers={"Authorization": f"Bearer {adm}"},
    )
    assert r_rest.status_code == 200
    assert _login(c, "temporal", "pass000").status_code == 401
    assert _login(c, "lucas", "pass999").status_code == 200
