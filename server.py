
import os
import uuid
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()

MAX_PLAYERS = 4

rooms = {}
room_counter = 0
room_lock = asyncio.Lock()


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


@app.get("/")
async def home():
    return {
        "game": "NeonArena",
        "status": "online",
        "max_players_per_room": MAX_PLAYERS
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.websocket("/game")
async def game_socket(websocket: WebSocket):
    await websocket.accept()

    player_id = str(uuid.uuid4())[:8]
    room_id = None
    player_name = "PLAYER"

    try:
        join_data = await websocket.receive_json()

        if join_data.get("type") != "join":
            await websocket.send_json({
                "type": "error",
                "message": "Join request required"
            })
            await websocket.close()
            return

        player_name = str(join_data.get("name", "PLAYER"))[:16]

        async with room_lock:
            room_id = find_or_create_room()
            rooms[room_id][player_id] = {
                "socket": websocket,
                "name": player_name
            }

        await websocket.send_json({
            "type": "joined",
            "player_id": player_id,
            "room": room_id,
            "max_players": MAX_PLAYERS
        })

        existing_players = []

        for other_id, player in rooms[room_id].items():
            if other_id != player_id:
                existing_players.append({
                    "player_id": other_id,
                    "name": player["name"]
                })

        await websocket.send_json({
            "type": "players",
            "players": existing_players
        })

        await send_to_room(room_id, {
            "type": "player_joined",
            "player_id": player_id,
            "name": player_name
        }, exclude=player_id)

        while True:
            data = await websocket.receive_json()

            if data.get("type") == "state":
                await send_to_room(room_id, {
                    "type": "state",
                    "player_id": player_id,
                    "state": data.get("state", {})
                }, exclude=player_id)

    except WebSocketDisconnect:
        pass

    except Exception as error:
        print("Connection error:", error)

    finally:
        if room_id is not None:
            async with room_lock:
                if room_id in rooms:
                    rooms[room_id].pop(player_id, None)

                    await send_to_room(room_id, {
                        "type": "player_left",
                        "player_id": player_id
                    })

                    if not rooms[room_id]:
                        del rooms[room_id]
