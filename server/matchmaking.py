"""
matchmaking.py
==============
OWNS: the three ways a player can end up seated in a room --
hosting a private room, joining one by code, or quick-matching into
an auto-paired room of a chosen size.

This is deliberately kept separate from rooms.py: rooms.py knows how to
CREATE and MANAGE a room once it exists, but has no opinion on how it got
filled. This module is where you'd add new matching strategies later
(skill-based ranked queues, party invites, etc.) without ever touching
rooms.py or game_dispatch.py.
"""

import asyncio
import json
import random
import string

from server import rooms as rooms_module
from server import sessions
from server.sessions import send_error

MIN_ROOM_SIZE = 2
MAX_ROOM_SIZE = 8

# room_code (str, e.g. "K3F9QZ") -> room_id
# Only private ("host_room") rooms get a code -- quick-match rooms don't
# need one since players never type them in.
room_codes = {}

# room_size (int) -> list of gamertags waiting for a quick match of that size.
queues_by_size = {}
player_characters = {}
player_themes = {}
_quickmatch_timers = {}

RANDOM_BOT_LEVELS = [
    ("Novice", "Level 1: Novice"),
    ("Apprentice", "Level 2: Apprentice"),
    ("Adept", "Level 3: Adept"),
    ("Master", "Level 4: Master"),
    ("Absolute King", "Level 5: Absolute King"),
]


def add_random_bot_to_room(room: dict, bot_num: int) -> str:
    """Helper to seat an AI bot with a random difficulty level into a room."""
    from server.bot import ServerBot

    clean_key, display_profile = random.choice(RANDOM_BOT_LEVELS)
    bot_name = f"Bot {bot_num} ({clean_key})"
    bot_instance = ServerBot(bot_name, profile_name=clean_key, action_delay=1.0)

    room.setdefault("bots", {})[bot_name] = bot_instance
    room.setdefault("bot_profiles", {})[bot_name] = display_profile

    bot_chars = ["tarma", "eri", "fio", "marco"]
    assigned_char = bot_chars[(bot_num - 1) % len(bot_chars)]
    room.setdefault("characters", {})[bot_name] = assigned_char
    assigned_theme = random.choice(["classic", "crayon_day_night"])
    room.setdefault("themes", {})[bot_name] = assigned_theme

    room["player_ids"].append(bot_name)
    room["ready_players"].add(bot_name)
    return bot_name


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def generate_room_code(length: int = 4) -> str:
    """Random 4-digit numeric code for private rooms, guaranteed unique
    among currently-active codes."""
    while True:
        code = f"{random.randint(1000, 9999)}"
        if code not in room_codes:
            return code


def clamp_room_size(raw_size) -> int:
    """Sanitize a client-supplied room size into the allowed range.

    Client input is never trusted as-is -- this guards against garbage
    values (strings, negative numbers, huge numbers) collapsing the
    server into a bad state.
    """
    try:
        size = int(raw_size)
    except (TypeError, ValueError):
        size = MIN_ROOM_SIZE
    return max(MIN_ROOM_SIZE, min(size, MAX_ROOM_SIZE))


def remove_from_queues(gamertag: str) -> None:
    """Called by connection.py when a gamertag disconnects or cancels search,
    cleaning up player queues and timers."""
    player_characters.pop(gamertag, None)
    player_themes.pop(gamertag, None)
    for room_size, queue in list(queues_by_size.items()):
        if gamertag in queue:
            queue.remove(gamertag)
            if not queue:
                timer = _quickmatch_timers.pop(room_size, None)
                if timer and not timer.done():
                    timer.cancel()


def handle_cancel_quick_match(gamertag: str, data: dict = None) -> None:
    """Player cancelled their quick match search from the UI."""
    remove_from_queues(gamertag)
    print(f"'{gamertag}' cancelled quick match search.")


# ---------------------------------------------------------------------------
# Host / Join -- private, code-based rooms
# ---------------------------------------------------------------------------

