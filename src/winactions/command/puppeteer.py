# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""AppPuppeteer and ReceiverManager — command orchestration layer.

Adapted from ufo/automator/puppeteer.py with COM receiver support removed.
"""

from __future__ import annotations

import platform
from collections import deque
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional, Type, Union

if TYPE_CHECKING or platform.system() == "Windows":
    from pywinauto.controls.uiawrapper import UIAWrapper
else:
    UIAWrapper = Any

from winactions.command.basic import CommandBasic, ReceiverBasic, ReceiverFactory

if TYPE_CHECKING:
    from winactions.control.controller import ControlReceiver


class AppPuppeteer:
    """The app puppeteer to automate Windows applications."""

    def __init__(self, process_name: str, app_root_name: str) -> None:
        self._process_name = process_name
        self._app_root_name = app_root_name
        self.command_queue: Deque[CommandBasic] = deque()
        self.receiver_manager = ReceiverManager()

    def create_command(
        self, command_name: str, params: Dict[str, Any], *args, **kwargs
    ) -> Optional[CommandBasic]:
        receiver = self.receiver_manager.get_receiver_from_command_name(command_name)
        command = receiver.command_registry.get(command_name.lower(), None)

        if receiver is None:
            raise ValueError(f"Receiver for command {command_name} is not found.")
        if command is None:
            raise ValueError(f"Command {command_name} is not supported.")

        return command(receiver, params, *args, **kwargs)

    def get_command_types(self, command_name: str) -> str:
        try:
            receiver = self.receiver_manager.get_receiver_from_command_name(
                command_name
            )
            return receiver.type_name
        except Exception:
            return ""

    def execute_command(
        self, command_name: str, params: Dict[str, Any], *args, **kwargs
    ) -> str:
        command = self.create_command(command_name, params, *args, **kwargs)
        return command.execute()

    def execute_all_commands(self) -> List[Any]:
        results = []
        while self.command_queue:
            command = self.command_queue.popleft()
            results.append(command.execute())
        return results

    def add_command(
        self, command_name: str, params: Dict[str, Any], *args, **kwargs
    ) -> None:
        command = self.create_command(command_name, params, *args, **kwargs)
        self.command_queue.append(command)

    def list_commands(self) -> set:
        receiver_list = self.receiver_manager.receiver_list
        command_list = []
        for receiver in receiver_list:
            command_list.extend(receiver.list_commands())
        return set(command_list)

    def get_command_queue_length(self) -> int:
        return len(self.command_queue)

    @staticmethod
    def get_command_string(command_name: str, params: Dict[str, str]) -> str:
        args_str = ", ".join(f"{k}={v!r}" for k, v in params.items())
        return f"{command_name}({args_str})"


class ReceiverManager:
    """Manages receivers and maps command names to the right receiver."""

    _receiver_factory_registry: Dict[str, Dict[str, Union[str, ReceiverFactory]]] = {}

    def __init__(self):
        self.receiver_registry = {}
        self.ui_control_receiver: Optional[ControlReceiver] = None
        self._receiver_list: List[ReceiverBasic] = []

    def create_ui_control_receiver(
        self, control: UIAWrapper, application: UIAWrapper
    ) -> "ControlReceiver":
        if not application:
            return None

        factory: ReceiverFactory = self.receiver_factory_registry.get("UIControl").get(
            "factory"
        )
        self.ui_control_receiver = factory.create_receiver(control, application)
        # Replace previous UI control receiver instead of accumulating them
        self._receiver_list = [
            r for r in self._receiver_list
            if not isinstance(r, type(self.ui_control_receiver))
        ]
        self._receiver_list.append(self.ui_control_receiver)
        self._update_receiver_registry()
        return self.ui_control_receiver

    def _update_receiver_registry(self) -> None:
        for receiver in self.receiver_list:
            if receiver is not None:
                self.receiver_registry.update(receiver.self_command_mapping())

    def get_receiver_from_command_name(self, command_name: str) -> ReceiverBasic:
        receiver = self.receiver_registry.get(command_name, None)
        if receiver is None:
            raise ValueError(f"Receiver for command {command_name} is not found.")
        return receiver

    @property
    def receiver_list(self) -> List[ReceiverBasic]:
        return self._receiver_list

    @property
    def receiver_factory_registry(
        self,
    ) -> Dict[str, Dict[str, Union[str, ReceiverFactory]]]:
        return self._receiver_factory_registry

    @classmethod
    def register(
        cls, receiver_factory_class: Type[ReceiverFactory]
    ) -> Type[ReceiverFactory]:
        """Decorator to register a receiver factory class."""
        cls._receiver_factory_registry[receiver_factory_class.name()] = {
            "factory": receiver_factory_class(),
            "is_api": receiver_factory_class.is_api(),
        }
        return receiver_factory_class
