# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Control inspector — facade for UI element discovery and information.

Copied from ufo/automator/ui_control/inspector.py with zero UFO dependencies.
"""

from __future__ import annotations

import functools
import platform
import time
from abc import ABC, abstractmethod
from typing import Callable, Dict, List, Optional, cast, TYPE_CHECKING, Any

import psutil

if TYPE_CHECKING or platform.system() == "Windows":
    import comtypes.gen.UIAutomationClient as UIAutomationClient_dll
    import pywinauto
    import pywinauto.uia_defines
    import uiautomation as auto
    from pywinauto import Desktop
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo
else:
    UIAutomationClient_dll = None
    pywinauto = None
    auto = None
    Desktop = None
    UIAWrapper = Any
    UIAElementInfo = Any


class BackendFactory:
    """A factory class to create backend strategies."""

    @staticmethod
    def create_backend(backend: str) -> BackendStrategy:
        if backend == "uia":
            return UIABackendStrategy()
        elif backend == "win32":
            return Win32BackendStrategy()
        else:
            raise ValueError(f"Backend {backend} not supported")


class BackendStrategy(ABC):
    """Define an interface for backend strategies."""

    @abstractmethod
    def get_desktop_windows(self, remove_empty: bool) -> List[UIAWrapper]:
        pass

    @abstractmethod
    def find_control_elements_in_descendants(
        self,
        window: UIAWrapper,
        control_type_list: Optional[List[str]] = None,
        class_name_list: Optional[List[str]] = None,
        title_list: Optional[List[str]] = None,
        is_visible: bool = True,
        is_enabled: bool = True,
        depth: int = 0,
    ) -> List[UIAWrapper]:
        pass


class UIAElementInfoFix(UIAElementInfo):
    _cached_rect = None
    _time_delay_marker = False

    def __init__(self, element, is_ref=False, source: Optional[str] = None):
        super().__init__(element, is_ref)
        self._source = source

    def sleep(self, ms: float = 0):
        if UIAElementInfoFix._time_delay_marker:
            ms = max(20, ms)
        else:
            ms = max(1, ms)
        time.sleep(ms / 1000.0)
        UIAElementInfoFix._time_delay_marker = False

    @staticmethod
    def _time_wrap(func):
        def dec(self, *args, **kvargs):
            before = time.time()
            result = func(self, *args, **kvargs)
            UIAElementInfoFix._time_delay_marker = (time.time() - before) > 0.005
            return result
        return dec

    @_time_wrap
    def _get_current_name(self):
        return super()._get_current_name()

    @_time_wrap
    def _get_current_rich_text(self):
        return super()._get_current_rich_text()

    @_time_wrap
    def _get_current_class_name(self):
        return super()._get_current_class_name()

    @_time_wrap
    def _get_current_control_type(self):
        return super()._get_current_control_type()

    @_time_wrap
    def _get_current_rectangle(self):
        bound_rect = self._element.CurrentBoundingRectangle
        rect = pywinauto.win32structures.RECT()
        rect.left = bound_rect.left
        rect.top = bound_rect.top
        rect.right = bound_rect.right
        rect.bottom = bound_rect.bottom
        return rect

    def _get_cached_rectangle(self):
        if self._cached_rect is None:
            self._cached_rect = self._get_current_rectangle()
        return self._cached_rect

    @property
    def rectangle(self):
        return self._get_cached_rectangle()

    @property
    def source(self):
        return self._source


class UIABackendStrategy(BackendStrategy):
    """The backend strategy for UIA."""

    def get_desktop_windows(self, remove_empty: bool) -> List[UIAWrapper]:
        desktop_windows = Desktop(backend="win32").windows()
        desktop_windows = [app for app in desktop_windows if app.is_visible()]

        if remove_empty:
            desktop_windows = [
                app
                for app in desktop_windows
                if app.window_text() != ""
                and app.element_info.class_name not in ["IME", "MSCTFIME UI"]
            ]

        uia_desktop_windows: List[UIAWrapper] = [
            UIAWrapper(UIAElementInfo(handle_or_elem=window.handle))
            for window in desktop_windows
        ]
        return uia_desktop_windows

    def find_control_elements_in_descendants(
        self,
        window: Optional[UIAWrapper],
        control_type_list: Optional[List[str]] = None,
        class_name_list: Optional[List[str]] = None,
        title_list: Optional[List[str]] = None,
        is_visible: bool = True,
        is_enabled: bool = True,
        depth: int = 0,
    ) -> List[UIAWrapper]:
        if control_type_list is None:
            control_type_list = []
        if class_name_list is None:
            class_name_list = []
        if title_list is None:
            title_list = []
        try:
            window.is_enabled()
        except Exception:
            return []

        assert (
            class_name_list is None or len(class_name_list) == 0
        ), "class_name_list is not supported for UIA backend"

        _, iuia_dll = UIABackendStrategy._get_uia_defs()
        window_elem_info = cast(UIAElementInfo, window.element_info)
        window_elem_com_ref = cast(
            UIAutomationClient_dll.IUIAutomationElement, window_elem_info._element
        )

        condition = UIABackendStrategy._get_control_filter_condition(
            control_type_list, is_visible, is_enabled,
        )
        cache_request = UIABackendStrategy._get_cache_request()

        com_elem_array = window_elem_com_ref.FindAllBuildCache(
            scope=iuia_dll.TreeScope_Descendants,
            condition=condition,
            cacheRequest=cache_request,
        )

        elem_info_list = [
            (
                elem,
                elem.CachedControlType,
                elem.CachedName,
                elem.CachedBoundingRectangle,
            )
            for elem in (
                com_elem_array.GetElement(n)
                for n in range(min(com_elem_array.Length, 500))
            )
        ]

        control_elements: List[UIAWrapper] = []

        for elem, elem_type, elem_name, elem_rect in elem_info_list:
            element_info = UIAElementInfoFix(elem, True, source="uia")
            elem_type_name = UIABackendStrategy._get_uia_control_name_map().get(
                elem_type, ""
            )

            element_info._cached_handle = 0
            element_info._cached_visible = True

            rect = pywinauto.win32structures.RECT()
            rect.left = elem_rect.left
            rect.top = elem_rect.top
            rect.right = elem_rect.right
            rect.bottom = elem_rect.bottom
            element_info._cached_rect = rect
            element_info._cached_name = elem_name
            element_info._cached_control_type = elem_type_name
            element_info._cached_rich_text = elem_name

            uia_interface = UIAWrapper(element_info)
            control_elements.append(uia_interface)

        return control_elements

    @staticmethod
    def _get_uia_control_id_map():
        iuia = pywinauto.uia_defines.IUIA()
        return iuia.known_control_types

    @staticmethod
    def _get_uia_control_name_map():
        iuia = pywinauto.uia_defines.IUIA()
        return iuia.known_control_type_ids

    @staticmethod
    @functools.lru_cache()
    def _get_cache_request():
        iuia_com, iuia_dll = UIABackendStrategy._get_uia_defs()
        cache_request = iuia_com.CreateCacheRequest()
        cache_request.AddProperty(iuia_dll.UIA_ControlTypePropertyId)
        cache_request.AddProperty(iuia_dll.UIA_NamePropertyId)
        cache_request.AddProperty(iuia_dll.UIA_BoundingRectanglePropertyId)
        return cache_request

    @staticmethod
    def _get_control_filter_condition(
        control_type_list: Optional[List[str]] = None,
        is_visible: bool = True,
        is_enabled: bool = True,
    ):
        if control_type_list is None:
            control_type_list = []
        iuia_com, iuia_dll = UIABackendStrategy._get_uia_defs()
        condition = iuia_com.CreateAndConditionFromArray(
            [
                iuia_com.CreatePropertyCondition(
                    iuia_dll.UIA_IsEnabledPropertyId, is_enabled
                ),
                iuia_com.CreatePropertyCondition(
                    iuia_dll.UIA_IsOffscreenPropertyId, not is_visible,
                ),
                iuia_com.CreatePropertyCondition(
                    iuia_dll.UIA_IsControlElementPropertyId, True
                ),
                iuia_com.CreateOrConditionFromArray(
                    [
                        iuia_com.CreatePropertyCondition(
                            iuia_dll.UIA_ControlTypePropertyId,
                            (
                                control_type
                                if control_type is int
                                else UIABackendStrategy._get_uia_control_id_map()[
                                    control_type
                                ]
                            ),
                        )
                        for control_type in control_type_list
                    ]
                ),
            ]
        )
        return condition

    @staticmethod
    def _get_uia_defs():
        iuia = pywinauto.uia_defines.IUIA()
        iuia_com: UIAutomationClient_dll.IUIAutomation = iuia.iuia
        iuia_dll: UIAutomationClient_dll = iuia.UIA_dll
        return iuia_com, iuia_dll


class Win32BackendStrategy(BackendStrategy):
    """The backend strategy for Win32."""

    def get_desktop_windows(self, remove_empty: bool) -> List[UIAWrapper]:
        desktop_windows = Desktop(backend="win32").windows()
        desktop_windows = [app for app in desktop_windows if app.is_visible()]

        if remove_empty:
            desktop_windows = [
                app
                for app in desktop_windows
                if app.window_text() != ""
                and app.element_info.class_name not in ["IME", "MSCTFIME UI"]
            ]
        return desktop_windows

    def find_control_elements_in_descendants(
        self,
        window: UIAWrapper,
        control_type_list: Optional[List[str]] = None,
        class_name_list: Optional[List[str]] = None,
        title_list: Optional[List[str]] = None,
        is_visible: bool = True,
        is_enabled: bool = True,
        depth: int = 0,
    ) -> List[UIAWrapper]:
        if window is None:
            return []
        if control_type_list is None:
            control_type_list = []
        if class_name_list is None:
            class_name_list = []
        if title_list is None:
            title_list = []

        control_elements = []
        if len(class_name_list) == 0:
            control_elements += window.descendants()
        else:
            for class_name in class_name_list:
                if depth == 0:
                    subcontrols = window.descendants(class_name=class_name)
                else:
                    subcontrols = window.descendants(class_name=class_name, depth=depth)
                control_elements += subcontrols

        if is_visible:
            control_elements = [
                control for control in control_elements if control.is_visible()
            ]
        if is_enabled:
            control_elements = [
                control for control in control_elements if control.is_enabled()
            ]
        if len(title_list) > 0:
            control_elements = [
                control
                for control in control_elements
                if control.window_text() in title_list
            ]
        if len(control_type_list) > 0:
            control_elements = [
                control
                for control in control_elements
                if control.element_info.control_type in control_type_list
            ]

        return [
            control for control in control_elements if control.element_info.name != ""
        ]


class ControlInspectorFacade:
    """Singleton facade for control inspection."""

    _instances = {}

    def __new__(cls, backend: str = "uia") -> "ControlInspectorFacade":
        if backend not in cls._instances:
            instance = super().__new__(cls)
            instance.backend = backend
            instance.backend_strategy = BackendFactory.create_backend(backend)
            cls._instances[backend] = instance
        return cls._instances[backend]

    def __init__(self, backend: str = "uia") -> None:
        self.backend = backend

    def get_desktop_windows(self, remove_empty: bool = True) -> List[UIAWrapper]:
        return self.backend_strategy.get_desktop_windows(remove_empty)

    def find_control_elements_in_descendants(
        self,
        window: UIAWrapper,
        control_type_list: Optional[List[str]] = None,
        class_name_list: Optional[List[str]] = None,
        title_list: Optional[List[str]] = None,
        is_visible: bool = True,
        is_enabled: bool = True,
        depth: int = 0,
    ) -> List[UIAWrapper]:
        if self.backend == "uia":
            return self.backend_strategy.find_control_elements_in_descendants(
                window, control_type_list, [], title_list, is_visible, is_enabled, depth
            )
        elif self.backend == "win32":
            return self.backend_strategy.find_control_elements_in_descendants(
                window, [], class_name_list, title_list, is_visible, is_enabled, depth
            )
        else:
            return []

    def get_desktop_app_dict(self, remove_empty: bool = True) -> Dict[str, UIAWrapper]:
        desktop_windows = self.get_desktop_windows(remove_empty)
        desktop_windows_with_gui = []
        for window in desktop_windows:
            try:
                window.is_normal()
                desktop_windows_with_gui.append(window)
            except Exception:
                pass

        desktop_windows_dict = dict(
            zip(
                [str(i + 1) for i in range(len(desktop_windows_with_gui))],
                desktop_windows_with_gui,
            )
        )
        return desktop_windows_dict

    def get_desktop_app_info(
        self,
        desktop_windows_dict: Dict[str, UIAWrapper],
        field_list: List[str] = ["control_text", "control_type"],
    ) -> List[Dict[str, str]]:
        desktop_windows_info = self.get_control_info_list_of_dict(
            desktop_windows_dict, field_list
        )
        return desktop_windows_info

    def get_control_info_batch(
        self, window_list: List[UIAWrapper], field_list: Optional[List[str]] = None
    ) -> List[Dict[str, str]]:
        if field_list is None:
            field_list = []
        control_info_list = []
        for window in window_list:
            control_info_list.append(self.get_control_info(window, field_list))
        return control_info_list

    def get_control_info_list_of_dict(
        self, window_dict: Dict[str, UIAWrapper], field_list: Optional[List[str]] = None
    ) -> List[Dict[str, str]]:
        control_info_list = []
        for key in window_dict.keys():
            window = window_dict[key]
            control_info = self.get_control_info(window, field_list)
            control_info["label"] = key
            control_info_list.append(control_info)
        return control_info_list

    @staticmethod
    def get_check_state(control_item: auto.Control) -> bool | None:
        is_checked = None
        try:
            assert isinstance(control_item, auto.Control)
            is_checked = (
                control_item.GetLegacyIAccessiblePattern().State
                & auto.AccessibleState.Checked
                == auto.AccessibleState.Checked
            )
            if is_checked:
                return is_checked
            is_selected = (
                control_item.GetLegacyIAccessiblePattern().State
                & auto.AccessibleState.Selected
                == auto.AccessibleState.Selected
            )
            if is_selected:
                return is_selected
            return None
        except Exception:
            return None

    @staticmethod
    def get_control_info(
        window: UIAWrapper, field_list: Optional[List[str]] = None
    ) -> Dict[str, str]:
        if field_list is None:
            field_list = []
        control_info: Dict[str, str] = {}

        def assign(prop_name: str, prop_value_func: Callable[[], str]) -> None:
            if len(field_list) > 0 and prop_name not in field_list:
                return
            control_info[prop_name] = prop_value_func()

        try:
            assign("control_type", lambda: window.element_info.control_type)
            assign("control_id", lambda: window.element_info.control_id)
            assign("control_class", lambda: window.element_info.class_name)
            assign("control_name", lambda: window.element_info.name)
            rectangle = window.element_info.rectangle
            assign(
                "control_rect",
                lambda: (
                    rectangle.left,
                    rectangle.top,
                    rectangle.right,
                    rectangle.bottom,
                ),
            )
            assign("control_text", lambda: window.element_info.name)
            assign("control_title", lambda: window.window_text())
            assign("selected", lambda: ControlInspectorFacade.get_check_state(window))

            try:
                source = window.element_info.source
                assign("source", lambda: source)
            except Exception:
                assign("source", lambda: "")

            return control_info
        except Exception:
            return {}

    @staticmethod
    def get_application_root_name(window: UIAWrapper) -> str:
        if window is None:
            return ""
        process_id = window.process_id()
        try:
            process = psutil.Process(process_id)
            return process.name()
        except psutil.NoSuchProcess:
            return ""

    @property
    def desktop(self) -> UIAWrapper:
        desktop_element = UIAElementInfo()
        return UIAWrapper(desktop_element)
