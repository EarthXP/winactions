"""Session client — TCP client for connecting to the daemon.

Used by the thin CLI path: serialize argv into a JSON request,
send to the daemon, receive the JSON response, print output.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from typing import Optional

from winactions.cli.session_server import (
    _recv_line,
    is_server_alive,
    read_pid_file,
    session_port,
)


def send_command(port: int, request: dict, timeout: float = 30.0) -> dict:
    """Connect, send one JSON request, receive one JSON response, close."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(("127.0.0.1", port))
        payload = json.dumps(request).encode("utf-8") + b"\n"
        sock.sendall(payload)
        data = _recv_line(sock, timeout=timeout)
        if data is None:
            return {"status": "error", "error": "No response from daemon"}
        return json.loads(data)
    except socket.timeout:
        return {"status": "error", "error": "Daemon response timeout"}
    except ConnectionRefusedError:
        return {"status": "error", "error": "Daemon connection refused"}
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"Invalid response JSON: {e}"}
    except OSError as e:
        return {"status": "error", "error": f"Connection error: {e}"}
    finally:
        try:
            sock.close()
        except OSError:
            pass


def ensure_server(
    session_name: str,
    *,
    vision: bool = False,
    infer: bool = False,
    vision_api_key: Optional[str] = None,
    vision_base_url: Optional[str] = None,
) -> int:
    """Ensure the daemon is running.  Returns the port number.

    If the daemon is not running, starts it as a detached subprocess
    and waits for the READY signal.
    """
    # Already running?
    if is_server_alive(session_name):
        info = read_pid_file(session_name)
        if info:
            return info["port"]

    port = session_port(session_name)

    # Build the command to start the daemon
    cmd = [
        sys.executable, "-m", "winactions.cli.app",
        "_serve",
        "--session-name", session_name,
        "--port", str(port),
    ]
    if vision:
        cmd.append("--vision")
    if infer:
        cmd.append("--infer")
    if vision_api_key:
        cmd.extend(["--vision-api-key", vision_api_key])
    if vision_base_url:
        cmd.extend(["--vision-base-url", vision_base_url])

    # Start daemon as a detached subprocess
    # On Windows, use CREATE_NEW_PROCESS_GROUP so the daemon survives
    # after the parent CLI process exits.
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        **kwargs,
    )

    # Wait for the READY signal (up to 10 seconds)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if proc.stdout:
            # Non-blocking check: see if READY line appeared
            try:
                import msvcrt
                import ctypes
                # On Windows, use a timeout-based approach
                pass
            except ImportError:
                pass

        # Poll: try TCP ping
        time.sleep(0.3)
        if _try_ping(port, session_name):
            return port

        # Check if process died
        if proc.poll() is not None:
            stderr_out = ""
            if proc.stderr:
                try:
                    stderr_out = proc.stderr.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
            raise RuntimeError(
                f"Daemon process exited with code {proc.returncode}: {stderr_out}"
            )

    raise RuntimeError(
        f"Daemon for session '{session_name}' did not become ready within 10s"
    )


def _try_ping(port: int, session_name: Optional[str] = None) -> bool:
    """Try to TCP-ping the daemon.  Returns True if alive."""
    try:
        resp = send_command(port, {"command": "_ping"}, timeout=2.0)
        if resp.get("status") != "ok":
            return False
        # Verify session name if provided
        if session_name and resp.get("session") and resp["session"] != session_name:
            return False
        return True
    except Exception:
        return False
