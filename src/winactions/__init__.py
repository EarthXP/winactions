"""winactions — Windows UI Automation Toolkit for AI Agents.

Core exports for library usage. Importing this module triggers
registration of ControlReceiver commands.
"""

from winactions._version import __version__
from winactions.config import ActionConfig, configure, get_action_config
from winactions.targets import TargetKind, TargetInfo, TargetRegistry
from winactions.models import (
    Result,
    ResultStatus,
    ActionCommandInfo,
    ListActionCommandInfo,
    BaseControlLog,
)
from winactions.command.basic import CommandBasic, ReceiverBasic, ReceiverFactory
from winactions.command.puppeteer import AppPuppeteer, ReceiverManager
from winactions.command.executor import ActionExecutor

# Import control module to trigger @ControlReceiver.register decorators
import winactions.control.controller  # noqa: F401

from winactions.control.controller import ControlReceiver, TextTransformer
from winactions.control.inspector import ControlInspectorFacade

__all__ = [
    "__version__",
    # Config
    "ActionConfig",
    "configure",
    "get_action_config",
    # Targets (protocol layer)
    "TargetKind",
    "TargetInfo",
    "TargetRegistry",
    # Models
    "Result",
    "ResultStatus",
    "ActionCommandInfo",
    "ListActionCommandInfo",
    "BaseControlLog",
    # Command layer
    "CommandBasic",
    "ReceiverBasic",
    "ReceiverFactory",
    "AppPuppeteer",
    "ReceiverManager",
    "ActionExecutor",
    # Control layer
    "ControlReceiver",
    "TextTransformer",
    "ControlInspectorFacade",
]
