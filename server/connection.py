from typing import Any
"""
connection.py
==============
OWNS: the raw websocket handshake and message loop -- identify a new
connection, register it, loop reading messages and handing them off to
the router, and clean up on disconnect.

Note this file no longer owns the players_by_gamertag dict itself --
that moved to sessions.py. This module now focuses purely on the
LIFECYCLE of a connection (handshake -> loop -> cleanup), while
sessions.py owns the actual state. That split is what lets this file
import protocol.py and matchmaking.py at the top of the file with no
circular-import problem: everything below this file only ever reaches
back down to sessions.py, never back up to connection.py.
"""

import json

import websockets

from server import sessions
from server import protocol
from server import matchmaking
from server import rooms


async def handle_client(websocket: Any) -> None:
    """Entry point for every new websocket connection.

    Responsibilities, in order:
      1. Wait for the client's "identify" message (which gamertag are they?)
      2. Register them in sessions.py
      3. Loop forever, parsing incoming JSON and handing it to
         protocol.route_message -- this function does NOT know what a
         "play_card" message means, it just delivers it.
      4. On disconnect, clean up -- but carefully (see the guard below).
    """
    print(f"Client connected from {websocket.remote_address}")

    # --- Step 1: identify ---------------------------------------------
    try:
        raw = await websocket.recv()
    except websockets.exceptions.ConnectionClosed:
        print("Client disconnected before identifying.")
        return

    print(f"Received: {raw}")

    try:
        identify_msg = json.loads(raw)
        gamertag = identify_msg["gamertag"]
    except (json.JSONDecodeError, KeyError, TypeError):
        print(f"Malformed identify message, dropping connection: {raw}")
        return

    # NOTE (Phase 0 of the production roadmap): this is where a real auth
    # check belongs. Right now anyone can claim any gamertag just by
    # typing it -- there's no verification the caller actually owns this
    # identity. Before this is safe to expose to strangers, `identify_msg`
    # needs to carry a signed token (JWT / session token) that gets
    # validated here, and `gamertag` should come FROM the verified token,
    # not from an unauthenticated client-supplied field.

    # --- Step 2: register (or re-link) this gamertag -------------------
    sessions.players_by_gamertag[gamertag] = websocket

    sessions.send_to(gamertag, json.dumps({
        "type": "Network",
        "logged_in": "true",
    }))
    sessions.send_to(gamertag, json.dumps({
        "type": "signed_in",
        "value": "true",
        "online_players": len(sessions.players_by_gamertag),
    }))
    sessions.broadcast_server_stats()

    print(f"'{gamertag}' linked to a new connection.")
    print(f"Live gamertags: {list(sessions.players_by_gamertag.keys())}")

    try:
        from server.logger import log_net_connect  # Deferred import: circular dependency
        log_net_connect(gamertag, websocket.remote_address)
    except Exception:
        pass

    # Check if this player reconnected to an active match in progress
    rooms.handle_player_reconnected(gamertag)

    # --- Step 3: message loop ------------------------------------------
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                print(f"Malformed message from {gamertag}: {message}")
                continue

            try:
                from server.logger import log_net_recv  # Deferred import: circular dependency
                log_net_recv(gamertag, data)
            except Exception:
                pass

            # Hand off entirely to protocol.py. This function's job ends
            # at "I have valid JSON from a known gamertag" -- it doesn't
            # need to know what happens next.
            await protocol.route_message(gamertag, data)

    except websockets.exceptions.ConnectionClosed as exc:
        print(f"'{gamertag}' disconnected (code={exc.code}, reason='{exc.reason}').")
        try:
            from server.logger import log_net_disconnect  # Deferred import: circular dependency
            log_net_disconnect(gamertag, exc.code, exc.reason)
        except Exception:
            pass

    finally:
        # --- Step 4: cleanup, with a race-condition guard ---------------
        # Why the `is websocket` check: imagine the player's wifi drops,
        # they reconnect on a NEW socket (which overwrites
        # sessions.players_by_gamertag[gamertag] with the new
        # connection), and THEN this finally block for the OLD
        # connection finally fires. Without this guard, we'd incorrectly
        # delete the entry for the new, still-live connection.
        if sessions.players_by_gamertag.get(gamertag) is websocket:
            del sessions.players_by_gamertag[gamertag]
            print(f"'{gamertag}' fully disconnected.")

            # Let matchmaking know this gamertag is gone, so it doesn't
            # sit forever in a quick-match queue for a socket that no
            # longer exists.
            matchmaking.remove_from_queues(gamertag)

            # Clean up or update the room this player was in
            rooms.handle_player_left(gamertag)
            sessions.broadcast_server_stats()
        else:
            print(f"Old connection for '{gamertag}' closed, but a newer one is already active.")

        print(f"Live gamertags: {list(sessions.players_by_gamertag.keys())}")
