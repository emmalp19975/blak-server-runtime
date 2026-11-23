"""
rooms.py
========
OWNS: the concept of a "room" -- creating them, adding players to them,
looking them up, and broadcasting room/game state to everyone in one.

This module does NOT decide *how* rooms get filled (host codes vs quick
match vs anything you add later) -- that's matchmaking.py's job. Keeping
that decision out of here means rooms.py stays simple and stable even as
matchmaking grows new modes.

This is also the lowest-level "shared resource" module -- it's allowed to
depend on sessions.py (to send messages), but nothing else should ever
need to depend on it in the OTHER direction (matchmaking.py and
game_dispatch.py both depend on rooms.py, never the reverse). Keeping
that one-way relationship is what makes a future swap to Redis-backed
state (see production roadmap, Phase 2) a change localized to this file.
"""

import asyncio
import json
import uuid

from game_engine import CardGame

from server import sessions

# room_id (str) -> room dict
# A room dict looks like:
#   {
#       "id": str,
#       "code": str | None,       # set only for "private" rooms
#       "mode": "private" | "quickmatch",
#       "game": CardGame,         # the engine instance scoped to this room
#       "room_size": int,         # how many players this room needs to start
#       "player_ids": [str, ...], # gamertags currently seated in this room
#       "players": {gamertag: websocket, ...},  # convenience cache for broadcasting
#       "ready_players": set(),   # gamertags who've sent "player_ready"
#       "host": str | None,       # gamertag of whoever hosted (private rooms only)
#   }
rooms = {}


def create_room(room_size: int, mode: str) -> dict:
    """Create an empty room shell.

    This does NOT seat any players yet -- call add_player_to_room()
    afterwards for each player. Splitting creation from seating is what
    lets private rooms fill up gradually (host creates the room, then
    other players trickle in over time via room codes), unlike the old
    single-file version where a room only ever came into existence with
    both players already known.
    """
    room_id = str(uuid.uuid4())[:8]
    game = CardGame()

    # Wire the game engine's signals to this room's broadcast functions.
    # `rid=room_id` captures the room id by value (not by reference) so
    # each room's callback correctly points at ITS OWN room, even though
    # this create_room() function will be called many times.
    game.state_updated.connect(lambda rid=room_id: on_state_updated(rid))
    game.ladder_completed.connect(
        lambda player_id, slot_index, rid=room_id: on_ladder_completed(rid, player_id, slot_index)
    )
    game.game_over.connect(lambda winner_id, rid=room_id: on_game_over(rid, winner_id))

    room = {
        "id": room_id,
        "code": None,
        "mode": mode,
        "game": game,
        "room_size": room_size,
        "player_ids": [],
        "players": {},
        "ready_players": set(),
        "host": None,
        "game_started": False,
        "seed": None,
        "bots": {},
        "bot_profiles": {},
        "characters": {},
        "themes": {},
        "active_bot_task": None,
        "timer_enabled": True,
        "initial_timer": 15.0,
        "timer_increment": 4.0,
    }
    rooms[room_id] = room
    return room


def add_player_to_room(room: dict, gamertag: str) -> None:
    """Seat a player into an already-created room.

    Pulls their websocket from sessions.py rather than assuming the
    caller has it -- this keeps connection state owned in exactly one
    place (sessions.py), which rooms.py just borrows from.
    """
    room["player_ids"].append(gamertag)
    room["players"][gamertag] = sessions.get_connection(gamertag)


def get_room(room_id: str):
    """Look up a room by id, or None if it doesn't exist."""
    return rooms.get(room_id)


def find_room_for_gamertag(gamertag: str):
    """Which room (if any) is this gamertag currently seated in?

    NOTE: this is an O(rooms) scan. Fine at prototype scale; if you ever
    have thousands of concurrent rooms, consider maintaining a
    gamertag -> room_id index alongside `rooms` instead.
    """
    for room in rooms.values():
        if gamertag in room["player_ids"]:
            return room
    return None


