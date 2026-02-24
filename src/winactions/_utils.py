"""Utility functions extracted from UFO."""

from __future__ import annotations

import json
import platform
from typing import Any, Tuple, TYPE_CHECKING

if TYPE_CHECKING or platform.system() == "Windows":
    from pywinauto.win32structures import RECT
else:
    RECT = Any


def is_json_serializable(obj: Any) -> bool:
    """Check if the object is JSON serializable.

    :param obj: The object to check.
    :return: True if the object is JSON serializable, False otherwise.
    """
    try:
        json.dumps(obj)
        return True
    except TypeError:
        return False


def coordinate_adjusted(window_rect: RECT, control_rect: RECT) -> Tuple:
    """Adjust control rectangle coordinates relative to the window rectangle.

    :param window_rect: The window rectangle.
    :param control_rect: The control rectangle.
    :return: The adjusted control rectangle (left, top, right, bottom).
    """
    adjusted_rect = (
        control_rect.left - window_rect.left,
        control_rect.top - window_rect.top,
        control_rect.right - window_rect.left,
        control_rect.bottom - window_rect.top,
    )
    return adjusted_rect