def handle_host_room(gamertag: str, data: dict) -> None:
    """Player wants to create a new private room and get a shareable code."""
    if rooms_module.find_room_for_gamertag(gamertag) is not None:
        send_error(gamertag, "You're already in a room.")
        return

    room_size = clamp_room_size(data.get("room_size", MIN_ROOM_SIZE))
    seed = data.get("seed")

    room = rooms_module.create_room(room_size, "private")
    room["seed"] = seed
    code = generate_room_code()
    room["code"] = code
    room_codes[code] = room["id"]
    room["host"] = gamertag
    player_char = str(data.get("character", "marco")).strip().lower()
    room.setdefault("characters", {})[gamertag] = player_char
    player_theme = str(data.get("theme", "classic")).strip().lower()
    room.setdefault("themes", {})[gamertag] = player_theme
    rooms_module.add_player_to_room(room, gamertag)

    sessions.send_to(gamertag, json.dumps({
        "type": "room_created",
        "room_id": room["id"],
        "room_code": code,
        "room_size": room_size,
        "players": room["player_ids"],
        "host": gamertag,
        "ready_players": [],
        "seed": seed,
        "characters": room.get("characters", {}),
        "themes": room.get("themes", {}),
    }))
    print(f"Private room {room['id']} ({code}) hosted by {gamertag}, size {room_size}, seed={seed}")


def handle_join_room(gamertag: str, data: dict) -> None:
    """Player wants to join an existing private room using its code."""
    if rooms_module.find_room_for_gamertag(gamertag) is not None:
        send_error(gamertag, "You're already in a room.")
        return

    code = str(data.get("room_code", "")).upper().strip()
    room_id = room_codes.get(code)
    room = rooms_module.get_room(room_id) if room_id else None

    if room is None:
        send_error(gamertag, f"No room found with code '{code}'.")
        return
    if len(room["player_ids"]) >= room["room_size"]:
        send_error(gamertag, "That room is already full.")
        return

    player_char = str(data.get("character", "marco")).strip().lower()
    room.setdefault("characters", {})[gamertag] = player_char
    player_theme = str(data.get("theme", "classic")).strip().lower()
    room.setdefault("themes", {})[gamertag] = player_theme
    rooms_module.add_player_to_room(room, gamertag)
    print(f"'{gamertag}' joined room {room['id']} ({code})")
    rooms_module.broadcast_room_update(room)


# ---------------------------------------------------------------------------
# Quick match -- auto-paired by desired room size, no code needed
# ---------------------------------------------------------------------------

def handle_quick_match(gamertag: str, data: dict) -> None:
    """Player wants to be auto-paired into a room of a given size (2, 3, or 4)."""
    if rooms_module.find_room_for_gamertag(gamertag) is not None:
        send_error(gamertag, "You're already in a room.")
        return
    for queue in queues_by_size.values():
        if gamertag in queue:
            send_error(gamertag, "You're already searching for a match.")
            return

    room_size = clamp_room_size(data.get("room_size", MIN_ROOM_SIZE))
    player_char = str(data.get("character", "marco")).strip().lower()
    player_characters[gamertag] = player_char
    player_theme = str(data.get("theme", "classic")).strip().lower()
    player_themes[gamertag] = player_theme

    queues_by_size.setdefault(room_size, []).append(gamertag)
    print(f"'{gamertag}' queued for a {room_size}-player quick match (character: {player_char}, theme: {player_theme}).")
    try_make_quickmatch(room_size)


def try_make_quickmatch(room_size: int) -> None:
    """Pair up waiting players, or schedule a countdown to backfill with bots."""
    queue = queues_by_size.get(room_size, [])
    if len(queue) >= room_size:
        timer = _quickmatch_timers.pop(room_size, None)
        if timer and not timer.done():
            timer.cancel()
        _create_and_start_quickmatch(room_size)
    else:
        timer = _quickmatch_timers.get(room_size)
        if timer is None or timer.done():
            _quickmatch_timers[room_size] = asyncio.create_task(_quickmatch_countdown(room_size))


