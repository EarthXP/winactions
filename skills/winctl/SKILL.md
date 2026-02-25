---
name: winctl
description: Automates Windows desktop applications via indexed UI controls. Use when inspecting controls, clicking, typing, dragging, scrolling, reading UI state, or any task targeting a Windows desktop application.
---

# winctl — Windows UI Automation CLI for AI Agents

## Overview

`winctl` automates Windows desktop applications via indexed UI controls.
The **index number** is the shared protocol between perception and execution:

```
winctl --session outlook state
# [1] [Button] "New mail"
# [2] [Edit] "Search"

winctl --session outlook click 1      # click by index
winctl --session outlook input 2 "hello"  # type by index
```

## Session Mode (Recommended)

**Always use `--session <name>` for multi-step workflows.** Session mode starts a persistent background daemon that holds the DesktopSession, control_map, and OS focus state across commands. This eliminates the three critical problems of the default per-process mode:

| Problem | Without `--session` | With `--session` |
|---------|-------------------|-----------------|
| **Focus drift** | OS focus returns to terminal between CLI calls; `keys` hits wrong control | Daemon keeps focus on target app continuously |
| **Index confusion** | Each call re-enumerates; `--return-state` indexes are for display only | Daemon's control_map persists; indexes from `state` are directly usable |
| **Stale coordinates** | `inspect` coordinates may become invalid by the next CLI call | Daemon maintains stable window focus, reducing coordinate drift |

```bash
# Start a session (daemon auto-starts on first use)
winctl --session outlook state

# All subsequent commands reuse the same persistent session
winctl --session outlook click 5
winctl --session outlook keys "ctrl+a"    # focus is continuous — no drift
winctl --session outlook state            # indexes are consistent with control_map
```

**When NOT to use session mode**: Single one-off commands, or when you explicitly need a fresh process (e.g. debugging).

## Perception Tiers

`winctl` supports multiple perception levels, organized along two dimensions: **coordinate source** (UIA rect vs screenshot) and **executor** (tool-automated vs agent-manual). See `PERCEPTION_TIERS.md` for full details.

|  | **Coordinates from UIA rect** (pixel-precise) | **Coordinates from screenshot** (±10-50px) |
|---|---|---|
| **Tool-automated** (in `state` output) | **Tier 1** (default) / **Tier 1+** (`--infer`) | **Tier 2** (`--vision`) |
| **Agent-manual** (multi-step CLI) | **Tier 3a** (`inspect` → reason → `drag-at`) | **Tier 3b** (screenshot → estimate → `click-at`) |

| Tier | Flag | Detects | Precision | Cost |
|------|------|---------|-----------|------|
| **Tier 1** | *(default)* | Standard UIA controls (buttons, edits, lists, etc.) | Pixel-precise | Free |
| **Tier 1+** | `--infer` | UIA controls + LLM-inferred hidden elements (column borders, row borders, resize handles, splitters). Pure text LLM — reads UIA rects, derives coordinates by arithmetic | Pixel-precise | ~$0.001-0.003 |
| **Tier 2** | `--vision` | Vision API-detected elements (icon-only buttons, canvas elements, custom controls). Multimodal LLM — reads screenshot, estimates bounding boxes | ±35px | ~$0.01 |
| **Tier 3a** | *(agent behavior)* | Same as Tier 1+ but done manually: `inspect` nearby UIA controls → reason about spatial layout → `drag-at` with computed coordinates. Fallback when `--infer` misses the target | Pixel-precise | Free |
| **Tier 3b** | *(agent behavior)* | Same as Tier 2 but done manually: look at screenshot → estimate coordinates → `click-at`. Last resort, never used in E2E testing | ±10-50px | Free |

Key relationships:
- **Tier 1+ ↔ Tier 3a**: Same principle (UIA rect derivation). Tier 1+ automates what Tier 3a does manually
- **Tier 2 ↔ Tier 3b**: Same principle (screenshot estimation). Tier 2 automates what Tier 3b does manually

Tool-automated tiers can be combined: `--infer --vision` fuses all sources with IOU-based deduplication.

### Inferred/vision elements in output

