"""
ngrok_launcher.py
=================
Interactive 1-click launcher for Blak game server + ngrok permanent HTTPS tunnel.

Features:
- Starts the unified WebSocket + Web client HTTP server.
- Automatically locates or downloads portable ngrok for Windows.
- Automatically configures authtoken and binds your permanent static domain.
- Displays the fixed public URL for mobile PWA standalone install and remote play.
- Ensures 100% clean teardown on exit (Ctrl+C).
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from typing import Optional, Tuple

NGROK_DOWNLOAD_URL: str = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"
PROJECT_ROOT: str = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOLS_DIR: str = os.path.join(PROJECT_ROOT, "tools")
LOCAL_NGROK_EXE: str = os.path.join(TOOLS_DIR, "ngrok.exe")
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


def save_env_pair(key: str, value: str) -> None:
    """Appends or updates a key=value pair in .env."""
    lines = []
    found = False
    if os.path.isfile(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"{key}={value}\n")
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.environ[key] = value


def find_ngrok() -> Optional[str]:
    """Finds ngrok in PATH, tools/, or project root."""
    in_path: Optional[str] = shutil.which("ngrok")
    if in_path:
        return in_path
    if os.path.isfile(LOCAL_NGROK_EXE):
        return LOCAL_NGROK_EXE
    root_exe: str = os.path.join(PROJECT_ROOT, "ngrok.exe")
    if os.path.isfile(root_exe):
        return root_exe
    return None


def download_ngrok(dest_dir: str) -> Optional[str]:
    """Downloads official portable ngrok zip and extracts ngrok.exe."""
    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, "ngrok_temp.zip")
    print(f"\nDownloading portable ngrok binary to:\n  {dest_dir}")
    print("Please wait a moment (approx. 12 MB)...")

    def _reporthook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0:
            downloaded = block_num * block_size
            pct = min(100.0, (downloaded / total_size) * 100.0)
            mb_done = downloaded / (1024 * 1024)
            mb_total = total_size / (1024 * 1024)
            sys.stdout.write(f"\r  [{pct:5.1f}%] {mb_done:.1f} MB / {mb_total:.1f} MB")
            sys.stdout.flush()

    try:
        urllib.request.urlretrieve(NGROK_DOWNLOAD_URL, zip_path, reporthook=_reporthook)
        print("\nExtracting ngrok.exe...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest_dir)
        if os.path.exists(zip_path):
            os.remove(zip_path)
        ngrok_exe = os.path.join(dest_dir, "ngrok.exe")
        if os.path.isfile(ngrok_exe):
            print("ngrok installed successfully!\n")
            return ngrok_exe
        return None
    except Exception as exc:
        print(f"\nFailed to download ngrok: {exc}")
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except OSError:
                pass
        return None


def configure_authtoken(ngrok_bin: str, token: str) -> bool:
    """Configures the ngrok authtoken using ngrok config add-authtoken."""
    try:
        res = subprocess.run([ngrok_bin, "config", "add-authtoken", token], capture_output=True, text=True, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"Error configuring authtoken: {exc.stderr or exc.stdout}")
        return False


async def wait_for_tunnel_url(expected_domain: Optional[str] = None, timeout: float = 12.0) -> Optional[str]:
    """Polls ngrok's local inspection API (127.0.0.1:4040) until public URL is available."""
    api_url = "http://127.0.0.1:4040/api/tunnels"
    start_time = asyncio.get_event_loop().time()

    while (asyncio.get_event_loop().time() - start_time) < timeout:
        await asyncio.sleep(0.5)
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "BlakLauncher"})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                tunnels = data.get("tunnels", [])
                for t in tunnels:
                    pub_url = t.get("public_url", "")
                    if pub_url.startswith("https://"):
                        return pub_url
                if tunnels:
                    return tunnels[0].get("public_url")
        except Exception:
            continue
    return f"https://{expected_domain}" if expected_domain else None


