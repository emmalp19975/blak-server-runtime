"""
game_dispatch.py
=================
OWNS: turning a validated player action into a call on the CardGame
engine, and deciding when a room's game actually starts.

This is the module every gameplay action passes through, which makes it
the natural home for Phase 0's "server-authoritative validation" work
later (e.g. confirming it's actually this player's turn, that they own
the card they're referencing) and Phase 5's anti-cheat/replay logging,
if this project ever grows that far. Right now it trusts client input
more than a production server should -- see the NOTE below.
"""

import json
import os
import random
from typing import Optional

from server import rooms as rooms_module
from server.sessions import send_error


def dispatch_action(gamertag: str, data: dict) -> None:
    """Route a gameplay action (play_card / discard_card /
    initiate_ladder_perk) to the right CardGame method, for whichever
    room this gamertag is currently seated in.
    """
    room = rooms_module.find_room_for_gamertag(gamertag)
    if room is None:
        print(f"'{gamertag}' sent an action but isn't in a room.")
        return

    game = room["game"]  # the CardGame instance scoped to THIS room only
    action_type = data.get("type")

    # NOTE (Phase 0 of the production roadmap): none of the values below
    # (card_index, owner_id, target_slot, etc.) are validated here before
    # being handed to the engine. A production version should confirm,
    # at minimum: it's this player's turn, the indices are in bounds, and
    # `owner_id` isn't being used to manipulate a card that isn't
    # actually theirs to reference. Whether CardGame itself enforces any
    # of this isn't visible from this file.

    recorder = room.get("recorder")
    if recorder is not None:
        try:
            recorder.record_step(game, action_type, data)
        except Exception as e:
            print(f"[Recorder] Step record failed: {e}")

    # Real-Time Preference Learning for Player Mimic Bot
    try:
        from server.mimic_tuner import learn_from_move  # Deferred import: circular dependency
        learn_from_move(gamertag, game, action_type, data, room)
    except Exception as exc:
        print(f"[MimicTuner] Learn from move failed: {exc}")

    if action_type == "play_card":
        source = data.get("source")
        owner_id = data.get("owner_id") or gamertag
        card_index = data.get("card_index")
        target_slot = data.get("target_slot")

        try:
            target_slot_int = int(target_slot)
        except (TypeError, ValueError):
            print(f"Invalid target_slot '{target_slot}' from {gamertag}")
            return

        if source == "hand":
            try:
                card_index_int = int(card_index)
            except (TypeError, ValueError):
                print(f"Invalid card_index '{card_index}' from {gamertag}")
                return
            game.play_from_hand(gamertag, card_index_int, target_slot_int)
        elif source == "discard":
            raw_slot = data.get("discard_slot", data.get("card_index", 0))
            try:
                discard_slot_int = int(raw_slot)
            except (TypeError, ValueError):
                discard_slot_int = 0
            game.play_from_discard(gamertag, owner_id, discard_slot_int, target_slot_int)
        elif source == "objective":
            game.play_from_objective(gamertag, owner_id, target_slot_int)
        else:
            print(f"Unknown play_card source '{source}' from {gamertag}")

    elif action_type == "discard_card":
        card_index = data.get("card_index")
        slot_index = data.get("slot_index", 0)
        end_turn = bool(data.get("end_turn", True))
        try:
            card_index_int = int(card_index)
            slot_index_int = int(slot_index)
        except (TypeError, ValueError):
            print(f"Invalid discard indices from {gamertag}")
            return
        success = game.discard_card(gamertag, card_index_int, slot_index_int, end_turn=end_turn)
        if success and end_turn:
            next_player = game.get_active_player()
            game.start_turn_draw(next_player)

    elif action_type == "end_turn":
        success = game.end_turn(gamertag)
        if success:
            next_player = game.get_active_player()
            game.start_turn_draw(next_player)

    elif action_type == "initiate_ladder_perk":
        slot_index = data.get("slot_index")
        try:
            slot_index_int = int(slot_index)
        except (TypeError, ValueError):
            print(f"Invalid perk slot_index from {gamertag}")
            return
        game.initiate_ladder_perk(slot_index_int, gamertag)

    elif action_type == "decline_ladder_perk":
        slot_index = data.get("slot_index")
        try:
            slot_index_int = int(slot_index)
        except (TypeError, ValueError):
            print(f"Invalid perk slot_index from {gamertag}")
            return
        game.decline_ladder_perk(slot_index_int, gamertag)

    else:
        print(f"Unknown action type '{action_type}' from {gamertag}")