Non-UIA elements always include their bounding rect (even in compact mode) so you can use coordinate commands directly:

```
[1] [Button] "Save"                                          ← UIA control (no rect in compact mode)
[2] [Edit] "Name"                                            ← UIA control (no rect in compact mode)
[305] [ColumnBorder] "Column border 1-2" rect=[-2294,850,-2290,900]  ← inferred (always has rect)
[306] [ResizeHandle] "Table resize handle" rect=[-2440,920,-2430,930] ← inferred (always has rect)
```

## Layered Information Architecture

`state` returns **compact output by default** (id + type + name, no rect). Use on-demand queries for details:

| Level | Command | Returns | Use When |
|-------|---------|---------|----------|
| **L0** | `state` | id, type, name | Overview — find the right control by name/type |
| **L0+** | `state --verbose` | id, type, name, rect | Need coordinates for all controls at once |
| **L1** | `inspect <index>` | Full control properties (rect, class, automation_id, patterns, etc.) | Need details about a specific control |
| **L2** | `get rect <index>` | Bounding rectangle only | Need precise coordinates for one control |
| **L2** | `get text <index>` | Text content only | Need text content of a control |
| **L2** | `get value <index>` | Value (toggle state, slider position, etc.) | Need control value |

**With `--session`**, follow-up queries (L1/L2) cost ~5ms TCP round-trip, so the compact default is efficient: scan the overview, then drill into specific controls as needed.

## Core Workflow

### Session mode (recommended for multi-step tasks)

```bash
# 1. Start session + perceive
winctl --session outlook state                # compact: id + type + name
# [1] [Button] "New mail"
# [71] [TabItem] "Insert"

# 2. Execute — indexes are stable because daemon holds control_map
winctl --session outlook click 1

# 3. Perceive again — daemon's state is coherent
winctl --session outlook state
# [1] [Button] "Send"
# [278] [Edit] "Message body"

# 4. Need coordinates? Query on-demand
winctl --session outlook get rect 278
# {"left": -3200, "top": 450, "right": -2800, "bottom": 900}

# 5. Continue operating
winctl --session outlook input 278 "Hello"
winctl --session outlook keys "ctrl+Enter"
```

### Non-session mode (--return-state workflow)

Use `--return-state` to combine execute + perceive in one call:

1. **Perceive** — `winctl --window <app> state` to get indexed control list
2. **Decide** — Analyze the list, choose target + action
3. **Execute + Perceive** — `winctl --return-state --window <app> click <index>`
4. **Decide** — The output contains both the action result AND fresh state; use the new indexes directly
5. Repeat from step 3

**Output with `--return-state`:**
```
OK                                    ← action result
Window: "Mail - Outlook" (olk.exe)    ← fresh state (taken 0.5s after action)
[1] [Button] "New mail"
[2] [Edit] "Search"
...
```

## Critical Rules

### Index Drift — Why Indexes Go Stale

Without `--session`, each `winctl` call is a separate process. `state` enumerates all controls sequentially (1, 2, 3, ...). When the UI changes, **the same index number maps to a different control**.

**With `--session`**: The daemon holds a persistent `control_map`, so indexes from `state` remain valid until the next `state` call. Focus drift is eliminated. This is the primary reason to prefer session mode.

**Rules:**

