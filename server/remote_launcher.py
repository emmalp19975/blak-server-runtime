"""
remote_launcher.py
==================
Interactive 1-click launcher for Blak game server + Cloudflare Tunnel.

Features:
- Starts the unified WebSocket + Web client HTTP server.
- Automatically locates or downloads portable cloudflared for Windows.
- Spawns a Cloudflare Quick Tunnel to expose the server via HTTPS/WSS.
- Displays the public URL for zero-install browser play.
- Ensures 100% clean teardown on exit (Ctrl+C).
"""

import asyncio
import os
import re
import shutil
import sys
import urllib.request
from typing import Optional

CLOUDFLARED_DOWNLOAD_URL: str = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
)
PROJECT_ROOT: str = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOLS_DIR: str = os.path.join(PROJECT_ROOT, "tools")
LOCAL_CLOUDFLARED_EXE: str = os.path.join(TOOLS_DIR, "cloudflared.exe")
ENV_FILE: str = os.path.join(PROJECT_ROOT, ".env")


def load_env() -> None:
    """Reads key=value pairs from .env into os.environ if not already present."""
    if not os.path.isfile(ENV_FILE):
        return
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k not in os.environ:
                    os.environ[k] = v
    except Exception as exc:
        print(f"Warning: could not read .env: {exc}")


def find_cloudflared() -> Optional[str]:

    """Finds cloudflared in PATH, tools/, or project root."""
    in_path: Optional[str] = shutil.which("cloudflared")
    if in_path:
        return in_path
    if os.path.isfile(LOCAL_CLOUDFLARED_EXE):
        return LOCAL_CLOUDFLARED_EXE
    root_exe: str = os.path.join(PROJECT_ROOT, "cloudflared.exe")
    if os.path.isfile(root_exe):
        return root_exe
    return None


