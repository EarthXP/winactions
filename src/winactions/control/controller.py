# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""ControlReceiver and command classes for UI control automation.

Adapted from ufo/automator/ui_control/controller.py with config decoupled
from UFO's global config system.
"""

import logging
import platform
import time
import warnings
from abc import abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Type, Union, TYPE_CHECKING

if TYPE_CHECKING or platform.system() == "Windows":
    import pyautogui
    import pywinauto
    from pywinauto import keyboard
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.win32structures import RECT
else:
    pyautogui = None
    pywinauto = None
    keyboard = None
    UIAWrapper = Any
    RECT = Any

from winactions.command.basic import CommandBasic, ReceiverBasic, ReceiverFactory
from winactions.command.puppeteer import ReceiverManager
from winactions.config import get_action_config

logger = logging.getLogger(__name__)

_pywinauto_timings_initialized = False


def _ensure_pywinauto_timings() -> None:
    """Lazy-initialize pywinauto timings from ActionConfig."""
    global _pywinauto_timings_initialized
    if _pywinauto_timings_initialized:
        return
    _pywinauto_timings_initialized = True

    if platform.system() != "Windows" or not pywinauto:
        return

    cfg = get_action_config()
    if cfg.after_click_wait is not None:
        pywinauto.timings.Timings.after_clickinput_wait = cfg.after_click_wait
        pywinauto.timings.Timings.after_click_wait = cfg.after_click_wait

    pyautogui.FAILSAFE = cfg.pyautogui_failsafe


class ControlReceiver(ReceiverBasic):
    """The control receiver — wraps a UIAWrapper control for command execution."""

    _command_registry: Dict[str, Type[CommandBasic]] = {}

    def __init__(
        self, control: Optional[UIAWrapper], application: Optional[UIAWrapper]
    ) -> None:
        _ensure_pywinauto_timings()

        self.control = control
        self.application = application

        # Only focus the application window (foreground activation).
        # Control-level set_focus() is deferred to _focus_control() and
        # called on-demand by operations that need keyboard focus
        # (set_edit_text, keyboard_input).  Mouse operations like
        # click_input do NOT need it — and calling it preemptively can
        # destroy contextual UI (e.g. ribbon tabs in WebView2 apps)
        # by shifting focus away from the currently active element.
        if application:
            self.application.set_focus()

    def _focus_control(self) -> None:
        """Acquire keyboard focus on the target control.

        Call before operations that require keyboard focus (typing,
        text editing).  Mouse-based operations (click, drag, scroll)
        should NOT call this — the click itself moves focus, and
        pre-focusing can destroy contextual UI in WebView2 apps.
        """
        if self.control:
            self.control.set_focus()
            self.wait_enabled()

    @property
    def type_name(self):
        return "UIControl"

    def atomic_execution(self, method_name: str, params: Dict[str, Any]) -> str:
        import traceback

        try:
            method = getattr(self.control, method_name)
            result = method(**params)
        except AttributeError:
            message = f"{self.control} doesn't have a method named {method_name}"
            logger.warning(message)
            result = message
        except Exception:
            full_traceback = traceback.format_exc()
            message = f"An error occurred: {full_traceback}"
            logger.warning(message)
            result = message
        return result

    def click_input(self, params: Dict[str, Union[str, bool]]) -> str:
        api_name = get_action_config().click_api

        if api_name == "click":
            self.atomic_execution("click", params)
        else:
            self.atomic_execution("click_input", params)
        return f"Click action has been executed, with parameters: {params}"

    def click_on_coordinates(self, params: Dict[str, str]) -> str:
        x = int(float(params.get("x", 0)))
        y = int(float(params.get("y", 0)))
        button = params.get("button", "left")
        double = params.get("double", False)

        self.application.set_focus()
        pyautogui.click(x, y, button=button, clicks=2 if double else 1)

        return (
            f"The click action has been executed at ({x}, {y}) "
            f"with button '{button}' and {'double' if double else 'single'} click."
        )

    def drag_on_coordinates(self, params: Dict[str, str]) -> str:
        start = (
            int(float(params.get("start_x", 0))),
            int(float(params.get("start_y", 0))),
        )
        end = (
            int(float(params.get("end_x", 0))),
            int(float(params.get("end_y", 0))),
        )
        duration = float(params.get("duration", 1))
        button = params.get("button", "left")
        key_hold = params.get("key_hold", None)

        self.application.set_focus()

        if key_hold:
            pyautogui.keyDown(key_hold)

        pyautogui.moveTo(start[0], start[1])
        pyautogui.dragTo(end[0], end[1], button=button, duration=duration)

        if key_hold:
            pyautogui.keyUp(key_hold)

        return (
            f"The drag action has been executed from {start} to {end}, "
            f"with a duration of {duration} and a button '{button}' held down."
        )

    def summary(self, params: Dict[str, str]) -> str:
        return params.get("text")

    def set_edit_text(self, params: Dict[str, str]) -> str:
        self._focus_control()
        cfg = get_action_config()
        text = params.get("text", "")
        inter_key_pause = cfg.input_text_inter_key_pause

        if params.get("clear_current_text", False):
            self.control.type_keys("^a", pause=inter_key_pause)
            self.control.type_keys("{DELETE}", pause=inter_key_pause)

        if cfg.input_text_api == "set_text":
            method_name = "set_edit_text"
            args = {"text": text}
        else:
            method_name = "type_keys"
            text = TextTransformer.transform_text(text, "all")
            args = {"keys": text, "pause": inter_key_pause, "with_spaces": True}

        try:
            result = self.atomic_execution(method_name, args)
            if (
                method_name == "set_text"
                and args["text"] not in self.control.window_text()
            ):
                raise Exception(f"Failed to use set_text: {args['text']}")
            if cfg.input_text_enter and method_name in ["type_keys", "set_text"]:
                self.atomic_execution("type_keys", params={"keys": "{ENTER}"})
            return result
        except Exception as e:
            if method_name == "set_text":
                logger.warning(
                    f"{self.control} doesn't have a method named {method_name}, "
                    "trying default input method"
                )
                clear_text_keys = "^a{BACKSPACE}"
                text_to_type = args["text"]
                keys_to_send = clear_text_keys + text_to_type
                args = {
                    "keys": keys_to_send,
                    "pause": inter_key_pause,
                    "with_spaces": True,
                }
                return self.atomic_execution("type_keys", args)
            else:
                return f"An error occurred: {e}"

    def keyboard_input(self, params: Dict[str, str]) -> str:
        control_focus = params.get("control_focus", True)
        keys = params.get("keys", "")

        if control_focus and self.control is not None:
            self.control.set_focus()
            self.atomic_execution("type_keys", {"keys": keys})
        else:
            self.application.type_keys(keys=keys)
        return keys

    def key_press(self, params: Dict[str, str]) -> str:
        keys = params.get("keys", [])
        for key in keys:
            pyautogui.keyDown(key.lower())
        for key in keys:
            pyautogui.keyUp(key.lower())
        return f"Key press action has been executed: {keys}"

    def texts(self) -> str:
        return self.control.texts()

    def wheel_mouse_input(self, params: Dict[str, str]):
        horizontal = params.pop("horizontal", False)
        if horizontal:
            # pywinauto does not support horizontal scrolling;
            # fall back to pyautogui.hscroll() at the control centre.
            dist = int(params.get("wheel_dist", 0))
            target = self.control if self.control is not None else self.application
            if target is not None:
                rect = target.rectangle()
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                pyautogui.hscroll(dist, x=cx, y=cy)
            else:
                pyautogui.hscroll(dist)
            return "The horizontal wheel mouse input action has been executed."
        if self.control is not None:
            self.atomic_execution("wheel_mouse_input", params)
            return "The wheel mouse input action has been executed on the selected control."
        else:
            keyboard.send_keys("{VK_CONTROL up}")
            dist = int(params.get("wheel_dist", 0))
            self.application.wheel_mouse_input(wheel_dist=dist)
            return "The wheel mouse input action has been executed on the application window."

    def scroll(self, params: Dict[str, str]) -> str:
        x = int(params.get("x", 0))
        y = int(params.get("y", 0))
        new_x, new_y = self.transform_point(x, y)

        scroll_x = int(params.get("scroll_x", 0))
        scroll_y = int(params.get("scroll_y", 0))

        pyautogui.vscroll(scroll_y, x=new_x, y=new_y)
        pyautogui.hscroll(scroll_x, x=new_x, y=new_y)

    def mouse_move(self, params: Dict[str, str]) -> str:
        x = int(params.get("x", 0))
        y = int(params.get("y", 0))
        new_x, new_y = self.transform_point(x, y)
        pyautogui.moveTo(new_x, new_y, duration=0.1)

    def type(self, params: Dict[str, str]) -> str:
        text = params.get("text", "")
        pyautogui.write(text, interval=0.1)

    def no_action(self):
        return ""

    def annotation(
        self, params: Dict[str, str], annotation_dict: Dict[str, UIAWrapper]
    ) -> List[str]:
        selected_controls_labels = params.get("control_labels", [])
        control_reannotate = [
            annotation_dict[str(label)] for label in selected_controls_labels
        ]
        return control_reannotate

    def wait_enabled(self, timeout: float = 10, retry_interval: float = 0.5) -> None:
        while not self.control.is_enabled():
            time.sleep(retry_interval)
            timeout -= retry_interval
            if timeout <= 0:
                warnings.warn(f"Timeout: {self.control} is not enabled.")
                break

    def wait_visible(self, timeout: float = 10, retry_interval: float = 0.5) -> None:
        while not self.control.is_visible():
            time.sleep(retry_interval)
            timeout -= retry_interval
            if timeout <= 0:
                warnings.warn(f"Timeout: {self.control} is not visible.")
                break

    def transform_point(self, fraction_x: float, fraction_y: float) -> Tuple[int, int]:
        application_rect: RECT = self.application.rectangle()
        application_x = application_rect.left
        application_y = application_rect.top
        application_width = application_rect.width()
        application_height = application_rect.height()

        x = application_x + int(application_width * fraction_x)
        y = application_y + int(application_height * fraction_y)
        return x, y

    def transform_absolute_point_to_fractional(
        self, x: int, y: int
    ) -> Tuple[int, int]:
        application_rect: RECT = self.application.rectangle()
        application_width = application_rect.width()
        application_height = application_rect.height()

        fraction_x = x / application_width
        fraction_y = y / application_height
        return fraction_x, fraction_y

    def transform_scaled_point_to_raw(
        self,
        scaled_x: int,
        scaled_y: int,
        scaled_width: int,
        scaled_height: int,
        raw_width: int,
        raw_height: int,
    ) -> Tuple[int, int]:
        ratio = min(scaled_width / raw_width, scaled_height / raw_height)
        raw_x = scaled_x / ratio
        raw_y = scaled_y / ratio
        return int(raw_x), int(raw_y)


@ReceiverManager.register
class UIControlReceiverFactory(ReceiverFactory):
    """Factory for creating ControlReceiver instances."""

    def create_receiver(self, control, application):
        return ControlReceiver(control, application)

    @classmethod
    def name(cls) -> str:
        return "UIControl"


# ---------------------------------------------------------------------------
# Command classes
# ---------------------------------------------------------------------------


class ControlCommand(CommandBasic):
    """Base class for control commands."""

    def __init__(self, receiver: ControlReceiver, params=None) -> None:
        self.receiver = receiver
        self.params = params if params is not None else {}

    @abstractmethod
    def execute(self):
        pass

    @classmethod
    def name(cls) -> str:
        return "control_command"


class AtomicCommand(ControlCommand):
    def __init__(
        self,
        receiver: ControlReceiver,
        method_name: str,
        params=Optional[Dict[str, str]],
    ) -> None:
        super().__init__(receiver, params)
        self.method_name = method_name

    def execute(self) -> str:
        return self.receiver.atomic_execution(self.method_name, self.params)

    @classmethod
    def name(cls) -> str:
        return "atomic_command"


@ControlReceiver.register
class ClickInputCommand(ControlCommand):
    def execute(self) -> str:
        return self.receiver.click_input(self.params)

    @classmethod
    def name(cls) -> str:
        return "click_input"


@ControlReceiver.register
class ClickOnCoordinatesCommand(ControlCommand):
    def execute(self) -> str:
        return self.receiver.click_on_coordinates(self.params)

    @classmethod
    def name(cls) -> str:
        return "click_on_coordinates"


@ControlReceiver.register
class DragOnCoordinatesCommand(ControlCommand):
    def execute(self) -> str:
        return self.receiver.drag_on_coordinates(self.params)

    @classmethod
    def name(cls) -> str:
        return "drag_on_coordinates"


@ControlReceiver.register
class SummaryCommand(ControlCommand):
    def execute(self) -> str:
        return self.receiver.summary(self.params)

    @classmethod
    def name(cls) -> str:
        return "summary"


@ControlReceiver.register
class SetEditTextCommand(ControlCommand):
    def execute(self) -> str:
        return self.receiver.set_edit_text(self.params)

    @classmethod
    def name(cls) -> str:
        return "set_edit_text"


@ControlReceiver.register
class GetTextsCommand(ControlCommand):
    def execute(self) -> str:
        return self.receiver.texts()

    @classmethod
    def name(cls) -> str:
        return "texts"


@ControlReceiver.register
class WheelMouseInputCommand(ControlCommand):
    def execute(self) -> str:
        return self.receiver.wheel_mouse_input(self.params)

    @classmethod
    def name(cls) -> str:
        return "wheel_mouse_input"


@ControlReceiver.register
class AnnotationCommand(ControlCommand):
    def __init__(
        self,
        receiver: ControlReceiver,
        params: Dict[str, str],
        annotation_dict: Dict[str, UIAWrapper],
    ) -> None:
        super().__init__(receiver, params)
        self.annotation_dict = annotation_dict

    def execute(self) -> str:
        return self.receiver.annotation(self.params, self.annotation_dict)

    @classmethod
    def name(cls) -> str:
        return "annotation"


@ControlReceiver.register
class KeyboardInputCommand(ControlCommand):
    def execute(self) -> str:
        return self.receiver.keyboard_input(self.params)

    @classmethod
    def name(cls) -> str:
        return "keyboard_input"


@ControlReceiver.register
class NoActionCommand(ControlCommand):
    def execute(self) -> str:
        return self.receiver.no_action()

    @classmethod
    def name(cls) -> str:
        return ""


@ControlReceiver.register
class ClickCommand(ControlCommand):
    def execute(self) -> str:
        x = int(self.params.get("x", 0))
        y = int(self.params.get("y", 0))

        if self.params.get("scaler", None) and self.receiver.application:
            scaled_width = self.params["scaler"][0]
            scaled_height = self.params["scaler"][1]
            raw_width = self.receiver.application.rectangle().width()
            raw_height = self.receiver.application.rectangle().height()
            x, y = self.receiver.transform_scaled_point_to_raw(
                x, y, scaled_width, scaled_height, raw_width, raw_height
            )

        button = self.params.get("button", "left")
        button = "middle" if button == "wheel" else button
        params = {"x": x, "y": y, "button": button}
        return self.receiver.click_on_coordinates(params)

    @classmethod
    def name(cls) -> str:
        return "click"


@ControlReceiver.register
class DoubleClickCommand(ControlCommand):
    def execute(self) -> str:
        x = int(self.params.get("x", 0))
        y = int(self.params.get("y", 0))

        if self.params.get("scaler", None) and self.receiver.application:
            scaled_width = self.params["scaler"][0]
            scaled_height = self.params["scaler"][1]
            raw_width = self.receiver.application.rectangle().width()
            raw_height = self.receiver.application.rectangle().height()
            x, y = self.receiver.transform_scaled_point_to_raw(
                x, y, scaled_width, scaled_height, raw_width, raw_height
            )

        button = self.params.get("button", "left")
        button = "middle" if button == "wheel" else button
        params = {"x": x, "y": y, "button": button, "double": True}
        return self.receiver.click_on_coordinates(params)

    @classmethod
    def name(cls) -> str:
        return "double_click"


@ControlReceiver.register
class DragCommand(ControlCommand):
    def execute(self) -> str:
        path = self.params.get("path", [])
        for i in range(len(path) - 1):
            start_x, start_y = path[i].get("x", 0), path[i].get("y", 0)
            end_x, end_y = path[i + 1].get("x", 0), path[i + 1].get("y", 0)

            if self.params.get("scaler", None) and self.receiver.application:
                scaled_width = self.params["scaler"][0]
                scaled_height = self.params["scaler"][1]
                raw_width = self.receiver.application.rectangle().width()
                raw_height = self.receiver.application.rectangle().height()
                start_x, start_y = self.receiver.transform_scaled_point_to_raw(
                    start_x, start_y, scaled_width, scaled_height, raw_width, raw_height
                )
                end_x, end_y = self.receiver.transform_scaled_point_to_raw(
                    end_x, end_y, scaled_width, scaled_height, raw_width, raw_height
                )

            params = {
                "start_x": start_x,
                "start_y": start_y,
                "end_x": end_x,
                "end_y": end_y,
            }
            self.receiver.drag_on_coordinates(params)

    @classmethod
    def name(cls) -> str:
        return "drag"


@ControlReceiver.register
class KeyPressCommand(ControlCommand):
    def execute(self) -> str:
        return self.receiver.key_press(self.params)

    @classmethod
    def name(cls) -> str:
        return "keypress"


@ControlReceiver.register
class MouseMoveCommand(ControlCommand):
    def execute(self) -> str:
        x = int(self.params.get("x", 0))
        y = int(self.params.get("y", 0))

        if self.params.get("scaler", None) and self.receiver.application:
            scaled_width = self.params["scaler"][0]
            scaled_height = self.params["scaler"][1]
            raw_width = self.receiver.application.rectangle().width()
            raw_height = self.receiver.application.rectangle().height()
            x, y = self.receiver.transform_scaled_point_to_raw(
                x, y, scaled_width, scaled_height, raw_width, raw_height
            )

        new_x, new_y = self.receiver.transform_absolute_point_to_fractional(x, y)
        params = {"x": new_x, "y": new_y}
        return self.receiver.mouse_move(params)

    @classmethod
    def name(cls) -> str:
        return "move"


@ControlReceiver.register
class ScrollCommand(ControlCommand):
    def execute(self) -> str:
        x = int(self.params.get("x", 0))
        y = int(self.params.get("y", 0))

        if self.params.get("scaler", None) and self.receiver.application:
            scaled_width = self.params["scaler"][0]
            scaled_height = self.params["scaler"][1]
            raw_width = self.receiver.application.rectangle().width()
            raw_height = self.receiver.application.rectangle().height()
            x, y = self.receiver.transform_scaled_point_to_raw(
                x, y, scaled_width, scaled_height, raw_width, raw_height
            )

        new_x, new_y = self.receiver.transform_absolute_point_to_fractional(x, y)
        scroll_x = int(self.params.get("scroll_x", 0))
        scroll_y = int(self.params.get("scroll_y", 0))
        params = {"x": new_x, "y": new_y, "scroll_x": scroll_x, "scroll_y": scroll_y}
        return self.receiver.scroll(params)

    @classmethod
    def name(cls) -> str:
        return "scroll"


@ControlReceiver.register
class TypeCommand(ControlCommand):
    def execute(self) -> str:
        return self.receiver.type(self.params)

    @classmethod
    def name(cls) -> str:
        return "type"


@ControlReceiver.register
class WaitCommand(ControlCommand):
    def execute(self) -> str:
        time.sleep(3)

    @classmethod
    def name(cls) -> str:
        return "wait"


# ---------------------------------------------------------------------------
# TextTransformer
# ---------------------------------------------------------------------------


class TextTransformer:
    """Escapes special pywinauto key sequences in text."""

    @staticmethod
    def transform_text(text: str, transform_tag: str) -> str:
        if transform_tag == "all":
            transform_tag = "+\n\t^%{VK_CONTROL}{VK_SHIFT}{VK_MENU}()"

        if "\n" in transform_tag:
            text = TextTransformer.transform_enter(text)
        if "\t" in transform_tag:
            text = TextTransformer.transform_tab(text)
        if "+" in transform_tag:
            text = TextTransformer.transform_plus(text)
        if "^" in transform_tag:
            text = TextTransformer.transform_caret(text)
        if "%" in transform_tag:
            text = TextTransformer.transform_percent(text)
        if "{VK_CONTROL}" in transform_tag:
            text = TextTransformer.transform_control(text)
        if "{VK_SHIFT}" in transform_tag:
            text = TextTransformer.transform_shift(text)
        if "{VK_MENU}" in transform_tag:
            text = TextTransformer.transform_alt(text)
        if "(" in transform_tag or ")" in transform_tag:
            text = TextTransformer.transform_brace(text)
        return text

    @staticmethod
    def transform_enter(text: str) -> str:
        return text.replace("\n", "{ENTER}")

    @staticmethod
    def transform_tab(text: str) -> str:
        return text.replace("\t", "{TAB}")

    @staticmethod
    def transform_plus(text: str) -> str:
        return text.replace("+", "{+}")

    @staticmethod
    def transform_caret(text: str) -> str:
        return text.replace("^", "{^}")

    @staticmethod
    def transform_brace(text: str) -> str:
        return text.replace("(", "{(}").replace(")", "{)}")

    @staticmethod
    def transform_percent(text: str) -> str:
        return text.replace("%", "{%}")

    @staticmethod
    def transform_control(text: str) -> str:
        return text.replace("{VK_CONTROL}", "^")

    @staticmethod
    def transform_shift(text: str) -> str:
        return text.replace("{VK_SHIFT}", "+")

    @staticmethod
    def transform_alt(text: str) -> str:
        return text.replace("{VK_MENU}", "%")
