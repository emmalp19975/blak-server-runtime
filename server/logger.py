"""
server/logger.py
================
Per-Session Network & LLM Debug Logger for Blak Game Server.

Captures all granular network traffic (incoming/outgoing WebSocket packets)
and exact LLM prompts, completions, thoughts, latencies, and errors into
per-session log files (logs/sessions/session_<timestamp>.log) and logs/latest.log.
Keeps server stdout/terminal clean and readable.
"""

from __future__ import annotations
import datetime
import json
import os
import threading
import uuid
from typing import Any, Optional

PROJECT_ROOT: str = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOGS_DIR: str = os.path.join(PROJECT_ROOT, "logs")
SESSIONS_DIR: str = os.path.join(LOGS_DIR, "sessions")
LATEST_LOG_PATH: str = os.path.join(LOGS_DIR, "latest.log")

_lock = threading.Lock()
_current_session_file: Optional[str] = None


def _get_session_log_path() -> str:
    global _current_session_file
    if _current_session_file is None:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = str(uuid.uuid4())[:6]
        _current_session_file = os.path.join(SESSIONS_DIR, f"session_{now_str}_{short_id}.log")
    return _current_session_file


def _write_log(entry: str) -> None:
    session_file = _get_session_log_path()
    try:
        with _lock:
            with open(session_file, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
            with open(LATEST_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
    except Exception as e:
        import sys  # Deferred import: circular dependency
        print(f"[Logger] Write failed: {e}", file=sys.stderr)


def _timestamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def log_net_recv(gamertag: str, data: Any) -> None:
    """Logs an incoming packet received from a client WebSocket."""
    data_str = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
    _write_log(f"[{_timestamp()}] [NET:RECV] [{gamertag}] {data_str}")


def log_net_send(gamertag: str, payload: Any) -> None:
    """Logs an outgoing packet sent to a client WebSocket."""
    data_str = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)
    _write_log(f"[{_timestamp()}] [NET:SEND] [{gamertag}] {data_str}")


def log_net_connect(gamertag: str, address: Any) -> None:
    """Logs client connection establishment."""
    _write_log(f"[{_timestamp()}] [NET:CONNECT] [{gamertag}] connected from {address}")


def log_net_disconnect(gamertag: str, code: Any = None, reason: Any = None) -> None:
    """Logs client disconnection with close code and reason."""
    reason_str = f" reason='{reason}'" if reason else ""
    code_str = f" code={code}" if code is not None else ""
    _write_log(f"[{_timestamp()}] [NET:DISCONNECT] [{gamertag}]{code_str}{reason_str}")


def log_room_event(room_id: str, event_type: str, details: Any = None) -> None:
    """Logs room lifecycle events (created, player joined, match started, etc.)."""
    det_str = f" | {json.dumps(details)}" if details else ""
    _write_log(f"[{_timestamp()}] [ROOM:{event_type}] [Room {room_id}]{det_str}")


def log_system(message: str) -> None:
    """Logs high-level server lifecycle status."""
    _write_log(f"[{_timestamp()}] [SYSTEM] {message}")


def log_error(category: str, message: str) -> None:
    """Logs error events."""
    _write_log(f"[{_timestamp()}] [ERROR:{category}] {message}")


def log_tournament(message: str) -> None:
    """Logs post-match tournament lifecycle events, duels, and title changes."""
    _write_log(f"[{_timestamp()}] [TOURNAMENT] {message}")