def broadcast_room_update(room: dict) -> None:
    """Tell every player in a room its current roster/host/code.

    Sent whenever room membership changes (e.g. someone joins a private
    room, or a quick-match room finishes filling).
    """
    payload = json.dumps({
        "type": "room_update",
        "room_id": room["id"],
        "room_code": room.get("code") or "",
        "room_size": room["room_size"],
        "players": room["player_ids"],
        "host": room.get("host") or "",
        "ready_players": list(room.get("ready_players", set())),
        "seed": room.get("seed"),
        "bot_players": list(room.get("bots", {}).keys()),
        "bot_profiles": room.get("bot_profiles", {}),
        "characters": room.get("characters", {}),
        "themes": room.get("themes", {}),
        "initial_timer": float(room.get("initial_timer", 15.0)),
        "timer_increment": float(room.get("timer_increment", 4.0)),
        "timer_enabled": bool(room.get("timer_enabled", True)),
    })
    for gamertag in room["players"]:
        sessions.send_to(gamertag, payload)


def broadcast_to_room(room: dict, payload: str) -> None:
    """Generic helper: send a raw JSON payload to everyone in a room.

    Used by game_dispatch.py (e.g. to announce "game_started") so it
    doesn't need to know room internals like the `players` dict shape --
    it just calls this.
    """
    for gamertag in room["players"]:
        sessions.send_to(gamertag, payload)


# ---------------------------------------------------------------------------
# Game-engine signal handlers.
#
# These bridge "the CardGame engine did something" -> "tell the room's
# players about it." They live here (not in game_dispatch.py) because
# they're fundamentally about ROOM-level fan-out/broadcasting, not about
# interpreting player actions.
# ---------------------------------------------------------------------------

def on_ladder_completed(room_id: str, player_id: str, slot_index: int) -> None:
    room = rooms.get(room_id)
    if room is None:
        return

    payload = json.dumps({
        "type": "ladder_completed",
        "player_id": player_id,
        "slot_index": slot_index,
    })
    broadcast_to_room(room, payload)


def on_state_updated(room_id: str) -> None:
    room = rooms.get(room_id)
    if room is None:
        return

    game = room["game"]
    timer_enabled = bool(room.get("timer_enabled", True))
    initial_timer = float(room.get("initial_timer", 15.0))
    timer_increment = float(room.get("timer_increment", 4.0))

    # Unlike the other broadcasts, this one is PER-PLAYER: each player
    # only gets to see their own hand / hidden info, never their
    # opponents'. `get_sanitized_state_for_player` is presumably where
    # CardGame strips out anything that player shouldn't see.
    for player_id in room["players"]:
        sanitized = game.get_sanitized_state_for_player(player_id)
        sanitized["timer_enabled"] = timer_enabled
        sanitized["initial_timer"] = initial_timer
        sanitized["timer_increment"] = timer_increment
        if game.get_active_player() == player_id:
            try:
                from server.mimic_tuner import get_ai_hint  # Deferred import: circular dependency
                hint = get_ai_hint(player_id, game)
                if hint:
                    sanitized["ai_hint"] = hint
            except Exception as e:
                print(f"[Rooms] AI hint error for {player_id}: {e}")
        payload = json.dumps({"type": "state_update", **sanitized})
        sessions.send_to(player_id, payload)

    # Check if active player is an AI bot and schedule turn
    if room.get("game_started") and room.get("bots"):
        active_player = game.get_active_player()
        if active_player in room["bots"]:
            current_task = room.get("active_bot_task")
            if current_task is None or current_task.done():
                room["active_bot_task"] = asyncio.create_task(_execute_bot_turn(room, active_player))


async def _execute_bot_turn(room: dict, bot_id: str) -> None:
    try:
        await asyncio.sleep(0.4)
        bot = room.get("bots", {}).get(bot_id)
        if bot is not None and room.get("game"):
            await bot.take_turn(room["game"], recorder=room.get("recorder"))
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print(f"Error during bot turn for '{bot_id}' in room {room.get('id')}: {exc}")
    finally:
        room["active_bot_task"] = None
        # If the next active player is ALSO a bot, trigger it
        if room.get("game_started") and room.get("bots"):
            game = room.get("game")
            if game:
                next_active = game.get_active_player()
                if next_active in room["bots"]:
                    room["active_bot_task"] = asyncio.create_task(_execute_bot_turn(room, next_active))