def print_banner(public_url: str) -> None:
    border = "=" * 74
    print(f"\n{border}")
    print("   BLAK N DARK -- PERMANENT MOBILE PWA SERVER IS ONLINE!")
    print(f"{border}")
    print("\n [FIXED HTTPS URL FOR MOBILE & DESKTOP PLAY]")
    print(f"\n   >>>  {public_url}  <<<\n")
    print(" HOW TO INSTALL AS A STANDALONE FULLSCREEN APP ON YOUR PHONE:")
    print("   1. Open the URL in Safari (iPhone) or Chrome (Android).")
    print("   2. (One-time only) If ngrok displays a warning screen, tap 'Visit Site'.")
    print("   3. On iOS: Tap Share -> 'Add to Home Screen'.")
    print("      On Android: Tap Menu (3 dots) -> 'Install App' or 'Add to Home screen'.")
    print("   4. Tap the 'Blak' icon on your Home Screen to launch in borderless")
    print("      fullscreen landscape mode with ZERO browser address bar!")
    print("\n Local Connections:")
    print("   - Browser (Host): http://localhost:8765")
    print("   - Godot Desktop:  ws://127.0.0.1:8765")
    print(f"                     or {public_url.replace('https://', 'wss://')}")
    print(f"\n Press Ctrl+C at any time to shut down the server and release ports.")
    print(f"{border}\n", flush=True)


async def main() -> None:
    load_env()

    # 1. Resolve authtoken and static domain
    authtoken = os.environ.get("NGROK_AUTHTOKEN", "").strip()
    if not authtoken:
        print("=" * 60)
        print(" ngrok Authtoken required.")
        print(" Get your free authtoken at: https://dashboard.ngrok.com/get-started/your-authtoken")
        print("=" * 60)
        authtoken = input("Enter your ngrok authtoken: ").strip()
        if authtoken:
            save_env_pair("NGROK_AUTHTOKEN", authtoken)

    domain = os.environ.get("NGROK_DOMAIN", "").strip()
    if not domain:
        print("=" * 60)
        print(" ngrok Free Static Domain (Optional but recommended for fixed PWA).")
        print(" Claim yours at: https://dashboard.ngrok.com/cloud-edge/domains")
        print("=" * 60)
        domain = input("Enter your ngrok static domain (or press Enter for ephemeral): ").strip()
        if domain:
            save_env_pair("NGROK_DOMAIN", domain)

    # 2. Locate or download ngrok.exe
    ngrok_bin = find_ngrok()
    if not ngrok_bin:
        ngrok_bin = download_ngrok(TOOLS_DIR)
        if not ngrok_bin:
            print("[ERROR] Could not obtain ngrok.exe. Exiting.")
            sys.exit(1)

    # 3. Configure authtoken
    if authtoken:
        configure_authtoken(ngrok_bin, authtoken)

    # 4. Start Blak game server
    import server.main  # Deferred import

    host: str = os.environ.get("BLAK_HOST", "0.0.0.0")
    port: int = int(os.environ.get("BLAK_PORT", os.environ.get("PORT", "8765")))

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

    try:
        async with server_coro:
            print(f"Launching ngrok tunnel on port {port}...")
            cmd = [ngrok_bin, "http", str(port)]
            if domain:
                cmd.extend(["--domain", domain])
            cmd.extend(["--log", "stdout", "--log-level", "info"])

            tunnel_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )

            # Retrieve active tunnel public URL
            public_url = await wait_for_tunnel_url(domain, timeout=10.0)
            if not public_url:
                if tunnel_proc.returncode is not None:
                    err_bytes = await tunnel_proc.stderr.read()
                    print(f"[ERROR] ngrok exited with code {tunnel_proc.returncode}: {err_bytes.decode('utf-8', errors='replace')}")
                public_url = f"https://{domain}" if domain else f"http://localhost:{port}"

            print_banner(public_url)

            try:
                await asyncio.Future()  # run until interrupted
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
            finally:
                print("\nShutting down server and releasing ports...")
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
            print("!" * 72 + "\n", flush=True)
            sys.exit(1)
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