1. **Always `state` before acting** — Index numbers are only valid for the most recent `state` output
2. **Use `--session` for multi-step workflows** — Eliminates focus drift, index confusion, and coordinate staleness. All three pitfalls below disappear with session mode
3. **ALWAYS use `keys --target` for keyboard input (non-session mode)** — Each CLI call is a separate process. UIA focus drifts between calls, especially in WebView2 apps (e.g. new Outlook). Without `--target`, keys go to whatever has UIA focus. In session mode, focus is continuous so `keys` without `--target` is safer, but `--target` is still recommended for precision
4. **Use `--return-state` on execution commands (non-session mode)** — Returns fresh indexes after each action. In session mode, `--return-state` is optional since you can call `state` separately with low overhead
5. **Re-`state` after `focus`** — Switching windows resets everything
6. **Indexes and rects are ephemeral** — Never cache or reuse indexes across `state` calls. If you need a coordinate, use `get rect <index>` or `inspect <index>` to get a fresh value
7. **Never mix indexes across different flags** — `state`, `--infer state`, and `--vision state` return different index sets. An index from `--infer state` is NOT valid for a plain `click <index>`. Use coordinate commands (`click-at`/`drag-at`) for inferred/vision elements
8. **Verify element state before coordinate operations** — Before `drag-at`, confirm the target is in the correct state. Use `state` or `inspect` immediately before
9. **Negative coordinates need `--`** — Use `winctl click-at -- -100 200` to prevent `-100` being parsed as a flag
10. **Options before `--`** — Put flags before the separator: `winctl drag-at --duration 1.5 -- -100 200 300 400`
11. **COM errors may be non-blocking** — Some web-based app controls produce COM error `-2147220991` on click, but the action still succeeds. Check UI state instead of treating the error as failure
12. **Use `type` for text, `keys` for shortcuts** — `keys "1+1="` does NOT type "1+1="; use `type "1+1="` instead. `keys` is for keyboard shortcuts (`ctrl+a`) and single key presses (`Enter`, `+`). See the `keys` vs `type` section below
13. **Launch UWP apps via URI protocol, not `launch`** — Admin processes cannot properly launch UWP apps. Use `powershell -NoProfile -Command "Start-Process 'calculator:'"` instead. If `state` returns 0 controls for a UWP app, this is likely the cause

## Global Options

| Option | Description |
|---|---|
| `--session TEXT` | **Recommended.** Named session — persistent daemon holds state, focus, and control_map across commands |
| `--window TEXT` | Target window by title or process name substring (e.g. `--window outlook`) |
| `--infer` | Enable Tier 1+ LLM structural inference of hidden UI elements |
| `--vision` | Enable Tier 2 vision-based UI element detection |
| `--json` | JSON output mode (for programmatic consumption) |
| `--return-state` | Append fresh state after execution commands (0.5s UI settle). Most useful in non-session mode |
| `--vision-api-key TEXT` | API key for vision/inference (or set `ANTHROPIC_API_KEY`) |
| `--vision-base-url TEXT` | Custom base URL for vision/inference API |

## Available Commands

### Perception (Read UI State)

| Command | Description |
|---|---|
| `winctl windows` | List all visible desktop windows |
| `winctl state` | Show indexed control list (compact: id + type + name, no rect) |
| `winctl state --verbose` | Include rect coordinates for all controls |
| `winctl state --screenshot` | Include screenshot path |
| `winctl state --annotated` | Include annotated screenshot |
| `winctl state --tree` | Show hierarchical control tree |
| `winctl inspect <index>` | Show detailed properties of one control (rect, class, etc.) |
| `winctl screenshot [path]` | Save screenshot to file |

### Execution (Perform Actions)

| Command | Description |
|---|---|
| `winctl click <index>` | Click a control |
| `winctl click --right <index>` | Right-click a control |
| `winctl dblclick <index>` | Double-click a control |
| `winctl rightclick <index>` | Right-click a control (alias for `click --right`) |
| `winctl input <index> <text>` | Set text on a control |
| `winctl type <text>` | Type text to focused element (via pyautogui) |
| `winctl keys <keys>` | Send keyboard shortcut (e.g. `ctrl+s`, `Enter`, `alt+f4`) |
| `winctl keys --target <index> <keys>` | Focus control first, then send keys (prevents focus drift) |
| `winctl scroll <index> <dir> [n]` | Scroll up/down/left/right |
| `winctl select <index> <value>` | Select dropdown item by value |

### Coordinate Mode

For elements without UIA controls (inferred/vision elements, or arbitrary screen positions):

| Command | Description |
|---|---|
| `winctl click-at <x> <y>` | Click at absolute screen coordinates |
| `winctl drag <index> <x2> <y2>` | Drag from a control's center to target coordinates |
| `winctl drag-at <x1> <y1> <x2> <y2>` | Drag between two absolute screen coordinates |

Options for drag commands: `--button left|right`, `--duration <seconds>`

### Window Management

| Command | Description |
|---|---|
| `winctl focus <window>` | Focus window (by title, process, or index) |
| `winctl launch <app>` | Launch an application |
| `winctl close` | Close the current window |

