"""SessionServer — TCP server + PID file lifecycle for daemon mode.

Single-threaded accept loop.  Each connection handles exactly one
JSON-line request-response, then disconnects.  UI automation is
inherently serial so there is no need for concurrency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import sys
import tempfile
from typing import Optional

from winactions.cli.session_dispatch import SessionDispatch

logger = logging.getLogger(__name__)

# Port range for session daemons (IANA dynamic/private range)
_PORT_MIN = 49152
_PORT_MAX = 65535


def session_port(name: str) -> int:
    """Deterministic port from session name (49152–65535)."""
    h = int(hashlib.sha256(name.encode()).hexdigest(), 16)
    return _PORT_MIN + (h % (_PORT_MAX - _PORT_MIN + 1))


def pid_file_path(name: str) -> str:
    """Return the PID file path for a given session name."""
    return os.path.join(tempfile.gettempdir(), f"winctl_{name}.pid")


def write_pid_file(name: str, port: int) -> str:
    """Write {pid, port, session_name} to the PID file.  Returns path."""
    path = pid_file_path(name)
    data = {"pid": os.getpid(), "port": port, "session_name": name}
    with open(path, "w") as f:
        json.dump(data, f)
    return path


def read_pid_file(name: str) -> Optional[dict]:
    """Read PID file.  Returns {pid, port, session_name} or None."""
    path = pid_file_path(name)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def remove_pid_file(name: str) -> None:
    """Remove the PID file (best-effort)."""
    try:
        os.unlink(pid_file_path(name))
    except OSError:
        pass


def is_server_alive(name: str) -> bool:
    """Check if a daemon is running: PID exists + TCP ping succeeds.

    If the PID file exists but the process is dead, cleans up the stale
    PID file and returns False.
    """
    info = read_pid_file(name)
    if info is None:
        return False

    pid = info.get("pid")
    port = info.get("port")
    stored_name = info.get("session_name")

    # Check if process is still alive
    try:
        import psutil
        if not psutil.pid_exists(pid):
            remove_pid_file(name)
            return False
    except ImportError:
        pass  # If psutil is not available, fall through to TCP check

    # TCP ping to verify it's actually our server
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(("127.0.0.1", port))
        sock.sendall(json.dumps({"command": "_ping"}).encode("utf-8") + b"\n")
        data = _recv_line(sock, timeout=2.0)
        sock.close()
        if data:
            resp = json.loads(data)
            # Verify session name matches (collision detection)
            if resp.get("session") and resp["session"] != name:
                logger.warning(
                    "Port %d collision: expected session '%s' but got '%s'",
                    port, name, resp["session"],
                )
                return False
            return resp.get("status") == "ok"
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    # Process exists but TCP failed — stale
    remove_pid_file(name)
    return False


class SessionServer:
    """Single-threaded TCP server for daemon mode."""

    def __init__(self, dispatch: SessionDispatch, port: int, session_name: str):
        self._dispatch = dispatch
        self._port = port
        self._session_name = session_name
        self._running = False
        self._server_socket: Optional[socket.socket] = None

    def serve_forever(self) -> None:
        """Bind, listen, and accept connections until shutdown."""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.settimeout(1.0)  # So we can check _running periodically
        self._server_socket.bind(("127.0.0.1", self._port))
        self._server_socket.listen(1)
        self._running = True

        logger.info(
            "Session daemon '%s' listening on 127.0.0.1:%d (PID %d)",
            self._session_name, self._port, os.getpid(),
        )

        # Signal readiness to parent (write to stdout before entering loop)
        try:
            sys.stdout.write(f"READY {self._port}\n")
            sys.stdout.flush()
        except Exception:
            pass

        try:
            while self._running:
                try:
                    conn, addr = self._server_socket.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    self._handle_connection(conn)
                except Exception as e:
                    logger.exception("Error handling connection: %s", e)
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass
        finally:
            self._server_socket.close()
            remove_pid_file(self._session_name)
            logger.info("Session daemon '%s' shut down.", self._session_name)

    def _handle_connection(self, conn: socket.socket) -> None:
        """Read one JSON line, dispatch, send response, close."""
        conn.settimeout(30.0)
        data = _recv_line(conn, timeout=30.0)
        if not data:
            return

        try:
            request = json.loads(data)
        except json.JSONDecodeError as e:
            response = {"status": "error", "error": f"Invalid JSON: {e}"}
            conn.sendall(json.dumps(response).encode("utf-8") + b"\n")
            return

        response = self._dispatch.handle(request)

        # Inject session_name into _ping responses
        if request.get("command") == "_ping":
            response["session"] = self._session_name

        # Check for shutdown
        if request.get("command") == "_shutdown":
            conn.sendall(json.dumps(response).encode("utf-8") + b"\n")
            self._running = False
            return

        conn.sendall(json.dumps(response, default=str).encode("utf-8") + b"\n")

    def shutdown(self) -> None:
        """Signal the server to stop."""
        self._running = False


def _recv_line(sock: socket.socket, timeout: float = 30.0) -> Optional[str]:
    """Receive a single newline-terminated line from a socket."""
    sock.settimeout(timeout)
    buf = b""
    while True:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            return None
        if not chunk:
            # Connection closed
            return buf.decode("utf-8").strip() if buf else None
        buf += chunk
        if b"\n" in buf:
            line, _ = buf.split(b"\n", 1)
            return line.decode("utf-8").strip()
        if len(buf) > 1024 * 1024:  # 1 MB safety limit
            return None