def _get_random_golden_seed(player_count: int) -> Optional[int]:
    """Selects a random certified golden seed matching the player count."""
    golden_file = os.path.join(os.path.dirname(__file__), "golden_seeds.json")
    if not os.path.isfile(golden_file):
        return None
    try:
        with open(golden_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = f"{player_count}_players"
        seeds = data.get(key, [])
        if seeds:
            chosen = random.choice(seeds)
            if isinstance(chosen, dict):
                return int(chosen.get("seed", 0))
            return int(chosen)
    except Exception as e:
        print(f"Warning: Failed to load golden seed: {e}")
    return None


def _start_room_game(room: dict) -> None:
    """Helper to initialize the CardGame engine and notify players."""
    room["room_size"] = len(room["player_ids"])
    room["game_started"] = True
    game = room["game"]

    # If no custom host seed provided, draw a certified Golden Seed matching the player count
    seed = room.get("seed")
    if seed is None:
        seed = _get_random_golden_seed(len(room["player_ids"]))
        room["seed"] = seed

    game.init_game(room["player_ids"], seed=seed)
    print(f"Room {room['id']}: game started with players {room['player_ids']} (seed={game.get_seed()}).")

    payload = json.dumps({
        "type": "game_started",
        "seed": game.get_seed(),
        "timer_enabled": bool(room.get("timer_enabled", True)),
        "initial_timer": float(room.get("initial_timer", 15.0)),
        "timer_increment": float(room.get("timer_increment", 4.0)),
        "themes": room.get("themes", {}),
    })
    rooms_module.broadcast_to_room(room, payload)

    try:
        from server.recorder import ProductionGameRecorder  # Deferred import: circular dependency
        player_types = {}
        for p_id in room["player_ids"]:
            if p_id in room.get("bots", {}):
                prof = room.get("bot_profiles", {}).get(p_id, "Tactical")
                player_types[p_id] = {"type": "bot", "profile": prof}
            else:
                player_types[p_id] = {"type": "human", "profile": None}

        recorder = ProductionGameRecorder(match_id=room["id"])
        recorder.start_match(room["player_ids"], seed=game.get_seed(), player_types=player_types)
        room["recorder"] = recorder
    except Exception as e:
        print(f"Warning: Failed to init recorder for room {room['id']}: {e}")


async def handle_player_ready(gamertag: str) -> None:
    """A player has toggled their ready status.
    
    In private rooms, ready status is broadcast so the host knows who is ready.
    In quickmatch rooms, once all seats are filled and ready, the game starts automatically.
    """
    room = rooms_module.find_room_for_gamertag(gamertag)
    if room is None:
        print(f"'{gamertag}' sent player_ready but isn't in a room.")
        return

    if gamertag in room["ready_players"]:
        room["ready_players"].remove(gamertag)
    else:
        room["ready_players"].add(gamertag)

    print(f"Room {room['id']}: ready = {room['ready_players']}")
    rooms_module.broadcast_room_update(room)

    # In quickmatch, automatically start when all expected players are ready
    if room.get("mode") == "quickmatch" and len(room["ready_players"]) == room["room_size"]:
        _start_room_game(room)


def handle_start_match(gamertag: str, data: dict) -> None:
    """Host requests to start the match with current seated players (2-4 players)."""
    room = rooms_module.find_room_for_gamertag(gamertag)
    if room is None:
        print(f"'{gamertag}' sent start_match but isn't in a room.")
        return

    if room.get("host") != gamertag:
        send_error(gamertag, "Only the room host can start the match.")
        return

    player_count = len(room["player_ids"])
    if player_count < 2:
        send_error(gamertag, "Need at least 2 players to start a match.")
        return
    if player_count > 4:
        send_error(gamertag, "Cannot start match with more than 4 players.")
        return

    # Check that all connected non-host players are marked ready
    non_host_players = [p for p in room["player_ids"] if p != room.get("host")]
    unready = [p for p in non_host_players if p not in room["ready_players"]]
    if unready:
        send_error(gamertag, f"Waiting for players to be ready: {', '.join(unready)}")
        return

    # Check if host specified a custom seed
    if "seed" in data and data["seed"] is not None and str(data["seed"]).strip() != "":
        try:
            room["seed"] = int(data["seed"])
        except ValueError:
            room["seed"] = str(data["seed"]).strip()

    if "timer_enabled" in data:
        room["timer_enabled"] = bool(data["timer_enabled"])

    if "initial_timer" in data:
        try:
            val = float(data["initial_timer"])
            room["initial_timer"] = max(3.0, min(120.0, val))
        except (ValueError, TypeError):
            pass

    if "timer_increment" in data:
        try:
            val = float(data["timer_increment"])
            room["timer_increment"] = max(0.0, min(30.0, val))
        except (ValueError, TypeError):
            pass

    _start_room_game(room)


def handle_set_timer_settings(gamertag: str, data: dict) -> None:
    """Host sets the initial turn timer duration and increment awarded per card played."""
    room = rooms_module.find_room_for_gamertag(gamertag)
    if room is None:
        print(f"'{gamertag}' sent set_timer_settings but isn't in a room.")
        return

    if room.get("host") != gamertag:
        send_error(gamertag, "Only the room host can configure timer settings.")
        return

    if "timer_enabled" in data:
        room["timer_enabled"] = bool(data["timer_enabled"])

    if "initial_timer" in data:
        try:
            val = float(data["initial_timer"])
            room["initial_timer"] = max(3.0, min(120.0, val))
        except (ValueError, TypeError):
            pass

    if "timer_increment" in data:
        try:
            val = float(data["timer_increment"])
            room["timer_increment"] = max(0.0, min(30.0, val))
        except (ValueError, TypeError):
            pass

    print(f"Room {room['id']} timer settings updated: enabled={room.get('timer_enabled', True)}, initial={room.get('initial_timer', 15.0)}s, increment={room.get('timer_increment', 4.0)}s")
    rooms_module.broadcast_room_update(room)

