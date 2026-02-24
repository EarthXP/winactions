"""SessionDispatch — command dispatch layer for daemon mode.

Maps request dicts to handler methods operating on a persistent
DesktopSession.  Transport-agnostic: works over TCP, stdio, or direct
in-process calls.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from winactions.cli.session import DesktopSession

logger = logging.getLogger(__name__)


class SessionDispatch:
    """Command dispatch: request dict -> response dict.  Never raises."""

    def __init__(
        self,
        session: DesktopSession,
        *,
        default_vision: bool = False,
        default_infer: bool = False,
    ):
        self.session = session
        self._default_vision = default_vision
        self._default_infer = default_infer
        self._current_vision = default_vision
        self._current_infer = default_infer
        self._dispatch: Dict[str, Callable] = self._build_dispatch_table()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle(self, request: dict) -> dict:
        """Process a single request, return a response dict."""
        command = request.get("command", "")
        args = request.get("args", {})
        flags = request.get("flags", {})

        # Built-in meta commands
        if command == "_ping":
            return {"status": "ok", "result": "pong"}
        if command == "_shutdown":
            return {"status": "ok", "result": "shutdown"}

        handler = self._dispatch.get(command)
        if handler is None:
            return {"status": "error", "error": f"Unknown command: {command}"}
        try:
            self._maybe_switch_provider(flags)
            self._maybe_switch_window(flags)
            return handler(args, flags)
        except Exception as e:
            logger.exception("Handler error for %s", command)
            return {"status": "error", "error": str(e), "command": command}

    # ------------------------------------------------------------------
    # Dispatch table
    # ------------------------------------------------------------------

    def _build_dispatch_table(self) -> Dict[str, Callable]:
        return {
            # Perception
            "state": self._handle_state,
            "windows": self._handle_windows,
            "inspect": self._handle_inspect,
            "screenshot": self._handle_screenshot,
            # Execution — index-based
            "click": self._handle_click,
            "dblclick": self._handle_dblclick,
            "rightclick": self._handle_rightclick,
            "input": self._handle_input,
            "type": self._handle_type,
            "keys": self._handle_keys,
            "scroll": self._handle_scroll,
            "select": self._handle_select,
            "drag": self._handle_drag,
            # Execution — coordinate-based
            "click-at": self._handle_click_at,
            "drag-at": self._handle_drag_at,
            # Window management
            "focus": self._handle_focus,
            "launch": self._handle_launch,
            "close": self._handle_close,
            # Data extraction
            "get text": self._handle_get_text,
            "get rect": self._handle_get_rect,
            "get value": self._handle_get_value,
            # Wait
            "wait": self._handle_wait,
        }

    # ------------------------------------------------------------------
    # Perception handlers
    # ------------------------------------------------------------------

    def _handle_state(self, args: dict, flags: dict) -> dict:
        self._ensure_window(flags)
        screenshot = args.get("screenshot", False)
        tree = args.get("tree", False)
        verbose = args.get("verbose", False)
        ui_state = self.session.refresh_state(screenshot=screenshot)
        if tree:
            tree_data = self.session.get_control_tree()
            return {"status": "ok", "result": tree_data}
        return {"status": "ok", "result": ui_state.to_json(verbose=verbose)}

    def _handle_windows(self, args: dict, flags: dict) -> dict:
        window_list = self.session.list_windows()
        return {"status": "ok", "result": window_list}

    def _handle_inspect(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        index = args.get("index", "")
        control = self.session.state.resolve(index)
        if control is None:
            return {"status": "error", "error": f"Control with index {index} not found"}
        from winactions.control.inspector import ControlInspectorFacade
        info = ControlInspectorFacade.get_control_info(control)
        return {"status": "ok", "result": info}

    def _handle_screenshot(self, args: dict, flags: dict) -> dict:
        self._ensure_window(flags)
        import os
        import tempfile
        path = args.get("path")
        if not path:
            fd, path = tempfile.mkstemp(suffix=".png", prefix="winctl_")
            os.close(fd)
        from winactions.screenshot.photographer import PhotographerFacade
        facade = PhotographerFacade()
        facade.capture_app_window_screenshot(self.session.window, save_path=path)
        return {"status": "ok", "result": {"path": path}}

    # ------------------------------------------------------------------
    # Execution handlers — index-based
    # ------------------------------------------------------------------

    def _handle_click(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        index = args.get("index", "")
        right = args.get("right", False)
        params = {"button": "right"} if right else {}
        result = self.session.execute_on_target("click_input", index, params)
        return self._action_response(result, flags)

    def _handle_dblclick(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        index = args.get("index", "")
        result = self.session.execute_on_target("click_input", index, {"double": True})
        return self._action_response(result, flags)

    def _handle_rightclick(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        index = args.get("index", "")
        result = self.session.execute_on_target("click_input", index, {"button": "right"})
        return self._action_response(result, flags)

    def _handle_input(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        index = args.get("index", "")
        text = args.get("text", "")
        result = self.session.execute_on_target("set_edit_text", index, {"text": text})
        return self._action_response(result, flags)

    def _handle_type(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        text = args.get("text", "")
        result = self.session.execute_global("type", {"text": text})
        return self._action_response(result, flags)

    def _handle_keys(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        keys_str = args.get("keys", "")
        target = args.get("target")
        translated = _translate_keys(keys_str)
        if target is not None:
            result = self.session.execute_on_target(
                "keyboard_input", target, {"keys": translated}
            )
        else:
            result = self.session.execute_global(
                "keyboard_input", {"keys": translated}
            )
        return self._action_response(result, flags)

    def _handle_scroll(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        index = args.get("index", "")
        direction = args.get("direction", "down")
        amount = int(args.get("amount", 3))
        if direction == "up":
            scroll_params = {"wheel_dist": amount}
        elif direction == "down":
            scroll_params = {"wheel_dist": -amount}
        elif direction == "left":
            scroll_params = {"wheel_dist": -amount, "horizontal": True}
        else:  # right
            scroll_params = {"wheel_dist": amount, "horizontal": True}
        result = self.session.execute_on_target(
            "wheel_mouse_input", index, scroll_params
        )
        return self._action_response(result, flags)

    def _handle_select(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        index = args.get("index", "")
        value = args.get("value", "")
        self.session.execute_on_target("click_input", index, {})
        time.sleep(0.3)
        result = self.session.execute_on_target(
            "set_edit_text", index, {"text": value}
        )
        return self._action_response(result, flags)

    def _handle_drag(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        index = args.get("index", "")
        x2 = int(args.get("x2", 0))
        y2 = int(args.get("y2", 0))
        button = args.get("button", "left")
        duration = float(args.get("duration", 1.0))

        control = self.session.state.resolve(index)
        if control is not None:
            rect = control.rectangle()
            start_x = (rect.left + rect.right) // 2
            start_y = (rect.top + rect.bottom) // 2
        else:
            target = next(
                (t for t in self.session.state.targets if t.id == index), None
            )
            if target is None or target.rect is None:
                return {"status": "error", "error": f"Control {index} not found or has no rect"}
            start_x = (target.rect[0] + target.rect[2]) // 2
            start_y = (target.rect[1] + target.rect[3]) // 2

        result = self.session.execute_global(
            "drag_on_coordinates",
            {
                "start_x": str(start_x), "start_y": str(start_y),
                "end_x": str(x2), "end_y": str(y2),
                "button": button, "duration": str(duration),
            },
        )
        return self._action_response(result, flags)

    # ------------------------------------------------------------------
    # Execution handlers — coordinate-based
    # ------------------------------------------------------------------

    def _handle_click_at(self, args: dict, flags: dict) -> dict:
        self._ensure_window(flags)
        x = int(args.get("x", 0))
        y = int(args.get("y", 0))
        result = self.session.execute_global(
            "click_on_coordinates", {"x": str(x), "y": str(y)}
        )
        return self._action_response(result, flags)

    def _handle_drag_at(self, args: dict, flags: dict) -> dict:
        self._ensure_window(flags)
        result = self.session.execute_global(
            "drag_on_coordinates",
            {
                "start_x": str(args.get("x1", 0)),
                "start_y": str(args.get("y1", 0)),
                "end_x": str(args.get("x2", 0)),
                "end_y": str(args.get("y2", 0)),
                "button": args.get("button", "left"),
                "duration": str(args.get("duration", 1.0)),
            },
        )
        return self._action_response(result, flags)

    # ------------------------------------------------------------------
    # Window management handlers
    # ------------------------------------------------------------------

    def _handle_focus(self, args: dict, flags: dict) -> dict:
        window = args.get("window", "")
        if self.session.focus_window(window):
            title = self.session.window.window_text() if self.session.window else "unknown"
            return {"status": "ok", "result": f'Focused: "{title}"'}
        return {"status": "error", "error": f'No window matching "{window}" found'}

    def _handle_launch(self, args: dict, flags: dict) -> dict:
        app = args.get("app", "")
        if self.session.launch_app(app):
            title = self.session.window.window_text() if self.session.window else "unknown"
            return {"status": "ok", "result": f'Launched: "{title}"'}
        return {"status": "error", "error": f"Failed to launch {app}"}

    def _handle_close(self, args: dict, flags: dict) -> dict:
        self._ensure_window(flags)
        if self.session.close_window():
            return {"status": "ok", "result": "Window closed"}
        return {"status": "error", "error": "Failed to close window"}

    # ------------------------------------------------------------------
    # Data extraction handlers
    # ------------------------------------------------------------------

    def _handle_get_text(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        index = args.get("index", "")
        result = self.session.execute_on_target("texts", index, {})
        return {"status": "ok", "result": result}

    def _handle_get_rect(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        index = args.get("index", "")
        control = self.session.state.resolve(index)
        if control is None:
            return {"status": "error", "error": f"Control {index} not found"}
        rect = control.rectangle()
        return {
            "status": "ok",
            "result": {
                "left": rect.left, "top": rect.top,
                "right": rect.right, "bottom": rect.bottom,
            },
        }

    def _handle_get_value(self, args: dict, flags: dict) -> dict:
        self._ensure_state(flags)
        index = args.get("index", "")
        control = self.session.state.resolve(index)
        if control is None:
            return {"status": "error", "error": f"Control {index} not found"}
        value = ""
        try:
            value = control.legacy_properties().get("Value", "")
        except Exception:
            pass
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
                pass
        return {"status": "ok", "result": value}

    # ------------------------------------------------------------------
    # Wait handler
    # ------------------------------------------------------------------

    def _handle_wait(self, args: dict, flags: dict) -> dict:
        wait_visible = args.get("visible")
        wait_enabled = args.get("enabled")
        timeout = float(args.get("timeout", 10.0))

        if wait_visible:
            self._ensure_state(flags)
            control = self.session.state.resolve(wait_visible)
            if control is None:
                return {"status": "error", "error": f"Control {wait_visible} not found"}
            remaining = timeout
            while not control.is_visible() and remaining > 0:
                time.sleep(0.5)
                remaining -= 0.5
            if control.is_visible():
                return {"status": "ok", "result": f"Control {wait_visible} is visible"}
            return {"status": "error", "error": f"Timeout: control {wait_visible} not visible after {timeout}s"}

        if wait_enabled:
            self._ensure_state(flags)
            control = self.session.state.resolve(wait_enabled)
            if control is None:
                return {"status": "error", "error": f"Control {wait_enabled} not found"}
            remaining = timeout
            while not control.is_enabled() and remaining > 0:
                time.sleep(0.5)
                remaining -= 0.5
            if control.is_enabled():
                return {"status": "ok", "result": f"Control {wait_enabled} is enabled"}
            return {"status": "error", "error": f"Timeout: control {wait_enabled} not enabled after {timeout}s"}

        seconds = float(args.get("seconds", 1.0))
        time.sleep(seconds)
        return {"status": "ok", "result": f"Waited {seconds}s"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_window(self, flags: dict) -> None:
        """Ensure a window is focused."""
        if self.session.window is None:
            window_name = flags.get("window")
            if window_name:
                if not self.session.focus_window(window_name):
                    raise RuntimeError(f'No window matching "{window_name}" found')
            else:
                self.session.focus_foreground()

    def _ensure_state(self, flags: dict) -> None:
        """Ensure state is available."""
        self._ensure_window(flags)
        if self.session.state is None:
            self.session.refresh_state()

    def _maybe_switch_window(self, flags: dict) -> None:
        """Switch window if the flags request a different one."""
        window_name = flags.get("window")
        if not window_name:
            return
        # If already focused on this window, skip
        if self.session.window is not None:
            try:
                current_title = self.session.window.window_text().lower()
                if window_name.lower() in current_title:
                    return
            except Exception:
                pass
        # Switch to the requested window
        if not self.session.focus_window(window_name):
            raise RuntimeError(f'No window matching "{window_name}" found')

    def _maybe_switch_provider(self, flags: dict) -> None:
        """Rebuild provider if vision/infer flags differ from current."""
        want_vision = flags.get("vision", self._default_vision)
        want_infer = flags.get("infer", self._default_infer)
        if (want_vision, want_infer) != (self._current_vision, self._current_infer):
            self._rebuild_provider(want_vision, want_infer)
            self._current_vision = want_vision
            self._current_infer = want_infer

    def _rebuild_provider(self, vision: bool, infer: bool) -> None:
        """Rebuild the session's provider (mirrors session.py constructor logic)."""
        import os
        from winactions.perception.provider import UIAStateProvider

        api_key = os.environ.get("WINACTIONS_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        base_url = os.environ.get("WINACTIONS_BASE_URL")

        uia_provider = UIAStateProvider(self.session.inspector)

        if infer:
            if not api_key:
                raise ValueError(
                    "Vision API key required for --infer. "
                    "Set WINACTIONS_API_KEY or ANTHROPIC_API_KEY environment variable."
                )
            from winactions.perception.structural_provider import StructuralInferenceProvider
            primary = StructuralInferenceProvider(
                uia_provider,
                api_key=api_key,
                base_url=base_url,
            )
        else:
            primary = uia_provider

        if vision:
            if not api_key:
                raise ValueError(
                    "Vision API key required for --vision. "
                    "Set WINACTIONS_API_KEY or ANTHROPIC_API_KEY environment variable."
                )
            from winactions.perception.vision_provider import VisionStateProvider
            from winactions.perception.provider import CompositeStateProvider
            vision_provider = VisionStateProvider(
                api_key=api_key,
                base_url=base_url,
            )
            self.session.provider = CompositeStateProvider(primary, vision_provider)
        else:
            self.session.provider = primary

        # Invalidate cached state since provider changed
        self.session.state = None

    def _action_response(self, result: Any, flags: dict) -> dict:
        """Build response for action commands, optionally with fresh state."""
        resp: dict = {"status": "ok", "result": str(result) if result else "OK"}
        if flags.get("return_state"):
            time.sleep(0.5)
            try:
                self._ensure_window(flags)
                ui_state = self.session.refresh_state()
                resp["state"] = ui_state.to_json()
            except Exception as e:
                resp["state_error"] = str(e)
        return resp


# ======================================================================
# Key translation (must stay in sync with app.py:_translate_keys)
# ======================================================================


def _translate_keys(keys_str: str) -> str:
    """Translate human-friendly key combos to pywinauto format.

    Examples: 'ctrl+a' -> '^a', 'alt+f4' -> '%{F4}', 'Enter' -> '{ENTER}'
    Handles mixed format: 'ctrl+a{DELETE}' -> '^a{DELETE}'
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
        remaining = remaining[m.end():]

    # If the remaining part is already in pywinauto format, prepend modifiers
    if re.search(r"[\\^%]|{.*}", remaining):
        return modifiers + remaining

    # Single character key with modifiers — done
    if modifiers and len(remaining) == 1:
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
    _key_aliases = {
        "escape": "ESC",
        "esc": "ESC",
        "del": "DEL",
        "delete": "DELETE",
        "bs": "BACKSPACE",
        "backspace": "BACKSPACE",
        "pageup": "PGUP",
        "pagedown": "PGDN",
        "pgup": "PGUP",
        "pgdn": "PGDN",
    }
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
