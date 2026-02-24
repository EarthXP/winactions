from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ActionConfig:
    """Configuration for UI automation actions."""

    click_api: str = "click_input"
    after_click_wait: float = 0.0
    input_text_api: str = "type_keys"
    input_text_enter: bool = False
    input_text_inter_key_pause: float = 0.05
    pyautogui_failsafe: bool = False


# Module-level late-binding singleton
_config: Optional[ActionConfig] = None


def configure(**kwargs) -> ActionConfig:
    """Create and set the global ActionConfig.

    :param kwargs: Fields to override on ActionConfig.
    :return: The configured ActionConfig instance.
    """
    global _config
    _config = ActionConfig(**kwargs)
    return _config


def get_action_config() -> ActionConfig:
    """Return the current config, creating a default if needed."""
    global _config
    if _config is None:
        _config = ActionConfig()
    return _config
