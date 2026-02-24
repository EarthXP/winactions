"""Tests for config module."""

from winactions.config import ActionConfig, configure, get_action_config


def test_default_config():
    cfg = ActionConfig()
    assert cfg.click_api == "click_input"
    assert cfg.input_text_api == "type_keys"
    assert cfg.input_text_enter is False
    assert cfg.input_text_inter_key_pause == 0.05
    assert cfg.pyautogui_failsafe is False


def test_configure():
    cfg = configure(click_api="click", input_text_enter=True)
    assert cfg.click_api == "click"
    assert cfg.input_text_enter is True

    # get_action_config should return the same instance
    assert get_action_config() is cfg

    # Reset to default for other tests
    configure()


def test_get_action_config_default():
    """get_action_config creates a default if none exists."""
    import winactions.config as cfg_module

    cfg_module._config = None
    cfg = get_action_config()
    assert isinstance(cfg, ActionConfig)
    assert cfg.click_api == "click_input"
