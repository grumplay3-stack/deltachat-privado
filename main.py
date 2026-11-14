import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Header,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import database
import github_backup
import image_service

STATIC_DIR = Path("static")
STATIC_DIR.mkdir(parents=True, exist_ok=True)


def get_client_ip(conn: Request | WebSocket) -> str:
    forwarded = conn.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if conn.client:
        return conn.client.host
    return "unknown"


def format_ban_error(ban: dict[str, Any]) -> str:
    reason = ban.get("reason") or "Sin motivo especificado"
    if ban["ban_type"] == "permaban":
        return f"Acceso denegado: Baneo permanente (Permaban). Motivo: {reason}"
    remaining_sec = max(0, int((ban.get("expires_at") or 0) - time.time()))
    mins = max(1, (remaining_sec + 59) // 60)
    return f"Acceso denegado: Suspensión temporal ({mins} min restantes). Motivo: {reason}"


class ConnectionManager:
    """Manages active WebSocket connections, presence, and personalized broadcasts."""

    def __init__(self) -> None:
        # list of dicts: {"ws": WebSocket, "user_id": int, "role": str, "alias": str, "ip": str}
        self.connections: list[dict[str, Any]] = []

    def online_user_ids(self) -> set[int]:
        return {c["user_id"] for c in self.connections}

    async def connect(self, ws: WebSocket, user: dict[str, Any], ip: str) -> bool:
        await ws.accept()
        was_online = user["id"] in self.online_user_ids()
        self.connections.append(
            {
                "ws": ws,
                "user_id": user["id"],
                "role": user["role"],
                "alias": user["alias"],
                "ip": ip,
            }
        )
        return not was_online

    def disconnect(self, ws: WebSocket) -> Optional[dict[str, Any]]:
        removed = None
        remaining = []
        for c in self.connections:
            if c["ws"] is ws:
                removed = c
            else:
                remaining.append(c)
        self.connections = remaining
        if removed and removed["user_id"] not in self.online_user_ids():
            return removed
        return None

    async def broadcast_json(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for c in list(self.connections):
            try:
                await c["ws"].send_json(payload)
            except Exception:
                dead.append(c["ws"])
        for d in dead:
            self.disconnect(d)

    async def broadcast_channel_message(self, msg_id: int) -> None:
        # Prepare both admin and member views once
        admin_msg = database.get_channel_message_by_id(msg_id, viewer_id=0, for_admin=True)
        member_msg = database.get_channel_message_by_id(msg_id, viewer_id=0, for_admin=False)
        if not admin_msg or not member_msg:
            return
        dead: list[WebSocket] = []
        for c in list(self.connections):
            try:
                msg_data = admin_msg if c["role"] == "admin" else member_msg
                await c["ws"].send_json({"type": "new_channel_message", "message": msg_data})
            except Exception:
                dead.append(c["ws"])
        for d in dead:
            self.disconnect(d)

    async def send_dm_message(self, dm_id: int, sender_id: int, receiver_id: int) -> None:
        admin_msg = database.get_dm_by_id(dm_id, viewer_id=0, for_admin=True)
        member_msg = database.get_dm_by_id(dm_id, viewer_id=0, for_admin=False)
        if not admin_msg or not member_msg:
            return
        dead: list[WebSocket] = []
        for c in list(self.connections):
            if c["user_id"] in (sender_id, receiver_id):
                try:
                    msg_data = admin_msg if c["role"] == "admin" else member_msg
                    await c["ws"].send_json({"type": "new_dm", "message": msg_data})
                except Exception:
                    dead.append(c["ws"])
        for d in dead:
            self.disconnect(d)

    async def broadcast_presence(self) -> None:
        online_ids = list(self.online_user_ids())
        await self.broadcast_json({"type": "presence", "online_user_ids": online_ids})

    async def kick_banned_target(self, target_user_id: Optional[int], ip_address: Optional[str], message: str) -> None:
        for c in list(self.connections):
            match_user = target_user_id is not None and c["user_id"] == target_user_id
            match_ip = ip_address and c["ip"] == ip_address
            if match_user or match_ip:
                try:
                    await c["ws"].send_json({"type": "banned", "reason": message})
                    await c["ws"].close(code=4003)
                except Exception:
                    pass
                self.disconnect(c["ws"])


manager = ConnectionManager()


async def hd_cleanup_loop() -> None:
    """Periodically check and delete expired 10-minute HD photos."""
    while True:
        try:
            expired = database.fetch_and_expire_hd_images()
            for ch_item in expired["channels"]:
                image_service.delete_upload_file(ch_item["image_url"])
                await manager.broadcast_json(
                    {
                        "type": "hd_expired",
                        "target_type": "channel",
                        "id": ch_item["id"],
                        "channel_id": ch_item["channel_id"],
                    }
                )
            for dm_item in expired["dms"]:
                image_service.delete_upload_file(dm_item["image_url"])
                for c in list(manager.connections):
                    if c["user_id"] in (dm_item["sender_id"], dm_item["receiver_id"]):
                        try:
                            await c["ws"].send_json(
                                {
                                    "type": "hd_expired",
                                    "target_type": "dm",
                                    "id": dm_item["id"],
                                }
                            )
                        except Exception:
                            pass
        except Exception as exc:
            print(f"[HD Cleanup Error] {exc}")
        await asyncio.sleep(15)


async def monthly_backup_loop() -> None:
    """Check hourly whether a new month began; if so, backup to GitHub and reset chat."""
    while True:
        try:
            result = github_backup.run_backup_and_reset(force=False)
            if result.get("executed"):
                await manager.broadcast_json(
                    {
                        "type": "chat_reset",
                        "filename": result["filename"],
                    }
                )
        except Exception as exc:
            print(f"[Monthly Backup Error] {exc}")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    # Run initial monthly check on startup
    try:
        github_backup.run_backup_and_reset(force=False)
    except Exception as exc:
        print(f"[Startup Backup Check Error] {exc}")

    t1 = asyncio.create_task(hd_cleanup_loop())
    t2 = asyncio.create_task(monthly_backup_loop())
    yield
    t1.cancel()
    t2.cancel()


app = FastAPI(title="DeltaChat", lifespan=lifespan)


# --- PYDANTIC SCHEMAS ---

class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    alias: str = Field(default="", max_length=32)
    password: str = Field(min_length=4, max_length=128)
    security_question: str = Field(min_length=3, max_length=120)
    security_answer: str = Field(min_length=1, max_length=120)
    avatar_color: str = Field(default="#b5bd68")


class LoginRequest(BaseModel):
    username: str
    password: str


class RecoverPasswordRequest(BaseModel):
    username: str
    security_answer: str
    new_password: str = Field(min_length=4, max_length=128)


class AliasUpdateRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=32)
    avatar_color: Optional[str] = None


class BanCreateRequest(BaseModel):
    target_user_id: Optional[int] = None
    ip_address: Optional[str] = None
    ban_type: str = Field(pattern="^(permaban|temp)$")
    reason: str = Field(default="")
    duration_minutes: Optional[int] = None


class AdminResetPasswordRequest(BaseModel):
    user_id: int
    new_password: str = Field(min_length=4, max_length=128)


# --- AUTH DEPENDENCIES ---

async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Sesión no iniciada.")
    user = database.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada.")

    ip = get_client_ip(request)
    ban = database.check_active_ban(user_id=user["id"], ip_address=ip)
    if ban:
        raise HTTPException(status_code=403, detail=format_ban_error(ban))

    database.update_user_ip(user["id"], ip)
    user["_token"] = token
    user["_ip"] = ip
    return user


async def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Solo el administrador puede hacer esto.")
    return user


# --- AUTH ROUTES ---

@app.post("/api/register")
async def register(payload: RegisterRequest, request: Request):
    ip = get_client_ip(request)
    ban = database.check_active_ban(ip_address=ip)
    if ban:
        raise HTTPException(status_code=403, detail=format_ban_error(ban))

    if payload.username.strip().lower() == database.ADMIN_USERNAME.lower():
        raise HTTPException(status_code=409, detail="Ese nombre de usuario está reservado.")

    try:
        user = database.create_user(
            username=payload.username,
            alias=payload.alias,
            password=payload.password,
            security_question=payload.security_question,
            security_answer=payload.security_answer,
            avatar_color=payload.avatar_color,
            ip_address=ip,
        )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(status_code=409, detail="El nombre de usuario ya existe.") from exc
        raise HTTPException(status_code=400, detail="No se pudo crear el usuario.") from exc

    token = database.create_session(user["id"], ip)
    await manager.broadcast_json({"type": "users_updated"})
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "alias": user["alias"],
            "role": user["role"],
            "avatar_color": user["avatar_color"],
        },
    }


