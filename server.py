import os
import uuid
import json
import hmac
import base64
import hashlib
import secrets
import asyncio
from datetime import datetime, timedelta, timezone

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from pydantic import BaseModel

app = FastAPI(title="NeonArena Server")
MAX_PLAYERS = 4
rooms = {}
room_counter = 0
room_lock = asyncio.Lock()
db_pool = None
password_hasher = PasswordHasher()
DATABASE_URL = os.getenv("DATABASE_URL", "")
JWT_SECRET = os.getenv("JWT_SECRET", "")
TOKEN_DAYS = 30


class AuthBody(BaseModel):
    username: str
    password: str


class ScoreBody(BaseModel):
    score: int
    level: int


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_token(user_id: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(json.dumps({
        "sub": user_id,
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=TOKEN_DAYS)).timestamp())
    }, separators=(",", ":")).encode())
    body = f"{header}.{payload}"
    signature = hmac.new(JWT_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{b64url(signature)}"


def read_token(token: str):
    try:
        header, payload, signature = token.split(".")
        body = f"{header}.{payload}"
        expected = b64url(hmac.new(JWT_SECRET.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if int(data.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return data
    except Exception:
        return None


async def get_user_from_header(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Please log in")
    claims = read_token(authorization[7:].strip())
    if not claims:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again")
    return claims


@app.on_event("startup")
async def startup():
    global db_pool
    if not DATABASE_URL:
        print("WARNING: DATABASE_URL is not configured")
        return
    if not JWT_SECRET:
        print("WARNING: JWT_SECRET is not configured")
        return
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, command_timeout=15)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY,
                username TEXT NOT NULL,
                username_norm TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                best_score BIGINT NOT NULL DEFAULT 0,
                best_level INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    print("NeonArena database connected")


@app.on_event("shutdown")
async def shutdown():
    global db_pool
    if db_pool:
        await db_pool.close()


def require_database():
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database is not ready. Check DATABASE_URL and JWT_SECRET in Render")
    if not JWT_SECRET:
        raise HTTPException(status_code=503, detail="JWT_SECRET is not configured in Render")


async def user_summary(user_id: str):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT u.username, COALESCE(s.best_score, 0) AS best_score,
                   COALESCE(s.best_level, 1) AS best_level
            FROM users u LEFT JOIN user_stats s ON s.user_id = u.id
            WHERE u.id = $1::uuid
        """, user_id)
        rank = await conn.fetchval("""
            SELECT COUNT(*) + 1 FROM user_stats WHERE best_score >
            COALESCE((SELECT best_score FROM user_stats WHERE user_id = $1::uuid), 0)
        """, user_id)
    if not row:
        return None
    return {"username": row["username"], "best_score": int(row["best_score"]),
            "best_level": int(row["best_level"]), "rank": int(rank or 1)}


@app.get("/")
async def home():
    return {"game": "NeonArena", "status": "online", "max_players_per_room": MAX_PLAYERS,
            "database": "connected" if db_pool else "not_ready"}


@app.get("/health")
async def health():
    return {"status": "healthy", "database": bool(db_pool)}


@app.post("/auth/register")
async def register(body: AuthBody):
    require_database()
    username = body.username.strip()
    password = body.password
    if not 3 <= len(username) <= 16 or not username.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Username must be 3-16 characters: letters, numbers, _ or -")
    if not 8 <= len(password) <= 128:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    user_id = uuid.uuid4()
    try:
        password_hash = password_hasher.hash(password)
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO users (id, username, username_norm, password_hash) VALUES ($1, $2, $3, $4)",
                               user_id, username, username.casefold(), password_hash)
            await conn.execute("INSERT INTO user_stats (user_id) VALUES ($1)", user_id)
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="That username is already taken")
    token = make_token(str(user_id), username)
    return {"ok": True, "token": token, **(await user_summary(str(user_id)))}


@app.post("/auth/login")
async def login(body: AuthBody):
    require_database()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, username, password_hash FROM users WHERE username_norm = $1",
                                  body.username.strip().casefold())
    if not row:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    try:
        password_hasher.verify(row["password_hash"], body.password)
    except (VerifyMismatchError, InvalidHash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    if password_hasher.check_needs_rehash(row["password_hash"]):
        new_hash = password_hasher.hash(body.password)
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET password_hash = $1 WHERE id = $2", new_hash, row["id"])
    token = make_token(str(row["id"]), row["username"])
    return {"ok": True, "token": token, **(await user_summary(str(row["id"]))) }


@app.get("/me")
async def me(authorization: str | None = Header(default=None)):
    require_database()
    claims = await get_user_from_header(authorization)
    result = await user_summary(claims["sub"])
    if not result:
        raise HTTPException(status_code=401, detail="Account not found")
    return result


@app.get("/leaderboard")
async def leaderboard():
    require_database()
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT u.username, s.best_score AS score, s.best_level AS level
            FROM user_stats s JOIN users u ON u.id = s.user_id
            WHERE s.best_score > 0
            ORDER BY s.best_score DESC, s.updated_at ASC
            LIMIT 10
        """)
    return {"entries": [{"name": r["username"], "score": int(r["score"]), "level": int(r["level"])} for r in rows]}


@app.post("/scores")
async def submit_score(body: ScoreBody, authorization: str | None = Header(default=None)):
    require_database()
    claims = await get_user_from_header(authorization)
    score = max(0, min(int(body.score), 2_000_000_000))
    level = max(1, min(int(body.level), 25))
    async with db_pool.acquire() as conn:
        previous = await conn.fetchrow("SELECT best_score FROM user_stats WHERE user_id = $1::uuid", claims["sub"])
        old_best = int(previous["best_score"]) if previous else 0
        await conn.execute("""
            INSERT INTO user_stats (user_id, best_score, best_level, updated_at)
            VALUES ($1::uuid, $2, $3, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                best_score = GREATEST(user_stats.best_score, EXCLUDED.best_score),
                best_level = CASE WHEN EXCLUDED.best_score > user_stats.best_score
                                  THEN EXCLUDED.best_level ELSE user_stats.best_level END,
                updated_at = CASE WHEN EXCLUDED.best_score > user_stats.best_score
                                  THEN NOW() ELSE user_stats.updated_at END
        """, claims["sub"], score, level)
    summary = await user_summary(claims["sub"])
    return {"ok": True, "new_record": score > old_best, **summary}


# Matchmaking and player state

def find_or_create_room():
    global room_counter
    for room_id, players in rooms.items():
        if len(players) < MAX_PLAYERS:
            return room_id
    room_counter += 1
    room_id = f"room-{room_counter:03d}"
    rooms[room_id] = {}
    return room_id


async def send_to_room(room_id, message, exclude=None):
    players = rooms.get(room_id, {})
    for player_id, player in list(players.items()):
        if player_id == exclude:
            continue
        try:
            await player["socket"].send_json(message)
        except Exception:
            pass


@app.websocket("/game")
async def game_socket(websocket: WebSocket):
    await websocket.accept()
    player_id = str(uuid.uuid4())[:8]
    room_id = None
    player_name = "PLAYER"
    try:
        join_data = await websocket.receive_json()
        if join_data.get("type") != "join":
            await websocket.send_json({"type": "error", "message": "Join request required"})
            await websocket.close()
            return
        claims = read_token(str(join_data.get("token", "")))
        if not claims:
            await websocket.send_json({"type": "error", "message": "Please log in to use online multiplayer"})
            await websocket.close()
            return
        player_name = str(claims.get("username", "PLAYER"))[:16]
        async with room_lock:
            room_id = find_or_create_room()
            rooms[room_id][player_id] = {"socket": websocket, "name": player_name}
        await websocket.send_json({"type": "joined", "player_id": player_id, "room": room_id,
                                   "max_players": MAX_PLAYERS})
        existing_players = [{"player_id": other_id, "name": player["name"]}
                            for other_id, player in rooms[room_id].items() if other_id != player_id]
        await websocket.send_json({"type": "players", "players": existing_players})
        await send_to_room(room_id, {"type": "player_joined", "player_id": player_id,
                                     "name": player_name}, exclude=player_id)
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "state":
                state = data.get("state", {})
                if not isinstance(state, dict):
                    continue
                await send_to_room(room_id, {"type": "state", "player_id": player_id,
                                             "state": state}, exclude=player_id)
    except WebSocketDisconnect:
        pass
    except Exception as error:
        print("Connection error:", str(error)[:200])
    finally:
        if room_id is not None:
            async with room_lock:
                if room_id in rooms:
                    rooms[room_id].pop(player_id, None)
                    await send_to_room(room_id, {"type": "player_left", "player_id": player_id})
                    if not rooms[room_id]:
                        del rooms[room_id]
