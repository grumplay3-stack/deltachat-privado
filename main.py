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
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auth_database
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


def get_user_agent(conn: Request | WebSocket) -> str:
    return conn.headers.get("user-agent", "")


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
                "is_impersonated": bool(user.get("is_impersonated")),
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

    async def send_to_user(self, user_id: int, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for c in list(self.connections):
            if c["user_id"] == user_id:
                try:
                    await c["ws"].send_json(payload)
                except Exception:
                    dead.append(c["ws"])
        for d in dead:
            self.disconnect(d)

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

    async def kick_banned_target(
        self, target_user_id: Optional[int], ip_address: Optional[str], message: str
    ) -> None:
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
                for uid in (dm_item["sender_id"], dm_item["receiver_id"]):
                    await manager.send_to_user(
                        uid,
                        {
                            "type": "hd_expired",
                            "target_type": "dm",
                            "id": dm_item["id"],
                        },
                    )
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
                    {"type": "chat_reset", "filename": result["filename"]}
                )
        except Exception as exc:
            print(f"[Monthly Backup Error] {exc}")
        await asyncio.sleep(3600)


async def users_backup_loop() -> None:
    """Check every 6 hours if 3 days have elapsed since the last auth_users.db backup."""
    while True:
        try:
            github_backup.run_users_db_backup(force=False)
        except Exception as exc:
            print(f"[Users Backup Error] {exc}")
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    try:
        github_backup.run_users_db_backup(force=False)
        github_backup.run_backup_and_reset(force=False)
    except Exception as exc:
        print(f"[Startup Backup Check Error] {exc}")

    t1 = asyncio.create_task(hd_cleanup_loop())
    t2 = asyncio.create_task(monthly_backup_loop())
    t3 = asyncio.create_task(users_backup_loop())
    yield
    t1.cancel()
    t2.cancel()
    t3.cancel()


app = FastAPI(title="DeltaChat", lifespan=lifespan)


# --- PYDANTIC SCHEMAS ---

class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    alias: str = Field(default="", max_length=32)
    password: str = Field(min_length=4, max_length=128)
    security_question: str = Field(min_length=3, max_length=150)
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


class SecurityUpdateRequest(BaseModel):
    current_password: str
    security_question: str = Field(min_length=3, max_length=150)
    security_answer: str = Field(min_length=1, max_length=120)


class ForcedPasswordChangeRequest(BaseModel):
    new_password: str = Field(min_length=4, max_length=128)


class AdminEditUserRequest(BaseModel):
    username: Optional[str] = Field(default=None, min_length=2, max_length=32)
    password: Optional[str] = Field(default=None, max_length=128)
    alias: Optional[str] = Field(default=None, max_length=32)
    avatar_color: Optional[str] = None
    role: Optional[str] = None
    security_question: Optional[str] = None
    security_answer: Optional[str] = None
    admin_notes: Optional[str] = None
    must_change_password: Optional[bool] = None


class BanCreateRequest(BaseModel):
    target_user_id: Optional[int] = None
    ip_address: Optional[str] = None
    ban_type: str = Field(pattern="^(permaban|temp)$")
    reason: str = Field(default="")
    duration_minutes: Optional[int] = None


class FriendRequestCreate(BaseModel):
    target_user_id: Optional[int] = None
    query: Optional[str] = None


class FriendRespondRequest(BaseModel):
    target_user_id: int
    action: str = Field(pattern="^(accept|reject|block|unfriend|unblock)$")


class ChannelVisibilityRequest(BaseModel):
    access_type: str = Field(pattern="^(public|whitelist|blacklist)$")
    user_ids: list[int] = Field(default_factory=list)
    permission_type: str = Field(default="allow", pattern="^(allow|deny)$")
    is_announcement: Optional[bool] = None


class ChannelPinRequest(BaseModel):
    message_id: Optional[int] = None


class CreatePollRequest(BaseModel):
    channel_id: int
    question: str = Field(min_length=1, max_length=200)
    options: list[str] = Field(min_length=2, max_length=5)


