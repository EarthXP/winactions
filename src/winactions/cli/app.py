"""winctl — CLI entry point for the Windows UI Automation Toolkit.

Each command is atomic. Perception commands output indexed lists.
Execution commands accept index numbers. Decision happens externally
(by an LLM agent or a human).
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional

import click

from winactions.cli.formatter import output, output_error, format_windows_list
from winactions.cli.session import DesktopSession


# Module-level session (lazy-initialized)
_session: Optional[DesktopSession] = None


def _get_session(ctx: click.Context) -> DesktopSession:
    """Get or create the global DesktopSession."""
    global _session
    if _session is None:
        use_vision = ctx.obj.get("vision", False)
        use_infer = ctx.obj.get("infer", False)
        _session = DesktopSession.create(
            vision=use_vision,
            infer=use_infer,
            vision_api_key=ctx.obj.get("vision_api_key"),
            vision_base_url=ctx.obj.get("vision_base_url"),
        )
    return _session


def _ensure_window(ctx: click.Context) -> DesktopSession:
    """Ensure a window is focused, auto-focusing foreground if needed."""
    session = _get_session(ctx)
    if session.window is None:
        window_name = ctx.obj.get("window")
        if window_name:
            if not session.focus_window(window_name):
                raise RuntimeError(f'No window matching "{window_name}" found')
        else:
            session.focus_foreground()
    return session


def _ensure_state(ctx: click.Context) -> DesktopSession:
    """Ensure state is available, refreshing if needed."""
    session = _ensure_window(ctx)
    if session.state is None:
        session.refresh_state()
    return session


# ============================================================================
# CLI Group
# ============================================================================


@click.group()
@click.option("--json", "output_json", is_flag=True, help="JSON output mode")
@click.option("--session", "session_name", default=None, help="Named session")
@click.option("--window", "window_name", default=None, help="Target window by title/process substring")
@click.option("--return-state", "return_state", is_flag=True, help="Return new state after execution commands")
@click.option("--vision", "use_vision", is_flag=True, help="Enable vision-based UI element detection (requires ANTHROPIC_API_KEY)")
@click.option("--infer", "use_infer", is_flag=True, help="Enable LLM structural inference of hidden UI elements (Tier 1+)")
@click.option("--vision-base-url", default=None, help="Custom base URL for vision API (or set ANTHROPIC_BASE_URL)")
@click.option("--vision-api-key", default=None, help="API key for vision detection (or set ANTHROPIC_API_KEY)")
@click.pass_context
def cli(ctx, output_json: bool, session_name: Optional[str], window_name: Optional[str], return_state: bool, use_vision: bool, use_infer: bool, vision_base_url: Optional[str], vision_api_key: Optional[str]):
    """winctl — Windows UI Automation for AI Agents.

    Perception commands output indexed control lists.
    Execution commands accept index numbers from those lists.

    Typical workflow:
        winctl --window outlook state    # see Outlook controls
        winctl --window outlook click 3  # click control #3
        winctl --window outlook state    # verify result
    """
    ctx.ensure_object(dict)
    ctx.obj["json"] = output_json
    ctx.obj["session"] = session_name
    ctx.obj["window"] = window_name
    ctx.obj["return_state"] = return_state
    ctx.obj["vision"] = use_vision
    ctx.obj["infer"] = use_infer
    ctx.obj["vision_base_url"] = vision_base_url
    ctx.obj["vision_api_key"] = vision_api_key


def _output_with_return_state(ctx: click.Context, result: str) -> None:
    """Output command result, then append fresh state if --return-state is set."""
    as_json = ctx.obj["json"]
    output(result or "OK", as_json=as_json)
    if ctx.obj.get("return_state"):
        time.sleep(0.5)  # let UI settle after action
        session = _ensure_window(ctx)
        ui_state = session.refresh_state()
        if as_json:
            output(ui_state.to_json(), as_json=True)
        else:
            output(ui_state.to_text())


# ============================================================================
# Perception Commands
# ============================================================================


@cli.command()
@click.option("--screenshot", is_flag=True, help="Include screenshot paths")
@click.option("--annotated", is_flag=True, help="Include annotated screenshot")
@click.option("--tree", is_flag=True, help="Show hierarchical control tree")
@click.option("--verbose", is_flag=True, help="Include rect coordinates for all controls")
@click.pass_context
def state(ctx, screenshot: bool, annotated: bool, tree: bool, verbose: bool):
    """Show indexed control list for the current window."""
    try:
        session = _ensure_window(ctx)
        ui_state = session.refresh_state(
            screenshot=screenshot or annotated,
        )

        as_json = ctx.obj["json"]
        if tree:
            tree_data = session.get_control_tree()
            if as_json:
                output(tree_data, as_json=True)
            else:
                output(_format_tree(tree_data))
        elif as_json:
            output(ui_state.to_json(verbose=verbose), as_json=True)
        else:
            output(ui_state.to_text(verbose=verbose))
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


def _format_tree(nodes, indent: int = 0) -> str:
    """Format a control tree as indented text."""
    lines = []
    prefix = "  " * indent
    for node in nodes:
        ctrl_id = node.get("id", "")
        ctrl_type = node.get("type", "")
        ctrl_name = node.get("name", "")
        id_str = f"[{ctrl_id}] " if ctrl_id else ""
        lines.append(f'{prefix}{id_str}[{ctrl_type}] "{ctrl_name}"')
        children = node.get("children", [])
        if children:
            lines.append(_format_tree(children, indent + 1))
    return "\n".join(lines)


@cli.command()
@click.pass_context
def windows(ctx):
    """List all visible desktop windows."""
    try:
        session = _get_session(ctx)
        window_list = session.list_windows()
        as_json = ctx.obj["json"]
        output(format_windows_list(window_list, as_json=as_json))
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@cli.command()
@click.argument("index")
@click.pass_context
def inspect(ctx, index: str):
    """Show detailed properties of a single control by index."""
    try:
        session = _ensure_state(ctx)
        control = session.state.resolve(index)
        if control is None:
            output_error(f"Control with index {index} not found", ctx.obj["json"])
            sys.exit(1)

        from winactions.control.inspector import ControlInspectorFacade

        info = ControlInspectorFacade.get_control_info(control)
        output(info, as_json=ctx.obj["json"])
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@cli.command()
@click.argument("path", required=False)
@click.pass_context
def screenshot(ctx, path: Optional[str]):
    """Take a screenshot of the current window."""
    try:
        session = _ensure_window(ctx)
        from winactions.screenshot.photographer import PhotographerFacade

        facade = PhotographerFacade()
        if path is None:
            import tempfile

            fd, path = tempfile.mkstemp(suffix=".png", prefix="winctl_")
            os.close(fd)

        facade.capture_app_window_screenshot(session.window, save_path=path)
        output({"path": path} if ctx.obj["json"] else path, as_json=ctx.obj["json"])
    except ImportError:
        output_error("Screenshot requires Pillow: pip install winactions[screenshot]", ctx.obj["json"])
        sys.exit(1)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


# ============================================================================
# Execution Commands
# ============================================================================


@cli.command("click")
@click.argument("index")
@click.option("--right", is_flag=True, help="Right-click instead of left-click")
@click.pass_context
def click_cmd(ctx, index: str, right: bool):
    """Click a control by index."""
    try:
        session = _ensure_state(ctx)
        params = {"button": "right"} if right else {}
        result = session.execute_on_target("click_input", index, params)
        _output_with_return_state(ctx, result)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@cli.command()
@click.argument("index")
@click.pass_context
def dblclick(ctx, index: str):
    """Double-click a control by index."""
    try:
        session = _ensure_state(ctx)
        result = session.execute_on_target("click_input", index, {"double": True})
        _output_with_return_state(ctx, result)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@cli.command()
@click.argument("index")
@click.pass_context
def rightclick(ctx, index: str):
    """Right-click a control by index."""
    try:
        session = _ensure_state(ctx)
        result = session.execute_on_target("click_input", index, {"button": "right"})
        _output_with_return_state(ctx, result)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@cli.command("input")
@click.argument("index")
@click.argument("text")
@click.pass_context
def input_cmd(ctx, index: str, text: str):
    """Set text on a control by index."""
    try:
        session = _ensure_state(ctx)
        result = session.execute_on_target("set_edit_text", index, {"text": text})
        _output_with_return_state(ctx, result)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@cli.command("type")
@click.argument("text")
@click.pass_context
def type_cmd(ctx, text: str):
    """Type text using pyautogui (to the focused element)."""
    try:
        session = _ensure_state(ctx)
        result = session.execute_global("type", {"text": text})
        _output_with_return_state(ctx, result)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


def _translate_keys(keys_str: str) -> str:
    """Translate human-friendly key combos to pywinauto format.

    Examples: 'ctrl+a' → '^a', 'alt+f4' → '%{F4}', 'Enter' → '{ENTER}'
    Handles mixed format: 'ctrl+a{DELETE}' → '^a{DELETE}'
    """
    import re

    # Extract leading human-friendly modifier prefixes (ctrl+, shift+, alt+)
    # BEFORE checking for pywinauto format markers.  This handles mixed
    # formats like "ctrl+a{DELETE}" where modifiers are human-friendly but
    # the key part uses pywinauto {NAMED_KEY} syntax.
    remaining = keys_str
    modifiers = ""
    while True:
        m = re.match(r"(?i)(ctrl|shift|alt)\+", remaining)
        if not m:
            break
        modifiers += {"ctrl": "^", "shift": "+", "alt": "%"}[
            m.group(1).lower()
        ]
        remaining = remaining[m.end() :]

    # If the remaining part is already in pywinauto format, prepend modifiers
    if re.search(r"[\\^%]|{.*}", remaining):
        return modifiers + remaining

    # Single character remaining — escape pywinauto modifier chars so they
    # are sent as literal keystrokes, not interpreted as Shift/Ctrl/Alt/etc.
    # Applies with OR without preceding modifiers (e.g. bare "+" or "ctrl++").
    _pywinauto_special = {"+", "^", "%", "~", "(", ")"}
    if len(remaining) == 1:
        if remaining in _pywinauto_special:
            return modifiers + "{" + remaining + "}"
        return modifiers + remaining

    # Pure human-friendly format without pre-extracted modifiers:
    # parse the full string for cases like bare "Enter", "F4", etc.
    if not modifiers:
        parts = [p.strip() for p in keys_str.split("+")]
        key_parts = []
        for part in parts:
            lower = part.lower()
            if lower == "ctrl":
                modifiers += "^"
            elif lower == "shift":
                modifiers += "+"
            elif lower == "alt":
                modifiers += "%"
            else:
                key_parts.append(part)
        if not key_parts:
            return keys_str
        key = key_parts[0]
    else:
        key = remaining
    # Normalize human-friendly aliases to pywinauto canonical names.
    # pywinauto accepts: {ESC} (not {ESCAPE}), {DEL}/{DELETE},
    # {PGUP}/{PGDN} (not {PAGEUP}/{PAGEDOWN}), {BACKSPACE}/{BACK}/{BS}
    _key_aliases = {
        "escape": "ESC",       # {ESCAPE} is NOT valid in pywinauto
        "esc": "ESC",
        "del": "DEL",
        "delete": "DELETE",
        "bs": "BACKSPACE",
        "backspace": "BACKSPACE",
        "pageup": "PGUP",     # {PAGEUP} is NOT valid in pywinauto
        "pagedown": "PGDN",   # {PAGEDOWN} is NOT valid in pywinauto
        "pgup": "PGUP",
        "pgdn": "PGDN",
    }
    # Named keys that should be wrapped in braces
    named_keys = {
        "enter", "tab", "home", "end", "up", "down", "left", "right",
        "space", "insert", "f1", "f2", "f3", "f4", "f5", "f6", "f7",
        "f8", "f9", "f10", "f11", "f12",
    }
    canonical = _key_aliases.get(key.lower(), key.upper())
    if key.lower() in named_keys or key.lower() in _key_aliases:
        key = "{" + canonical + "}"
    elif len(key) > 1:
        key = "{" + key.upper() + "}"

    return modifiers + key


@cli.command()
@click.argument("keys")
@click.option("--target", default=None, help="Focus a control by index before sending keys")
@click.pass_context
def keys(ctx, keys: str, target: Optional[str]):
    """Send keyboard keys (e.g. 'ctrl+a', 'Enter', 'alt+f4')."""
    try:
        session = _ensure_state(ctx)
        translated = _translate_keys(keys)
        if target is not None:
            result = session.execute_on_target(
                "keyboard_input", target, {"keys": translated}
            )
        else:
            result = session.execute_global(
                "keyboard_input", {"keys": translated}
            )
        _output_with_return_state(ctx, result)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@cli.command()
@click.argument("index")
@click.argument("direction", type=click.Choice(["up", "down", "left", "right"]))
@click.argument("amount", default=3, type=int)
@click.pass_context
def scroll(ctx, index: str, direction: str, amount: int):
    """Scroll a control by index."""
    try:
        session = _ensure_state(ctx)
        if direction == "up":
            scroll_params = {"wheel_dist": amount}
        elif direction == "down":
            scroll_params = {"wheel_dist": -amount}
        elif direction == "left":
            scroll_params = {"wheel_dist": -amount, "horizontal": True}
        else:  # right
            scroll_params = {"wheel_dist": amount, "horizontal": True}

        result = session.execute_on_target("wheel_mouse_input", index, scroll_params)
        _output_with_return_state(ctx, result)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@cli.command()
@click.argument("index")
@click.argument("value")
@click.pass_context
def select(ctx, index: str, value: str):
    """Select a value in a dropdown/combo by index."""
    try:
        session = _ensure_state(ctx)
        # First click to open the dropdown, then select
        result = session.execute_on_target("click_input", index, {})
        time.sleep(0.3)
        result = session.execute_on_target(
            "set_edit_text", index, {"text": value}
        )
        _output_with_return_state(ctx, result)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@cli.command()
@click.argument("index")
@click.argument("x2", type=int)
@click.argument("y2", type=int)
@click.option("--button", default="left", help="Mouse button (left/right)")
@click.option("--duration", default=1.0, type=float, help="Drag duration in seconds")
@click.pass_context
def drag(ctx, index: str, x2: int, y2: int, button: str, duration: float):
    """Drag from a control to absolute coordinates (x2, y2)."""
    try:
        session = _ensure_state(ctx)
        control = session.state.resolve(index)
        if control is not None:
            # Tier 1: UIA control — get start coords from UIAWrapper
            rect = control.rectangle()
            start_x = (rect.left + rect.right) // 2
            start_y = (rect.top + rect.bottom) // 2
        else:
            # Tier 2: Vision target — get start coords from TargetInfo.rect
            target = next(
                (t for t in session.state.targets if t.id == index), None
            )
            if target is None or target.rect is None:
                output_error(f"Control {index} not found or has no rect", ctx.obj["json"])
                sys.exit(1)
            start_x = (target.rect[0] + target.rect[2]) // 2
            start_y = (target.rect[1] + target.rect[3]) // 2

        result = session.execute_global(
            "drag_on_coordinates",
            {
                "start_x": str(start_x),
                "start_y": str(start_y),
                "end_x": str(x2),
                "end_y": str(y2),
                "button": button,
                "duration": str(duration),
            },
        )
        _output_with_return_state(ctx, result)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


# ============================================================================
# Window Management Commands
# ============================================================================


@cli.command()
@click.argument("window")
@click.pass_context
def focus(ctx, window: str):
    """Focus a window by title, process name, or index number."""
    try:
        session = _get_session(ctx)
        if session.focus_window(window):
            title = session.window.window_text() if session.window else "unknown"
            output(f'Focused: "{title}"', as_json=ctx.obj["json"])
        else:
            output_error(f'No window matching "{window}" found', ctx.obj["json"])
            sys.exit(1)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@cli.command()
@click.argument("app")
@click.pass_context
def launch(ctx, app: str):
    """Launch an application."""
    try:
        session = _get_session(ctx)
        if session.launch_app(app):
            title = session.window.window_text() if session.window else "unknown"
            output(f'Launched: "{title}"', as_json=ctx.obj["json"])
        else:
            output_error(f"Failed to launch {app}", ctx.obj["json"])
            sys.exit(1)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@cli.command()
@click.pass_context
def close(ctx):
    """Close the current window."""
    try:
        session = _ensure_window(ctx)
        if session.close_window():
            output("Window closed", as_json=ctx.obj["json"])
        else:
            output_error("Failed to close window", ctx.obj["json"])
            sys.exit(1)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


# ============================================================================
# Coordinate Mode (Windows-specific)
# ============================================================================


@cli.command("click-at")
@click.argument("x", type=int)
@click.argument("y", type=int)
@click.pass_context
def click_at(ctx, x: int, y: int):
    """Click at absolute screen coordinates."""
    try:
        session = _ensure_window(ctx)
        result = session.execute_global(
            "click_on_coordinates", {"x": str(x), "y": str(y)}
        )
        _output_with_return_state(ctx, result)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@cli.command("drag-at")
@click.argument("x1", type=int)
@click.argument("y1", type=int)
@click.argument("x2", type=int)
@click.argument("y2", type=int)
@click.option("--button", default="left", help="Mouse button (left/right)")
@click.option("--duration", default=1.0, type=float, help="Drag duration in seconds")
@click.pass_context
def drag_at(ctx, x1: int, y1: int, x2: int, y2: int, button: str, duration: float):
    """Drag from (x1, y1) to (x2, y2) using absolute screen coordinates."""
    try:
        session = _ensure_window(ctx)
        result = session.execute_global(
            "drag_on_coordinates",
            {
                "start_x": str(x1),
                "start_y": str(y1),
                "end_x": str(x2),
                "end_y": str(y2),
                "button": button,
                "duration": str(duration),
            },
        )
        _output_with_return_state(ctx, result)
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


# ============================================================================
# Data Extraction
# ============================================================================


@cli.group()
@click.pass_context
def get(ctx):
    """Extract data from controls."""
    pass


@get.command("text")
@click.argument("index")
@click.pass_context
def get_text(ctx, index: str):
    """Get the text content of a control."""
    try:
        session = _ensure_state(ctx)
        result = session.execute_on_target("texts", index, {})
        output(result, as_json=ctx.obj["json"])
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@get.command("rect")
@click.argument("index")
@click.pass_context
def get_rect(ctx, index: str):
    """Get the bounding rectangle of a control."""
    try:
        session = _ensure_state(ctx)
        control = session.state.resolve(index)
        if control is None:
            output_error(f"Control {index} not found", ctx.obj["json"])
            sys.exit(1)
        rect = control.rectangle()
        result = {
            "left": rect.left,
            "top": rect.top,
            "right": rect.right,
            "bottom": rect.bottom,
        }
        output(result, as_json=ctx.obj["json"])
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


@get.command("value")
@click.argument("index")
@click.pass_context
def get_value(ctx, index: str):
    """Get the value of a control (e.g. toggle state, slider position)."""
    try:
        session = _ensure_state(ctx)
        control = session.state.resolve(index)
        if control is None:
            output_error(f"Control {index} not found", ctx.obj["json"])
            sys.exit(1)
        # Try legacy_properties for Value pattern, fall back to texts
        try:
            value = control.legacy_properties().get("Value", "")
        except Exception:
            value = ""
        if not value:
            try:
                value = control.get_value()
            except Exception:
                pass
        if not value:
            try:
                texts = control.texts()
                value = texts[0] if texts else ""
            except Exception:
                value = ""
        output(value, as_json=ctx.obj["json"])
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


# ============================================================================
# Wait Commands
# ============================================================================


@cli.command("wait")
@click.argument("seconds", default=1.0, type=float, required=False)
@click.option("--visible", "wait_visible", default=None, help="Wait until control becomes visible (by index)")
@click.option("--enabled", "wait_enabled", default=None, help="Wait until control becomes enabled (by index)")
@click.option("--timeout", default=10.0, type=float, help="Timeout in seconds for --visible/--enabled")
@click.pass_context
def wait_cmd(ctx, seconds: float, wait_visible: str, wait_enabled: str, timeout: float):
    """Wait for seconds, or until a control becomes visible/enabled."""
    try:
        if wait_visible:
            session = _ensure_state(ctx)
            control = session.state.resolve(wait_visible)
            if control is None:
                output_error(f"Control {wait_visible} not found", ctx.obj["json"])
                sys.exit(1)
            remaining = timeout
            while not control.is_visible() and remaining > 0:
                time.sleep(0.5)
                remaining -= 0.5
            if control.is_visible():
                output(f"Control {wait_visible} is visible", as_json=ctx.obj["json"])
            else:
                output_error(f"Timeout: control {wait_visible} not visible after {timeout}s", ctx.obj["json"])
                sys.exit(1)
        elif wait_enabled:
            session = _ensure_state(ctx)
            control = session.state.resolve(wait_enabled)
            if control is None:
                output_error(f"Control {wait_enabled} not found", ctx.obj["json"])
                sys.exit(1)
            remaining = timeout
            while not control.is_enabled() and remaining > 0:
                time.sleep(0.5)
                remaining -= 0.5
            if control.is_enabled():
                output(f"Control {wait_enabled} is enabled", as_json=ctx.obj["json"])
            else:
                output_error(f"Timeout: control {wait_enabled} not enabled after {timeout}s", ctx.obj["json"])
                sys.exit(1)
        else:
            time.sleep(seconds)
            output(f"Waited {seconds}s", as_json=ctx.obj["json"])
    except Exception as e:
        output_error(str(e), ctx.obj["json"])
        sys.exit(1)


# ============================================================================
# Session Daemon
# ============================================================================


@cli.command("_serve", hidden=True)
@click.option("--session-name", required=True, help="Session name")
@click.option("--port", type=int, required=True, help="TCP port")
@click.option("--vision", "use_vision", is_flag=True)
@click.option("--infer", "use_infer", is_flag=True)
@click.option("--vision-api-key", default=None)
@click.option("--vision-base-url", default=None)
@click.pass_context
def _serve_cmd(ctx, session_name, port, use_vision, use_infer, vision_api_key, vision_base_url):
    """[Internal] Start the session daemon server."""
    from winactions.cli.session_dispatch import SessionDispatch
    from winactions.cli.session_server import SessionServer, write_pid_file

    session = DesktopSession.create(
        vision=use_vision,
        infer=use_infer,
        vision_api_key=vision_api_key,
        vision_base_url=vision_base_url,
    )
    dispatch = SessionDispatch(
        session,
        default_vision=use_vision,
        default_infer=use_infer,
    )
    write_pid_file(session_name, port)
    server = SessionServer(dispatch, port, session_name)
    server.serve_forever()


# ============================================================================
# --session Daemon Routing (pre-Click fast path)
# ============================================================================


def _extract_session_flag(args: list[str]) -> Optional[str]:
    """Extract --session value from argv.  Returns None if not present."""
    for i, arg in enumerate(args):
        if arg == "--session" and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--session="):
            return arg.split("=", 1)[1]
    return None


def _is_serve_command(args: list[str]) -> bool:
    """Check if argv contains the _serve internal command."""
    return "_serve" in args


def _parse_args_for_daemon(args: list[str]) -> dict:
    """Parse argv into a daemon request dict.

    Extracts global flags (--window, --json, --return-state, --vision,
    --infer, etc.), the command name, and command-specific arguments.
    """
    # Global flags to extract (flag_name -> (key, has_value))
    global_flags = {
        "--window": ("window", True),
        "--json": ("json", False),
        "--return-state": ("return_state", False),
        "--vision": ("vision", False),
        "--infer": ("infer", False),
        "--vision-api-key": ("vision_api_key", True),
        "--vision-base-url": ("vision_base_url", True),
    }

    flags: dict = {}
    remaining: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]

        # Skip --session and its value
        if arg == "--session":
            i += 2
            continue
        if arg.startswith("--session="):
            i += 1
            continue

        matched = False
        for flag, (key, has_value) in global_flags.items():
            if arg == flag:
                if has_value:
                    if i + 1 < len(args):
                        flags[key] = args[i + 1]
                        i += 2
                    else:
                        i += 1
                else:
                    flags[key] = True
                    i += 1
                matched = True
                break
            if has_value and arg.startswith(flag + "="):
                flags[key] = arg.split("=", 1)[1]
                i += 1
                matched = True
                break

        if not matched:
            remaining.append(arg)
            i += 1

    if not remaining:
        return {"command": "", "args": {}, "flags": flags}

    command = remaining[0]
    cmd_args = remaining[1:]

    # Handle subcommands like "get text", "get rect", "get value"
    if command == "get" and cmd_args:
        command = f"get {cmd_args[0]}"
        cmd_args = cmd_args[1:]

    # Parse command-specific arguments based on the command
    args_dict = _parse_command_args(command, cmd_args)

    return {"command": command, "args": args_dict, "flags": flags}


def _parse_command_args(command: str, cmd_args: list[str]) -> dict:
    """Parse positional + optional args for a specific command."""
    result: dict = {}

    # Separate options from positional args
    positional = []
    i = 0
    while i < len(cmd_args):
        arg = cmd_args[i]
        if arg == "--":
            # Everything after -- is positional
            positional.extend(cmd_args[i + 1:])
            break
        if arg.startswith("--"):
            # Option flag
            opt_name = arg[2:]
            # Check for --flag=value
            if "=" in opt_name:
                k, v = opt_name.split("=", 1)
                result[k.replace("-", "_")] = v
                i += 1
                continue
            # Boolean flags
            if opt_name in ("right", "screenshot", "annotated", "tree", "verbose"):
                result[opt_name] = True
                i += 1
                continue
            # Options with values
            if i + 1 < len(cmd_args):
                result[opt_name.replace("-", "_")] = cmd_args[i + 1]
                i += 2
                continue
            i += 1
            continue
        positional.append(arg)
        i += 1

    # Map positional args based on command
    _positional_maps = {
        "state": [],
        "windows": [],
        "inspect": ["index"],
        "screenshot": ["path"],
        "click": ["index"],
        "dblclick": ["index"],
        "rightclick": ["index"],
        "input": ["index", "text"],
        "type": ["text"],
        "keys": ["keys"],
        "scroll": ["index", "direction", "amount"],
        "select": ["index", "value"],
        "drag": ["index", "x2", "y2"],
        "click-at": ["x", "y"],
        "drag-at": ["x1", "y1", "x2", "y2"],
        "focus": ["window"],
        "launch": ["app"],
        "close": [],
        "wait": ["seconds"],
        "get text": ["index"],
        "get rect": ["index"],
        "get value": ["index"],
    }

    param_names = _positional_maps.get(command, [])
    for j, name in enumerate(param_names):
        if j < len(positional):
            result[name] = positional[j]

    return result


def _daemon_forward(session_name: str, args: list[str]) -> None:
    """Forward the CLI invocation to the daemon and print the result."""
    from winactions.cli.session_client import ensure_server, send_command
    from winactions.cli.formatter import output, output_error

    request = _parse_args_for_daemon(args)

    if not request["command"]:
        # No command specified — fall through to Click for --help etc.
        cli()
        return

    # Extract flags for server startup
    flags = request.get("flags", {})
    try:
        port = ensure_server(
            session_name,
            vision=flags.get("vision", False),
            infer=flags.get("infer", False),
            vision_api_key=flags.get("vision_api_key"),
            vision_base_url=flags.get("vision_base_url"),
        )
    except RuntimeError as e:
        output_error(str(e), as_json=flags.get("json", False))
        sys.exit(1)

    response = send_command(port, request)
    as_json = flags.get("json", False)

    if response.get("status") == "error":
        output_error(response.get("error", "Unknown error"), as_json=as_json)
        sys.exit(1)

    result = response.get("result")
    if result is not None:
        output(result, as_json=as_json)

    # If state was returned (--return-state), output it too
    state_data = response.get("state")
    if state_data is not None:
        output(state_data, as_json=as_json)


# ============================================================================
# Entry Point
# ============================================================================


def _setup_utf8_io() -> None:
    """Force UTF-8 encoding on stdout/stderr to prevent GBK errors on Windows.

    ``reconfigure()`` is the cleanest approach but can fail with
    ``AttributeError`` when the stream is not a ``TextIOWrapper`` (e.g.
    certain pipe or IDE configurations).  In that case we wrap the
    underlying binary buffer in a new ``TextIOWrapper``.
    """
    import io

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        # Already UTF-8 — nothing to do
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if enc == "utf8":
            # Still ensure errors="replace" so unencodable chars never crash
            try:
                stream.reconfigure(errors="replace")
            except (AttributeError, OSError):
                pass
            continue
        # Try reconfigure (works on standard TextIOWrapper)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
            continue
        except (AttributeError, OSError):
            pass
        # Fallback: wrap the underlying binary buffer
        binary = getattr(stream, "buffer", None)
        if binary is not None:
            wrapper = io.TextIOWrapper(
                binary,
                encoding="utf-8",
                errors="replace",
                line_buffering=getattr(stream, "line_buffering", True),
            )
            setattr(sys, name, wrapper)


def main():
    import os

    if os.name == "nt":
        _setup_utf8_io()

    # --session fast path: route to daemon, bypass Click
    args = sys.argv[1:]
    session_name = _extract_session_flag(args)
    if session_name and not _is_serve_command(args):
        try:
            _daemon_forward(session_name, args)
        except SystemExit:
            raise
        except Exception as e:
            try:
                print(f"winctl: session error: {e}", file=sys.stderr)
            except Exception:
                pass
            sys.exit(1)
        return

    try:
        cli()
    except SystemExit:
        raise
    except Exception as e:
        # Last-resort handler for anything that escapes Click and
        # per-command try/except blocks.  Prevents silent exit-code-1.
        try:
            print(f"winctl: unexpected error: {e}", file=sys.stderr)
        except Exception:
            # stderr itself is broken — write raw bytes
            try:
                buf = getattr(sys.stderr, "buffer", None)
                if buf:
                    buf.write(f"winctl: unexpected error: {e}\n"
                              .encode("utf-8", errors="replace"))
                    buf.flush()
            except Exception:
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()
