from typing import Any
"""
sessions.py
===========
OWNS: which gamertags are currently connected, on which websocket, and
how to send them a message. Nothing else.

This is a LEAF module: it imports nothing from the rest of this package,
on purpose. Every other module is allowed to import FROM sessions.py,
but sessions.py must never import any of them back -- that one-way rule
is what keeps the whole package's import graph a clean tree instead of
a tangle.

(This used to live inside connection.py. It got pulled out because
protocol.py, matchmaking.py, and game_dispatch.py all needed to reach
players and send errors, but connection.py ALSO needed to import
protocol.py to route incoming messages -- connection -> protocol ->
(matchmaking/game_dispatch) -> connection was a circular import. Moving
the shared state to its own leaf module breaks that cycle for good,
instead of just working around it.)
"""

import asyncio
import json

# gamertag (str) -> websocket
# This is the CURRENT live connection for that gamertag. If a player
# reconnects (e.g. flaky wifi, tab refresh), this entry gets overwritten
# with their new socket -- see connection.py's handle_client() for how
# that's done safely (it guards against deleting a newer connection out
# from under itself on disconnect cleanup).
players_by_gamertag = {}


def get_connection(gamertag: str) -> Any:
    """Look up the live websocket for a gamertag, or None if offline.

    Every module should use this instead of touching
    players_by_gamertag directly -- it keeps this dict as the ONE place
    that owns connection state.
    """
    return players_by_gamertag.get(gamertag)


def send_to(gamertag, payload: str):
    """Fire-and-forget a JSON string to a gamertag's current connection.

    The single choke point for "send a message to a player." If they're
    offline, this just no-ops. (Phase 1 of the production roadmap is
    where you'd add supervised error handling for failed sends here,
    instead of scattering `asyncio.create_task(ws.send(...))` calls
    across every module.)
    """
    ws = players_by_gamertag.get(gamertag)
    if ws is None:
        return
    try:
        from server.logger import log_net_send  # Deferred import: circular dependency
        log_net_send(gamertag, payload)
    except Exception as e:
        import sys  # Deferred import: circular dependency
        print(f"[Sessions] Send log failed: {e}", file=sys.stderr)
    asyncio.create_task(ws.send(payload))


def send_error(gamertag: str, message: str) -> None:
    """Shared helper: tell one player something went wrong.

    Lives here (not in protocol.py) because it's really just "format an
    error payload and call send_to" -- no routing logic involved. Every
    module that needs to report an error (matchmaking.py,
    game_dispatch.py, etc.) can import this directly with no risk of a
    circular import, since sessions.py never imports anything back.
    """
    send_to(gamertag, json.dumps({"type": "error", "message": message}))


def broadcast_server_stats() -> None:
    """Broadcasts current active player count to all connected clients."""
    count = len(players_by_gamertag)
    payload = json.dumps({"type": "server_stats", "online_players": count})
    for p_tag in list(players_by_gamertag.keys()):
        send_to(p_tag, payload)