@app.post("/api/login")
async def login(payload: LoginRequest, request: Request):
    ip = get_client_ip(request)
    ip_ban = database.check_active_ban(ip_address=ip)
    if ip_ban:
        raise HTTPException(status_code=403, detail=format_ban_error(ip_ban))

    user = database.authenticate_user(payload.username, payload.password, ip)
    if not user:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")

    ban = database.check_active_ban(user_id=user["id"], ip_address=ip)
    if ban:
        raise HTTPException(status_code=403, detail=format_ban_error(ban))

    token = database.create_session(user["id"], ip)
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "alias": user["alias"],
            "role": user["role"],
            "avatar_color": user["avatar_color"],
        },
    }


@app.post("/api/logout")
async def logout(user: dict[str, Any] = Depends(get_current_user)):
    database.delete_session(user["_token"])
    return {"ok": True}


@app.get("/api/recover-question/{username}")
async def get_recover_question(username: str):
    q = database.get_security_question(username)
    if not q:
        raise HTTPException(status_code=404, detail="Usuario no encontrado o protegido.")
    return {"security_question": q}


@app.post("/api/recover-password")
async def recover_password(payload: RecoverPasswordRequest):
    ok = database.recover_password(payload.username, payload.security_answer, payload.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail="Respuesta secreta incorrecta o usuario no válido.")
    return {"ok": True}


