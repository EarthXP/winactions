"""Action models — Result, ActionCommandInfo, and related data structures.

Inlines ResultStatus and Result from aip/messages.py to avoid external dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from winactions.targets import TargetInfo


# --- Inlined from aip/messages.py ---


class ResultStatus(str, Enum):
    """Represents the status of a command execution result."""

    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"
    NONE = "none"


class Result(BaseModel):
    """Represents the result of a command execution."""

    status: ResultStatus = Field(..., description="Execution status")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    result: Any = Field(default=None, description="Result payload")


# --- Adapted from ufo/agents/processors/schemas/actions.py ---


@dataclass
class BaseControlLog:
    """The control log data."""

    control_name: str = ""
    control_class: str = ""
    control_type: str = ""
    control_automation_id: str = ""
    control_friendly_class_name: str = ""
    control_matched: bool = True
    control_coordinates: Dict[str, int] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return self == BaseControlLog()


@dataclass
class ActionExecutionLog:
    """The action execution log data."""

    status: str = ""
    error: str = ""
    traceback: str = ""
    return_value: Any = None


class ActionCommandInfo(BaseModel):
    """Action information — what function to call, on which target, with what arguments."""

    function: str = ""
    status: str = ""
    arguments: Dict[str, Any] = Field(default_factory=dict)
    target: Optional[TargetInfo] = None
    result: Result = Field(default_factory=lambda: Result(status="none"))
    action_string: str = ""
    action_representation: str = ""

    def model_post_init(self, __context: Any) -> None:
        self.action_string = ActionCommandInfo.to_string(
            self.function, self.arguments
        )

    @staticmethod
    def to_string(command_name: str, params: Dict[str, Any]) -> str:
        """Generate a function call string."""
        args_str = ", ".join(f"{k}={v!r}" for k, v in params.items())
        return f"{command_name}({args_str})"

    def to_representation(self) -> str:
        """Generate a human-readable action representation."""
        components = []
        components.append(f"[Action] {self.action_string}")
        if self.target:
            target_info = ", ".join(
                f"{k}={v}"
                for k, v in self.target.model_dump(exclude_none=True).items()
                if k not in {"rect"}
            )
            components.append(f"[Target] {target_info}")

        if self.result:
            components.append(f"[Status] {self.result.status}")
            if self.result.error:
                components.append(f"[Error] {self.result.error}")
            components.append(f"[Result] {self.result.result}")

        return "\n".join(components)


class ListActionCommandInfo:
    """A sequence of one-step actions."""

    def __init__(self, actions: Optional[List[ActionCommandInfo]] = None):
        if actions is None:
            actions = []
        self._actions = actions

    @property
    def actions(self) -> List[ActionCommandInfo]:
        return self._actions

    @property
    def length(self) -> int:
        return len(self._actions)

    @property
    def status(self) -> str:
        if not self.actions:
            status = "FINISH"
        else:
            status = "CONTINUE"
            for action in self.actions:
                if action.result.status == ResultStatus.SUCCESS:
                    status = action.status
        return status

    def add_action(self, action: ActionCommandInfo) -> None:
        self._actions.append(action)

    def to_list_of_dicts(
        self,
        success_only: bool = False,
        keep_keys: Optional[List[str]] = None,
        previous_actions: Optional[List[ActionCommandInfo | Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        action_list = []
        for action in self.actions:
            if success_only and action.result.status != ResultStatus.SUCCESS:
                continue
            action_dict = action.model_dump()
            if keep_keys:
                action_dict = {k: v for k, v in action_dict.items() if k in keep_keys}
            if previous_actions:
                repeat_time = self.count_repeat_times(action, previous_actions)
                action_dict["repeat_time"] = repeat_time
            action_list.append(action_dict)
        return action_list

    def to_string(
        self,
        success_only: bool = False,
        previous_actions: Optional[List[ActionCommandInfo]] = None,
    ) -> str:
        return json.dumps(
            self.to_list_of_dicts(success_only, previous_actions), ensure_ascii=False
        )

    def to_representation(self, success_only: bool = False) -> List[str]:
        representations = []
        for action in self.actions:
            if success_only and action.result.status != ResultStatus.SUCCESS:
                continue
            representations.append(action.to_representation())
        return representations

    def color_print(self, success_only: bool = False) -> None:
        """Pretty-print using rich if available, fallback to plain text."""
        try:
            from rich.console import Console
            from rich.panel import Panel

            console = Console()
            for action in self.actions:
                if success_only and action.result.status != ResultStatus.SUCCESS:
                    continue
                console.print(Panel(action.to_representation(), title="Action"))
        except ImportError:
            for action in self.actions:
                if success_only and action.result.status != ResultStatus.SUCCESS:
                    continue
                print(action.to_representation())
                print("---")

    @staticmethod
    def is_same_action(
        action1: ActionCommandInfo | Dict[str, Any],
        action2: ActionCommandInfo | Dict[str, Any],
    ) -> bool:
        if isinstance(action1, ActionCommandInfo):
            action_dict_1 = action1.model_dump()
        else:
            action_dict_1 = action1

        if isinstance(action2, ActionCommandInfo):
            action_dict_2 = action2.model_dump()
        else:
            action_dict_2 = action2

        return action_dict_1.get("function") == action_dict_2.get(
            "function"
        ) and action_dict_1.get("arguments") == action_dict_2.get("arguments")

    def count_repeat_times(
        self,
        target_action: ActionCommandInfo,
        previous_actions: List[ActionCommandInfo | Dict[str, Any]],
    ) -> int:
        count = 0
        for action in previous_actions[::-1]:
            if self.is_same_action(action, target_action):
                count += 1
            else:
                break
        return count

    def get_results(self, success_only: bool = False) -> List[Dict[str, Any]]:
        return [
            action.result.model_dump()
            for action in self.actions
            if not success_only or action.result.status == ResultStatus.SUCCESS
        ]

    def get_target_info(self, success_only: bool = False) -> List[Dict[str, Any]]:
        target_info = []
        for action in self.actions:
            if not success_only or action.result.status == ResultStatus.SUCCESS:
                if action.target:
                    target_info.append(action.target.model_dump())
                else:
                    target_info.append({})
        return target_info

    def get_target_objects(self, success_only: bool = False) -> List[TargetInfo]:
        target_objects = []
        for action in self.actions:
            if not success_only or action.result.status == ResultStatus.SUCCESS:
                if action.target:
                    target_objects.append(action.target)
        return target_objects

    def get_function_calls(self, is_success_only: bool = False) -> List[str]:
        return [
            action.action_string
            for action in self.actions
            if not is_success_only or action.result.status == ResultStatus.SUCCESS
        ]
