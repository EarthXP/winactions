"""DesktopSession — cross-command state management for the CLI.

Holds the current window, latest UIState snapshot, and the machinery
needed to resolve index numbers and execute commands.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import psutil

from winactions.config import get_action_config
from winactions.control.inspector import ControlInspectorFacade
from winactions.perception.provider import UIAStateProvider
from winactions.perception.state import UIState
from winactions.command.puppeteer import AppPuppeteer
from winactions.targets import TargetInfo, TargetKind

if TYPE_CHECKING or platform.system() == "Windows":
    from pywinauto.controls.uiawrapper import UIAWrapper
else:
    UIAWrapper = Any

logger = logging.getLogger(__name__)


class DesktopSession:
    """Cross-CLI-command state management.

    Typical flow:
        session = DesktopSession.create()
        session.focus_window("Notepad")
        state = session.refresh_state()
        session.execute_on_target("click_input", "1", {})
    """

    def __init__(
        self,
        inspector: Optional[ControlInspectorFacade] = None,
        provider: Optional[UIAStateProvider] = None,
        vision: bool = False,
        infer: bool = False,
        vision_api_key: Optional[str] = None,
        vision_base_url: Optional[str] = None,
    ):
        self.inspector = inspector or ControlInspectorFacade()
        uia_provider = provider or UIAStateProvider(self.inspector)

        effective_api_key = vision_api_key or os.environ.get("WINACTIONS_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        effective_base_url = vision_base_url or os.environ.get("WINACTIONS_BASE_URL")

        # Build the primary provider: UIA or UIA+inference
        if infer:
            if not effective_api_key:
                raise ValueError(
                    "Vision API key required for --infer. "
                    "Set WINACTIONS_API_KEY or ANTHROPIC_API_KEY environment variable, "
                    "or pass --vision-api-key."
                )
            from winactions.perception.structural_provider import StructuralInferenceProvider

            primary = StructuralInferenceProvider(
                uia_provider,
                api_key=effective_api_key,
                base_url=effective_base_url,
            )
        else:
            primary = uia_provider

        # Optionally wrap with vision as an additional source
        if vision:
            if not effective_api_key:
                raise ValueError(
                    "Vision API key required for --vision. "
                    "Set WINACTIONS_API_KEY or ANTHROPIC_API_KEY environment variable, "
                    "or pass --vision-api-key."
                )
            from winactions.perception.vision_provider import VisionStateProvider
            from winactions.perception.provider import CompositeStateProvider

            vision_provider = VisionStateProvider(
                api_key=effective_api_key,
                base_url=effective_base_url,
            )
            self.provider = CompositeStateProvider(primary, vision_provider)
        else:
            self.provider = primary

        self.window: Optional[UIAWrapper] = None
        self.state: Optional[UIState] = None
        self.puppeteer: Optional[AppPuppeteer] = None
        self._controls: List[UIAWrapper] = []

    @classmethod
    def create(
        cls,
        vision: bool = False,
        infer: bool = False,
        vision_api_key: Optional[str] = None,
        vision_base_url: Optional[str] = None,
    ) -> "DesktopSession":
        """Create a new session with default settings."""
        return cls(
            vision=vision,
            infer=infer,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
        )

    # --- Window management ---

    def list_windows(self) -> List[Dict[str, Any]]:
        """List all visible desktop windows."""
        windows_dict = self.inspector.get_desktop_app_dict(remove_empty=True)
        result = []
        for idx, window in windows_dict.items():
            try:
                process_name = self.inspector.get_application_root_name(window)
                result.append(
                    {
                        "id": idx,
                        "title": window.window_text(),
                        "process": process_name,
                        "handle": window.handle,
                    }
                )
            except Exception:
                pass
        return result

    def focus_window(self, identifier: str) -> bool:
        """Focus a window by title substring, process name, or index.

        :param identifier: Window title substring, process name, or numeric index.
        :return: True if a window was found and focused.
        """
        windows = self.inspector.get_desktop_windows(remove_empty=True)

        # Try numeric index first
        if identifier.isdigit():
            windows_dict = self.inspector.get_desktop_app_dict(remove_empty=True)
            window = windows_dict.get(identifier)
            if window:
                self._set_window(window)
                return True

        # Try title/process match
        identifier_lower = identifier.lower()
        for window in windows:
            try:
                title = window.window_text().lower()
                process = self.inspector.get_application_root_name(window).lower()
                if identifier_lower in title or identifier_lower in process:
                    self._set_window(window)
                    return True
            except Exception:
                continue

        return False

    def focus_foreground(self) -> bool:
        """Focus on the current foreground window."""
        try:
            import win32gui
            from pywinauto.controls.uiawrapper import UIAWrapper
            from pywinauto.uia_element_info import UIAElementInfo

            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                window = UIAWrapper(UIAElementInfo(handle_or_elem=hwnd))
                self._set_window(window)
                return True
        except Exception:
            pass

        # Fallback: use first window
        windows = self.inspector.get_desktop_windows(remove_empty=True)
        if windows:
            self._set_window(windows[0])
            return True
        return False

    def _set_window(self, window: UIAWrapper) -> None:
        """Set the current window and create a puppeteer for it."""
        self.window = window
        self.state = None  # Invalidate old state
        process_name = self.inspector.get_application_root_name(window)
        self.puppeteer = AppPuppeteer(
            process_name=process_name, app_root_name=process_name
        )

    def launch_app(self, app_path: str) -> bool:
        """Launch an application and wait for its window to appear."""
        try:
            subprocess.Popen(app_path)
            # Wait a bit for the app to start
            time.sleep(2)
            # Try to focus the newly opened window
            app_name = os.path.basename(app_path).lower().replace(".exe", "")
            return self.focus_window(app_name)
        except Exception as e:
            logger.error(f"Failed to launch {app_path}: {e}")
            return False

    def close_window(self) -> bool:
        """Close the current window."""
        if not self.window:
            return False
        try:
            self.window.close()
            self.window = None
            self.state = None
            return True
        except Exception as e:
            logger.error(f"Failed to close window: {e}")
            return False

    # --- State (perception) ---

    def get_control_tree(self) -> list:
        """Build a hierarchical tree of controls for the current window."""
        if not self.window:
            return []

        def _build_node(element, index_map, depth=0):
            info = self.inspector.get_control_info(element)
            ctrl_type = info.get("control_type", "")
            ctrl_name = info.get("control_text", "")
            # Find matching index if this element is in our state
            ctrl_id = ""
            if self.state:
                rect = info.get("control_rect")
                if rect:
                    rect_list = list(rect)
                    for t in self.state.targets:
                        if t.rect == rect_list and t.type == ctrl_type:
                            ctrl_id = t.id
                            break

            node = {"type": ctrl_type, "name": ctrl_name}
            if ctrl_id:
                node["id"] = ctrl_id

            if depth < 3:  # limit recursion depth
                try:
                    children_elements = element.children()
                    if children_elements:
                        children = []
                        for child in children_elements:
                            try:
                                children.append(_build_node(child, index_map, depth + 1))
                            except Exception:
                                continue
                        if children:
                            node["children"] = children
                except Exception:
                    pass
            return node

        try:
            children = self.window.children()
            return [_build_node(c, {}, 0) for c in children]
        except Exception:
            return []

    def refresh_state(self, screenshot: bool = False) -> UIState:
        """Re-scan the current window and assign fresh indexes."""
        if not self.window:
            raise RuntimeError(
                "No window focused. Use 'winctl focus <window>' or 'winctl windows' first."
            )

        targets, controls = self.provider.detect(self.window)
        self._controls = controls

        control_map = {}
        for target, ctrl in zip(targets, controls):
            control_map[target.id] = ctrl

        process_name = self.inspector.get_application_root_name(self.window)

        self.state = UIState(
            window_title=self.window.window_text(),
            window_handle=self.window.handle,
            process_name=process_name,
            targets=targets,
            control_map=control_map,
        )

        if screenshot:
            self._capture_screenshot()

        return self.state

    def _capture_screenshot(self) -> None:
        """Capture screenshots and update state paths."""
        if not self.state or not self.window:
            return
        try:
            from winactions.screenshot.photographer import PhotographerFacade

            facade = PhotographerFacade()
            tmp_dir = tempfile.mkdtemp(prefix="winctl_")

            # Plain screenshot
            path = os.path.join(tmp_dir, "screenshot.png")
            facade.capture_app_window_screenshot(self.window, save_path=path)
            self.state.screenshot_path = path

            # Annotated screenshot
            ann_path = os.path.join(tmp_dir, "annotated.png")
            facade.capture_app_window_screenshot_with_annotation(
                self.window,
                self._controls,
                save_path=ann_path,
            )
            self.state.annotated_screenshot_path = ann_path
        except ImportError:
            logger.debug("Pillow not installed, skipping screenshots")
        except Exception as e:
            logger.warning(f"Screenshot capture failed: {e}")

    # --- Execution ---

    def execute_on_target(
        self, command_name: str, target_id: str, params: Dict[str, Any]
    ) -> Any:
        """Resolve index → control → create receiver → execute command.

        For vision-only targets (control is None), the framework
        automatically computes the bbox centre from TargetInfo.rect and
        falls back to coordinate-based clicking.  The agent only ever
        outputs an index number — it never predicts coordinates.
        """
        if not self.state:
            raise RuntimeError("No state available. Run 'winctl state' first.")
        if not self.puppeteer:
            raise RuntimeError("No puppeteer available. Focus a window first.")

        control = self.state.resolve(target_id)

        if control is not None:
            # Tier-1: UIA path — pywinauto direct operation
            self.puppeteer.receiver_manager.create_ui_control_receiver(
                control, self.window
            )
            return self.puppeteer.execute_command(command_name, params)

        # Tier-2: Vision fallback — no UIAWrapper, use rect centre
        target = next(
            (t for t in self.state.targets if t.id == target_id), None
        )
        if target is None:
            raise RuntimeError(f"Target {target_id} not found in state.")
        if target.rect is None:
            raise RuntimeError(
                f"Target {target_id} is a vision-only element with no "
                f"bounding rect; cannot use coordinate fallback."
            )

        center_x = (target.rect[0] + target.rect[2]) // 2
        center_y = (target.rect[1] + target.rect[3]) // 2
        logger.info(
            "Target %s is vision-only (no UIA control). "
            "Falling back to coordinates (%d, %d).",
            target_id,
            center_x,
            center_y,
        )

        coord_params: Dict[str, Any] = {
            "x": str(center_x),
            "y": str(center_y),
        }

        if command_name in ("click_input", "click_on_coordinates"):
            coord_params.update(
                {k: v for k, v in params.items() if k in ("button", "double")}
            )
            return self.execute_global("click_on_coordinates", coord_params)

        if command_name == "set_edit_text":
            # Click to focus, then type via keyboard
            self.execute_global("click_on_coordinates", coord_params)
            return self.execute_global(
                "keyboard_input", {"keys": params.get("text", "")}
            )

        # Other commands: best-effort click at centre
        logger.warning(
            "Vision fallback: command '%s' on target %s mapped to click at centre.",
            command_name,
            target_id,
        )
        return self.execute_global("click_on_coordinates", coord_params)

    def execute_global(self, command_name: str, params: Dict[str, Any]) -> Any:
        """Execute a command that doesn't target a specific control."""
        if not self.puppeteer:
            raise RuntimeError("No puppeteer available. Focus a window first.")

        self.puppeteer.receiver_manager.create_ui_control_receiver(None, self.window)
        return self.puppeteer.execute_command(command_name, params)

    # --- Persistence ---

    def save(self, path: str) -> None:
        """Save session state to a JSON file for cross-process sharing."""
        data = {
            "window_handle": self.window.handle if self.window else None,
            "window_title": self.window.window_text() if self.window else None,
        }
        with open(path, "w") as f:
            json.dump(data, f)

    def load(self, path: str) -> bool:
        """Load session state from a JSON file."""
        try:
            with open(path, "r") as f:
                data = json.load(f)

            handle = data.get("window_handle")
            if handle:
                from pywinauto.controls.uiawrapper import UIAWrapper
                from pywinauto.uia_element_info import UIAElementInfo

                window = UIAWrapper(UIAElementInfo(handle_or_elem=handle))
                self._set_window(window)
                return True
        except Exception as e:
            logger.warning(f"Failed to load session from {path}: {e}")
        return False