@app.get("/api/me")
async def get_me(user: dict[str, Any] = Depends(get_current_user)):
    return {
        "id": user["id"],
        "username": user["username"],
        "alias": user["alias"],
        "role": user["role"],
        "avatar_color": user["avatar_color"],
    }


@app.put("/api/profile/alias")
async def update_alias(payload: AliasUpdateRequest, user: dict[str, Any] = Depends(get_current_user)):
    updated = database.update_user_alias(user["id"], payload.alias, payload.avatar_color)
    if not updated:
        raise HTTPException(status_code=400, detail="Alias inválido.")
    for c in manager.connections:
        if c["user_id"] == user["id"]:
            c["alias"] = updated["alias"]
    await manager.broadcast_json(
        {
            "type": "alias_updated",
            "user_id": updated["id"],
            "alias": updated["alias"],
            "avatar_color": updated["avatar_color"],
        }
    )
    return {
        "id": updated["id"],
        "alias": updated["alias"],
        "avatar_color": updated["avatar_color"],
    }


# --- CHAT & MEDIA ROUTES ---

@app.get("/api/users")
async def list_users(user: dict[str, Any] = Depends(get_current_user)):
    is_admin = user["role"] == "admin"
    users = database.list_users(for_admin=is_admin)
    online = manager.online_user_ids()
    for u in users:
        u["online"] = u["id"] in online
    return {"users": users}


@app.get("/api/channels")
async def list_channels(user: dict[str, Any] = Depends(get_current_user)):
    return {"channels": database.list_channels()}


@app.get("/api/messages/{channel_id}")
async def get_channel_messages(
    channel_id: int,
    search: Optional[str] = Query(default=None),
    user: dict[str, Any] = Depends(get_current_user),
):
    ch = database.get_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Tablón no encontrado.")
    is_admin = user["role"] == "admin"
    msgs = database.get_channel_messages(
        channel_id=channel_id,
        viewer_id=user["id"],
        for_admin=is_admin,
        limit=150,
        search=search,
    )
    return {"channel": ch, "messages": msgs}


@app.get("/api/dms/{friend_id}")
async def get_dms(
    friend_id: int,
    search: Optional[str] = Query(default=None),
    user: dict[str, Any] = Depends(get_current_user),
):
    friend = database.get_user_by_id(friend_id)
    if not friend:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    is_admin = user["role"] == "admin"
    msgs = database.get_direct_messages(
        user_a=user["id"],
        user_b=friend_id,
        viewer_id=user["id"],
        for_admin=is_admin,
        limit=150,
        search=search,
    )
    friend_info: dict[str, Any] = {
        "id": friend["id"],
        "alias": friend["alias"],
        "avatar_color": friend["avatar_color"],
    }
    if is_admin:
        friend_info["username"] = friend["username"]
    return {"friend": friend_info, "messages": msgs}