### Data Extraction

| Command | Description |
|---|---|
| `winctl get text <index>` | Get control's text content |
| `winctl get rect <index>` | Get control's bounding rectangle |
| `winctl get value <index>` | Get control's value (e.g. toggle state, slider position) |

### Wait

| Command | Description |
|---|---|
| `winctl wait [seconds]` | Wait (default: 1 second) |
| `winctl wait --visible <index>` | Wait until a control becomes visible |
| `winctl wait --enabled <index>` | Wait until a control becomes enabled |
| `winctl wait --timeout <seconds>` | Timeout for `--visible`/`--enabled` |

## `keys` vs `type` — Choosing the Right Input Command

| Command | Mechanism | Special chars | Use for |
|---------|-----------|---------------|---------|
| `keys <combo>` | pywinauto `type_keys()` | `+` `^` `%` `~` are modifier/control chars — single special chars are auto-escaped to `{+}` etc. | Keyboard shortcuts (`ctrl+a`), single key presses (`Enter`, `+`, `F5`) |
| `type <text>` | pyautogui `write()` | All characters sent literally, no interpretation | Text content, mathematical expressions, any multi-character string with special chars |

```bash
# Keyboard shortcuts → keys
winctl keys "ctrl+a"          # Select all
winctl keys "alt+f4"          # Close window
winctl keys "Enter"           # Press Enter
winctl keys "+"               # Press literal + key

# Text / expressions → type
winctl type "1+1="            # Type each character literally
winctl type "Hello, world!"   # Type text content
```

**Rule of thumb**: If the input is a **shortcut or single key press**, use `keys`. If it's **text content to type out**, use `type`.

## Examples

### Session mode: Multi-step Outlook workflow (recommended)

```bash
# Session auto-starts daemon on first use
winctl --session outlook state
# Window: "Mail - Outlook" (olk.exe)
# [1] [Button] "New mail"
# [71] [TabItem] "Insert"
# ...

# Click — daemon holds focus, no drift
winctl --session outlook click 1

# Fresh state — daemon's control_map is coherent
winctl --session outlook state
# [1] [Button] "Send"
# [278] [Edit] "Message body"

# Need coordinates for a specific control? Query on-demand
winctl --session outlook get rect 278

# Type into message body — focus is continuous
winctl --session outlook input 278 "Meeting agenda"
winctl --session outlook keys "ctrl+Enter"
```

### Session mode: Inferred element interaction

```bash
winctl --session outlook --infer state
# [1] [Button] "New mail"
# ...
# [340] [ResizeHandle] "Table resize handle" rect=[-2632, 583, -2622, 603]

# Inferred elements always have rect — use drag-at directly
winctl --session outlook drag-at --duration 1.5 -- -2627 593 -2427 593
```

### Non-session: --return-state workflow

```bash
winctl --window outlook state
# [1] [Button] "New mail"
# [61] [TabItem] "File"

winctl --return-state --window outlook click 61
# OK
# Window: "..." (olk.exe)         ← backstage menu now open
# [313] [ListItem] "Account info"
# [315] [ListItem] "Save as"

# Indexes shifted after menu opened — but --return-state gave us fresh ones
winctl --return-state --window outlook click 315
```

### Multi-window workflow

```bash
winctl --session work windows
# [1] Notepad (notepad.exe)
# [2] File Explorer (explorer.exe)
winctl --session work focus 1
winctl --session work state              # Must state after focus change
winctl --session work focus 2
winctl --session work state              # Must state after focus change
```

### Coordinate click with negative coordinates

```bash
# Use -- to separate negative coordinates from flags
winctl --session outlook click-at -- -3659 94

# Options must come BEFORE --
winctl --session outlook drag-at --duration 2.0 -- -3000 500 -2800 500
```

## Decision Guide for Agents