class VotePollRequest(BaseModel):
    option_id: int


class SetRadioRequest(BaseModel):
    url: str = Field(default="", max_length=500)
    title: str = Field(default="ConectFm", max_length=100)
    active: bool = True


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
    user = auth_database.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada.")

    ip = get_client_ip(request)
    ban = auth_database.check_active_ban(user_id=user["id"], ip_address=ip)
    if ban:
        raise HTTPException(status_code=403, detail=format_ban_error(ban))

    if not user.get("is_impersonated"):
        auth_database.update_user_ip(user["id"], ip, get_user_agent(request))
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
    ua = get_user_agent(request)
    ban = auth_database.check_active_ban(ip_address=ip)
    if ban:
        raise HTTPException(status_code=403, detail=format_ban_error(ban))

    if payload.username.strip().lower() == auth_database.ADMIN_USERNAME.lower():
        raise HTTPException(status_code=409, detail="Ese nombre de usuario está reservado.")

    try:
        user = auth_database.create_user(
            username=payload.username,
            alias=payload.alias,
            password=payload.password,
            security_question=payload.security_question,
            security_answer=payload.security_answer,
            avatar_color=payload.avatar_color,
            ip_address=ip,
            user_agent=ua,
        )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(status_code=409, detail="El nombre de usuario ya existe.") from exc
        raise HTTPException(status_code=400, detail="No se pudo crear el usuario.") from exc

    token = auth_database.create_session(user["id"], ip, ua)
    await manager.broadcast_json({"type": "users_updated"})
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "alias": user["alias"],
            "role": user["role"],
            "avatar_color": user["avatar_color"],
            "must_change_password": bool(user["must_change_password"]),
        },
    }


@app.post("/api/login")
async def login(payload: LoginRequest, request: Request):
    ip = get_client_ip(request)
    ua = get_user_agent(request)
    is_admin_attempt = payload.username.strip().lower() == auth_database.ADMIN_USERNAME.lower()
    if not is_admin_attempt:
        ip_ban = auth_database.check_active_ban(ip_address=ip)
        if ip_ban:
            raise HTTPException(status_code=403, detail=format_ban_error(ip_ban))

    user, err = auth_database.authenticate_user(payload.username, payload.password, ip, ua)
    if err:
        code = 429 if "Demasiados intentos" in err else 401
        raise HTTPException(code, detail=err)

    if user.get("role") != "admin":
        ban = auth_database.check_active_ban(user_id=user["id"], ip_address=ip)
        if ban:
            raise HTTPException(status_code=403, detail=format_ban_error(ban))

    token = auth_database.create_session(user["id"], ip, ua)
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "alias": user["alias"],
            "role": user["role"],
            "avatar_color": user["avatar_color"],
            "must_change_password": bool(user["must_change_password"]),
        },
    }


@app.post("/api/logout")
async def logout(user: dict[str, Any] = Depends(get_current_user)):
    auth_database.delete_session(user["_token"])
    return {"ok": True}


@app.get("/api/recover-question/{username}")
async def get_recover_question(username: str, request: Request):
    ip = get_client_ip(request)
    rate_key = f"{username.strip()}|{ip}"
    locked_mins = auth_database.check_rate_limit("recovery", rate_key)
    if locked_mins:
        raise HTTPException(
            status_code=429,
            detail=f"Demasiados intentos fallidos. Espera {locked_mins} minutos.",
        )
    q = auth_database.get_security_question(username)
    if not q:
        raise HTTPException(status_code=404, detail="Datos de recuperación no disponibles para esa cuenta.")
    return {"security_question": q}


@app.post("/api/recover-password")
async def recover_password(payload: RecoverPasswordRequest, request: Request):
    ip = get_client_ip(request)
    ua = get_user_agent(request)
    user, err = auth_database.recover_password_step2(
        username=payload.username,
        security_answer=payload.security_answer,
        new_password=payload.new_password,
        ip_address=ip,
        user_agent=ua,
    )
    if err:
        code = 429 if "Demasiados intentos" in err else 400
        raise HTTPException(status_code=code, detail=err)

    ban = auth_database.check_active_ban(user_id=user["id"], ip_address=ip)
    if ban:
        raise HTTPException(status_code=403, detail=format_ban_error(ban))

    token = auth_database.create_session(user["id"], ip, ua)
    return {
        "ok": True,
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "alias": user["alias"],
            "role": user["role"],
            "avatar_color": user["avatar_color"],
            "must_change_password": False,
        },
    }


