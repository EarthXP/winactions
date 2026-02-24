"""Tests for session daemon: dispatch, protocol, client, lifecycle."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ======================================================================
# Dispatch tests (no TCP, no Windows dependencies)
# ======================================================================


class TestSessionDispatch:
    """Test SessionDispatch.handle() with a mocked DesktopSession."""

    def _make_dispatch(self, **kwargs):
        from winactions.cli.session_dispatch import SessionDispatch

        session = MagicMock()
        session.window = MagicMock()
        session.state = MagicMock()
        session.state.resolve.return_value = MagicMock()
        session.state.targets = []
        session.list_windows.return_value = [
            {"id": "1", "title": "Notepad", "process": "notepad.exe", "handle": 123}
        ]
        session.refresh_state.return_value = MagicMock(
            to_json=lambda verbose=False: {"window": "Test", "targets": []}
        )
        session.execute_on_target.return_value = "Action executed"
        session.execute_global.return_value = "Global action executed"
        session.focus_window.return_value = True
        session.launch_app.return_value = True
        session.close_window.return_value = True
        session.inspector = MagicMock()

        dispatch = SessionDispatch(session, **kwargs)
        return dispatch, session

    def test_ping(self):
        dispatch, _ = self._make_dispatch()
        resp = dispatch.handle({"command": "_ping"})
        assert resp["status"] == "ok"
        assert resp["result"] == "pong"

    def test_shutdown(self):
        dispatch, _ = self._make_dispatch()
        resp = dispatch.handle({"command": "_shutdown"})
        assert resp["status"] == "ok"
        assert resp["result"] == "shutdown"

    def test_unknown_command(self):
        dispatch, _ = self._make_dispatch()
        resp = dispatch.handle({"command": "nonexistent"})
        assert resp["status"] == "error"
        assert "Unknown command" in resp["error"]

    def test_windows(self):
        dispatch, session = self._make_dispatch()
        resp = dispatch.handle({"command": "windows"})
        assert resp["status"] == "ok"
        assert isinstance(resp["result"], list)
        session.list_windows.assert_called_once()

    def test_state(self):
        dispatch, session = self._make_dispatch()
        resp = dispatch.handle({"command": "state", "flags": {}})
        assert resp["status"] == "ok"
        session.refresh_state.assert_called()

    def test_click(self):
        dispatch, session = self._make_dispatch()
        resp = dispatch.handle({
            "command": "click",
            "args": {"index": "5"},
            "flags": {},
        })
        assert resp["status"] == "ok"
        session.execute_on_target.assert_called_with("click_input", "5", {})

    def test_click_right(self):
        dispatch, session = self._make_dispatch()
        resp = dispatch.handle({
            "command": "click",
            "args": {"index": "3", "right": True},
            "flags": {},
        })
        assert resp["status"] == "ok"
        session.execute_on_target.assert_called_with(
            "click_input", "3", {"button": "right"}
        )

    def test_input(self):
        dispatch, session = self._make_dispatch()
        resp = dispatch.handle({
            "command": "input",
            "args": {"index": "2", "text": "hello"},
            "flags": {},
        })
        assert resp["status"] == "ok"
        session.execute_on_target.assert_called_with(
            "set_edit_text", "2", {"text": "hello"}
        )

    def test_keys(self):
        dispatch, session = self._make_dispatch()
        resp = dispatch.handle({
            "command": "keys",
            "args": {"keys": "ctrl+a"},
            "flags": {},
        })
        assert resp["status"] == "ok"
        session.execute_global.assert_called_with(
            "keyboard_input", {"keys": "^a"}
        )

    def test_keys_with_target(self):
        dispatch, session = self._make_dispatch()
        resp = dispatch.handle({
            "command": "keys",
            "args": {"keys": "Enter", "target": "5"},
            "flags": {},
        })
        assert resp["status"] == "ok"
        session.execute_on_target.assert_called_with(
            "keyboard_input", "5", {"keys": "{ENTER}"}
        )

    def test_focus(self):
        dispatch, session = self._make_dispatch()
        session.window.window_text.return_value = "Notepad"
        resp = dispatch.handle({
            "command": "focus",
            "args": {"window": "notepad"},
        })
        assert resp["status"] == "ok"
        assert "Focused" in resp["result"]

    def test_focus_not_found(self):
        dispatch, session = self._make_dispatch()
        session.focus_window.return_value = False
        resp = dispatch.handle({
            "command": "focus",
            "args": {"window": "nonexistent"},
        })
        assert resp["status"] == "error"

    def test_handler_exception(self):
        dispatch, session = self._make_dispatch()
        session.execute_on_target.side_effect = RuntimeError("Control 99 not found")
        resp = dispatch.handle({
            "command": "click",
            "args": {"index": "99"},
            "flags": {},
        })
        assert resp["status"] == "error"
        assert "Control 99 not found" in resp["error"]

    def test_return_state_flag(self):
        dispatch, session = self._make_dispatch()
        resp = dispatch.handle({
            "command": "click",
            "args": {"index": "1"},
            "flags": {"return_state": True},
        })
        assert resp["status"] == "ok"
        assert "state" in resp

    def test_wait_seconds(self):
        dispatch, _ = self._make_dispatch()
        start = time.monotonic()
        resp = dispatch.handle({
            "command": "wait",
            "args": {"seconds": "0.1"},
            "flags": {},
        })
        elapsed = time.monotonic() - start
        assert resp["status"] == "ok"
        assert elapsed >= 0.05  # Allow some tolerance

    def test_get_text(self):
        dispatch, session = self._make_dispatch()
        resp = dispatch.handle({
            "command": "get text",
            "args": {"index": "1"},
            "flags": {},
        })
        assert resp["status"] == "ok"

    def test_close(self):
        dispatch, session = self._make_dispatch()
        resp = dispatch.handle({"command": "close", "flags": {}})
        assert resp["status"] == "ok"
        session.close_window.assert_called_once()


# ======================================================================
# Key translation tests
# ======================================================================


class TestKeyTranslation:
    def test_ctrl_a(self):
        from winactions.cli.session_dispatch import _translate_keys
        assert _translate_keys("ctrl+a") == "^a"

    def test_enter(self):
        from winactions.cli.session_dispatch import _translate_keys
        assert _translate_keys("Enter") == "{ENTER}"

    def test_alt_f4(self):
        from winactions.cli.session_dispatch import _translate_keys
        assert _translate_keys("alt+f4") == "%{F4}"

    def test_passthrough_pywinauto_format(self):
        from winactions.cli.session_dispatch import _translate_keys
        assert _translate_keys("^a") == "^a"
        assert _translate_keys("{ENTER}") == "{ENTER}"


# ======================================================================
# Protocol / JSON round-trip tests
# ======================================================================


class TestProtocol:
    """Test JSON serialization round-trips."""

    def test_request_roundtrip(self):
        request = {
            "command": "click",
            "args": {"index": "5", "right": False},
            "flags": {"return_state": True, "window": "outlook"},
        }
        encoded = json.dumps(request).encode("utf-8") + b"\n"
        decoded = json.loads(encoded.decode("utf-8").strip())
        assert decoded == request

    def test_response_roundtrip(self):
        response = {
            "status": "ok",
            "result": "Click action executed",
            "state": {"window": "Test", "targets": []},
        }
        encoded = json.dumps(response).encode("utf-8") + b"\n"
        decoded = json.loads(encoded.decode("utf-8").strip())
        assert decoded == response

    def test_error_response(self):
        response = {
            "status": "error",
            "error": "Control 99 not found",
            "command": "click",
        }
        encoded = json.dumps(response).encode("utf-8") + b"\n"
        decoded = json.loads(encoded.decode("utf-8").strip())
        assert decoded["status"] == "error"


# ======================================================================
# Server port + PID file tests
# ======================================================================


class TestServerLifecycle:
    def test_session_port_deterministic(self):
        from winactions.cli.session_server import session_port
        port1 = session_port("test")
        port2 = session_port("test")
        assert port1 == port2
        assert 49152 <= port1 <= 65535

    def test_session_port_different_names(self):
        from winactions.cli.session_server import session_port
        p1 = session_port("outlook")
        p2 = session_port("notepad")
        # Very unlikely to collide with SHA256
        assert p1 != p2

    def test_pid_file_write_read(self):
        from winactions.cli.session_server import (
            write_pid_file, read_pid_file, remove_pid_file, pid_file_path,
        )
        name = f"_test_{os.getpid()}"
        try:
            path = write_pid_file(name, 50000)
            assert os.path.exists(path)
            info = read_pid_file(name)
            assert info is not None
            assert info["pid"] == os.getpid()
            assert info["port"] == 50000
            assert info["session_name"] == name
        finally:
            remove_pid_file(name)

    def test_read_nonexistent_pid_file(self):
        from winactions.cli.session_server import read_pid_file
        assert read_pid_file("_nonexistent_session_xyz") is None

    def test_remove_pid_file_no_error(self):
        from winactions.cli.session_server import remove_pid_file
        # Should not raise even if file doesn't exist
        remove_pid_file("_nonexistent_session_xyz")


# ======================================================================
# Client send_command tests (with a real socket pair)
# ======================================================================


class TestClientSendCommand:
    """Test send_command with a simple mock TCP server."""

    def test_send_and_receive(self):
        from winactions.cli.session_client import send_command

        # Create a simple server that echoes back a fixed response
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        server.listen(1)

        fixed_response = {"status": "ok", "result": "pong"}

        def handler():
            conn, _ = server.accept()
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            conn.sendall(json.dumps(fixed_response).encode("utf-8") + b"\n")
            conn.close()

        t = threading.Thread(target=handler)
        t.start()

        try:
            resp = send_command(port, {"command": "_ping"}, timeout=5.0)
            assert resp["status"] == "ok"
            assert resp["result"] == "pong"
        finally:
            t.join(timeout=5)
            server.close()

    def test_connection_refused(self):
        from winactions.cli.session_client import send_command
        # Use a port that's almost certainly not listening
        resp = send_command(49151, {"command": "_ping"}, timeout=1.0)
        assert resp["status"] == "error"


# ======================================================================
# Arg parsing tests (no Windows deps)
# ======================================================================


class TestArgParsing:
    def test_extract_session_flag(self):
        from winactions.cli.app import _extract_session_flag
        assert _extract_session_flag(["--session", "my", "state"]) == "my"
        assert _extract_session_flag(["--session=my", "state"]) == "my"
        assert _extract_session_flag(["state"]) is None

    def test_is_serve_command(self):
        from winactions.cli.app import _is_serve_command
        assert _is_serve_command(["_serve", "--session-name", "x", "--port", "50000"])
        assert not _is_serve_command(["--session", "my", "state"])

    def test_parse_simple_command(self):
        from winactions.cli.app import _parse_args_for_daemon
        result = _parse_args_for_daemon(["--session", "my", "state"])
        assert result["command"] == "state"
        assert result["flags"] == {}

    def test_parse_with_window(self):
        from winactions.cli.app import _parse_args_for_daemon
        result = _parse_args_for_daemon([
            "--session", "my", "--window", "outlook", "click", "5"
        ])
        assert result["command"] == "click"
        assert result["args"]["index"] == "5"
        assert result["flags"]["window"] == "outlook"

    def test_parse_click_right(self):
        from winactions.cli.app import _parse_args_for_daemon
        result = _parse_args_for_daemon([
            "--session", "my", "click", "--right", "3"
        ])
        assert result["command"] == "click"
        assert result["args"]["right"] is True
        assert result["args"]["index"] == "3"

    def test_parse_return_state(self):
        from winactions.cli.app import _parse_args_for_daemon
        result = _parse_args_for_daemon([
            "--session", "my", "--return-state", "click", "5"
        ])
        assert result["flags"]["return_state"] is True

    def test_parse_keys_with_target(self):
        from winactions.cli.app import _parse_args_for_daemon
        result = _parse_args_for_daemon([
            "--session", "my", "keys", "--target", "5", "ctrl+a"
        ])
        assert result["command"] == "keys"
        assert result["args"]["keys"] == "ctrl+a"
        assert result["args"]["target"] == "5"

    def test_parse_get_text(self):
        from winactions.cli.app import _parse_args_for_daemon
        result = _parse_args_for_daemon([
            "--session", "my", "get", "text", "1"
        ])
        assert result["command"] == "get text"
        assert result["args"]["index"] == "1"

    def test_parse_drag_with_separator(self):
        from winactions.cli.app import _parse_args_for_daemon
        result = _parse_args_for_daemon([
            "--session", "my", "drag", "5", "--", "100", "-200"
        ])
        assert result["command"] == "drag"
        # After --, everything is positional
        # index=5 is before --, x2=100, y2=-200 after --
        # Actually "5" is first positional, then "100" and "-200" after --
        assert result["args"]["index"] == "5"
        assert result["args"]["x2"] == "100"
        assert result["args"]["y2"] == "-200"

    def test_parse_session_equals(self):
        from winactions.cli.app import _parse_args_for_daemon
        result = _parse_args_for_daemon(["--session=my", "windows"])
        assert result["command"] == "windows"

    def test_parse_no_command(self):
        from winactions.cli.app import _parse_args_for_daemon
        result = _parse_args_for_daemon(["--session", "my"])
        assert result["command"] == ""

    def test_parse_vision_flag(self):
        from winactions.cli.app import _parse_args_for_daemon
        result = _parse_args_for_daemon([
            "--session", "my", "--vision", "--infer", "state"
        ])
        assert result["flags"]["vision"] is True
        assert result["flags"]["infer"] is True

    def test_parse_scroll(self):
        from winactions.cli.app import _parse_args_for_daemon
        result = _parse_args_for_daemon([
            "--session", "my", "scroll", "3", "down", "5"
        ])
        assert result["command"] == "scroll"
        assert result["args"]["index"] == "3"
        assert result["args"]["direction"] == "down"
        assert result["args"]["amount"] == "5"

    def test_parse_input(self):
        from winactions.cli.app import _parse_args_for_daemon
        result = _parse_args_for_daemon([
            "--session", "my", "input", "2", "hello world"
        ])
        assert result["command"] == "input"
        assert result["args"]["index"] == "2"
        assert result["args"]["text"] == "hello world"


# ======================================================================
# Integration: Server + Client over TCP (no Windows deps in dispatch)
# ======================================================================


class TestServerClientIntegration:
    """Spin up a real SessionServer with mocked dispatch, connect a client."""

    def test_ping_roundtrip(self):
        from winactions.cli.session_dispatch import SessionDispatch
        from winactions.cli.session_server import SessionServer
        from winactions.cli.session_client import send_command

        session = MagicMock()
        session.window = None
        session.state = None
        dispatch = SessionDispatch(session)

        # Find a free port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        server = SessionServer(dispatch, port, "test_session")
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        # Wait for server to be ready
        time.sleep(0.5)

        try:
            resp = send_command(port, {"command": "_ping"}, timeout=5.0)
            assert resp["status"] == "ok"
            assert resp["result"] == "pong"
            assert resp["session"] == "test_session"
        finally:
            # Shut down
            send_command(port, {"command": "_shutdown"}, timeout=2.0)
            t.join(timeout=5)

    def test_unknown_command_roundtrip(self):
        from winactions.cli.session_dispatch import SessionDispatch
        from winactions.cli.session_server import SessionServer
        from winactions.cli.session_client import send_command

        session = MagicMock()
        session.window = None
        session.state = None
        dispatch = SessionDispatch(session)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        server = SessionServer(dispatch, port, "test2")
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.5)

        try:
            resp = send_command(port, {"command": "bogus"}, timeout=5.0)
            assert resp["status"] == "error"
            assert "Unknown command" in resp["error"]
        finally:
            send_command(port, {"command": "_shutdown"}, timeout=2.0)
            t.join(timeout=5)

    def test_multiple_sequential_requests(self):
        """Verify multiple requests reuse the same server."""
        from winactions.cli.session_dispatch import SessionDispatch
        from winactions.cli.session_server import SessionServer
        from winactions.cli.session_client import send_command

        session = MagicMock()
        session.window = MagicMock()
        session.state = None
        session.list_windows.return_value = [{"id": "1", "title": "T"}]
        session.refresh_state.return_value = MagicMock(
            to_json=lambda: {"window": "T", "targets": []}
        )
        dispatch = SessionDispatch(session)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        server = SessionServer(dispatch, port, "multi")
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.5)

        try:
            r1 = send_command(port, {"command": "_ping"})
            assert r1["status"] == "ok"

            r2 = send_command(port, {"command": "windows"})
            assert r2["status"] == "ok"

            r3 = send_command(port, {"command": "_ping"})
            assert r3["status"] == "ok"
        finally:
            send_command(port, {"command": "_shutdown"}, timeout=2.0)
            t.join(timeout=5)


# ======================================================================
# Provider switching tests
# ======================================================================


class TestProviderSwitching:
    def test_no_switch_when_same_flags(self):
        from winactions.cli.session_dispatch import SessionDispatch

        session = MagicMock()
        session.window = MagicMock()
        session.state = MagicMock()
        session.state.resolve.return_value = MagicMock()
        session.execute_on_target.return_value = "ok"
        dispatch = SessionDispatch(session, default_vision=False, default_infer=False)

        # No provider rebuild when flags match defaults
        with patch.object(dispatch, "_rebuild_provider") as mock_rebuild:
            dispatch.handle({
                "command": "click",
                "args": {"index": "1"},
                "flags": {},
            })
            mock_rebuild.assert_not_called()

    def test_switch_when_vision_flag_changes(self):
        from winactions.cli.session_dispatch import SessionDispatch

        session = MagicMock()
        session.window = MagicMock()
        session.state = MagicMock()
        session.state.resolve.return_value = MagicMock()
        session.execute_on_target.return_value = "ok"
        session.inspector = MagicMock()
        dispatch = SessionDispatch(session, default_vision=False, default_infer=False)

        with patch.object(dispatch, "_rebuild_provider") as mock_rebuild:
            dispatch.handle({
                "command": "click",
                "args": {"index": "1"},
                "flags": {"vision": True},
            })
            mock_rebuild.assert_called_once_with(True, False)
