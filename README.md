# winactions

Windows UI Automation Toolkit for AI Agents.

`winactions` exposes Windows desktop applications as **indexed UI controls** that AI agents (or humans) can perceive and act on through a simple CLI called `winctl`.

```
winctl state                        # perceive: list all UI controls with indexes
# [1] [Button] "New mail"
# [2] [Edit] "Search"
# [3] [ListItem] "Inbox"

winctl click 1                      # act: click by index
winctl input 2 "meeting tomorrow"   # act: type into a control by index
```

## Features

- **Index-based protocol** — perception outputs numbered controls, execution takes those numbers
- **Session mode** — persistent daemon keeps window focus and control map across commands
- **Multi-tier perception** — UIA controls (free), LLM-inferred elements (`--infer`), Vision API detection (`--vision`)
- **Rich command set** — click, double-click, right-click, input, type, keys, scroll, select, drag, wait
- **JSON output** — `--json` flag for programmatic consumption
- **Window management** — launch, focus, close applications

## Installation

```bash
pip install -e ".[dev]"
```

This installs the `winctl` command and all dependencies including CLI, vision, and test tools.

For minimal installation (no vision/CLI extras):

```bash
pip install -e .
```

## Requirements

- Windows 10/11
- Python 3.10+

## Quick Start

```bash
# List all visible windows
winctl windows

# Get UI state of the focused window
winctl state

# Use session mode for multi-step workflows (recommended)
winctl --session myapp state
winctl --session myapp click 3
winctl --session myapp input 5 "hello world"
winctl --session myapp keys "ctrl+s"

# Take a screenshot
winctl screenshot output.png

# Launch an application
winctl launch notepad
```

## Session Mode

For multi-step workflows, always use `--session <name>`. Session mode starts a background daemon that maintains window focus and control indexes across commands.

```bash
winctl --session outlook state          # daemon auto-starts
winctl --session outlook click 5        # reuses same session
winctl --session outlook keys "ctrl+a"  # focus stays on target app
```

Without session mode, each command is a fresh process — the OS may shift focus back to the terminal between calls.

## Perception Tiers

| Tier | Flag | Detects | Precision | Cost |
|------|------|---------|-----------|------|
| **Tier 1** | *(default)* | Standard UIA controls (buttons, edits, lists) | Pixel-precise | Free |
| **Tier 1+** | `--infer` | + LLM-inferred hidden elements (column borders, resize handles) | Pixel-precise | ~$0.001-0.003 |
| **Tier 2** | `--vision` | + Vision API-detected elements (icon buttons, canvas items) | ~35px | ~$0.01 |

Tiers can be combined: `--infer --vision` fuses all sources.

## Vision API Configuration

Tier 1+ (`--infer`) and Tier 2 (`--vision`) require an API key:

```bash
# Option 1: Environment variable (recommended)
export WINACTIONS_API_KEY="your-api-key"

# Option 2: Use Anthropic API key
export ANTHROPIC_API_KEY="your-api-key"

# Option 3: Pass directly
winctl --vision-api-key "your-key" --vision state
```

Optionally set a custom base URL:

```bash
export WINACTIONS_BASE_URL="https://api.example.com"
```

## Command Reference

### Perception
| Command | Description |
|---------|-------------|
| `windows` | List visible windows |
| `state` | List UI controls (indexed) |
| `inspect <index>` | Detailed info on a control |
| `screenshot [path]` | Capture screenshot |

### Execution (index-based)
| Command | Description |
|---------|-------------|
| `click <index>` | Click a control |
| `dblclick <index>` | Double-click |
| `rightclick <index>` | Right-click |
| `input <index> <text>` | Clear and type into a control |
| `type <text>` | Type text at current focus |
| `keys <combo>` | Send key combination (e.g. `ctrl+s`) |
| `scroll <index> <direction> [clicks]` | Scroll a control |
| `select <index> <value>` | Select from a combo box |
| `drag <from> <to>` | Drag between controls |

### Execution (coordinate-based)
| Command | Description |
|---------|-------------|
| `click-at <x> <y>` | Click at coordinates |
| `drag-at <x1> <y1> <x2> <y2>` | Drag between coordinates |

### Window Management
| Command | Description |
|---------|-------------|
| `focus <title>` | Focus a window by title |
| `launch <program>` | Launch an application |
| `close` | Close the focused window |

### Data Extraction
| Command | Description |
|---------|-------------|
| `get text <index>` | Get text content |
| `get rect <index>` | Get bounding rectangle |
| `get value <index>` | Get control value |

## License

[MIT](LICENSE)
