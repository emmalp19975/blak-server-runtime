"""
protocol.py
===========
OWNS: the wire format -- specifically, the mapping from an incoming
message's "type" field to whichever function should handle it.

This is what keeps connection.py's message loop a one-liner instead of a
growing if/elif chain. It's also the natural place to add Phase 4's
schema validation later: validate `data` against a schema here, BEFORE
dispatching, in one spot instead of scattered across every handler.

Import note: this file can now import matchmaking.py and game_dispatch.py
directly at the top, with no circular-import trickery needed. That's only
possible because send_error (which those two modules need) lives in
sessions.py, a true leaf module -- not here. Compare this to
connection.py, which still can't be imported by matchmaking.py or
game_dispatch.py, since connection.py sits ABOVE this file in the
dependency graph (connection.py -> protocol.py -> matchmaking.py /
game_dispatch.py -> rooms.py -> sessions.py). Keep it that way: if you
ever feel tempted to `from server import connection` inside this file or
anything it imports, that's a sign something's owned in the wrong module.
"""

from server import matchmaking
from server import game_dispatch

# Message type -> handler function.
# Handlers come in two flavors, and route_message() calls each correctly:
#   - async handlers (currently just handle_player_ready, since it needs
#     `await` internally) get awaited
#   - plain sync handlers (everything else) get called directly
HANDLERS = {
    "player_ready": game_dispatch.handle_player_ready,
    "player_signing_in": matchmaking.handle_player_sign_in,
    "host_room": matchmaking.handle_host_room,
    "join_room": matchmaking.handle_join_room,
    "quick_match": matchmaking.handle_quick_match,
    "cancel_quick_match": matchmaking.handle_cancel_quick_match,
    "play_card": game_dispatch.dispatch_action,
    "discard_card": game_dispatch.dispatch_action,
    "end_turn": game_dispatch.dispatch_action,
    "initiate_ladder_perk": game_dispatch.dispatch_action,
    "decline_ladder_perk": game_dispatch.dispatch_action,
    "start_match": game_dispatch.handle_start_match,
    "leave_room": matchmaking.handle_leave_room,
    "add_bot": matchmaking.handle_add_bot,
    "remove_bot": matchmaking.handle_remove_bot,
    "update_bot_profile": matchmaking.handle_update_bot_profile,
    "set_timer_settings": game_dispatch.handle_set_timer_settings,
    "get_scoreboard": matchmaking.handle_get_scoreboard,
}

# Message types whose handler is a coroutine and needs to be awaited,
# rather than called directly. Keeping this as an explicit set (rather
# than e.g. inspecting the function at call time) makes it obvious at a
# glance which handlers are async, just by reading this file.
ASYNC_HANDLERS = {"player_ready"}


async def route_message(gamertag: str, data: dict) -> None:
    """Look at data["type"] and call the matching handler.

    NOTE (Phase 4 of the production roadmap): this is also where you'd
    validate `data`'s shape against a schema (e.g. with pydantic) before
    handing it to a handler, instead of letting each handler defensively
    `.get()` its way through possibly-malformed input.
    """
    msg_type = data.get("type")
    handler = HANDLERS.get(msg_type)

    if handler is None:
        print(f"Unknown message type '{msg_type}' from {gamertag}")
        return

    if msg_type in ASYNC_HANDLERS:
        await handler(gamertag)
    else:
        handler(gamertag, data)