@app.get("/api/me")
async def get_me(user: dict[str, Any] = Depends(get_current_user)):
    return {
        "id": user["id"],
        "username": user["username"],
        "alias": user["alias"],
        "role": user["role"],
        "avatar_color": user["avatar_color"],
        "security_question": user["security_question"],
        "must_change_password": bool(user["must_change_password"]),
    }


@app.put("/api/profile/alias")
async def update_alias(
    payload: AliasUpdateRequest, user: dict[str, Any] = Depends(get_current_user)
):
    updated = auth_database.update_user_alias(
        user["id"], payload.alias, payload.avatar_color, actor_id=user["id"], actor_role="self"
    )
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


@app.put("/api/profile/security")
async def update_security(
    payload: SecurityUpdateRequest, user: dict[str, Any] = Depends(get_current_user)
):
    ok = auth_database.update_own_security(
        user["id"], payload.current_password, payload.security_question, payload.security_answer
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Tu contraseña actual no coincide.")
    return {"ok": True}


@app.post("/api/profile/change-forced-password")
async def change_forced_password(
    payload: ForcedPasswordChangeRequest, user: dict[str, Any] = Depends(get_current_user)
):
    updated = auth_database.change_forced_password(user["id"], payload.new_password)
    if not updated:
        raise HTTPException(status_code=400, detail="No se pudo actualizar la contraseña.")
    return {"ok": True}


# --- USERS, FRIENDS & DMS ROUTES ---

@app.get("/api/users")
async def list_users(user: dict[str, Any] = Depends(get_current_user)):
    is_admin = user["role"] == "admin"
    users = auth_database.list_users(for_admin=is_admin)
    online = manager.online_user_ids()
    rels = database.get_user_relationships(user["id"], for_admin=is_admin)
    blocked_by = set(rels["blocked_by_ids"])
    blocked_mine = {b["user_id"] for b in rels["blocked"]}

    visible_users = []
    for u in users:
        if not is_admin and u["id"] in blocked_by:
            u["online"] = False
        else:
            u["online"] = u["id"] in online
        u["is_blocked"] = u["id"] in blocked_mine
        visible_users.append(u)
    return {"users": visible_users}


@app.get("/api/friends")
async def get_friends(user: dict[str, Any] = Depends(get_current_user)):
    is_admin = user["role"] == "admin"
    rels = database.get_user_relationships(user["id"], for_admin=is_admin)
    online = manager.online_user_ids()
    for grp in ("friends", "incoming_requests", "outgoing_requests", "blocked"):
        for item in rels[grp]:
            item["online"] = item["user_id"] in online if grp != "blocked" else False
    return rels


@app.post("/api/friends/request")
async def send_friend_request(
    payload: FriendRequestCreate, user: dict[str, Any] = Depends(get_current_user)
):
    target_id = payload.target_user_id
    if target_id is None and payload.query:
        q = payload.query.strip().lower()
        all_users = auth_database.list_users(for_admin=True)
        for candidate in all_users:
            if candidate["id"] == user["id"]:
                continue
            if candidate["alias"].lower() == q or candidate["username"].lower() == q:
                target_id = candidate["id"]
                break
        if target_id is None:
            for candidate in all_users:
                if candidate["id"] == user["id"]:
                    continue
                if q in candidate["alias"].lower():
                    target_id = candidate["id"]
                    break

    if not target_id:
        raise HTTPException(status_code=404, detail="No se encontró ningún amigo con ese nombre o alias.")

    try:
        rel = database.send_friend_request(user["id"], target_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await manager.send_to_user(target_id, {"type": "friends_updated"})
    await manager.send_to_user(user["id"], {"type": "friends_updated"})
    return {"friendship": rel}


@app.post("/api/friends/respond")
async def respond_friend_request(
    payload: FriendRespondRequest, user: dict[str, Any] = Depends(get_current_user)
):
    try:
        rel = database.respond_friendship(user["id"], payload.target_user_id, payload.action)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await manager.send_to_user(payload.target_user_id, {"type": "friends_updated"})
    await manager.send_to_user(user["id"], {"type": "friends_updated"})
    return {"friendship": rel}


@app.get("/api/dms/summary")
async def get_dms_summary(user: dict[str, Any] = Depends(get_current_user)):
    is_admin = user["role"] == "admin"
    summary = database.get_dms_conversations_summary(user["id"], for_admin=is_admin)
    online = manager.online_user_ids()
    for s in summary:
        s["online"] = s["user_id"] in online
    return {"conversations": summary}


@app.post("/api/dms/{friend_id}/read")
async def mark_dm_read(friend_id: int, user: dict[str, Any] = Depends(get_current_user)):
    database.mark_dm_as_read(user["id"], friend_id)
    return {"ok": True}


@app.get("/api/channels")
async def list_channels(user: dict[str, Any] = Depends(get_current_user)):
    is_admin = user["role"] == "admin"
    return {"channels": database.list_channels_for_user(user["id"], is_admin=is_admin)}


@app.get("/api/messages/{channel_id}")
async def get_channel_messages(
    channel_id: int,
    search: Optional[str] = Query(default=None),
    user: dict[str, Any] = Depends(get_current_user),
):
    is_admin = user["role"] == "admin"
    if not database.can_user_access_channel(user["id"], channel_id, is_admin=is_admin):
        raise HTTPException(status_code=403, detail="No tienes acceso a este tablón.")

    ch = database.get_channel(channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Tablón no encontrado.")
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
    friend = auth_database.get_user_by_id(friend_id)
    if not friend:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    rel = database.get_friendship_between(user["id"], friend_id)
    if rel and rel["status"] == "blocked":
        raise HTTPException(status_code=403, detail="Conversación bloqueada.")

    database.mark_dm_as_read(user["id"], friend_id)
    is_admin = user["role"] == "admin"
    msgs = database.get_direct_messages(
        user_a=user["id"],
        user_b=friend_id,
        viewer_id=user["id"],
        for_admin=is_admin,
        limit=150,
        search=search,
    )
    can_send, block_reason = database.can_send_dm(user["id"], friend_id)
    is_incoming_request = bool(
        rel and rel["status"] == "pending" and rel["action_user_id"] != user["id"]
    )

    friend_info: dict[str, Any] = {
        "id": friend["id"],
        "alias": friend["alias"],
        "avatar_color": friend["avatar_color"],
        "friendship_status": rel["status"] if rel else "none",
        "is_incoming_request": is_incoming_request,
        "can_send": can_send,
        "block_reason": block_reason,
    }
    if is_admin:
        friend_info["username"] = friend["username"]
    return {"friend": friend_info, "messages": msgs}


@app.post("/api/upload")
async def upload_image(
    file: UploadFile = File(...),
    is_hd: bool = Form(default=False),
    is_nsfw: bool = Form(default=False),
    user: dict[str, Any] = Depends(get_current_user),
):
    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="Debes cambiar tu contraseña antes de continuar.")
    raw_bytes = await file.read()
    try:
        meta = image_service.process_and_save_image(
            raw_bytes, file.filename or "imagen.webp", is_hd=is_hd
        )
        meta["is_nsfw"] = bool(is_nsfw)
        meta["media_type"] = "image"
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return meta


@app.post("/api/upload-media")
async def upload_media(
    file: UploadFile = File(...),
    media_type: str = Form(default="video"),
    thumbnail: Optional[UploadFile] = File(default=None),
    duration: Optional[float] = Form(default=None),
    is_gif_mode: bool = Form(default=False),
    is_nsfw: bool = Form(default=False),
    user: dict[str, Any] = Depends(get_current_user),
):
    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="Debes cambiar tu contraseña antes de continuar.")
    raw_bytes = await file.read()
    thumb_bytes = await thumbnail.read() if thumbnail else None
    try:
        import media_service
        meta = media_service.save_uploaded_media(
            raw_bytes=raw_bytes,
            original_filename=file.filename or f"media.{media_type}",
            media_type=media_type,
            thumbnail_bytes=thumb_bytes,
            duration_seconds=duration,
            is_gif_mode=is_gif_mode,
            is_nsfw=is_nsfw,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return meta


# --- RADIO ENDPOINTS ---

@app.get("/api/radio")
async def get_radio_stream():
    return database.get_radio_stream()


@app.post("/api/admin/radio")
async def admin_set_radio_stream(
    payload: SetRadioRequest, admin: dict[str, Any] = Depends(require_admin)
):
    res = database.set_radio_stream(url=payload.url, title=payload.title, active=payload.active)
    await manager.broadcast_json({"type": "radio_updated", "radio": res})
    return res


# --- POLLS ENDPOINTS ---

@app.post("/api/polls")
async def create_poll_endpoint(
    payload: CreatePollRequest, user: dict[str, Any] = Depends(get_current_user)
):
    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="Debes cambiar tu contraseña antes de continuar.")
    can_post, reason = database.can_user_post_in_channel(
        user["id"], payload.channel_id, is_admin=(user["role"] == "admin")
    )
    if not can_post:
        raise HTTPException(status_code=403, detail=reason)
    try:
        poll = database.create_poll(
            channel_id=payload.channel_id,
            creator_id=user["id"],
            question=payload.question,
            options=payload.options,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Find the message ID created for this poll and broadcast it
    with database.get_conn() as conn:
        m_row = conn.execute("SELECT id FROM messages WHERE poll_id = ?", (poll["id"],)).fetchone()
        if m_row:
            await manager.broadcast_channel_message(m_row["id"])

    return {"poll": poll}


@app.post("/api/polls/{poll_id}/vote")
async def vote_poll_endpoint(
    poll_id: int, payload: VotePollRequest, user: dict[str, Any] = Depends(get_current_user)
):
    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="Debes cambiar tu contraseña antes de continuar.")
    try:
        updated_poll = database.vote_poll(poll_id, user["id"], payload.option_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await manager.broadcast_json({"type": "poll_updated", "poll": updated_poll})
    return {"poll": updated_poll}


@app.post("/api/polls/{poll_id}/close")
async def close_poll_endpoint(
    poll_id: int, user: dict[str, Any] = Depends(get_current_user)
):
    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="Debes cambiar tu contraseña antes de continuar.")
    is_admin = user["role"] == "admin"
    try:
        closed_poll = database.close_poll(poll_id, user["id"], is_admin)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await manager.broadcast_json({"type": "poll_updated", "poll": closed_poll})
    return {"poll": closed_poll}


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
    for uid in (deleted["sender_id"], deleted["receiver_id"]):
        await manager.send_to_user(
            uid,
            {"type": "message_deleted", "target_type": "dm", "id": dm_id},
        )
    return {"ok": True}


# --- ADMIN MODERATION, SUPERPOWERS, DB & BACKUPS ---

@app.get("/api/admin/database/users")
async def admin_db_users(admin: dict[str, Any] = Depends(require_admin)):
    users = auth_database.list_users(for_admin=True)
    online = manager.online_user_ids()
    for u in users:
        u["online"] = u["id"] in online
    return {"users": users}


@app.put("/api/admin/database/users/{user_id}")
async def admin_db_edit_user(
    user_id: int,
    payload: AdminEditUserRequest,
    admin: dict[str, Any] = Depends(require_admin),
):
    try:
        updated = auth_database.admin_update_user(
            admin_id=admin["id"],
            user_id=user_id,
            username=payload.username,
            password=payload.password,
            alias=payload.alias,
            avatar_color=payload.avatar_color,
            role=payload.role,
            security_question=payload.security_question,
            security_answer=payload.security_answer,
            admin_notes=payload.admin_notes,
            must_change_password=payload.must_change_password,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await manager.broadcast_json(
        {
            "type": "alias_updated",
            "user_id": updated["id"],
            "alias": updated["alias"],
            "avatar_color": updated["avatar_color"],
        }
    )
    return {"user": updated}


@app.get("/api/admin/database/users/{user_id}/logs")
async def admin_db_user_logs(
    user_id: int, admin: dict[str, Any] = Depends(require_admin)
):
    return auth_database.get_user_logs_and_history(user_id)


@app.post("/api/admin/generate-temp-password/{user_id}")
async def admin_gen_temp_password(
    user_id: int, admin: dict[str, Any] = Depends(require_admin)
):
    target = auth_database.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    temp_pw = auth_database.admin_generate_temp_password(admin["id"], user_id)
    auth_database.add_audit_log(
        "admin_temp_password", target["username"], f"Admin generó contraseña temporal para @{target['username']}"
    )
    return {"temp_password": temp_pw, "username": target["username"]}


@app.post("/api/admin/impersonate/{user_id}")
async def admin_impersonate(
    user_id: int, request: Request, admin: dict[str, Any] = Depends(require_admin)
):
    target = auth_database.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    token = auth_database.create_session(
        user_id=target["id"],
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        is_impersonated=True,
    )
    auth_database.add_audit_log(
        "admin_impersonate", target["username"], f"Admin ingresó como @{target['username']}"
    )
    return {
        "token": token,
        "user": {
            "id": target["id"],
            "username": target["username"],
            "alias": target["alias"],
            "role": target["role"],
        },
    }


@app.get("/api/admin/audit-logs")
async def admin_audit_logs(admin: dict[str, Any] = Depends(require_admin)):
    return {"logs": auth_database.get_audit_logs(limit=60)}


@app.get("/api/admin/dms/inspect")
async def admin_inspect_dms_overview(admin: dict[str, Any] = Depends(require_admin)):
    return {"threads": database.admin_list_all_dm_threads()}


@app.get("/api/admin/dms/inspect/{user_a}/{user_b}")
async def admin_inspect_dm_thread(
    user_a: int, user_b: int, admin: dict[str, Any] = Depends(require_admin)
):
    msgs = database.get_direct_messages(user_a, user_b, viewer_id=admin["id"], for_admin=True, limit=250)
    return {"messages": msgs}


@app.get("/api/admin/channels")
async def admin_list_channels(admin: dict[str, Any] = Depends(require_admin)):
    channels = database.list_channels_for_user(admin["id"], is_admin=True)
    for ch in channels:
        ch["permissions"] = database.get_channel_permissions(ch["id"])
    return {"channels": channels}


@app.put("/api/admin/channels/{channel_id}/visibility")
async def admin_update_channel_visibility(
    channel_id: int,
    payload: ChannelVisibilityRequest,
    admin: dict[str, Any] = Depends(require_admin),
):
    ch = database.admin_set_channel_visibility(
        channel_id=channel_id,
        access_type=payload.access_type,
        user_ids=payload.user_ids,
        permission_type=payload.permission_type,
        is_announcement=payload.is_announcement,
    )
    auth_database.add_audit_log(
        "channel_visibility_update",
        admin["username"],
        f"Actualizó visibilidad del tablón #{channel_id} ({payload.access_type})",
    )
    await manager.broadcast_json({"type": "channels_updated"})
    return {"ok": True, "channel": ch}


@app.post("/api/admin/channels/{channel_id}/pin")
async def admin_pin_channel_message(
    channel_id: int,
    payload: ChannelPinRequest,
    admin: dict[str, Any] = Depends(require_admin),
):
    ch = database.admin_pin_message(channel_id, payload.message_id)
    await manager.broadcast_json({"type": "channels_updated", "pinned_channel_id": channel_id})
    return {"ok": True, "channel": ch}


@app.get("/api/admin/database/export")
async def admin_export_users_db(
    format: str = Query(default="json", pattern="^(json|db)$"),
    admin: dict[str, Any] = Depends(require_admin),
):
    date_str = datetime.now().strftime("%m-%d-%Y")
    if format == "db":
        db_path = Path(auth_database.AUTH_DB_PATH)
        if not db_path.is_file():
            raise HTTPException(status_code=404, detail="Archivo .db no encontrado.")
        return FileResponse(
            db_path,
            media_type="application/octet-stream",
            filename=f"auth_users_{date_str}.db",
        )
    json_str = auth_database.export_users_db_json()
    return Response(
        content=json_str,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="users_backup_{date_str}.json"'},
    )


@app.post("/api/admin/database/restore")
async def admin_restore_users_db(
    file: UploadFile = File(...),
    admin: dict[str, Any] = Depends(require_admin),
):
    raw = await file.read()
    fname = (file.filename or "").lower()
    try:
        if fname.endswith(".db") or fname.endswith(".sqlite"):
            count = auth_database.restore_users_from_sqlite_bytes(raw)
        else:
            count = auth_database.restore_users_from_json(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await manager.broadcast_json({"type": "users_updated"})
    return {"ok": True, "restored_users": count}


@app.post("/api/admin/database/backup-now")
async def admin_backup_users_now(admin: dict[str, Any] = Depends(require_admin)):
    res = github_backup.run_users_db_backup(force=True)
    return res


@app.get("/api/admin/database/backups")
async def admin_list_users_backups(admin: dict[str, Any] = Depends(require_admin)):
    return {"files": github_backup.list_users_backups()}


@app.get("/api/admin/database/backups/{filename}")
async def admin_download_users_backup(
    filename: str, admin: dict[str, Any] = Depends(require_admin)
):
    safe_name = Path(filename).name
    target = (github_backup.USERS_BACKUP_DIR / safe_name).resolve()
    if target.parent != github_backup.USERS_BACKUP_DIR.resolve() or not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo de respaldo no encontrado.")
    return FileResponse(target, filename=safe_name)


@app.get("/api/admin/bans")
async def admin_list_bans(
    include_inactive: bool = Query(default=False),
    admin: dict[str, Any] = Depends(require_admin),
):
    return {"bans": auth_database.list_bans(include_inactive=include_inactive)}


@app.post("/api/admin/bans")
async def admin_create_ban(
    payload: BanCreateRequest, admin: dict[str, Any] = Depends(require_admin)
):
    if payload.target_user_id is None and not (payload.ip_address and payload.ip_address.strip()):
        raise HTTPException(status_code=400, detail="Debes indicar un usuario o una IP.")
    try:
        ban = auth_database.create_ban(
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
    ok = auth_database.remove_ban(ban_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Baneo no encontrado.")
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
async def admin_download_backup(
    filename: str, admin: dict[str, Any] = Depends(require_admin)
):
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
    ua = get_user_agent(ws)
    user = auth_database.get_user_by_token(token)
    if not user:
        await ws.close(code=4001)
        return

    ban = auth_database.check_active_ban(user_id=user["id"], ip_address=ip)
    if ban:
        await ws.accept()
        await ws.send_json({"type": "banned", "reason": format_ban_error(ban)})
        await ws.close(code=4003)
        return

    if not user.get("is_impersonated"):
        auth_database.update_user_ip(user["id"], ip, ua)

    became_online = await manager.connect(ws, user, ip)
    await manager.broadcast_presence()

    if became_online and not user.get("is_impersonated"):
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
            active_ban = auth_database.check_active_ban(user_id=user["id"], ip_address=ip)
            if active_ban:
                await ws.send_json({"type": "banned", "reason": format_ban_error(active_ban)})
                await ws.close(code=4003)
                break

            fresh_user = auth_database.get_user_by_id(user["id"])
            if fresh_user and fresh_user.get("must_change_password"):
                await ws.send_json(
                    {"type": "error", "detail": "Debes cambiar tu contraseña antes de enviar mensajes."}
                )
                continue

            action = data.get("action")
            if action == "ping":
                await ws.send_json({"type": "pong"})
                continue

            if action == "send_channel_message":
                channel_id = int(data.get("channel_id") or 1)
                content = (data.get("content") or "").strip()
                image_meta = data.get("image")
                reply_to_id = data.get("reply_to_id")
                if not content and not image_meta:
                    continue

                can_post, reason = database.can_user_post_in_channel(
                    user["id"], channel_id, is_admin=(user["role"] == "admin")
                )
                if not can_post:
                    await ws.send_json({"type": "error", "detail": reason})
                    continue

                is_nsfw = bool(data.get("is_nsfw")) or (bool(image_meta.get("is_nsfw")) if image_meta else False)
                media_type = data.get("media_type") or (image_meta.get("media_type") if image_meta else ("image" if image_meta else "text"))
                media_duration = data.get("media_duration") or (image_meta.get("media_duration") if image_meta else None)
                is_gif_mode = bool(data.get("is_gif_mode") or (image_meta.get("is_gif_mode") if image_meta else False))

                msg_id = database.create_channel_message(
                    channel_id=channel_id,
                    user_id=user["id"],
                    content=content,
                    image_url=image_meta.get("image_url") or image_meta.get("media_url") if image_meta else None,
                    image_name=image_meta.get("image_name") or image_meta.get("media_name") if image_meta else None,
                    image_size_kb=image_meta.get("image_size_kb") or image_meta.get("media_size_kb") if image_meta else None,
                    image_dims=image_meta.get("image_dims") if image_meta else None,
                    is_hd=bool(image_meta.get("is_hd")) if image_meta else False,
                    expires_at=image_meta.get("expires_at") if image_meta else None,
                    is_nsfw=is_nsfw,
                    reply_to_id=int(reply_to_id) if reply_to_id else None,
                    media_type=media_type,
                    media_duration=media_duration,
                    is_gif_mode=is_gif_mode,
                )
                await manager.broadcast_channel_message(msg_id)

            elif action == "send_dm":
                receiver_id = int(data.get("receiver_id") or 0)
                content = (data.get("content") or "").strip()
                image_meta = data.get("image")
                reply_to_id = data.get("reply_to_id")
                if not receiver_id or (not content and not image_meta):
                    continue
                allowed, reason = database.can_send_dm(user["id"], receiver_id)
                if not allowed:
                    await ws.send_json({"type": "error", "detail": reason})
                    continue

                is_nsfw = bool(data.get("is_nsfw")) or (bool(image_meta.get("is_nsfw")) if image_meta else False)
                media_type = data.get("media_type") or (image_meta.get("media_type") if image_meta else ("image" if image_meta else "text"))
                media_duration = data.get("media_duration") or (image_meta.get("media_duration") if image_meta else None)
                is_gif_mode = bool(data.get("is_gif_mode") or (image_meta.get("is_gif_mode") if image_meta else False))

                dm_id = database.create_direct_message(
                    sender_id=user["id"],
                    receiver_id=receiver_id,
                    content=content,
                    image_url=image_meta.get("image_url") or image_meta.get("media_url") if image_meta else None,
                    image_name=image_meta.get("image_name") or image_meta.get("media_name") if image_meta else None,
                    image_size_kb=image_meta.get("image_size_kb") or image_meta.get("media_size_kb") if image_meta else None,
                    image_dims=image_meta.get("image_dims") if image_meta else None,
                    is_hd=bool(image_meta.get("is_hd")) if image_meta else False,
                    expires_at=image_meta.get("expires_at") if image_meta else None,
                    is_nsfw=is_nsfw,
                    reply_to_id=int(reply_to_id) if reply_to_id else None,
                    media_type=media_type,
                    media_duration=media_duration,
                    is_gif_mode=is_gif_mode,
                )
                await manager.send_dm_message(dm_id, user["id"], receiver_id)
                await manager.send_to_user(receiver_id, {"type": "friends_updated"})
                await manager.send_to_user(user["id"], {"type": "friends_updated"})

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
        if went_offline and not went_offline.get("is_impersonated"):
            latest_user = auth_database.get_user_by_id(went_offline["user_id"])
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