```
Element visible in `state` output?
  YES → [Tier 1] Use index commands: click/input/drag <index>
        Need coordinates? → `get rect <index>` or `inspect <index>`
  NO (UIA-invisible element) →
    |-- [Tier 1+] Run `--infer state` (LLM structural inference, ~$0.003)
    |    Found with rect? → Use click-at/drag-at with rect values (pixel-precise)
    |
    |-- [Tier 2] Run `--vision state` (Vision API, ~$0.01)
    |    Found? + target > 20px → Use click-at/drag-at with rect values
    |
    |-- [Tier 3a] Nearby UIA controls available as anchors?
    |    YES → `inspect <neighbor>` → compute coordinates from rect → drag-at
    |          (pixel-precise, free — fallback for Tier 1+)
    |
    +-- [Tier 3b] Last resort: screenshot → estimate coordinates → click-at
```

## Common Pitfalls

These are real mistakes observed during E2E testing. Each cost multiple retry cycles. **Session mode (`--session`) eliminates Pitfalls 1 and 3.**

### Pitfall 1: `keys` without `--target` hits the wrong control

**Solved by `--session`.** In session mode, focus is continuous between commands, so `keys` reliably hits the correct control.

In non-session mode:
```bash
# WRONG — focus drifted to the message list between calls
winctl --window outlook click 278          # click table cell (process A)
winctl --window outlook keys "ctrl+a"      # Ctrl+A went to message list! (process B)

# CORRECT (non-session) — --target focuses the control in the same process
winctl --window outlook keys --target 311 "ctrl+a"

# CORRECT (session) — focus is continuous, no drift
winctl --session outlook click 278
winctl --session outlook keys "ctrl+a"     # goes to the right control
```

**Why**: In WebView2 apps (new Outlook, Teams, Edge-based apps), `click <index>` physically clicks the element, but UIA focus may remain on a native control. Without session mode, the next `keys` call sends keystrokes to the UIA-focused control, not the one you clicked.

### Pitfall 2: Mixing indexes from different flag modes

```bash
# WRONG — indexes from --infer don't match plain mode
winctl --infer --window outlook state      # [274] = RowBorder (virtual element)
winctl --window outlook inspect 274        # inspects a Pane, not the RowBorder!

# CORRECT — use rect from inferred output for coordinate commands
winctl --infer --session outlook state
# [305] [ResizeHandle] "Table resize handle" rect=[-2632, 583, -2622, 603]
winctl --session outlook drag-at -- -2627 593 -2427 593    # use rect directly
```

**Why**: `--infer` inserts virtual elements between UIA elements, shifting index numbers. A plain `inspect` or `click` re-enumerates without virtual elements, so index N maps to a different control.

### Pitfall 3: Drag on stale coordinates

**Mitigated by `--session`.** The daemon maintains continuous window focus, reducing coordinate drift. Still verify with `inspect` before precision operations.

In non-session mode:
```bash
# WRONG — several commands elapsed since inspect, table may have moved
winctl --window outlook inspect 283        # get coordinates
winctl --window outlook click 277          # click a cell (UI may shift)
winctl --window outlook keys "ctrl+a"      # select (UI may shift again)
winctl --window outlook drag-at -- -3228 2090 -3128 2140  # coordinates are stale!

# CORRECT — inspect immediately before drag
winctl --window outlook keys --target 311 "ctrl+a"
winctl --window outlook inspect 283        # get FRESH coordinates right before drag
winctl --window outlook drag-at -- -3228 2090 -3128 2140
```

### Pitfall 4: Closing the wrong window due to title substring match

```bash
# WRONG — matches both the Calculator app AND a Claude Code window
winctl --window Calculator close          # might close your Claude Code window!

# CORRECT — use `windows` first to verify which window you're targeting
winctl windows
# [1] Calculator (Calculator.exe)
# [2] "winctl automation test" (claude.exe)

# Confirm the process name matches the intended app, then close
winctl --window Calculator.exe close  # use process name, not title
```

**Why**: `--window` does substring matching on the window title. If multiple windows contain the same substring, you may close the wrong one. **Always run `winctl windows` first and verify by process name or full title before closing.**

### Pitfall 5: UWP apps launched from admin have empty UIA trees

