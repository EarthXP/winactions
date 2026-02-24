"""CLI smoke tests — verify commands are registered and help works."""

from click.testing import CliRunner
from winactions.cli.app import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "winctl" in result.output or "Windows UI Automation" in result.output


def test_cli_windows_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["windows", "--help"])
    assert result.exit_code == 0


def test_cli_state_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["state", "--help"])
    assert result.exit_code == 0
    assert "--screenshot" in result.output


def test_cli_click_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["click", "--help"])
    assert result.exit_code == 0


def test_cli_input_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["input", "--help"])
    assert result.exit_code == 0


def test_cli_keys_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["keys", "--help"])
    assert result.exit_code == 0


def test_cli_focus_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["focus", "--help"])
    assert result.exit_code == 0


def test_cli_launch_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["launch", "--help"])
    assert result.exit_code == 0


def test_cli_wait():
    """wait command should succeed immediately with 0 seconds."""
    runner = CliRunner()
    result = runner.invoke(cli, ["wait", "0"])
    assert result.exit_code == 0
    assert "Waited" in result.output


def test_cli_get_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["get", "--help"])
    assert result.exit_code == 0


def test_cli_drag_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["drag", "--help"])
    assert result.exit_code == 0
    assert "--button" in result.output
    assert "--duration" in result.output


def test_cli_get_value_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["get", "value", "--help"])
    assert result.exit_code == 0


def test_cli_state_tree_option():
    runner = CliRunner()
    result = runner.invoke(cli, ["state", "--help"])
    assert "--tree" in result.output


def test_cli_wait_visible_option():
    runner = CliRunner()
    result = runner.invoke(cli, ["wait", "--help"])
    assert "--visible" in result.output
    assert "--enabled" in result.output
    assert "--timeout" in result.output


def test_cli_return_state_option():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert "--return-state" in result.output