def download_cloudflared(dest_path: str) -> bool:
    """Downloads official portable cloudflared binary with progress indicator."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    print(f"\nDownloading portable Cloudflare Tunnel binary to:\n  {dest_path}")
    print("Please wait a moment (approx. 35 MB)...")

    def _reporthook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0:
            downloaded = block_num * block_size
            pct = min(100.0, (downloaded / total_size) * 100.0)
            mb_done = downloaded / (1024 * 1024)
            mb_total = total_size / (1024 * 1024)
            sys.stdout.write(f"\r  [{pct:5.1f}%] {mb_done:.1f} MB / {mb_total:.1f} MB")
            sys.stdout.flush()

    try:
        urllib.request.urlretrieve(CLOUDFLARED_DOWNLOAD_URL, dest_path, reporthook=_reporthook)
        print("\nDownload complete!\n")
        return True
    except Exception as exc:
        print(f"\nFailed to download cloudflared: {exc}")
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass
        return False


async def stream_cloudflared_logs(
    process: asyncio.subprocess.Process, permanent_url: Optional[str] = None
) -> None:
    """Reads cloudflared stderr to detect and display the public tunnel URL."""
    tunnel_regex = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    url_found: bool = False

    while True:
        line_bytes = await process.stderr.readline()
        if not line_bytes:
            break
        line = line_bytes.decode("utf-8", errors="replace").strip()

        if not url_found:
            if permanent_url and ("Registered tunnel connection" in line or "Updated to new configuration" in line):
                url_found = True
                _print_banner(permanent_url)
                continue
            elif not permanent_url:
                match = tunnel_regex.search(line)
                if match:
                    url_found = True
                    public_url = match.group(0)
                    _print_banner(public_url)
                    continue

        # Print cloudflared errors or relevant connection notes
        if "ERR" in line or "error" in line.lower():
            print(f"[Tunnel] {line}")


def _print_banner(public_url: str) -> None:
    border = "=" * 72
    print(f"\n{border}")
    print("   BLAK N DARK -- REMOTE PLAY SERVER IS ONLINE!")
    print(f"{border}")
    print("\n Send this Web link to your friends (ZERO INSTALL required):")
    print(f"\n   >>>  {public_url}  <<<\n")
    print(" They can open that URL in Chrome, Edge, Safari, or Firefox to play!")
    print("\n Local Connections:")
    print("   - Browser (Host): http://localhost:8765")
    print("   - Godot Desktop:  ws://127.0.0.1:8765")
    print(f"                     or {public_url.replace('https://', 'wss://')}")
    print(f"\n Press Ctrl+C at any time to shut down the server and tunnel.")
    print(f"{border}\n", flush=True)


async def main() -> None:
    import server.main  # Deferred import: circular dependency

    load_env()
    host: str = os.environ.get("BLAK_HOST", "0.0.0.0")
    port: int = int(os.environ.get("BLAK_PORT", os.environ.get("PORT", "8765")))
    tunnel_token: str = os.environ.get("CLOUDFLARE_TUNNEL_TOKEN", "").strip()
    permanent_url: Optional[str] = (
        os.environ.get("CLOUDFLARE_TUNNEL_URL", "https://emmalp.app").strip()
        if tunnel_token
        else None
    )

    # 1. Check for cloudflared
    cloudflared_bin = find_cloudflared()
    if not cloudflared_bin:
        print("=" * 60)
        print(" Cloudflare Tunnel (cloudflared.exe) was not found.")
        print(" A tunnel creates a temporary, secure public URL so friends")
        print(" can connect over the internet without manual port forwarding.")
        print("=" * 60)
        choice = input("\nWould you like to automatically download cloudflared now? [Y/n]: ").strip().lower()
        if choice in ("", "y", "yes"):
            if download_cloudflared(LOCAL_CLOUDFLARED_EXE):
                cloudflared_bin = LOCAL_CLOUDFLARED_EXE
            else:
                print("Proceeding in local LAN mode only.")
        else:
            print("Proceeding in local LAN mode only.")

    # 2. Start the internal server
    print(f"\nStarting Blak game server on port {port}...")
    server_coro = server.main.websockets.serve(
        server.main.handle_client,
        host,
        port,
        process_request=server.main.process_request,
        ping_interval=30,
        ping_timeout=60,
    )

    tunnel_proc: Optional[asyncio.subprocess.Process] = None
    tunnel_task: Optional[asyncio.Task] = None

    try:
        async with server_coro:
            if cloudflared_bin:
                if tunnel_token:
                    print(f"Launching Cloudflare Named Tunnel for {permanent_url}...")
                    cmd = [
                        cloudflared_bin,
                        "tunnel",
                        "run",
                        "--token",
                        tunnel_token,
                    ]

                else:
                    print(f"Launching Cloudflare Quick Tunnel via: {cloudflared_bin}...")
                    cmd = [
                        cloudflared_bin,
                        "tunnel",
                        "--url",
                        f"http://127.0.0.1:{port}",
                        "--protocol",
                        "http2",
                        "--no-autoupdate",
                    ]
                try:
                    tunnel_proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    tunnel_task = asyncio.create_task(
                        stream_cloudflared_logs(tunnel_proc, permanent_url=permanent_url)
                    )
                except Exception as exc:
                    print(f"Failed to start cloudflared: {exc}")
                    print(f"Server is still running locally on ws://127.0.0.1:{port}")
            else:
                print("\n" + "=" * 60)
                print(" SERVER RUNNING IN LOCAL / LAN MODE")
                print(f" - Browser URL: http://localhost:{port}")
                print(f" - Godot Desktop: ws://127.0.0.1:{port}")
                print("=" * 60 + "\n")


            try:
                await asyncio.Future()  # run until interrupted
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
            finally:
                print("\nShutting down server and releasing ports...")
                if tunnel_task and not tunnel_task.done():
                    tunnel_task.cancel()
                if tunnel_proc:
                    try:
                        tunnel_proc.terminate()
                        await asyncio.wait_for(tunnel_proc.wait(), timeout=3.0)
                    except Exception:
                        try:
                            tunnel_proc.kill()
                            await tunnel_proc.wait()
                        except Exception:
                            pass
                print("Shutdown complete. All processes and ports released.")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048 or "already in use" in str(exc).lower():
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
