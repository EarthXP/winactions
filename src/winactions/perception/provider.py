"""State providers — pluggable backends for UI element detection.

The StateProvider protocol defines the contract: given a window, return
a list of TargetInfo objects with assigned index numbers.
"""

from __future__ import annotations

import platform
from typing import Any, List, Protocol, TYPE_CHECKING

from winactions.targets import TargetInfo, TargetKind

if TYPE_CHECKING or platform.system() == "Windows":
    from pywinauto.controls.uiawrapper import UIAWrapper
else:
    UIAWrapper = Any

if TYPE_CHECKING:
    from winactions.control.inspector import ControlInspectorFacade


class StateProvider(Protocol):
    """Pluggable perception source — detects controls and returns indexed TargetInfo list."""

    def detect(self, window: UIAWrapper) -> List[TargetInfo]:
        ...


# Default control types to scan for (matches UFO's common set)
DEFAULT_CONTROL_TYPES = [
    "Button",
    "Edit",
    "TabItem",
    "Document",
    "ListItem",
    "MenuItem",
    "ScrollBar",
    "TreeItem",
    "Hyperlink",
    "ComboBox",
    "RadioButton",
    "CheckBox",
    "Slider",
    "Spinner",
    "DataItem",
    "Custom",
    "Group",
    "HeaderItem",
    "Header",
    "SplitButton",
    "MenuBar",
    "ToolBar",
    "Text",
    "Pane",
    "Window",
    "Table",
    "TitleBar",
    "Image",
    "List",
    "DataGrid",
    "Tree",
    "Tab",
]


class UIAStateProvider:
    """Default perception source using pywinauto UIA backend."""

    def __init__(
        self,
        inspector: "ControlInspectorFacade",
        control_type_list: List[str] | None = None,
    ):
        self.inspector = inspector
        self.control_type_list = control_type_list or DEFAULT_CONTROL_TYPES

    def detect(self, window: UIAWrapper) -> tuple[List[TargetInfo], List[UIAWrapper]]:
        """Scan the window control tree, return (targets, controls) with 1-indexed IDs.

        Returns both TargetInfo list and raw UIAWrapper list so callers can
        build the control_map needed for command execution.
        """
        controls = self.inspector.find_control_elements_in_descendants(
            window, control_type_list=self.control_type_list
        )
        targets = []
        for i, control in enumerate(controls):
            info = self.inspector.get_control_info(control)
            targets.append(
                TargetInfo(
                    kind=TargetKind.CONTROL,
                    id=str(i + 1),  # 1-indexed
                    name=info.get("control_text", ""),
                    type=info.get("control_type", ""),
                    rect=(
                        list(info["control_rect"])
                        if info.get("control_rect")
                        else None
                    ),
                )
            )
        return targets, controls


class CompositeStateProvider:
    """Fuses multiple perception sources using IOU-based deduplication.

    Returns (targets, controls) tuple where controls[i] corresponds to
    targets[i].  UIA targets carry UIAWrapper handles; vision-only targets
    carry None, signalling the execution layer to fall back to coordinate
    based clicking.
    """

    def __init__(
        self,
        primary: StateProvider,
        *additional: StateProvider,
        iou_threshold: float = 0.1,
    ):
        self.primary = primary
        self.additional = additional
        self.iou_threshold = iou_threshold

    def detect(
        self, window: UIAWrapper,
    ) -> tuple[List[TargetInfo], List]:
        primary_targets, primary_controls = self.primary.detect(window)
        merged_targets = list(primary_targets)
        merged_controls = list(primary_controls)

        for provider in self.additional:
            extra_targets, extra_controls = provider.detect(window)
            merged_targets, merged_controls = _merge_by_iou_with_controls(
                merged_targets,
                merged_controls,
                extra_targets,
                extra_controls,
                self.iou_threshold,
            )

        # Re-assign sequential IDs after merge
        for i, t in enumerate(merged_targets):
            t.id = str(i + 1)
        return merged_targets, merged_controls


def merge_by_iou(
    main: List[TargetInfo],
    additional: List[TargetInfo],
    threshold: float,
) -> List[TargetInfo]:
    """IOU-based deduplication merge (from UFO's merge_target_info_list algorithm)."""
    merged = main.copy()
    for extra in additional:
        is_overlapping = False
        if extra.rect:
            for m in main:
                if m.rect and _iou(m.rect, extra.rect) > threshold:
                    is_overlapping = True
                    break
        if not is_overlapping:
            merged.append(extra)
    return merged


def _merge_by_iou_with_controls(
    main_targets: List[TargetInfo],
    main_controls: List,
    extra_targets: List[TargetInfo],
    extra_controls: List,
    threshold: float,
) -> tuple[List[TargetInfo], List]:
    """IOU-based deduplication merge that preserves the parallel controls list.

    Same algorithm as merge_by_iou, but maintains a controls list in lockstep
    with the targets list so that CompositeStateProvider can return both.
    """
    merged_targets = list(main_targets)
    merged_controls = list(main_controls)

    for extra_t, extra_c in zip(extra_targets, extra_controls):
        is_overlapping = False
        if extra_t.rect:
            for m in main_targets:
                if m.rect and _iou(m.rect, extra_t.rect) > threshold:
                    is_overlapping = True
                    break
        if not is_overlapping:
            merged_targets.append(extra_t)
            merged_controls.append(extra_c)

    return merged_targets, merged_controls


def _iou(rect1: List[int], rect2: List[int]) -> float:
    """Compute Intersection over Union for two [left, top, right, bottom] rects."""
    left = max(rect1[0], rect2[0])
    top = max(rect1[1], rect2[1])
    right = min(rect1[2], rect2[2])
    bottom = min(rect1[3], rect2[3])

    intersection = max(0, right - left) * max(0, bottom - top)
    area1 = (rect1[2] - rect1[0]) * (rect1[3] - rect1[1])
    area2 = (rect2[2] - rect2[0]) * (rect2[3] - rect2[1])
    union = area1 + area2 - intersection

    return intersection / union if union > 0 else 0.0