```bash
# WRONG — admin process launches UWP in broken state (0 controls visible)
winctl launch calculator
winctl --window Calculator state
# Window: "Calculator" (ApplicationFrameHost.exe)
# (no controls!)

# CORRECT — launch via URI protocol (explorer.exe auto-downgrades to Medium integrity)
powershell -NoProfile -Command "Start-Process 'calculator:'"
winctl wait 3
winctl --window Calculator state
# Window: "Calculator" (ApplicationFrameHost.exe)
# [1] [Window] "Calculator"
# ... (68 controls)
```

**Why**: UWP apps run in a Medium-integrity AppContainer sandbox. When launched from a High-integrity (admin) process, the UI doesn't render properly and the UIA tree is empty or incomplete. This affects **all** UWP/MSIX apps (Calculator, Paint, Clock, Mail, Store, etc.). Win32 desktop apps are not affected.

**Diagnosis**: `state` returns `targets: []` or abnormally few controls (e.g. <10 for an app that should have 50+) → most likely an admin privilege issue. Relaunch via URI protocol.

### Pitfall 6: Dynamic UI causes index drift in non-session mode

```bash
# WRONG — Calculator adds an expression text control after "1+",
#    shifting ALL subsequent indexes by +1
winctl --window Calculator state         # [57]="1", [52]="+", [53]="="
winctl --window Calculator click 57      # "1" OK
winctl --window Calculator click 52      # "+" OK — but now expression text appears, indexes shift!
winctl --window Calculator click 57      # expects "1", gets "0" (shifted +1)
winctl --window Calculator click 53      # expects "=", gets "+" (shifted +1)
# Result: 1 + 0 + (wrong!)

# CORRECT — session mode freezes control_map as COM object references
winctl --session calc --window Calculator state  # [57]="1", [52]="+", [53]="="
winctl --session calc click 57       # "1" OK — COM ref points to actual button
winctl --session calc click 52       # "+" OK — UI changes, but COM refs are stable
winctl --session calc click 57       # "1" OK — still the same COM object!
winctl --session calc click 53       # "=" OK
# Result: 2 (correct!)
```

**Why**: In non-session mode, each command re-enumerates the UIA tree. Apps that dynamically add/remove controls during interaction (Calculator, dialogs with progressive disclosure, etc.) cause index numbers to shift between commands. Session mode stores COM object references in the `control_map`, which remain valid regardless of tree changes.

**Alternative for non-session mode**: Use `type "1+1="` instead of clicking buttons — pyautogui sends characters directly without relying on indexes.

## Error Handling

- If `state` returns empty: the window may have closed or be unresponsive. For UWP apps, this likely means an admin privilege issue — relaunch via URI protocol (see Pitfall 5)
- If `click` produces unexpected results: indexes may have shifted — use `--session` mode for dynamic UIs, or re-run `state` before each click (see Pitfall 6)
- If `click` fails: the control may be disabled — check with `winctl inspect <index>`
- If indexes seem wrong: always re-run `winctl state` — never trust stale indexes
- If a window isn't found: use `winctl windows` to see all available windows
- If negative coordinates fail: use `--` separator before the coordinates
- If session daemon fails to start: check if the port is in use (`%TEMP%/winctl_<name>.pid`)

## Output Formats

**Compact text mode (default)** — No rect for UIA controls:
```
Window: "Mail - Outlook" (olk.exe)
[1] [Button] "New mail"
[2] [Edit] "Search"
[305] [ColumnBorder] "Column border 1-2" rect=[-2294, 850, -2290, 900]  ← non-UIA: always has rect
```

**Verbose text mode (`state --verbose`)** — Rect for all controls:
```
Window: "Mail - Outlook" (olk.exe)
[1] [Button] "New mail" rect=[100, 130, 200, 177]
[2] [Edit] "Search" rect=[250, 130, 500, 160]
[305] [ColumnBorder] "Column border 1-2" rect=[-2294, 850, -2290, 900]
```

**JSON mode (`--json`)** — Compact by default, `--verbose` adds rect:
```json
{
  "window": "Mail - Outlook",
  "process": "olk.exe",
  "targets": [
    {"id": "1", "name": "New mail", "type": "Button"},
    {"id": "305", "name": "Column border 1-2", "type": "ColumnBorder", "rect": [-2294, 850, -2290, 900]}
  ]
}
```