@app.post("/api/upload")
async def upload_image(
    file: UploadFile = File(...),
    is_hd: bool = Form(default=False),
    user: dict[str, Any] = Depends(get_current_user),
):
    raw_bytes = await file.read()
    try:
        meta = image_service.process_and_save_image(raw_bytes, file.filename or "imagen.webp", is_hd=is_hd)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return meta


@app.delete("/api/messages/{msg_id}")
async def delete_message(msg_id: int, user: dict[str, Any] = Depends(get_current_user)):
    is_admin = user["role"] == "admin"
    try:
        deleted = database.delete_channel_message(msg_id, user["id"], is_admin)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")
    image_service.delete_upload_file(deleted.get("image_url"))
    await manager.broadcast_json(
        {
            "type": "message_deleted",
            "target_type": "channel",
            "id": msg_id,
            "channel_id": deleted["channel_id"],
        }
    )
    return {"ok": True}


@app.delete("/api/dms/{dm_id}")
async def delete_dm(dm_id: int, user: dict[str, Any] = Depends(get_current_user)):
    is_admin = user["role"] == "admin"
    try:
        deleted = database.delete_direct_message(dm_id, user["id"], is_admin)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Mensaje privado no encontrado.")
    image_service.delete_upload_file(deleted.get("image_url"))
    for c in list(manager.connections):
        if c["user_id"] in (deleted["sender_id"], deleted["receiver_id"]):
            try:
                await c["ws"].send_json(
                    {
                        "type": "message_deleted",
                        "target_type": "dm",
                        "id": dm_id,
                    }
                )
            except Exception:
                pass
    return {"ok": True}


# --- ADMIN MODERATION, BANS & BACKUPS ---

@app.get("/api/admin/bans")
async def admin_list_bans(
    include_inactive: bool = Query(default=False),
    admin: dict[str, Any] = Depends(require_admin),
):
    return {"bans": database.list_bans(include_inactive=include_inactive)}


