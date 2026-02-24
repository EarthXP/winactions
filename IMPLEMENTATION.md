# winactions Implementation Documentation

This document provides a comprehensive record of the `winactions` package implementation: which files were copied from Microsoft's UFO project, what changes were made and why, which files are entirely new, and how everything connects into a working system.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Two Kinds of COM in This Codebase](#two-kinds-of-com-in-this-codebase)
3. [Files Copied from UFO Without Change](#files-copied-from-ufo-without-change)
4. [Files Copied from UFO With Changes](#files-copied-from-ufo-with-changes)
5. [Purely New Files](#purely-new-files)
6. [How the Layers Work Together](#how-the-layers-work-together)
7. [Cross-Cutting Changes Applied to All UFO-Derived Files](#cross-cutting-changes-applied-to-all-ufo-derived-files)
8. [Key Design Decisions](#key-design-decisions)
9. [Source File Mapping Table](#source-file-mapping-table)

---

## Architecture Overview

The core design principle of `winactions` is:

> **Index numbers are the shared communication protocol between perception, decision, and execution layers.**

```
Perception (CLI)              Decision (External)         Execution (CLI)
────────────────              ──────────────────          ───────────────
winctl state ──→ indexed  ──→ LLM/Human analyzes    ──→ winctl click 3
winctl screenshot    list     (chooses target+action)    winctl input 2 "hello"
winctl windows                                           winctl keys "ctrl+s"
```

The package is organized into layers:

| Layer | Files | Purpose |
|---|---|---|
| **Config** | `config.py`, `_version.py` | Global singleton configuration |
| **Utils** | `_utils.py` | Minimal utility functions |
| **Protocol** | `targets.py`, `models.py` | Shared data models (TargetInfo, ActionCommandInfo, Result) |
| **Command** | `command/basic.py`, `command/puppeteer.py`, `command/executor.py` | Command Pattern: ABC base, orchestration, execution |
| **Control** | `control/controller.py`, `control/inspector.py` | 16+ registered UI commands, UIA/Win32 element discovery |
| **Screenshot** | `screenshot/photographer.py` | Capture, annotate, encode screenshots |
| **Perception** | `perception/provider.py`, `perception/state.py`, `perception/vision_provider.py`, `perception/structural_provider.py` | Pluggable control detection, atomic UI snapshots, vision detection (Tier 2), structural inference (Tier 1+) |
| **CLI** | `cli/app.py`, `cli/session.py`, `cli/formatter.py` | Click-based `winctl` CLI, session management, dual-mode output |

---

## Two Kinds of COM in This Codebase

The codebase references "COM" in two completely different contexts. Understanding this distinction is important:

### UIA COM — OS-Level UI Automation Infrastructure (Retained)

Windows UI Automation (UIA) is itself a COM API. The `inspector.py` file uses `comtypes.gen.UIAutomationClient` to call UIA COM interfaces like `FindAllBuildCache`, `CreateOrConditionFromArray`, and `IUIAutomationElement`. This is **the standard and only way** to discover UI elements on Windows programmatically.

```python
# inspector.py — UIA COM: discovering UI elements via the OS-level UIA API
com_elem_array = window_elem_com_ref.FindAllBuildCache(
    scope=iuia_dll.TreeScope_Descendants,
    condition=condition,
    cacheRequest=cache_request,
)
```

This is analogous to HTTP being the transport layer for web APIs — you cannot do UIA without going through COM. The dependencies `comtypes`, `pywinauto`, and `uiautomation` are all Python wrappers around this UIA COM interface.

### Application-Level COM Automation (Removed)

UFO also had a separate layer for controlling applications through their **application-specific COM APIs** — for example, `win32com.client.Dispatch("Word.Application")` to directly manipulate Word documents, save files, export to XML, etc. This was implemented by `WinCOMReceiverBasic` and app-specific receivers.

```python
# UFO puppeteer.py — App COM: controlling Office internals (REMOVED from winactions)
com_receiver = self.receiver_manager.com_receiver
com_receiver.save()         # Call Word/Excel COM API to save
com_receiver.full_path      # Get document path via COM
com_receiver.save_to_xml()  # Export via COM
```

**winactions removed this entire application-level COM layer** (the `WinCOMReceiverBasic`, `create_api_receiver()`, `com_receiver` property, `save()`, `save_to_xml()`, `close()`, `full_path`) because the project focuses on UI-level interaction (clicking buttons, typing text) rather than application-internal manipulation.

| | UIA COM (Retained) | Application COM (Removed) |
|---|---|---|
| **Purpose** | Discover and interact with UI controls | Directly manipulate application internals |
| **Level** | OS infrastructure | Application-specific API |
| **Analogy** | Operating the UI with mouse/keyboard | Running VBA macros inside the app |
| **Replaceable** | No (UIA IS a COM API) | Yes (not needed for UI automation) |
| **In winactions** | `inspector.py` uses it to find controls | Deleted from `puppeteer.py` |
| **Dependencies** | `comtypes`, `pywinauto` | `WinCOMReceiverBasic` (deleted) |

---

## Files Copied from UFO Without Change

These files preserve 100% of the original UFO logic. The only modifications are cosmetic: docstring compression (multi-line `:param` style to one-line summaries), bare `except:` to `except Exception:`, and `== None` to `is None`.

### 1. `command/basic.py`

**UFO source:** `ufo/automator/basic.py` (128 lines)
**winactions:** `src/winactions/command/basic.py` (80 lines)

This is the Command Pattern foundation. Three abstract base classes with zero external dependencies:

- **`ReceiverBasic(ABC)`** — Abstract receiver with `_command_registry` dict, `register()` classmethod decorator for registering command classes, `self_command_mapping()` for building command-name-to-class maps, `supported_command_names` property.
- **`CommandBasic(ABC)`** — Abstract command with `execute()`, `undo()`, `redo()`, and `name()` classmethod.
- **`ReceiverFactory(ABC)`** — Abstract factory with `create_receiver()`, `name()`, and `is_api()`.

Every method signature, return value, and code path is identical to UFO. The line count reduction (128→80) is entirely from stripping verbose docstrings.

### 2. `targets.py`

**UFO source:** `ufo/agents/processors/schemas/target.py` (165 lines)
**winactions:** `src/winactions/targets.py` (119 lines)

The shared protocol data model. All classes are functionally identical:

- **`TargetKind(str, Enum)`** — Values: `WINDOW`, `CONTROL`, `THIRD_PARTY_AGENT`.
- **`TargetInfo(BaseModel)`** — Pydantic model with fields: `kind`, `name`, `id` (the index number), `type`, `rect` (bounding box as `[left, top, right, bottom]`).
- **`TargetRegistry`** — Registry with `register()`, `get()`, `find_by_name()`, `find_by_id()`, `find_by_kind()`, `all_targets()`, `unregister()`, `to_list()`, `clear()`.

`TargetInfo.id` is the index number string ("1", "2", ...) that serves as the protocol between perception and execution.

### 3. `control/inspector.py`

**UFO source:** `ufo/automator/ui_control/inspector.py` (706 lines)
**winactions:** `src/winactions/control/inspector.py` (532 lines)

The UI element discovery engine. This file had **zero UFO-internal imports** in the original, so no import rewiring was needed. All classes and methods are functionally identical:

- **`BackendFactory`** — Static factory returning `UIABackendStrategy` or `Win32BackendStrategy`.
- **`BackendStrategy(ABC)`** — Interface: `get_desktop_windows()`, `find_control_elements_in_descendants()`.
- **`UIAElementInfoFix(UIAElementInfo)`** — Monkey-patches pywinauto's `UIAElementInfo` with cached rectangles and timing-aware property accessors via a `_time_wrap` decorator.
- **`UIABackendStrategy`** — UIA implementation using the Windows UI Automation COM interface (`FindAllBuildCache` with property caching). Caps element scanning at 500 elements. Note: this is the OS-level UIA COM interface (the standard way to discover UI elements on Windows), not application-level COM automation — see [Two Kinds of COM](#two-kinds-of-com-in-this-codebase) below.
- **`Win32BackendStrategy`** — Win32 backend using `window.descendants()`.
- **`ControlInspectorFacade`** — Singleton facade with `get_desktop_windows()`, `find_control_elements_in_descendants()`, `get_control_info()` (static), and `get_desktop_app_dict()`.

The only non-cosmetic change: diagnostic `print()` calls in `_time_wrap` were removed (timing logic preserved).

---

## Files Copied from UFO With Changes

These files preserve the core logic but have substantive modifications to decouple from UFO's config system, remove COM/API dependencies, or simplify for standalone use.

### 4. `command/puppeteer.py`

**UFO source:** `ufo/automator/puppeteer.py` (309 lines)
**winactions:** `src/winactions/command/puppeteer.py` (147 lines)

**What was preserved:**
- `AppPuppeteer.__init__()`, `create_command()`, `execute_command()`, `execute_all_commands()`, `add_command()`, `list_commands()`, `get_command_queue_length()`, `get_command_string()`, `get_command_types()` — all identical logic.
- `ReceiverManager.__init__()`, `create_ui_control_receiver()`, `_update_receiver_registry()`, `get_receiver_from_command_name()`, `receiver_list`, `receiver_factory_registry`, `register()` classmethod — all identical logic.

**What was changed:**
- Import paths: `ufo.automator.basic` → `winactions.command.basic`, `ufo.automator.ui_control.controller` → `winactions.control.controller`.

**What was deleted and why:**
- `AppPuppeteer.full_path` — Depended on COM receiver for getting the document file path.
- `AppPuppeteer.save()` — COM-only functionality for saving documents via COM API.
- `AppPuppeteer.save_to_xml()` — COM-only functionality for XML export.
- `AppPuppeteer.close()` — COM-only functionality for closing documents.
- `ReceiverManager.create_api_receiver()` — Iterated over receiver factories marked `is_api` to create COM/API receivers.
- `ReceiverManager.com_receiver` property — Searched the receiver list for `WinCOMReceiverBasic` subclass instances.
- The `from ufo.automator.app_apis.basic import WinCOMReceiverBasic` import.

**Why:** winactions focuses purely on UI Automation (UIA) control interaction. The application-level COM automation layer (for directly controlling Office apps via their COM APIs, e.g., `Word.Application`) was intentionally stripped out as it's not needed for the core use case. See [Two Kinds of COM](#two-kinds-of-com-in-this-codebase) for the distinction.

### 5. `command/executor.py`

**UFO source:** `ufo/automator/action_execution.py` (134 lines)
**winactions:** `src/winactions/command/executor.py` (113 lines)

**What was preserved:**
- `ActionExecutor.__init__()`, `_control_validation()`, `_get_control_log()`, `execute()` — all identical logic.

**What was changed:**
- Import paths: `ufo.agents.processors.schemas.actions` → `winactions.models`, `ufo.automator.puppeteer` → `winactions.command.puppeteer`.
- Function call style: `utils.coordinate_adjusted(...)` → `coordinate_adjusted(...)` and `utils.is_json_serializable(...)` → `is_json_serializable(...)` (direct function imports instead of module-level utils import).

**Nothing was deleted.** This is a near-exact port with only import rewiring.

### 6. `control/controller.py` (Most Critical File)

**UFO source:** `ufo/automator/ui_control/controller.py` (1205 lines)
**winactions:** `src/winactions/control/controller.py` (720 lines)

This is the heart of the execution layer. It contains the `ControlReceiver` with all UI action methods and 16+ registered command classes.

**What was preserved (every method and command class):**
- `ControlReceiver`: `__init__()`, `click_input()`, `click_on_coordinates()`, `drag_on_coordinates()`, `set_edit_text()`, `keyboard_input()`, `key_press()`, `texts()`, `wheel_mouse_input()`, `scroll()`, `mouse_move()`, `type()`, `no_action()`, `annotation()`, `wait_enabled()`, `wait_visible()`, `transform_point()`, `transfrom_absolute_point_to_fractional()`, `transform_scaled_point_to_raw()`, `atomic_execution()`, `summary()`.
- All 16 command classes with `@ControlReceiver.register` decorators: `ClickInputCommand`, `ClickOnCoordinatesCommand`, `DragOnCoordinatesCommand`, `SummaryCommand`, `SetEditTextCommand`, `GetTextsCommand`, `WheelMouseInputCommand`, `AnnotationCommand`, `KeyboardInputCommand`, `NoActionCommand`, `ClickCommand`, `DoubleClickCommand`, `DragCommand`, `KeyPressCommand`, `MouseMoveCommand`, `ScrollCommand`, `TypeCommand`, `WaitCommand`.
- `UIControlReceiverFactory` with `@ReceiverManager.register`.
- `TextTransformer` with all 10 static methods.

**What was changed:**

1. **Config system completely decoupled:**
   - `from config.config_loader import get_ufo_config` → `from winactions.config import get_action_config`
   - Every `ufo_config.system.X` reference replaced:
     - `ufo_config.system.click_api` → `get_action_config().click_api`
     - `ufo_config.system.input_text_inter_key_pause` → `cfg.input_text_inter_key_pause`
     - `ufo_config.system.input_text_api` → `cfg.input_text_api`
     - `ufo_config.system.input_text_enter` → `cfg.input_text_enter`

2. **Module-level config initialization replaced with lazy initialization:**
   - UFO had `ufo_config = get_ufo_config()` at module level plus immediate pywinauto timing setup at import time.
   - winactions introduces a `_pywinauto_timings_initialized` flag and `_ensure_pywinauto_timings()` function called lazily in `ControlReceiver.__init__()`.
   - This avoids import-time side effects and allows the config to be set after import but before first use.

3. **`pyautogui.FAILSAFE`** changed from hardcoded `False` to `cfg.pyautogui_failsafe` (configurable).

4. **Class renamed: `keyboardInputCommand` → `KeyboardInputCommand`** — Fixed PEP 8 naming convention.

5. **Import paths rewired:** `ufo.automator.basic` → `winactions.command.basic`, `ufo.automator.puppeteer` → `winactions.command.puppeteer`.

**Nothing was deleted** in terms of methods or command classes. The line reduction (1205→720) is entirely from docstring compression.

### 7. `models.py`

**UFO sources:** `ufo/agents/processors/schemas/actions.py` (319 lines) + `aip/messages.py` (556 lines)
**winactions:** `src/winactions/models.py` (257 lines)

This file merges two UFO source files and inlines the AIP protocol types.

**What was preserved from `actions.py`:**
- `BaseControlLog` dataclass with `is_empty()` — identical.
- `ActionExecutionLog` dataclass — identical.
- `ActionCommandInfo(BaseModel)` with all fields and methods (`model_post_init()`, `to_string()`, `to_representation()`) — identical.
- `ListActionCommandInfo` with all methods (`add_action()`, `to_list_of_dicts()`, `to_string()`, `to_representation()`, `is_same_action()`, `count_repeat_times()`, `get_results()`, `get_target_info()`, `get_target_objects()`, `get_function_calls()`) — identical.

**What was preserved from `messages.py`:**
- `ResultStatus(str, Enum)` — `SUCCESS`, `FAILURE`, `SKIPPED`, `NONE` — identical.
- `Result(BaseModel)` — `status`, `error`, `result` fields — identical core.

**What was changed:**

1. **`ResultStatus` and `Result` inlined** — Instead of importing from `aip.messages`, these are defined directly in `models.py` with a `# --- Inlined from aip/messages.py ---` comment. This eliminates the AIP protocol library dependency.

2. **`Result` model simplified** — UFO had 5 fields (`status`, `error`, `result`, `namespace`, `call_id`). winactions has 3 fields. The `namespace` and `call_id` were AIP-protocol-specific.

3. **`ListActionCommandInfo.color_print()` rewritten** — UFO called `PresenterFactory` from the agent framework. winactions uses `rich` directly with a try/except fallback to plain `print()`, making it self-contained.

4. **`rich` made optional** — Module-level `from rich.console import Console` removed; `rich` is imported lazily inside `color_print()`.

**What was deleted from `messages.py`:** Everything except `ResultStatus` and `Result` — the `Rect`, `ControlInfo`, `WindowInfo`, `AppWindowControlInfo`, `MCPToolInfo`, `MCPToolCall`, `Command`, `TaskStatus`, message types, validators, and binary transfer models all belong to the AIP orchestration layer.

### 8. `screenshot/photographer.py`

**UFO source:** `ufo/automator/ui_control/screenshot.py` (1276 lines)
**winactions:** `src/winactions/screenshot/photographer.py` (376 lines)

This is the most heavily trimmed file.

**What was preserved:**
- `Photographer(ABC)` and `rescale_image()` static method.
- `ControlPhotographer`, `DesktopPhotographer` — identical.
- `PhotographerDecorator` base — identical.
- `AnnotationDecorator` — `__init__()`, `draw_rectangles_controls()`, `_get_button_img()`, `_get_font()`, `get_annotation_dict()`, `capture_with_annotation_dict()`, `capture()`.
- `PhotographerFactory.create_screenshot()`.
- `PhotographerFacade` singleton pattern, `capture_app_window_screenshot()`, `capture_desktop_screen_screenshot()`, `capture_app_window_screenshot_with_annotation()`, `get_annotation_dict()`, `target_info_iou()`, `merge_target_info_list()`, `encode_image()`.

**What was changed:**
1. **Config decoupled** — `ufo_config.system.default_png_compress_level` → `DEFAULT_PNG_COMPRESS_LEVEL = 6` (module constant). Same for `annotation_font_size` and `annotation_colors`.
2. **`coordinate_adjusted()`** — Was a static method on `PhotographerDecorator`; now imported from `winactions._utils`.
3. **`encode_image()`** — Simplified to PNG-only (removed JPEG support, RGBA-to-RGB conversion with white background).
4. **Annotation types** — Only `"number"` style supported (removed `"letter"` branch and `number_to_letter()`).

**What was deleted and why:**
- **`RectangleDecorator`** — Rectangle overlay on screenshots, not needed for the core annotation workflow.
- **`TargetAnnotationDecorator`** — Annotates using `TargetInfo` objects instead of `UIAWrapper`; UFO-specific feature.
- **11 `PhotographerFacade` methods** — `capture_app_window_screenshot_with_rectangle()`, `capture_app_window_screenshot_with_rectangle_from_adjusted_coords()`, `capture_app_window_screenshot_with_annotation_dict()`, `capture_app_window_screenshot_with_point_from_path()`, `get_cropped_icons_dict()`, `concat_screenshots()`, `load_image()`, `image_to_base64()`, `control_iou()`, `merge_control_list()`, `encode_image_from_path()`, `capture_app_window_screenshot_with_target_list()`. These were UFO-framework-specific features not needed in a standalone library.

### 9. `_utils.py`

**UFO source:** `ufo/utils/__init__.py` (420 lines)
**winactions:** `src/winactions/_utils.py` (42 lines)

Only two functions were extracted with identical logic:

- **`is_json_serializable(obj)`** — Used in `executor.py` to validate command results.
- **`coordinate_adjusted(window_rect, control_rect)`** — Used in `photographer.py` and `executor.py` for coordinate math.

**Everything else was deleted** (18 functions): `print_with_color()`, `create_folder()`, `json_parser()`, `LazyImport`, `find_desktop_path()`, `get_hugginface_embedding()`, `decode_base64_image()`, `encode_image_from_path()`, `encode_image()`, etc. These were either UFO-framework-specific (colorama printing, HuggingFace embeddings, lazy imports) or image-processing utilities that were inlined into `photographer.py` or not needed.

---

## Purely New Files

These files have no direct UFO counterpart. They were designed from scratch based on the architecture plan, drawing inspiration from browser-use CLI, playwright-cli, and UFO's IOU merge algorithm.

### 10. `config.py`

**Line count:** 40

Replaces UFO's complex `config.config_loader.get_ufo_config()` system with a simple dataclass singleton:

```python
@dataclass
class ActionConfig:
    click_api: str = "click_input"
    after_click_wait: float = 0.0
    input_text_api: str = "type_keys"
    input_text_enter: bool = False
    input_text_inter_key_pause: float = 0.05
    pyautogui_failsafe: bool = False
```

- `configure(**kwargs)` — Creates and sets the global singleton.
- `get_action_config()` — Returns the current config; auto-creates default if not yet configured.
- Module-level `_config: Optional[ActionConfig] = None` for late-binding.

This is referenced by `controller.py` (5 config lookups), `session.py` (config initialization), and the lazy `_ensure_pywinauto_timings()` function.

### 11. `perception/provider.py`

**Line count:** 164

The pluggable perception backend system. Provides the "perception end" of the index protocol.

- **`StateProvider(Protocol)`** — Defines the detect interface: `detect(window) -> List[TargetInfo]`.
- **`UIAStateProvider`** — Default provider. Uses `ControlInspectorFacade.find_control_elements_in_descendants()` to scan the window control tree, then builds `TargetInfo` objects with 1-indexed IDs. Returns both the target list and raw `UIAWrapper` list (for `control_map` construction in `UIState`).
- **`DEFAULT_CONTROL_TYPES`** — 34 UIA control types (Button, Edit, TabItem, Document, ListItem, MenuItem, etc.) matching UFO's common set. Without this default list, the UIA backend's `CreateOrConditionFromArray` with an empty array returns zero results.
- **`CompositeStateProvider`** — Fuses multiple perception sources using IOU-based deduplication, re-assigning sequential IDs after merge.
- **`merge_by_iou(main, additional, threshold)`** — IOU deduplication algorithm adapted from UFO's `merge_target_info_list`.
- **`_iou(rect1, rect2)`** — Intersection-over-Union computation for `[left, top, right, bottom]` rectangles.

The `StateProvider` protocol is extensible. Three implementations exist:
- `UIAStateProvider` — Default, uses pywinauto UIA backend (Tier 1)
- `VisionStateProvider` — Multimodal model visual detection (Tier 2), see below
- `StructuralInferenceProvider` — LLM structural reasoning wrapping UIAStateProvider (Tier 1+), see below

`CompositeStateProvider` fuses any combination of these via IOU-based deduplication, maintaining a parallel `controls` list (UIAWrapper for UIA elements, None for vision/inferred) so the execution layer knows which fallback to use.

### 12. `perception/state.py`

**Line count:** 64

The atomic UI state snapshot — the complete output of the perception layer.

```python
@dataclass
class UIState:
    window_title: str
    window_handle: int
    process_name: str
    targets: List[TargetInfo]           # Indexed control list
    control_map: Dict[str, Any]         # id → UIAWrapper (internal, not serialized)
    screenshot_path: Optional[str]
    annotated_screenshot_path: Optional[str]
    timestamp: str
```

Key methods:
- **`to_text()`** — Produces human-readable output: `Window: "Title" (process)` followed by `[1] [Button] "Save"` lines. **Inferred/vision elements** (where `control_map[id]` is `None`) include their `rect` so the agent can use `drag-at`/`click-at` directly: `[305] [ColumnBorder] "Column border 1-2" rect=[-2294,850,-2290,900]`.
- **`to_json()`** — Produces agent-consumable JSON with window info, targets array, and screenshot path.
- **`resolve(target_id)`** — Maps an index number back to the actual `UIAWrapper` control object via `control_map`. Returns `None` for vision/inferred targets. This is the bridge from the execution layer's index input to the actual control.
- **`target_count`** — Property returning `len(targets)`.

### 12a. `perception/vision_provider.py` (Tier 2 — Vision Detection)

**Line count:** 316

A multimodal model-based perception source using the Anthropic Claude API to detect UI elements invisible to UIA (resize handles, column borders, icon-only buttons, canvas elements, splitters, custom controls).

- **`VisionStateProvider`** — Captures a window screenshot, sends it to Claude with a detection prompt, and parses the structured JSON response into `TargetInfo` objects with absolute screen coordinates.
- **`_capture_screenshot(window)`** — Captures the window, resizes if the longest side exceeds 1568px (Claude's internal resize limit), and returns `(base64, scale_x, scale_y)` for coordinate conversion.
- **`_call_model(image_b64)`** — Sends the screenshot to the Anthropic Messages API with `_DETECTION_PROMPT` and parses the response.
- **`_parse_response(response_text)`** — Strips markdown code fences, extracts JSON array, handles non-list responses gracefully.
- **`_DETECTION_PROMPT`** — Instructs the model to detect resize handles, column/row borders, icon-only buttons, canvas elements, splitter bars, custom controls, and return `[{name, type, rect}]` in image-relative pixel coordinates.

Key design decisions:
- **Coordinate pipeline:** Model returns image-space coords → multiply by `scale_x`/`scale_y` (if resized) → add `window.rectangle().left/top` → absolute screen coordinates.
- **No caching:** Every `detect()` call makes a fresh API request. Earlier file-based caching was removed for simplicity — the CLI's single-process-per-invocation model means caching adds complexity without benefit.
- **Graceful degradation:** On any exception, returns `([], [])` — the UIA primary provider still works.

### 12b. `perception/structural_provider.py` (Tier 1+ — Structural Inference)

**Line count:** 305

An LLM-based structural reasoning provider that wraps `UIAStateProvider` and infers hidden interactive elements from the UIA control list (pure text, no screenshots).

- **`StructuralInferenceProvider`** — Wraps any StateProvider (typically `UIAStateProvider`). Formats the UIA target list as text, sends it to Claude Haiku with an inference prompt, filters by confidence threshold, and appends inferred elements to the UIA results.
- **`_format_uia_data(targets)`** — Formats targets as `[id] [Type] "Name" rect=[l,t,r,b]` lines.
- **`_call_model(uia_text)`** — Sends the UIA text to the Anthropic Messages API with `_INFERENCE_PROMPT`.
- **`_parse_response(response_text)`** — Strips markdown fences, extracts the outermost `[...]` bracket pair (handles models that append reasoning text after JSON), parses JSON.
- **`_INFERENCE_PROMPT`** — Instructs the model to infer column borders, row borders, table resize handles, splitter bars, and panel resize grips. Requires `derived_from` field showing the coordinate formula. Includes confidence calibration guidelines.

Key design decisions:
- **Text-only input:** ~10x cheaper than vision ($0.001-0.003 vs $0.01), ~3x faster (0.5-1.5s vs 2-5s).
- **Pixel-precise coordinates:** Derived from UIA rect arithmetic (e.g., `border_x = HeaderItem.rect[2]`), not visual estimation.
- **Confidence filtering:** Default threshold 0.7 removes low-confidence hallucinations. The prompt calibrates confidence ranges for different UI patterns.
- **Bracket extraction:** Handles LLM responses like `[...] \n\nI found these elements because...` by extracting only the outermost `[...]`.
- **Graceful degradation:** On LLM failure, returns UIA-only results — never breaks the primary perception.

### 12c. `perception/__init__.py`

**Line count:** 31

Updated to conditionally export both new providers:

```python
try:
    from winactions.perception.vision_provider import VisionStateProvider
except ImportError:
    VisionStateProvider = None

try:
    from winactions.perception.structural_provider import StructuralInferenceProvider
except ImportError:
    StructuralInferenceProvider = None
```

Both providers require the optional `anthropic` package. The conditional import ensures the rest of the package works without it installed.

### 13. `cli/app.py`

**Line count:** 713

The Click-based CLI entry point, inspired by browser-use CLI's command structure. Registers 20+ commands organized into groups:

**Perception commands:**
- `winctl state [--screenshot] [--annotated]` — Shows indexed control list for the current window.
- `winctl windows` — Lists all visible desktop windows.
- `winctl inspect <index>` — Shows detailed properties of a single control.
- `winctl screenshot [path]` — Takes a screenshot of the current window.

**Execution commands:**
- `winctl click <index>` — Click a control by index.
- `winctl dblclick <index>` — Double-click.
- `winctl rightclick <index>` — Right-click.
- `winctl input <index> <text>` — Set text on a control.
- `winctl type <text>` — Type text to the focused element via pyautogui.
- `winctl keys <keys>` — Send keyboard keys (e.g., "ctrl+a", "Enter").
- `winctl scroll <index> <direction> [amount]` — Scroll a control.
- `winctl select <index> <value>` — Select a dropdown value.

**Window management:**
- `winctl focus <window>` — Focus a window by title, process name, or index.
- `winctl launch <app>` — Launch an application.
- `winctl close` — Close the current window.

**Coordinate mode:**
- `winctl click-at <x> <y>` — Click at absolute screen coordinates.
- `winctl drag-at <x1> <y1> <x2> <y2>` — Drag between absolute coordinates.

**Data extraction:**
- `winctl get text <index>` — Get text content of a control.
- `winctl get rect <index>` — Get bounding rectangle.
- `winctl get value <index>` — Get control value (toggle state, slider position).

**Wait:**
- `winctl wait [seconds]` — Wait for specified seconds.
- `winctl wait --visible <index>` — Wait until control becomes visible.
- `winctl wait --enabled <index>` — Wait until control becomes enabled.

**Global options:**
- `--json` — JSON output mode for all commands.
- `--session <name>` — Named session for state persistence.
- `--window <name>` — Target window by title/process substring.
- `--return-state` — Auto-return fresh state after execution commands.
- `--infer` — Enable Tier 1+ LLM structural inference (hidden elements like column borders, resize handles).
- `--vision` — Enable Tier 2 vision-based UI detection (icon-only buttons, canvas elements).
- `--vision-api-key` / `--vision-base-url` — API configuration for inference/vision providers.

Key design patterns:
- Module-level lazy singleton `_session: Optional[DesktopSession] = None` with `_get_session()` helper.
- `_ensure_window()` — Auto-focuses the foreground window if none is set.
- `_ensure_state()` — Auto-refreshes state if none exists.
- UTF-8 reconfiguration in `main()` for Windows GBK console compatibility.

### 14. `cli/session.py`

**Line count:** 430

The glue layer connecting all components. `DesktopSession` manages cross-command state:

```python
class DesktopSession:
    inspector: ControlInspectorFacade
    provider: StateProvider  # UIAStateProvider | StructuralInferenceProvider | CompositeStateProvider
    window: Optional[UIAWrapper]
    state: Optional[UIState]
    puppeteer: Optional[AppPuppeteer]
    _controls: List[Any]
```

**Constructor (`__init__`):**
- Accepts `vision=False`, `infer=False`, `vision_api_key`, `vision_base_url` parameters.
- **Provider composition logic:**
  - Default: `UIAStateProvider`
  - `infer=True`: `StructuralInferenceProvider(wraps UIAStateProvider)`
  - `vision=True`: `CompositeStateProvider(UIAStateProvider, VisionStateProvider)`
  - `infer=True, vision=True`: `CompositeStateProvider(StructuralInferenceProvider, VisionStateProvider)`

**Class method:**
- `create(vision, infer, vision_api_key, vision_base_url)` — Factory that initializes inspector and provider with the specified perception tier configuration.

**Window management:**
- `list_windows()` — Uses inspector to enumerate desktop windows, returns list of dicts with id/title/process.
- `focus_window(identifier)` — Matches by title substring, process name, or window list index.
- `focus_foreground()` — Uses `win32gui.GetForegroundWindow()` to auto-detect the active window.
- `_set_window(window)` — Sets the current window, creates/updates the `AppPuppeteer`, clears old state.
- `launch_app(app)` — Starts an application via `subprocess.Popen`, waits, then finds and focuses its window.
- `close_window()` — Closes the current window via `win32gui.PostMessage(WM_CLOSE)`.

**State (perception):**
- `refresh_state(screenshot=False)` — Calls `provider.detect(window)` to get targets and controls, builds the `control_map` (id→UIAWrapper), optionally captures screenshots, and constructs a `UIState` snapshot.
- `_capture_screenshot(annotated=False)` — Uses `PhotographerFacade` to capture and optionally annotate.

**Execution:**
- `execute_on_target(command_name, target_id, params)` — Resolves the index to a `UIAWrapper` via `state.resolve()`. If the control exists (UIA Tier 1), creates a `ControlReceiver` and calls `puppeteer.execute_command()`. If the control is `None` (vision/inferred Tier 2), automatically computes the bbox center from `TargetInfo.rect` and falls back to `click_on_coordinates`. The agent only ever outputs an index number — it never predicts coordinates.
- `execute_global(command_name, params)` — For commands that don't target a specific control (e.g., `type`, `keyboard_input`, `click_on_coordinates`, `drag_on_coordinates`).

**Persistence:**
- `save(path)` / `load(path)` — Serializes/deserializes session state (window handle, title, targets) to JSON files in the temp directory for cross-process sharing.

### 15. `cli/formatter.py`

**Line count:** 74

Dual-mode output formatting (text for humans, JSON for agents):

- **`_safe_print(text, file)`** — Catches `UnicodeEncodeError` and falls back to `errors="replace"` encoding. Essential for Windows consoles using GBK or other non-UTF-8 encodings that can't display Unicode characters (e.g., Braille characters in window titles).
- **`output(data, as_json)`** — Polymorphic output. JSON mode: serializes dicts/lists with `json.dumps()`, parses JSON strings for pretty-printing, wraps plain strings in `{"message": ...}`. Text mode: prints strings directly, formats dicts as `key: value`, lists as one item per line.
- **`output_error(message, as_json)`** — Writes errors to stderr as `{"error": ...}` (JSON) or `Error: ...` (text).
- **`format_windows_list(windows, as_json)`** — Formats window list as JSON string or `[idx] title (process)` lines.

### 16. `__init__.py`

**Line count:** 55

The public API surface for library (non-CLI) usage. Exports:

- Config: `ActionConfig`, `configure`, `get_action_config`
- Targets: `TargetKind`, `TargetInfo`, `TargetRegistry`
- Models: `Result`, `ResultStatus`, `ActionCommandInfo`, `ListActionCommandInfo`, `BaseControlLog`
- Command layer: `CommandBasic`, `ReceiverBasic`, `ReceiverFactory`, `AppPuppeteer`, `ReceiverManager`, `ActionExecutor`
- Control layer: `ControlReceiver`, `TextTransformer`, `ControlInspectorFacade`

**Critical side-effect import:**
```python
import winactions.control.controller  # noqa: F401  — triggers @ControlReceiver.register decorators
```

Without this import, the 16+ command classes defined in `controller.py` would never have their `@ControlReceiver.register` decorators executed, leaving `ControlReceiver._command_registry` empty and making `puppeteer.create_command()` fail for all commands.

### 17. `pyproject.toml`

Package definition with hatchling build system:

- **Core dependencies:** pydantic>=2.0, pywinauto>=0.6.8, pyautogui>=0.9.54, psutil>=5.9, comtypes>=1.2, uiautomation>=2.0.18
- **Optional groups:** `screenshot` (Pillow), `cli` (click), `rich` (rich), `dev` (all of the above + pytest)
- **Entry point:** `winctl = winactions.cli.app:main`

### 18. `WINCTL_SKILL.md`

**Line count:** 228 (rewritten after E2E testing)

Agent operation manual for AI agents (Claude Code, Cursor, etc.). Comprehensively rewritten to document the full three-tier perception system:

- **Perception Tiers table:** Tier 1 (UIA default), Tier 1+ (`--infer` LLM inference), Tier 2 (`--vision` visual detection).
- **Inferred/vision element output format:** `[305] [ColumnBorder] "..." rect=[-2294,850,-2290,900]` — elements with rect for coordinate commands.
- Core workflow: Perceive → Decide → Execute → Verify cycle.
- Critical rules #1-6: Including negative coordinate `--` separator and options-before-`--` rule.
- Complete command reference: All perception, execution, coordinate, data extraction, wait, and window management commands.
- **Decision Guide flowchart** for agents: UIA tree? → Tier 1 / Not in UIA → `--infer` / `--vision` / `inspect` spatial reasoning.
- Global options: `--window`, `--infer`, `--vision`, `--json`, `--session`, `--return-state`, `--vision-api-key`, `--vision-base-url`.
- Error handling strategies and output format examples.

---

## How the Layers Work Together

### The Index Protocol Flow

The central insight is that **index numbers are ephemeral references** that link perception output to execution input. Here's how a typical `winctl click 3` works end-to-end:

#### Perception Tiers

```
Tier 1 (default):   UIAStateProvider           → UIA controls only (free, instant)
Tier 1+ (--infer):  StructuralInferenceProvider → UIA + LLM-inferred hidden elements ($0.001-0.003, 0.5-1.5s)
Tier 2 (--vision):  CompositeStateProvider      → UIA + vision-detected elements ($0.01, 2-5s)
Combined:           CompositeStateProvider(StructuralInferenceProvider, VisionStateProvider) → all three
```

#### Step 1: Perception (`winctl state`)

```
DesktopSession.refresh_state()
  │
  ├─→ UIAStateProvider.detect(window)
  │     │
  │     ├─→ ControlInspectorFacade.find_control_elements_in_descendants(window, DEFAULT_CONTROL_TYPES)
  │     │     └─→ UIABackendStrategy (COM FindAllBuildCache with property caching)
  │     │         └─→ Returns List[UIAWrapper] (raw controls)
  │     │
  │     └─→ For each control: ControlInspectorFacade.get_control_info(control)
  │           └─→ Builds TargetInfo(id="1", name="Save", type="Button", rect=[...])
  │           └─→ Returns (List[TargetInfo], List[UIAWrapper])
  │
  ├─→ Builds control_map: {"1": UIAWrapper_for_save_button, "2": ...}
  │
  └─→ Creates UIState(targets=[...], control_map={...}, ...)
        │
        ├─→ UIState.to_text()  →  "[1] [Button] \"Save\"\n[2] [Edit] \"...\""
        └─→ UIState.to_json()  →  {"targets": [...], "window": "..."}
```

#### Step 2: Decision (External)

The LLM agent or human reads the indexed control list and decides: "I need to click control #3."

#### Step 3: Execution (`winctl click 3`)

```
DesktopSession.execute_on_target("click_input", "3", {})
  │
  ├─→ UIState.resolve("3")
  │     └─→ control_map["3"] → UIAWrapper or None
  │
  ├─ [If control is not None — Tier 1: UIA path]
  │   ├─→ ReceiverManager.create_ui_control_receiver(control, window)
  │   │     └─→ UIControlReceiverFactory.create_receiver(control, window)
  │   │           └─→ ControlReceiver(control, window)
  │   │                 └─→ _ensure_pywinauto_timings() (lazy init)
  │   │
  │   └─→ AppPuppeteer.execute_command("click_input", {})
  │         ├─→ ClickInputCommand.execute()
  │         └─→ ControlReceiver.click_input(**params)
  │               └─→ control.click_input(button="left", double=False)
  │
  └─ [If control is None — Tier 2: Vision/Inferred fallback]
      ├─→ Find TargetInfo by id → get rect
      ├─→ center_x = (rect[0] + rect[2]) // 2
      ├─→ center_y = (rect[1] + rect[3]) // 2
      └─→ execute_global("click_on_coordinates", {x, y})
            └─→ pyautogui.click(center_x, center_y)
```

The agent only ever outputs an index number. The framework automatically determines whether to use pywinauto (UIA control) or coordinate-based clicking (vision/inferred element).

### The Command Registration Chain

The command pattern registration is a critical chain of side effects:

1. `command/basic.py` defines `ReceiverBasic` with `_command_registry` dict and `register()` classmethod.
2. `control/controller.py` defines `ControlReceiver(ReceiverBasic)` with its own `_command_registry: Dict = {}` (isolated from parent).
3. Each command class is decorated with `@ControlReceiver.register`:
   ```python
   @ControlReceiver.register
   class ClickInputCommand(ControlCommand):
       ...
   ```
   This populates `ControlReceiver._command_registry["click_input"] = ClickInputCommand`.
4. `control/controller.py` also defines `UIControlReceiverFactory` decorated with `@ReceiverManager.register`, which registers the factory in `ReceiverManager._receiver_factory_registry`.
5. `__init__.py` imports `winactions.control.controller` to trigger all these registrations at package import time.
6. When `ReceiverManager.create_ui_control_receiver()` is called, it creates a `ControlReceiver` via the registered factory, and `ReceiverManager._update_receiver_registry()` copies all command names from the receiver's registry to its internal lookup.

### Session Lifecycle

```
winctl state                     winctl click 3
    │                                │
    ▼                                ▼
_get_session()                  _ensure_state()
    │                                │
    ▼                                ├─→ _ensure_window()
DesktopSession.create()              │      └─→ focus_foreground() if no window
    │                                │
    ▼                                ├─→ refresh_state() if no state
    inspector = InspectorFacade()    │
    provider = UIAStateProvider()    │
    window = None                    ▼
    state = None              execute_on_target("click_input", "3", {})
    puppeteer = None                 │
                                     ▼
                              state.resolve("3") → UIAWrapper
                              puppeteer.execute_command(...)
```

### Dual Output Mode

Every CLI command supports both human-readable and agent-consumable output:

```
winctl state          →  [1] [Button] "Save"       (human reads this)
winctl --json state   →  {"targets": [...]}         (agent parses this)
```

The `--json` flag propagates through `ctx.obj["json"]` and is passed to `output()` and `output_error()` in `formatter.py`.

---

## Cross-Cutting Changes Applied to All UFO-Derived Files

These patterns were applied consistently across every file copied from UFO:

1. **Import rewiring** — Every `ufo.*` import path was changed to `winactions.*` equivalents. Every `config.config_loader.get_ufo_config` was replaced with `winactions.config.get_action_config`.

2. **Bare `except:` to `except Exception:`** — Applied in `puppeteer.py`, `executor.py`, `controller.py`, `inspector.py`. This is a Python best practice that prevents catching `SystemExit`, `KeyboardInterrupt`, etc.

3. **`== None` to `is None`** — Applied in `inspector.py` where encountered. PEP 8 recommends identity comparison for None.

4. **Docstring compression** — Every multi-line docstring with `:param`, `:return`, and detailed descriptions was reduced to a single-line summary. This accounts for the majority of line count reduction across all files (e.g., `controller.py` went from 1205 to 720 lines, `inspector.py` from 706 to 532 lines).

5. **Copyright headers removed** — UFO's `# Copyright (c) Microsoft Corporation. # Licensed under the MIT License.` headers were removed from individual files.

6. **Module docstrings added** — Each file received a module-level docstring explaining its purpose and noting its UFO origin (e.g., `"""Adapted from ufo/automator/puppeteer.py with COM receiver support removed."""`).

7. **Conditional imports for cross-platform compatibility** — The pattern `if TYPE_CHECKING or platform.system() == "Windows": import pywinauto` was applied throughout, allowing the package to be imported on non-Windows for type checking and testing of non-Windows-specific code paths.

---

## Key Design Decisions

### 1. `_command_registry` Isolation

`ControlReceiver` must define its own `_command_registry: Dict[str, Type[CommandBasic]] = {}`. Without this, it would share the parent class's empty dict, and subclass registrations wouldn't work correctly. This is a subtle Python class variable inheritance behavior.

### 2. Lazy pywinauto Timing Initialization

UFO set pywinauto timings at module import time, which caused side effects on import and required the config to be available before any winactions code was imported. The `_ensure_pywinauto_timings()` pattern defers this to first use, allowing:
```python
from winactions import configure
configure(click_api="click")  # Set config AFTER import
# ... later, first ControlReceiver.__init__() triggers timing setup
```

### 3. Index Lifecycle

Indexes are ephemeral. Every call to `refresh_state()` scans the UI tree and assigns new sequential IDs. Old indexes become invalid. The SKILL.md emphasizes: "Always run `state` before acting. Re-run `state` after every action."

### 4. StateProvider as Protocol

`StateProvider` is a `Protocol` (structural typing), not an abstract base class. This means any class with a `detect(window)` method satisfies the interface without inheriting from it. Three implementations exist: `UIAStateProvider` (Tier 1), `StructuralInferenceProvider` (Tier 1+, wraps UIA), and `VisionStateProvider` (Tier 2, standalone).

### 4a. Provider Composition Pattern

The `--infer` and `--vision` flags compose providers in a layered architecture:
- `StructuralInferenceProvider` wraps `UIAStateProvider` (decorator pattern) — it calls the inner provider first, then adds inferred elements.
- `CompositeStateProvider` fuses independent providers via IOU deduplication — it calls each provider separately and merges results.
- Combined: `CompositeStateProvider(StructuralInferenceProvider(UIA), Vision)` gives all three tiers.

### 4b. Graceful Degradation

Both `VisionStateProvider` and `StructuralInferenceProvider` catch all exceptions and fall back to their base results. If the LLM API is down or returns garbage, the system silently degrades to Tier 1 (UIA-only). This is critical for production reliability.

### 5. Session Auto-Detection

The CLI auto-detects the foreground window when no window is explicitly focused. This makes the simplest workflow `winctl state` (auto-focuses whatever is in front) without requiring `winctl focus` first.

### 6. UTF-8 Safety on Windows

Windows consoles may use GBK, Shift-JIS, or other non-UTF-8 encodings. Two safety mechanisms:
- `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` in `main()`.
- `_safe_print()` catches `UnicodeEncodeError` and falls back to `errors="replace"`.

---

## Source File Mapping Table

| winactions File | UFO Source | Operation | Key Changes |
|---|---|---|---|
| `_version.py` | — | New | — |
| `config.py` | — | New | Replaces `get_ufo_config()` |
| `_utils.py` | `ufo/utils/__init__.py` | Extract 2 functions | 18 functions deleted |
| `targets.py` | `ufo/.../schemas/target.py` | Verbatim copy | Docstring trimming only |
| `models.py` | `ufo/.../schemas/actions.py` + `aip/messages.py` | Merge + inline | Result simplified, rich optional |
| `command/basic.py` | `ufo/automator/basic.py` | Verbatim copy | Docstring trimming only |
| `command/puppeteer.py` | `ufo/automator/puppeteer.py` | Trim COM | 6 methods/properties deleted |
| `command/executor.py` | `ufo/automator/action_execution.py` | Import rewire | Near-exact port |
| `control/controller.py` | `ufo/automator/ui_control/controller.py` | Config decouple | 5 config refs + lazy init |
| `control/inspector.py` | `ufo/automator/ui_control/inspector.py` | Verbatim copy | Debug prints removed |
| `screenshot/photographer.py` | `ufo/automator/ui_control/screenshot.py` | Heavy trim | ~900 lines deleted |
| `perception/provider.py` | — | New | StateProvider protocol + IOU merge + CompositeStateProvider with controls |
| `perception/state.py` | — | New | UIState snapshot dataclass, to_text() with rect for non-UIA elements |
| `perception/vision_provider.py` | — | New | Tier 2 vision detection via Anthropic Claude API |
| `perception/structural_provider.py` | — | New | Tier 1+ structural inference via Claude Haiku |
| `perception/__init__.py` | — | New | Conditional exports for optional providers |
| `cli/app.py` | — | New | Click CLI with 20+ commands, --infer/--vision/--window flags |
| `cli/session.py` | — | New | Cross-command state, provider composition, vision fallback |
| `cli/formatter.py` | — | New | Dual-mode output formatting |
| `__init__.py` | — | New | Public API + registration trigger |
| `WINCTL_SKILL.md` | — | New → Rewritten | Agent operation manual (three-tier perception) |
| `pyproject.toml` | — | New | Package definition |