# ---------------------------------------------------------------------------
# Room Teardown & Departure Handlers
# ---------------------------------------------------------------------------

room_closed_callbacks = []


def register_room_closed_callback(callback) -> None:
    room_closed_callbacks.append(callback)


def close_room(room_id: str) -> None:
    """Destroy a room, notify listeners (e.g. to unregister room code), and clean up state."""
    room = rooms.pop(room_id, None)
    if room is None:
        return

    print(f"Closing room {room_id} (code: {room.get('code')})")

    for callback in room_closed_callbacks:
        callback(room)

    task = room.get("active_bot_task")
    if task and not task.done():
        task.cancel()

    for p_id in room.get("player_ids", []):
        grace_task = _disconnect_grace_tasks.pop(p_id, None)
        if grace_task and not grace_task.done():
            grace_task.cancel()

    room.get("bots", {}).clear()
    room.get("bot_profiles", {}).clear()
    room["player_ids"].clear()
    room["players"].clear()
    room["ready_players"].clear()


def on_game_over(room_id: str, winner_id: str) -> None:
    """Broadcast victory to all players in the room, then cleanly destroy the room."""
    room = rooms.get(room_id)
    if room is None:
        return

    recorder = room.get("recorder")
    if recorder is not None:
        try:
            recorder.end_match(winner_id)
            saved_path = recorder.save()
            print(f"Room {room_id}: match trajectory saved to {saved_path} (winner defeated: '{recorder.winner_defeated}', type: '{recorder.match_type}').")
        except Exception as e:
            print(f"Warning: Failed to save match trajectory: {e}")

    payload = json.dumps({
        "type": "game_over",
        "winner": winner_id,
    })
    broadcast_to_room(room, payload)
    human_players = [p for p in room.get("player_ids", []) if not p.startswith("Bot ")]

    # Broadcast current static scoreboard snapshot instantly (0ms latency, zero CPU simulation)
    try:
        from server.scoreboard import get_public_scoreboard
        sb_data = get_public_scoreboard()
        sb_json = json.dumps(sb_data)
        for p_tag in list(sessions.players_by_gamertag.keys()):
            sessions.send_to(p_tag, sb_json)
    except Exception as exc:
        print(f"[Rooms] Scoreboard snapshot broadcast warning: {exc}")

    close_room(room_id)


_disconnect_grace_tasks: dict[str, asyncio.Task] = {}
DISCONNECT_GRACE_SECONDS: float = 20.0


def _finalize_active_player_left(gamertag: str, room_id: str) -> None:
    """Executed after the disconnect grace timer expires without reconnection."""
    room = rooms.get(room_id)
    if room is None:
        return

    game = room.get("game")
    if not game:
        return

    print(f"Room {room_id}: Disconnect grace period expired for '{gamertag}'. Finalizing departure.")
    try:
        from server.logger import log_system
        log_system(f"Room {room_id}: Disconnect grace period expired for '{gamertag}'. Finalizing departure.")
    except Exception:
        pass

    human_players = [p for p in room["player_ids"] if p not in room.get("bots", {}) and p != gamertag]
    if len(human_players) == 0:
        print(f"Room {room_id}: All human players have left. Closing room.")
        close_room(room_id)
        return

    remaining_players = [p for p in room["player_ids"] if p != gamertag]

    if len(remaining_players) <= 1:
        winner = remaining_players[0] if remaining_players else None
        if winner:
            payload = json.dumps({
                "type": "game_over",
                "winner": winner,
                "reason": f"{gamertag} disconnected",
            })
            broadcast_to_room(room, payload)
        close_room(room_id)
    else:
        if gamertag in room["player_ids"]:
            room["player_ids"].remove(gamertag)
        room["players"].pop(gamertag, None)
        room["ready_players"].discard(gamertag)

        if gamertag == room.get("host") and remaining_players:
            room["host"] = remaining_players[0]

        broadcast_to_room(room, json.dumps({
            "type": "player_left_match",
            "player_id": gamertag,
            "remaining_players": remaining_players,
        }))

        game.remove_player(gamertag)


