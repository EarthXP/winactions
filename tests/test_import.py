"""Basic import tests — verify the package structure is correct."""


def test_version():
    from winactions._version import __version__
    assert __version__ == "0.1.0"


def test_top_level_imports():
    from winactions import (
        ActionConfig,
        ControlReceiver,
        AppPuppeteer,
        ActionCommandInfo,
        TargetInfo,
        TargetKind,
        TargetRegistry,
        Result,
        ResultStatus,
        ActionExecutor,
        ReceiverManager,
        ControlInspectorFacade,
        TextTransformer,
        ListActionCommandInfo,
        BaseControlLog,
    )
    assert ActionConfig is not None
    assert ControlReceiver is not None
    assert AppPuppeteer is not None
    assert ActionCommandInfo is not None


def test_command_registry_populated():
    """ControlReceiver should have commands registered after import."""
    from winactions import ControlReceiver

    registry = ControlReceiver._command_registry
    assert len(registry) > 0
    assert "click_input" in registry
    assert "set_edit_text" in registry
    assert "keyboard_input" in registry
