"""UIState — atomic snapshot of the UI at a point in time.

This is the complete output of the perception layer: an indexed list of
controls with metadata, plus a control_map for resolving indexes to
actual UIAWrapper objects for command execution.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from winactions.targets import TargetInfo

if TYPE_CHECKING or platform.system() == "Windows":
    from pywinauto.controls.uiawrapper import UIAWrapper
else:
    UIAWrapper = Any


@dataclass
class UIState:
    """Atomic UI state snapshot — the perception layer's complete output."""

    window_title: str
    window_handle: int
    process_name: str
    targets: List[TargetInfo]
    control_map: Dict[str, Any] = field(default_factory=dict, repr=False)
    screenshot_path: Optional[str] = None
    annotated_screenshot_path: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_text(self, verbose: bool = False) -> str:
        """Text format output (CLI default, human-readable).

        Default (compact): ``[id] [type] "name"`` — no rect.
        Verbose: adds rect for all targets.
        Vision-only elements (no UIAWrapper control) always include rect
        so the agent can use coordinate-based commands directly.
        """
        lines = [f'Window: "{self.window_title}" ({self.process_name})']
        for t in self.targets:
            ctrl = self.control_map.get(t.id)
            if verbose and t.rect:
                lines.append(f'[{t.id}] [{t.type}] "{t.name}" rect={t.rect}')
            elif ctrl is None and t.rect:
                # Vision-only: always show rect (no UIAWrapper to inspect later)
                lines.append(f'[{t.id}] [{t.type}] "{t.name}" rect={t.rect}')
            else:
                lines.append(f'[{t.id}] [{t.type}] "{t.name}"')
        return "\n".join(lines)

    def to_json(self, verbose: bool = False) -> dict:
        """JSON format output (for Agent consumption).

        Default (compact): id, name, type only — no rect.
        Verbose: includes rect for all targets.
        Vision-only elements always include rect.
        """
        targets_out = []
        for t in self.targets:
            ctrl = self.control_map.get(t.id)
            if verbose or (ctrl is None and t.rect):
                targets_out.append(
                    t.model_dump(include={"id", "name", "type", "rect"})
                )
            else:
                targets_out.append(
                    t.model_dump(include={"id", "name", "type"})
                )
        return {
            "window": self.window_title,
            "handle": self.window_handle,
            "process": self.process_name,
            "targets": targets_out,
            "screenshot": self.screenshot_path,
            "annotated_screenshot": self.annotated_screenshot_path,
            "timestamp": self.timestamp,
        }

    def resolve(self, target_id: str) -> Optional[Any]:
        """Resolve an index number to the actual UIAWrapper control."""
        return self.control_map.get(target_id)

    @property
    def target_count(self) -> int:
        return len(self.targets)