def handle_player_left(gamertag: str) -> None:
    """Handle a player disconnecting or leaving their room.
    
    - In Lobby: If host leaves, dissolve room. If guest leaves, remove and broadcast update.
    - In Active Game:
        - Grants a 20-second grace window to allow mobile/network auto-reconnect.
        - If not reconnected by expiry, remaining player wins by forfeit or match auto-advances.
    """
    room = find_room_for_gamertag(gamertag)
    if room is None:
        return

    room_id = room["id"]
    game = room.get("game")
    is_active_game = bool(game and len(game.player_order) > 0 and room.get("game_started", False))

    print(f"Player '{gamertag}' disconnected/left room {room_id} (active_game={is_active_game})")

    if not is_active_game:
        # Pre-game lobby: immediate teardown
        if gamertag == room.get("host"):
            payload = json.dumps({
                "type": "room_closed",
                "reason": "Host left the room",
            })
            broadcast_to_room(room, payload)
            close_room(room_id)
        else:
            if gamertag in room["player_ids"]:
                room["player_ids"].remove(gamertag)
            room["players"].pop(gamertag, None)
            room["ready_players"].discard(gamertag)
            broadcast_room_update(room)
    else:
        # Active match: start 20s grace period for reconnect
        old_task = _disconnect_grace_tasks.pop(gamertag, None)
        if old_task and not old_task.done():
            old_task.cancel()

        print(f"Room {room_id}: Player '{gamertag}' disconnected. Starting {DISCONNECT_GRACE_SECONDS}s grace period.")
        try:
            from server.logger import log_system
            log_system(f"Room {room_id}: Disconnect grace period ({DISCONNECT_GRACE_SECONDS}s) started for '{gamertag}'")
        except Exception:
            pass

        broadcast_to_room(room, json.dumps({
            "type": "player_connection_status",
            "player_id": gamertag,
            "status": "disconnected",
            "grace_seconds": DISCONNECT_GRACE_SECONDS,
        }))

        async def _grace_coro():
            try:
                await asyncio.sleep(DISCONNECT_GRACE_SECONDS)
                _finalize_active_player_left(gamertag, room_id)
            except asyncio.CancelledError:
                pass
            finally:
                _disconnect_grace_tasks.pop(gamertag, None)

        task = asyncio.create_task(_grace_coro())
        _disconnect_grace_tasks[gamertag] = task


def handle_player_reconnected(gamertag: str) -> bool:
    """Invoked when a player reconnects and logs in. If they were in an active match, restores their session."""
    grace_task = _disconnect_grace_tasks.pop(gamertag, None)
    if grace_task and not grace_task.done():
        grace_task.cancel()
        print(f"Room: Cancelled disconnect grace timer for '{gamertag}' (reconnected successfully).")

    room = find_room_for_gamertag(gamertag)
    if room is None:
        return False

    ws = sessions.get_connection(gamertag)
    if ws is not None:
        room["players"][gamertag] = ws

    game = room.get("game")
    is_active_game = bool(game and len(game.player_order) > 0 and room.get("game_started", False))
    if is_active_game:
        print(f"Player '{gamertag}' re-linked to active match in room {room['id']}.")
        try:
            from server.logger import log_system
            log_system(f"Player '{gamertag}' reconnected and restored to active match in room {room['id']}")
        except Exception:
            pass

        broadcast_to_room(room, json.dumps({
            "type": "player_connection_status",
            "player_id": gamertag,
            "status": "reconnected",
        }))

        state = game.get_game_state_for_player(gamertag)
        state["type"] = "state_update"
        if room.get("timer_enabled") is not None:
            state["timer_enabled"] = room.get("timer_enabled")
            state["initial_timer"] = float(room.get("initial_timer", 15.0))
            state["timer_increment"] = float(room.get("timer_increment", 4.0))
        sessions.send_to(gamertag, json.dumps(state))
        return True

    return False
