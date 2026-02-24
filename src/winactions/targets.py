"""Target information and registry — the shared communication protocol.

Index numbers (TargetInfo.id) are the shared protocol between perception,
decision, and execution layers.
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel


class TargetKind(str, Enum):
    """Enumeration for different types of targets."""

    WINDOW = "window"
    CONTROL = "control"
    THIRD_PARTY_AGENT = "third_party_agent"


class TargetInfo(BaseModel):
    """Information about a UI target element."""

    kind: TargetKind
    name: str
    id: Optional[str] = None
    type: Optional[str] = None
    rect: Optional[List[int]] = None  # [left, top, right, bottom]


class TargetRegistry:
    """Registry for managing target information."""

    def __init__(self) -> None:
        self._targets: Dict[str, TargetInfo] = {}
        self._counter = 0
        self.logger = logging.getLogger(self.__class__.__name__)

    def register(self, target: Union[TargetInfo, List[TargetInfo]]) -> List[TargetInfo]:
        """Register a target or a list of targets."""
        if not isinstance(target, list):
            target = [target]

        registered = []
        for t in target:
            if not t.id:
                self._counter += 1
                t.id = str(self._counter)

            if t.id in self._targets:
                self.logger.warning(
                    f"Target with ID {t.id} is already registered, ignoring.",
                )
            else:
                self._targets[t.id] = t
                registered.append(t)

        return registered

    def register_from_dict(self, target_dict: Dict[str, Any]) -> TargetInfo:
        """Register a target from a dictionary."""
        target = TargetInfo(
            kind=TargetKind(target_dict["kind"]),
            name=target_dict["name"],
            id=target_dict.get("id"),
            type=target_dict.get("type"),
            rect=target_dict.get("rect"),
        )
        return self.register(target)

    def register_from_dicts(
        self, target_dicts: List[Dict[str, Any]]
    ) -> List[TargetInfo]:
        """Register targets from a list of dictionaries."""
        return [self.register_from_dict(d) for d in target_dicts]

    def get(self, target_id: str) -> Optional[TargetInfo]:
        """Get a target by its ID."""
        return self._targets.get(target_id)

    def find_by_name(self, name: str) -> List[TargetInfo]:
        """Find targets by their name."""
        return [t for t in self._targets.values() if t.name == name]

    def find_by_id(self, target_id: str) -> Optional[TargetInfo]:
        """Find a target by its ID."""
        return self._targets.get(target_id)

    def find_by_kind(self, kind: TargetKind) -> List[TargetInfo]:
        """Find targets by their kind."""
        return [t for t in self._targets.values() if t.kind == kind]

    def all_targets(self) -> List[TargetInfo]:
        """Get all registered targets."""
        return list(self._targets.values())

    def unregister(self, target_id: str) -> bool:
        """Unregister a target by its ID."""
        if target_id in self._targets:
            del self._targets[target_id]
            return True
        return False

    def to_list(self, keep_keys: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Convert the registered targets to a list of dictionaries."""
        if keep_keys:
            return [
                {k: v for k, v in t.model_dump().items() if k in keep_keys}
                for t in self._targets.values()
            ]
        else:
            return [t.model_dump() for t in self._targets.values()]

    def clear(self) -> None:
        """Clear all registered targets."""
        self._targets.clear()
        self._counter = 0