@app.post("/api/admin/bans")
async def admin_create_ban(payload: BanCreateRequest, admin: dict[str, Any] = Depends(require_admin)):
    if payload.target_user_id is None and not (payload.ip_address and payload.ip_address.strip()):
        raise HTTPException(status_code=400, detail="Debes indicar un usuario o una IP.")
    try:
        ban = database.create_ban(
            admin_id=admin["id"],
            target_user_id=payload.target_user_id,
            ip_address=payload.ip_address,
            ban_type=payload.ban_type,
            reason=payload.reason,
            duration_minutes=payload.duration_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await manager.kick_banned_target(
        target_user_id=ban.get("user_id"),
        ip_address=ban.get("ip_address"),
        message=format_ban_error(ban),
    )
    return {"ban": ban}


@app.delete("/api/admin/bans/{ban_id}")
async def admin_remove_ban(ban_id: int, admin: dict[str, Any] = Depends(require_admin)):
    ok = database.remove_ban(ban_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Baneo no encontrado.")
    return {"ok": True}


@app.post("/api/admin/reset-password")
async def admin_reset_user_password(
    payload: AdminResetPasswordRequest,
    admin: dict[str, Any] = Depends(require_admin),
):
    ok = database.admin_reset_password(payload.user_id, payload.new_password)
    if not ok:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return {"ok": True}


@app.post("/api/admin/backup-now")
async def admin_force_backup(admin: dict[str, Any] = Depends(require_admin)):
    result = github_backup.run_backup_and_reset(force=True)
    await manager.broadcast_json({"type": "chat_reset", "filename": result["filename"]})
    return result


@app.get("/api/admin/backups")
async def admin_list_backups(admin: dict[str, Any] = Depends(require_admin)):
    return {"files": github_backup.list_local_backups()}


@app.get("/api/admin/backups/{filename}")
async def admin_download_backup(filename: str, admin: dict[str, Any] = Depends(require_admin)):
    safe_name = Path(filename).name
    target = (github_backup.BACKUP_DIR / safe_name).resolve()
    if target.parent != github_backup.BACKUP_DIR.resolve() or not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo de respaldo no encontrado.")
    return FileResponse(target, media_type="text/plain; charset=utf-8", filename=safe_name)


@app.get("/api/admin/export-snapshot")
async def admin_export_snapshot(admin: dict[str, Any] = Depends(require_admin)):
    date_str = datetime.now().strftime("%m-%d-%Y")
    text = github_backup.format_backup_text(date_str)
    return PlainTextResponse(
        content=text,
        headers={"Content-Disposition": f'attachment; filename="snapshot_{date_str}.txt"'},
    )


# --- WEBSOCKET REAL-TIME HUB ---

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(default="")):
    ip = get_client_ip(ws)
    user = database.get_user_by_token(token)
    if not user:
        await ws.close(code=4001)
        return

    ban = database.check_active_ban(user_id=user["id"], ip_address=ip)
    if ban:
        await ws.accept()
        await ws.send_json({"type": "banned", "reason": format_ban_error(ban)})
        await ws.close(code=4003)
        return

    database.update_user_ip(user["id"], ip)
    became_online = await manager.connect(ws, user, ip)
    await manager.broadcast_presence()

    if became_online:
        await manager.broadcast_json(
            {
                "type": "system_notice",
                "text": f"{user['alias']} se ha conectado.",
                "created_at": time.time(),
            }
        )

    try:
        while True:
            data = await ws.receive_json()
            # Re-verify user & ban status per action
            active_ban = database.check_active_ban(user_id=user["id"], ip_address=ip)
            if active_ban:
                await ws.send_json({"type": "banned", "reason": format_ban_error(active_ban)})
                await ws.close(code=4003)
                break

            action = data.get("action")
            if action == "send_channel_message":
                channel_id = int(data.get("channel_id") or 1)
                content = (data.get("content") or "").strip()
                image_meta = data.get("image")
                reply_to_id = data.get("reply_to_id")
                if not content and not image_meta:
                    continue
                msg_id = database.create_channel_message(
                    channel_id=channel_id,
                    user_id=user["id"],
                    content=content,
                    image_url=image_meta.get("image_url") if image_meta else None,
                    image_name=image_meta.get("image_name") if image_meta else None,
                    image_size_kb=image_meta.get("image_size_kb") if image_meta else None,
                    image_dims=image_meta.get("image_dims") if image_meta else None,
                    is_hd=bool(image_meta.get("is_hd")) if image_meta else False,
                    expires_at=image_meta.get("expires_at") if image_meta else None,
                    reply_to_id=int(reply_to_id) if reply_to_id else None,
                )
                await manager.broadcast_channel_message(msg_id)

            elif action == "send_dm":
                receiver_id = int(data.get("receiver_id") or 0)
                content = (data.get("content") or "").strip()
                image_meta = data.get("image")
                reply_to_id = data.get("reply_to_id")
                if not receiver_id or (not content and not image_meta):
                    continue
                dm_id = database.create_direct_message(
                    sender_id=user["id"],
                    receiver_id=receiver_id,
                    content=content,
                    image_url=image_meta.get("image_url") if image_meta else None,
                    image_name=image_meta.get("image_name") if image_meta else None,
                    image_size_kb=image_meta.get("image_size_kb") if image_meta else None,
                    image_dims=image_meta.get("image_dims") if image_meta else None,
                    is_hd=bool(image_meta.get("is_hd")) if image_meta else False,
                    expires_at=image_meta.get("expires_at") if image_meta else None,
                    reply_to_id=int(reply_to_id) if reply_to_id else None,
                )
                await manager.send_dm_message(dm_id, user["id"], receiver_id)

            elif action == "toggle_reaction":
                target_type = data.get("target_type")
                target_id = int(data.get("target_id") or 0)
                emoji = data.get("emoji") or ""
                if target_type not in ("channel", "dm") or not target_id:
                    continue
                try:
                    summary = database.toggle_reaction(target_type, target_id, user["id"], emoji)
                except ValueError:
                    continue
                await manager.broadcast_json(
                    {
                        "type": "reaction_updated",
                        "target_type": target_type,
                        "target_id": target_id,
                        "reactions": summary,
                        "actor_id": user["id"],
                    }
                )

    except WebSocketDisconnect:
        pass
    finally:
        went_offline = manager.disconnect(ws)
        await manager.broadcast_presence()
        if went_offline:
            latest_user = database.get_user_by_id(went_offline["user_id"])
            alias = latest_user["alias"] if latest_user else went_offline["alias"]
            await manager.broadcast_json(
                {
                    "type": "system_notice",
                    "text": f"{alias} se ha desconectado.",
                    "created_at": time.time(),
                }
            )


# --- STATIC & UPLOADS MOUNTING ---

app.mount("/uploads", StaticFiles(directory=str(image_service.UPLOAD_DIR)), name="uploads")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
