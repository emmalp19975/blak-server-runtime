"""
main.py
=======
Server entrypoint. Starts the unified WebSocket game server and static
HTTP file server for zero-install browser play.
"""

import asyncio
import mimetypes
import os
from typing import Optional

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

from server.connection import handle_client

# Ensure critical web assembly and package types are recognized
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/octet-stream", ".pck")

PROJECT_ROOT: str = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WEB_DIR: str = os.path.join(PROJECT_ROOT, "web_build")


def _read_file_bytes(file_path: str) -> bytes:
    with open(file_path, "rb") as f:
        return f.read()


async def process_request(connection, request) -> Optional[Response]:
    """
    Dual-purpose HTTP/WebSocket handler:
    - If request has 'Upgrade: websocket', return None so websockets conducts handshake.
    - Otherwise, serve static files from web_build/ with appropriate COOP/COEP headers.
    """
    upgrade_header: str = request.headers.get("Upgrade", "")
    if "websocket" in upgrade_header.lower():
        return None

    raw_path: str = request.path.split("?")[0].lstrip("/")
    if raw_path == "health":
        return Response(
            200,
            "OK",
            Headers([("Content-Type", "application/json; charset=utf-8")]),
            b'{"status":"ok","service":"blak-server"}',
        )

    if not os.path.isdir(WEB_DIR) or not os.path.isfile(os.path.join(WEB_DIR, "index.html")):
        return Response(
            200,
            "OK",
            Headers([("Content-Type", "application/json; charset=utf-8")]),
            b'{"status":"ok","service":"blak-server","mode":"websocket-only"}',
        )

    # Sanitize and resolve URL path
    if not raw_path or raw_path == "/":
        raw_path = "index.html"

    # Special route: graphics_config.cfg served directly from project root for live hot-reloading
    root_cfg_path: str = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "graphics_config.cfg"))
    if raw_path == "graphics_config.cfg" and os.path.isfile(root_cfg_path):
        local_path = root_cfg_path
    else:
        local_path = os.path.abspath(os.path.join(WEB_DIR, raw_path))
        # Path traversal protection
        if not local_path.startswith(WEB_DIR) or not os.path.isfile(local_path):
            return Response(
                404,
                "Not Found",
                Headers([("Content-Type", "text/plain; charset=utf-8")]),
                b"404 Not Found",
            )

    # Strict file firewall: never serve model weights, datasets, or backend scripts over HTTP/Cloudflare
    ALLOWED_EXTENSIONS = {".html", ".js", ".wasm", ".pck", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".ttf", ".woff2", ".tres", ".json", ".webmanifest", ".cfg", ".mp3", ".wav", ".ogg"}
    _, ext = os.path.splitext(local_path)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        return Response(
            403,
            "Forbidden",
            Headers([("Content-Type", "text/plain; charset=utf-8")]),
            b"403 Forbidden: File type not permitted.",
        )

    try:
        body: bytes = await asyncio.to_thread(_read_file_bytes, local_path)
    except Exception as exc:
        return Response(
            500,
            "Internal Server Error",
            Headers([("Content-Type", "text/plain; charset=utf-8")]),
            f"500 Internal Error: {exc}".encode("utf-8"),
        )

    mime_type, _ = mimetypes.guess_type(local_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    headers = Headers([
        ("Content-Type", mime_type),
        ("Content-Length", str(len(body))),
        ("Cross-Origin-Opener-Policy", "same-origin"),
        ("Cross-Origin-Embedder-Policy", "require-corp"),
        ("Cache-Control", "no-cache, no-store, must-revalidate"),
        ("Pragma", "no-cache"),
        ("Expires", "0"),
    ])
    return Response(200, "OK", headers, body=body)


async def main() -> None:
    host: str = os.environ.get("BLAK_HOST", "0.0.0.0")
    port: int = int(os.environ.get("BLAK_PORT", os.environ.get("PORT", "8765")))

    print(f"Starting Blak game server on ws://{host}:{port}")
    if os.path.isdir(WEB_DIR):
        print(f"Serving Web client from {WEB_DIR} on http://{host}:{port}")
    else:
        print(f"Notice: Web build directory not found at {WEB_DIR}. Serving WebSockets only.")

    try:
        async with websockets.serve(
            handle_client,
            host,
            port,
            process_request=process_request,
            ping_interval=30,
            ping_timeout=60,
        ):
            await asyncio.Future()  # run forever
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048 or "already in use" in str(exc).lower():
            import sys  # Deferred import: circular dependency
            print("\n" + "!" * 72)
            print(f" [PORT CONFLICT ERROR] Port {port} is already in use by another process!")
            print(f" To resolve:")
            print(f"   1. Close any running Blak server windows or background terminals.")
            print(f"   2. Or in PowerShell, inspect what's using port {port}:")
            print(f"        Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select OwningProcess")
            print(f"   3. Force release: Stop-Process -Id <OwningProcess> -Force")
            print("!" * 72 + "\n", flush=True)
            sys.exit(1)
        raise


if __name__ == "__main__":
    asyncio.run(main())