async def _quickmatch_countdown(room_size: int, delay: float = 2.0) -> None:
    """Wait briefly for human players; if seats remain unfilled, backfill with random bots."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    finally:
        _quickmatch_timers.pop(room_size, None)

    queue = queues_by_size.get(room_size, [])
    if queue:
        _create_and_start_quickmatch(room_size)


def _create_and_start_quickmatch(room_size: int) -> None:
    queue = queues_by_size.get(room_size, [])
    if not queue:
        return

    num_to_take = min(len(queue), room_size)
    matched = [queue.pop(0) for _ in range(num_to_take)]

    room = rooms_module.create_room(room_size, "quickmatch")
    room["host"] = matched[0]

    for p_tag in matched:
        char = player_characters.pop(p_tag, "marco")
        theme = player_themes.pop(p_tag, "classic")
        room.setdefault("characters", {})[p_tag] = char
        room.setdefault("themes", {})[p_tag] = theme
        rooms_module.add_player_to_room(room, p_tag)
        room["ready_players"].add(p_tag)

    # If any seats remain empty, backfill with random bots
    needed_bots = room_size - len(matched)
    for b_idx in range(needed_bots):
        bot_num = b_idx + 1
        add_random_bot_to_room(room, bot_num)

    print(f"Quickmatch room {room['id']} (size {room_size}) created: humans={matched}, bots={list(room.get('bots', {}).keys())}")
    rooms_module.broadcast_room_update(room)

    from server import game_dispatch
    game_dispatch._start_room_game(room)

    # Continue matching if more players are queued
    if queue:
        try_make_quickmatch(room_size)

def handle_player_sign_in(gamertag: str, data: dict) -> None:
    sign_in_payload= {"type": "signed_in", "value": "true"}
    sessions.send_to(gamertag, json.dumps(sign_in_payload))
    try:
        from server.scoreboard import get_public_scoreboard  # Deferred import: circular dependency
        sessions.send_to(gamertag, json.dumps(get_public_scoreboard()))
    except Exception:
        pass


def handle_get_scoreboard(gamertag: str, data: dict = None) -> None:
    """Sends current champion and leaderboard standings to requesting client."""
    from server.scoreboard import get_public_scoreboard  # Deferred import: circular dependency
    sessions.send_to(gamertag, json.dumps(get_public_scoreboard()))


def handle_add_bot(gamertag: str, data: dict) -> None:
    """Host requests to seat an AI bot with a specified profile into the room."""
    room = rooms_module.find_room_for_gamertag(gamertag)
    if room is None:
        send_error(gamertag, "You are not in a room.")
        return

    if room.get("host") != gamertag:
        send_error(gamertag, "Only the room host can add bots.")
        return

    if room.get("game_started"):
        send_error(gamertag, "Cannot add bots after match has started.")
        return

    if len(room["player_ids"]) >= 4:
        send_error(gamertag, "Room is full (maximum 4 players).")
        return

    profile = str(data.get("profile", "Adept")).strip()
    if not profile:
        profile = "Adept"

    existing_bots = room.setdefault("bots", {})
    bot_num = 1
    existing_nums = [
        int(name.split(" ")[1])
        for name in room["player_ids"]
        if name.startswith("Bot ") and len(name.split(" ")) >= 2 and name.split(" ")[1].isdigit()
    ]
    while bot_num in existing_nums:
        bot_num += 1

    from sandbox.planner import DIFFICULTY_PROFILES  # Deferred import: circular dependency
    from server.bot import ServerBot  # Deferred import: circular dependency

    is_champion = any(k in profile for k in ("Champion", "Apex", "Mutant"))
    is_mimic = ("Mimic" in profile)

    if is_champion:
        from server.scoreboard import get_champion_config, get_current_champion  # Deferred import: circular dependency
        champ = get_current_champion()
        champ_name = champ.get("name", "Apex Mutant")
        mut_tag = champ.get("mutation_tag", "")
        tag_str = f" [{mut_tag}]" if mut_tag else ""
        custom_config = get_champion_config()
        display_profile = f"Apex Mutant ({champ_name}{tag_str})"
        bot_name = f"Bot {bot_num} (Apex Mutant{tag_str})"
        bot_instance = ServerBot(bot_name, profile_name=display_profile, action_delay=1.2, custom_config=custom_config)
    elif is_mimic:
        from server.mimic_tuner import load_profile  # Deferred import: circular dependency
        host = room.get("host") or gamertag
        custom_config = load_profile(host)
        display_profile = f"Mimic ({host})"
        bot_name = f"Bot {bot_num} (Mimic {host})"
        bot_instance = ServerBot(bot_name, profile_name=display_profile, action_delay=1.2, custom_config=custom_config)
    else:
        # Normalize profile key (strip potential "(Classic)", "(Neural)", or "(Cheater)" suffix if present)
        is_neural = any(k in profile for k in ("AlphaZero", "Neural"))
        clean_key = profile.replace("(Classic)", "").replace("(Neural)", "").replace("(Cheater)", "").strip()
        for key in ("Absolute King", "King", "Master", "Adept", "Apprentice", "Novice", "Oracle", "Houdini", "Apex", "Casual", "Tactical", "Aggressive", "Grandmaster"):
            if key.lower() in clean_key.lower():
                clean_key = key
                break
        if not is_neural and clean_key not in DIFFICULTY_PROFILES:
            clean_key = "Adept"

        custom_config = None
        if any(k in data for k in ("cheat_level", "cheat_rubberband", "cheat_hand_vision", "cheat_objective_lookahead", "cheat_deck_lookahead")):
            import copy
            base_cfg = DIFFICULTY_PROFILES.get(clean_key, DIFFICULTY_PROFILES["Tactical"])
            custom_config = copy.deepcopy(base_cfg)
            if "cheat_level" in data:
                custom_config.cheat_level = float(data["cheat_level"])
            if "cheat_rubberband" in data:
                custom_config.cheat_rubberband = bool(data["cheat_rubberband"])
            if "cheat_hand_vision" in data:
                custom_config.cheat_hand_vision = float(data["cheat_hand_vision"])
            if "cheat_objective_lookahead" in data:
                custom_config.cheat_objective_lookahead = int(data["cheat_objective_lookahead"])
            if "cheat_deck_lookahead" in data:
                custom_config.cheat_deck_lookahead = int(data["cheat_deck_lookahead"])

        if clean_key in ("Oracle", "Houdini", "Apex") or (custom_config and (custom_config.cheat_level > 0 or custom_config.cheat_rubberband)):
            display_profile = f"{clean_key} (Cheater)"
        else:
            display_profile = f"{clean_key} (Neural)" if is_neural else clean_key
        bot_name = f"Bot {bot_num} ({clean_key})"
        bot_instance = ServerBot(bot_name, profile_name=display_profile, action_delay=1.2, custom_config=custom_config)

    existing_bots[bot_name] = bot_instance
    room.setdefault("bot_profiles", {})[bot_name] = display_profile
    bot_chars = ["tarma", "eri", "fio", "marco"]
    assigned_char = bot_chars[(bot_num - 1) % len(bot_chars)]
    room.setdefault("characters", {})[bot_name] = assigned_char
    assigned_theme = random.choice(["classic", "crayon_day_night"])
    room.setdefault("themes", {})[bot_name] = assigned_theme

    room["player_ids"].append(bot_name)
    room["ready_players"].add(bot_name)

    print(f"Room {room['id']}: Added bot '{bot_name}' with profile '{display_profile}' and theme '{assigned_theme}'.")
    rooms_module.broadcast_room_update(room)


def handle_remove_bot(gamertag: str, data: dict = None) -> None:
    """Host requests to remove a bot (either target bot_name or most recently added) from the room."""
    room = rooms_module.find_room_for_gamertag(gamertag)
    if room is None:
        send_error(gamertag, "You are not in a room.")
        return

    if room.get("host") != gamertag:
        send_error(gamertag, "Only the room host can remove bots.")
        return

    if room.get("game_started"):
        send_error(gamertag, "Cannot remove bots after match has started.")
        return

    bots = room.get("bots", {})
    if not bots:
        send_error(gamertag, "There are no bots in this room.")
        return

    target_bot = data.get("bot_name") if data else None
    if not target_bot or target_bot not in bots:
        target_bot = list(bots.keys())[-1]

    del bots[target_bot]
    room.get("bot_profiles", {}).pop(target_bot, None)
    room.get("characters", {}).pop(target_bot, None)
    room.get("themes", {}).pop(target_bot, None)
    if target_bot in room["player_ids"]:
        room["player_ids"].remove(target_bot)
    room["ready_players"].discard(target_bot)

    print(f"Room {room['id']}: Removed bot '{target_bot}'.")
    rooms_module.broadcast_room_update(room)


def handle_update_bot_profile(gamertag: str, data: dict) -> None:
    """Host requests to change difficulty level for a specific bot."""
    room = rooms_module.find_room_for_gamertag(gamertag)
    if room is None or room.get("host") != gamertag or room.get("game_started"):
        return

    bot_name = data.get("bot_name")
    new_profile = data.get("profile")
    bots = room.get("bots", {})
    if not bot_name or not new_profile or bot_name not in bots:
        return

    import re
    from sandbox.planner import DIFFICULTY_PROFILES
    from server.bot import ServerBot

    clean_key = str(new_profile).replace("(Classic)", "").replace("(Neural)", "").replace("(Cheater)", "").strip()
    for key in ("Absolute King", "King", "Master", "Adept", "Apprentice", "Novice", "Oracle", "Houdini", "Apex", "Casual", "Tactical", "Aggressive", "Grandmaster"):
        if key.lower() in clean_key.lower():
            clean_key = key
            break
    if clean_key not in DIFFICULTY_PROFILES:
        clean_key = "Adept"

    display_profile = clean_key

    # Extract bot number or prefix (e.g. "Bot 1" from "Bot 1 (Adept)")
    match = re.match(r"^(Bot\s+\d+)", bot_name)
    bot_prefix = match.group(1) if match else bot_name
    new_bot_name = f"{bot_prefix} ({clean_key})"

    # Create new bot instance with updated name and profile
    bot_instance = ServerBot(new_bot_name, profile_name=display_profile, action_delay=1.2)

    # Migrate room state if bot name changed
    if new_bot_name != bot_name:
        if bot_name in room["player_ids"]:
            idx = room["player_ids"].index(bot_name)
            room["player_ids"][idx] = new_bot_name
        if bot_name in room.get("ready_players", set()):
            room["ready_players"].remove(bot_name)
            room["ready_players"].add(new_bot_name)
        if bot_name in room.get("characters", {}):
            char = room["characters"].pop(bot_name)
            room["characters"][new_bot_name] = char
        if bot_name in room.get("themes", {}):
            th = room["themes"].pop(bot_name)
            room["themes"][new_bot_name] = th
        room.setdefault("bot_profiles", {}).pop(bot_name, None)
        room.setdefault("bots", {}).pop(bot_name, None)

    room.setdefault("bots", {})[new_bot_name] = bot_instance
    room.setdefault("bot_profiles", {})[new_bot_name] = display_profile
    print(f"Room {room['id']}: Updated bot '{bot_name}' -> '{new_bot_name}' with profile '{display_profile}'.")
    rooms_module.broadcast_room_update(room)


def handle_leave_room(gamertag: str, data: dict = None) -> None:
    """Explicitly leave whichever room this player is seated in."""
    rooms_module.handle_player_left(gamertag)
    sessions.send_to(gamertag, json.dumps({"type": "left_room"}))


def _on_room_closed(room: dict) -> None:
    code = room.get("code")
    if code and code in room_codes:
        del room_codes[code]
        print(f"Unregistered room code: {code}")


rooms_module.register_room_closed_callback(_on_room_closed)
