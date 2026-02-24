# Plan: `winactions` — Windows UI Automation Toolkit for AI Agents (已实现部分)

> 本文件提取自原始 plan，仅保留已实际落地的内容。未实现的条目已标注并在文末汇总。

## Context

基于对 browser-use/playwright-cli 的 CLI 模式分析和 UFO 源码的深入研究，设计一个完整的 Windows 桌面 UI 自动化工具包。核心设计理念：

**索引编号是感知层、决策层、执行层之间的共享通信协议。** CLI 提供协议的两端接口——感知端（`winctl state`）和执行端（`winctl click/input/...`）。决策在 CLI 之外由 Agent 或人完成。

### 架构总览

```
感知端 CLI                    决策层（外部）              执行端 CLI
──────────                   ──────────               ──────────
winctl state ──→ 索引列表 ──→ LLM/人 分析判断 ──→ winctl click 3
winctl screenshot            （选择目标+动作）         winctl input 2 "hello"
winctl windows                                        winctl keys "ctrl+s"
```

### 参考项目

- **browser-use CLI**: 命令结构、state/click/input 模式、SKILL.md
- **playwright-cli**: 元素引用(e1,e2)、snapshot 命令、session 管理
- **UFO**: Command/Receiver 模式、Inspector、ControlReceiver(18 commands)、IOU 融合算法

## 包结构

```
D:\src\Kilodex\winactions\
├── pyproject.toml
├── SKILL.md                          # Agent 操作手册（面向 Claude Code/Cursor）
├── src/
│   └── winactions/
│       ├── __init__.py               # 公共 API 导出
│       ├── _version.py               # __version__ = "0.1.0"
│       ├── config.py                 # ActionConfig dataclass
│       ├── _utils.py                 # 工具函数
│       │
│       │── targets.py                # TargetKind, TargetInfo, TargetRegistry
│       │── models.py                 # Result, ResultStatus, ActionCommandInfo, ...
│       │
│       ├── command/                  # === 执行层 ===
│       │   ├── __init__.py
│       │   ├── basic.py              # ReceiverBasic, CommandBasic, ReceiverFactory
│       │   ├── puppeteer.py          # AppPuppeteer, ReceiverManager
│       │   └── executor.py           # ActionExecutor
│       │
│       ├── control/                  # === 控件操作 + 检查 ===
│       │   ├── __init__.py
│       │   ├── controller.py         # ControlReceiver + 18 Command 类 + TextTransformer
│       │   └── inspector.py          # ControlInspectorFacade, BackendFactory
│       │
│       ├── perception/               # === 感知层 ===
│       │   ├── __init__.py           # 导出 StateProvider, UIAStateProvider, VisionStateProvider, StructuralInferenceProvider, UIState
│       │   ├── provider.py           # StateProvider 协议 + UIAStateProvider + CompositeStateProvider
│       │   ├── state.py              # UIState 快照 + 索引分配
│       │   ├── vision_provider.py    # VisionStateProvider (Tier 2 视觉检测)
│       │   └── structural_provider.py # StructuralInferenceProvider (Tier 1+ 结构推理)
│       │
│       ├── screenshot/               # === 截图（可选）===
│       │   ├── __init__.py
│       │   └── photographer.py       # PhotographerFacade
│       │
│       └── cli/                      # === CLI 层 ===
│           ├── __init__.py
│           ├── app.py                # winctl 入口 + 命令注册 + --session daemon 路由
│           ├── session.py            # DesktopSession (跨命令状态)
│           ├── session_dispatch.py   # 命令分发层（传输无关）
│           ├── session_server.py     # TCP 服务器 + PID file 管理
│           ├── session_client.py     # TCP 客户端（thin CLI 转发）
│           └── formatter.py          # 输出格式化 (text/json)
│
└── tests/
    ├── test_import.py                # 基础 import 测试
    ├── test_config.py
    ├── test_targets.py
    ├── test_cli_smoke.py             # CLI 冒烟测试
    └── test_session_daemon.py        # Session daemon 单元测试 (dispatch, protocol, client)
```

## Phase 1: 基础设施（无依赖）— 已实现

### 1.1 pyproject.toml — 已实现

```toml
[project]
name = "winactions"
dynamic = ["version"]
requires-python = ">=3.10"
dependencies = [
    "pydantic>=2.0",
    "pywinauto>=0.6.8",
    "pyautogui>=0.9.54",
    "psutil>=5.9",
    "comtypes>=1.2",
    "uiautomation>=2.0.18",
]

[project.optional-dependencies]
screenshot = ["Pillow>=10.0"]
cli = ["click>=8.0"]
rich = ["rich>=13.0"]
dev = ["pytest", "Pillow>=10.0", "click>=8.0", "rich>=13.0"]

[project.scripts]
winctl = "winactions.cli.app:main"  # CLI 入口
```

### 1.2 _version.py — 已实现

```python
__version__ = "0.1.0"
```

### 1.3 config.py — 已实现（替代 UFO config 系统）

```python
@dataclass
class ActionConfig:
    click_api: str = "click_input"
    after_click_wait: float = 0.0
    input_text_api: str = "type_keys"
    input_text_enter: bool = False
    input_text_inter_key_pause: float = 0.05
    pyautogui_failsafe: bool = False

# 模块级 late-binding
_config: ActionConfig | None = None

def configure(**kwargs) -> ActionConfig: ...
def get_action_config() -> ActionConfig: ...
```

### 1.4 _utils.py — 已实现（从 `ufo/utils/__init__.py` 提取 2 个纯函数）

- `is_json_serializable(obj)`
- `coordinate_adjusted(window_rect, control_rect)`

## Phase 2: 共享协议层 — 已实现

索引编号作为感知→决策→执行的通信协议，`targets.py` 和 `models.py` 是协议的数据定义。

### 2.1 targets.py — 已实现（复制自 `ufo/agents/processors/schemas/target.py`）

- 修改: 无。仅依赖 pydantic + logging。
- 类: `TargetKind(Enum)`, `TargetInfo(BaseModel)`, `TargetRegistry`
- `TargetInfo.id` 就是索引编号（字符串数字 "1", "2", ...）

### 2.2 models.py — 已实现（复制自 `ufo/agents/processors/schemas/actions.py` + `aip/messages.py`）

- 内联 `ResultStatus(str, Enum)` 和 `Result(BaseModel)` (从 aip/messages.py)
- 替换 import: `from winactions.targets import TargetInfo`
- 删除 `from rich...` 硬依赖，`color_print()` 改为 try/except optional
- 删除 `from ufo.agents.presenters import PresenterFactory`

## Phase 3: 执行层（从 UFO 提取）— 已实现

执行层负责：根据索引编号找到控件 → 执行动作 → 返回结果。

### 3.1 command/basic.py — 已实现（原样复制自 `ufo/automator/basic.py`）

- 纯 ABC，零外部依赖
- 类: `ReceiverBasic`, `CommandBasic`, `ReceiverFactory`
- 关键: `@Receiver.register` 装饰器 + `_command_registry` 类变量

### 3.2 command/puppeteer.py — 已实现（复制自 `ufo/automator/puppeteer.py`，去除 COM）

- 删除 `from ufo.automator.app_apis.basic import WinCOMReceiverBasic`
- import 改为 `from winactions.command.basic import ...`
- **AppPuppeteer**: 删除 `full_path`, `save`, `save_to_xml`, `close` (COM 方法)
- **ReceiverManager**: 删除 `com_receiver` property, `create_api_receiver` method

### 3.3 control/controller.py — 已实现（复制自 `ufo/automator/ui_control/controller.py`，配置解耦）

来源: 1205 行，**核心难点**

修改:
1. `from config.config_loader import get_ufo_config` → `from winactions.config import get_action_config`
2. 其余 import 改为 winactions 内部路径
3. 删除模块级 `ufo_config = get_ufo_config()` 及 pywinauto timing 设置
4. 新增 `_ensure_pywinauto_timings()` 延迟初始化，在 `ControlReceiver.__init__` 中调用
5. 5 处 config 引用替换:
   - `ufo_config.system.click_api` → `get_action_config().click_api`
   - `ufo_config.system.input_text_inter_key_pause` → `get_action_config().input_text_inter_key_pause`
   - `ufo_config.system.input_text_api` → `get_action_config().input_text_api`
   - `ufo_config.system.input_text_enter` → `get_action_config().input_text_enter`
   - `pyautogui.FAILSAFE = False` → `pyautogui.FAILSAFE = get_action_config().pyautogui_failsafe`
6. 保留全部 18+ Command 类和 TextTransformer

### 3.4 command/executor.py — 已实现（复制自 `ufo/automator/action_execution.py`）

- 替换 3 个 import 路径
- `utils.X()` → 直接调用 `from winactions._utils import ...`

### 3.5 control/inspector.py — 已实现（原样复制自 `ufo/automator/ui_control/inspector.py`）

- 零 UFO 内部依赖
- 类: `BackendStrategy(ABC)`, `UIABackendStrategy`, `Win32BackendStrategy`, `BackendFactory`, `ControlInspectorFacade`

## Phase 4: 感知层（新建）— 已实现

感知层负责：扫描 UI → 分配索引编号 → 输出索引化的控件列表。

### 4.1 perception/provider.py — 已实现

```python
from typing import Protocol, List
from winactions.targets import TargetInfo, TargetKind

class StateProvider(Protocol):
    """感知源接口——可插拔的控件检测后端"""
    def detect(self, window) -> List[TargetInfo]: ...

class UIAStateProvider:
    """基于 pywinauto UIA 的感知源（默认）"""

    DEFAULT_CONTROL_TYPES = [
        "Button", "Edit", "TabItem", "Document", "ListItem",
        "MenuItem", "ScrollBar", "TreeItem", "Hyperlink", "ComboBox",
        "RadioButton", "CheckBox", "Slider", "Spinner", "DataItem",
        "Custom", "Group", "HeaderItem", "Header", "SplitButton",
        "MenuBar", "ToolBar", "Text", "Pane", "Window", "Table",
        "TitleBar", "Image", "List", "DataGrid", "Tree", "Tab",
    ]

    def __init__(self, inspector: ControlInspectorFacade, control_type_list=None):
        self.inspector = inspector
        self.control_type_list = control_type_list or DEFAULT_CONTROL_TYPES

    def detect(self, window) -> tuple[List[TargetInfo], List[UIAWrapper]]:
        """扫描窗口控件树，返回 (targets, controls) 带 1-indexed 索引"""
        controls = self.inspector.find_control_elements_in_descendants(
            window, control_type_list=self.control_type_list
        )
        targets = []
        for i, control in enumerate(controls):
            info = self.inspector.get_control_info(control)
            targets.append(TargetInfo(
                kind=TargetKind.CONTROL,
                id=str(i + 1),  # 1-indexed 索引编号
                name=info.get("control_text", ""),
                type=info.get("control_type", ""),
                rect=(list(info["control_rect"]) if info.get("control_rect") else None),
            ))
        return targets, controls

class CompositeStateProvider:
    """融合多个感知源（参考 UFO 的 IOU 去重融合），同时维护 controls 列表"""

    def __init__(self, primary: StateProvider, *additional: StateProvider,
                 iou_threshold: float = 0.1):
        self.primary = primary
        self.additional = additional
        self.iou_threshold = iou_threshold

    def detect(self, window) -> tuple[List[TargetInfo], List]:
        primary_targets, primary_controls = self.primary.detect(window)
        merged_targets = list(primary_targets)
        merged_controls = list(primary_controls)
        for provider in self.additional:
            extra_targets, extra_controls = provider.detect(window)
            merged_targets, merged_controls = _merge_by_iou_with_controls(
                merged_targets, merged_controls,
                extra_targets, extra_controls,
                self.iou_threshold,
            )
        # 融合后重新分配连续 ID
        for i, t in enumerate(merged_targets):
            t.id = str(i + 1)
        return merged_targets, merged_controls

def merge_by_iou(main, additional, threshold) -> List[TargetInfo]:
    """IOU 去重融合，复用 UFO 的 merge_target_info_list 算法"""
    merged = main.copy()
    for extra in additional:
        is_overlapping = False
        if extra.rect:
            for m in main:
                if m.rect and _iou(m.rect, extra.rect) > threshold:
                    is_overlapping = True
                    break
        if not is_overlapping:
            merged.append(extra)
    return merged

def _iou(rect1, rect2) -> float:
    """Intersection over Union for [left, top, right, bottom] rects."""
    ...
```

实现备注：
- 相比原始 plan，`UIAStateProvider.detect()` 的返回值从 `List[TargetInfo]` 改为 `tuple[List[TargetInfo], List[UIAWrapper]]`，同时返回原始控件列表，以便 `DesktopSession` 构建 `control_map`。
- 新增了 `DEFAULT_CONTROL_TYPES` 列表（34 种 UIA 控件类型），解决了原始 plan 中 `control_type_list or []` 导致空数组无法匹配任何控件的问题。
- `CompositeStateProvider.detect()` 同时返回 `(targets, controls)` 并使用 `_merge_by_iou_with_controls()` 在融合过程中保持 controls 列表同步。UIA 元素的 control 是 `UIAWrapper`，视觉/推断元素的 control 是 `None`。
- 新增 `_merge_by_iou_with_controls()` 函数，与 `merge_by_iou()` 算法相同，但在锁步中维护 controls 列表。

### 4.2 perception/state.py — 已实现

```python
@dataclass
class UIState:
    """原子化 UI 状态快照——感知层的完整输出"""
    window_title: str
    window_handle: int
    process_name: str
    targets: List[TargetInfo]           # 索引化的控件列表
    control_map: Dict[str, Any]         # id → UIAWrapper (内部用, 不序列化)
    screenshot_path: Optional[str]      # 截图路径
    annotated_screenshot_path: Optional[str]  # 标注截图路径
    timestamp: str

    def to_text(self, verbose: bool = False) -> str:
        """文本格式输出（CLI 默认）

        Default (compact): [id] [type] "name" — 不含 rect。
        Verbose: 所有 target 附带 rect。
        视觉/推断元素（无 UIAWrapper）始终附带 rect，以便 agent 直接使用 drag-at/click-at。
        """
        lines = [f'Window: "{self.window_title}" ({self.process_name})']
        for t in self.targets:
            ctrl = self.control_map.get(t.id)
            if verbose and t.rect:
                lines.append(f'[{t.id}] [{t.type}] "{t.name}" rect={t.rect}')
            elif ctrl is None and t.rect:
                lines.append(f'[{t.id}] [{t.type}] "{t.name}" rect={t.rect}')
            else:
                lines.append(f'[{t.id}] [{t.type}] "{t.name}"')
        return "\n".join(lines)

    def to_json(self, verbose: bool = False) -> dict:
        """JSON 格式输出（Agent 用）

        Default (compact): 仅含 id, name, type — 不含 rect。
        Verbose: 包含 rect。
        视觉/推断元素始终包含 rect。
        """
        targets_out = []
        for t in self.targets:
            ctrl = self.control_map.get(t.id)
            if verbose or (ctrl is None and t.rect):
                targets_out.append(t.model_dump(include={"id", "name", "type", "rect"}))
            else:
                targets_out.append(t.model_dump(include={"id", "name", "type"}))
        return {
            "window": self.window_title,
            "handle": self.window_handle,
            "process": self.process_name,
            "targets": targets_out,
            "screenshot": self.screenshot_path,
            "annotated_screenshot": self.annotated_screenshot_path,
            "timestamp": self.timestamp,
        }

    def resolve(self, target_id: str) -> Any:
        """根据索引编号解析为实际 UIAWrapper 控件"""
        return self.control_map.get(target_id)
```

实现备注（compact state 优化）：
- **Session 模式驱动的设计变更**: Session daemon 使后续查询仅需 ~5ms TCP 往返，因此默认 state 输出应精简——agent 按需查询详细信息（`inspect`/`get rect`/`get text`/`get value`）
- **默认紧凑输出**: `to_text()`/`to_json()` 默认省略 rect，仅输出 `id + type + name`
- **Verbose 模式**: `--verbose` 标志使所有 target 输出 rect
- **视觉/推断元素始终含 rect**: `control_map.get(t.id) is None` 的元素（无 UIAWrapper 控件）始终输出 rect，因为坐标是操作它们的唯一方式
- **体积缩减**: 实测 Edge 浏览器 ~300 控件：compact=4,796 chars vs verbose=12,050 chars（文本模式减少 60%）；JSON 模式减少 41%

## Phase 5: CLI 层（新建，参考 browser-use/playwright-cli）— 已实现

CLI 是通信协议的用户接口。设计原则：
- 每个命令都是原子的
- 感知命令输出索引列表（供 Agent/人阅读）
- 执行命令接受索引编号（Agent/人的决策结果）
- `--json` 全局选项支持结构化输出

### 5.1 cli/app.py — 已实现（入口）

```python
import click

@click.group()
@click.option("--json", "output_json", is_flag=True, help="JSON output")
@click.option("--session", default=None, help="Named session")
@click.option("--window", default=None, help="Target window by title/process substring")
@click.option("--return-state", is_flag=True, help="Return new state after execution commands")
@click.option("--vision", is_flag=True, help="Enable Tier 2 vision-based UI element detection")
@click.option("--infer", is_flag=True, help="Enable Tier 1+ LLM structural inference")
@click.option("--vision-api-key", default=None, help="API key for vision/inference")
@click.option("--vision-base-url", default=None, help="Custom base URL for vision/inference API")
@click.pass_context
def cli(ctx, output_json, session, window, return_state, vision, infer, vision_api_key, vision_base_url): ...

# === 感知端命令 ===
@cli.command()  # winctl state [--screenshot] [--annotated] [--tree]
@cli.command()  # winctl windows
@cli.command()  # winctl inspect <index>
@cli.command()  # winctl screenshot [path]

# === 执行端命令 ===
@cli.command()  # winctl click <index>
@cli.command()  # winctl dblclick <index>
@cli.command()  # winctl rightclick <index>
@cli.command()  # winctl input <index> <text>
@cli.command()  # winctl type <text>
@cli.command()  # winctl keys <keys>
@cli.command()  # winctl scroll <index> <direction> [amount]
@cli.command()  # winctl select <index> <value>
@cli.command()  # winctl drag <index> <x2> <y2> [--button] [--duration]

# === 窗口管理 ===
@cli.command()  # winctl focus <window>
@cli.command()  # winctl launch <app>
@cli.command()  # winctl close

# === 坐标模式（Windows 特有）===
@cli.command()  # winctl click-at <x> <y>

# === 数据提取 ===
@cli.group()    # winctl get
@get.command()  # winctl get text <index>
@get.command()  # winctl get rect <index>
@get.command()  # winctl get value <index>

# === 等待 ===
@cli.command()  # winctl wait [seconds] [--visible <index>] [--enabled <index>] [--timeout N]

def main():
    # Windows UTF-8 安全处理
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    cli()
```

实现备注：
- `--return-state` 全局选项：所有执行命令（click/dblclick/rightclick/input/type/keys/scroll/select/drag）执行后通过 `_output_with_return_state()` 自动追加 `refresh_state()` 输出。
- `state --tree`：调用 `session.get_control_tree()` 递归构建控件层级树（深度限制 3 层），通过 `_format_tree()` 输出缩进文本或 JSON。
- `drag` 命令：从控件中心拖拽到绝对坐标 (x2, y2)，底层调用已注册的 `DragOnCoordinatesCommand`。
- `get value`：依次尝试 `legacy_properties["Value"]` → `get_value()` → `texts()` 获取控件值。
- `wait --visible/--enabled`：轮询 `control.is_visible()` / `control.is_enabled()`，支持 `--timeout` 超时参数。

### 5.2 cli/session.py — 已实现

```python
class DesktopSession:
    """跨 CLI 命令的状态管理"""

    window: UIAWrapper              # 当前操作窗口
    state: Optional[UIState]        # 最新的 UI 状态快照
    inspector: ControlInspectorFacade
    provider: StateProvider          # 可以是 UIAStateProvider / StructuralInferenceProvider / CompositeStateProvider
    puppeteer: Optional[AppPuppeteer]
    _controls: List[Any]            # 当前状态的原始控件列表

    def __init__(self, ..., vision=False, infer=False, vision_api_key=None, vision_base_url=None):
        uia_provider = UIAStateProvider(self.inspector)

        # 构建 primary provider: UIA 或 UIA+推理
        if infer:
            primary = StructuralInferenceProvider(uia_provider, api_key=..., base_url=...)
        else:
            primary = uia_provider

        # 可选: 包裹 vision 作为附加感知源
        if vision:
            self.provider = CompositeStateProvider(primary, vision_provider)
        else:
            self.provider = primary

    @classmethod
    def create(cls, vision=False, infer=False, ...) -> DesktopSession: ...

    # 窗口管理
    def list_windows(self) -> List[Dict]: ...
    def focus_window(self, identifier: str) -> bool: ...
    def focus_foreground(self) -> bool: ...
    def launch_app(self, app: str) -> bool: ...
    def close_window(self) -> bool: ...

    def get_control_tree(self) -> list:
        """递归构建控件层级树（深度限制 3 层），匹配已有索引 ID"""
        ...

    def refresh_state(self, screenshot=False) -> UIState:
        """重新扫描，更新索引"""
        targets, controls = self.provider.detect(self.window)
        self._controls = controls
        control_map = {t.id: ctrl for t, ctrl in zip(targets, controls)}
        self.state = UIState(...)
        return self.state

    def execute_on_target(self, command_name: str, target_id: str, params: dict):
        """索引解析 → 创建 Receiver → 执行命令
        Tier 1 (UIA): control ≠ None → pywinauto 直接操作
        Tier 2 (Vision/Inferred): control == None → 坐标回退
        """
        control = self.state.resolve(target_id)
        if control is not None:
            # Tier 1: UIA path
            self.puppeteer.receiver_manager.create_ui_control_receiver(control, self.window)
            return self.puppeteer.execute_command(command_name, params)
        # Tier 2: Vision/Inferred fallback — use rect centre coordinates
        target = next((t for t in self.state.targets if t.id == target_id), None)
        center_x = (target.rect[0] + target.rect[2]) // 2
        center_y = (target.rect[1] + target.rect[3]) // 2
        return self.execute_global("click_on_coordinates", {"x": str(center_x), "y": str(center_y)})

    def execute_global(self, command_name: str, params: dict):
        """不指定控件的全局命令（type, keyboard_input, click_on_coordinates）"""
        ...

    # 持久化: 通过临时 JSON 文件跨进程共享
    def save(self, path): ...
    def load(self, path): ...
```

实现备注：
- 相比原始 plan 的 `resolve_and_execute()`，实际拆分为 `execute_on_target()`（针对特定控件）和 `execute_global()`（全局命令如 type/keys）。
- 新增了 `list_windows()`、`focus_foreground()`（使用 `win32gui.GetForegroundWindow()`）、`launch_app()`、`close_window()` 等窗口管理方法。
- 新增了 `_capture_screenshot()` 方法集成 `PhotographerFacade`。
- **Provider 组合逻辑**: `__init__` 根据 `infer` 和 `vision` 标志组合 provider 层次:
  - 默认: `UIAStateProvider`
  - `--infer`: `StructuralInferenceProvider(wraps UIAStateProvider)`
  - `--vision`: `CompositeStateProvider(UIAStateProvider, VisionStateProvider)`
  - `--infer --vision`: `CompositeStateProvider(StructuralInferenceProvider, VisionStateProvider)`
- **Vision 回退**: `execute_on_target()` 当 `control is None`（视觉/推断元素无 UIAWrapper）时，自动从 `TargetInfo.rect` 计算中心坐标，回退到 `click_on_coordinates`。Agent 只需输出索引号，无需感知坐标。

### 5.3 cli/formatter.py — 已实现

输出格式化，支持 text（人读）和 json（Agent 读）两种模式。

实现内容：
- `_safe_print(text, file)` — 处理 Windows 控制台 GBK 编码的 UnicodeEncodeError
- `output(data, as_json)` — 多态输出，JSON 模式序列化/文本模式直接打印
- `output_error(message, as_json)` — 错误输出到 stderr
- `format_windows_list(windows, as_json)` — 窗口列表格式化

### 5.4 CLI 命令实现状态

```
感知端命令:                                              状态
─────────────────────────────────────────────────────  ──────
winctl windows                    列举桌面所有窗口       ✅ 已实现
winctl state                      当前窗口的索引化控件列表（紧凑模式，不含 rect） ✅ 已实现
winctl state --verbose            包含所有控件的 rect 坐标 ✅ 已实现
winctl state --tree               控件树层级视图         ✅ 已实现
winctl state --screenshot         附带截图路径           ✅ 已实现
winctl state --annotated          UFO 风格标注截图       ✅ 已实现
winctl inspect <index>            单控件详细属性          ✅ 已实现
winctl screenshot [path]          截图                   ✅ 已实现

执行端命令:
─────────────────────────────────────────────────────  ──────
winctl click <index>              点击                   ✅ 已实现
winctl dblclick <index>           双击                   ✅ 已实现
winctl rightclick <index>         右键                   ✅ 已实现
winctl input <index> <text>       设置文本               ✅ 已实现
winctl type <text>                向焦点控件输入          ✅ 已实现
winctl keys <keys>                发送按键               ✅ 已实现
winctl scroll <index> <dir> [n]   滚动                   ✅ 已实现
winctl select <index> <value>     选择下拉项             ✅ 已实现
winctl drag <index> <x2> <y2>    拖拽                   ✅ 已实现

窗口管理:
─────────────────────────────────────────────────────  ──────
winctl focus <window>             切换窗口               ✅ 已实现
winctl launch <app>               启动应用               ✅ 已实现
winctl close                      关闭当前窗口           ✅ 已实现

坐标模式 (Windows 特有):
─────────────────────────────────────────────────────  ──────
winctl click-at <x> <y>          绝对坐标点击           ✅ 已实现
winctl drag-at <x1> <y1> <x2> <y2>  绝对坐标拖拽      ✅ 已实现

数据提取:
─────────────────────────────────────────────────────  ──────
winctl get text <index>           控件文本               ✅ 已实现
winctl get value <index>          控件值                 ✅ 已实现
winctl get rect <index>           控件矩形               ✅ 已实现

等待:
─────────────────────────────────────────────────────  ──────
winctl wait [seconds]             等待                   ✅ 已实现
winctl wait --visible <index>     等控件可见             ✅ 已实现
winctl wait --enabled <index>     等控件启用             ✅ 已实现

内部命令 (daemon):
─────────────────────────────────────────────────────  ──────
winctl _serve --session-name X --port N  启动 daemon 服务器（hidden） ✅ 已实现

全局选项:
─────────────────────────────────────────────────────  ──────
--json                            JSON 输出              ✅ 已实现
--session <name>                  命名会话（TCP daemon 模式） ✅ 已实现（Phase 10 升级）
--window <name>                   目标窗口匹配           ✅ 已实现
--return-state                    执行后自动返回新状态    ✅ 已实现
--infer                           Tier 1+ 结构推理       ✅ 已实现
--vision                          Tier 2 视觉检测        ✅ 已实现
--vision-api-key <key>            视觉/推理 API key      ✅ 已实现
--vision-base-url <url>           视觉/推理 API base URL ✅ 已实现
```

## Phase 6: SKILL.md（新建）— 已实现

面向 Claude Code / Cursor 等 AI Agent 的操作手册，参考 browser-use SKILL.md 格式。

已实现的核心内容（E2E 测试后全面重写）:
1. **三层感知 Tiers**: Tier 1 (UIA), Tier 1+ (--infer LLM 推理), Tier 2 (--vision 视觉)
2. 完整可用命令列表及参数（包括 drag-at, get value, wait --visible 等）
3. 全局选项文档: --window, --infer, --vision, --return-state, --json, --session
4. 典型工作流模式（Perceive → Decide → Execute → Verify 循环）
5. 关键规则 #5-6: 负坐标需 `--` 分隔符，选项必须在 `--` 之前
6. Agent 决策流程图: UIA 树有？→ Tier 1 / 需要发现？→ Tier 1+/2 / inspect+推理 → Tier 3a
7. 推断/视觉元素输出格式: `[305] [ColumnBorder] "..." rect=[-2294,850,-2290,900]`
8. 错误处理策略

## Phase 7: screenshot/photographer.py（可选）— 已实现

- 来源: `ufo/automator/ui_control/screenshot.py`
- 已提取: `PhotographerFacade`, `AnnotationDecorator`, `PhotographerFactory`
- 已提取: `merge_target_info_list`, `target_info_iou` (用于 CompositeStateProvider)
- 已提取: `encode_image` (base64 编码)
- 修改: config 引用替换为模块级常量
- 未提取: `TargetAnnotationDecorator`, `RectangleDecorator` (UFO 特有功能)

## 源文件 → 目标文件映射

| 目标文件 | 来源 | 操作 | 状态 |
|---|---|---|---|
| `pyproject.toml` | — | 新建 | ✅ 已实现 |
| `_version.py` | — | 新建 | ✅ 已实现 |
| `config.py` | — | 新建 | ✅ 已实现 |
| `_utils.py` | `ufo/utils/__init__.py` | 提取 2 函数 | ✅ 已实现 |
| `targets.py` | `ufo/agents/processors/schemas/target.py` | 原样复制 | ✅ 已实现 |
| `models.py` | `ufo/.../actions.py` + `aip/messages.py` | 内联 Result + 去 rich | ✅ 已实现 |
| `command/basic.py` | `ufo/automator/basic.py` | 原样复制 | ✅ 已实现 |
| `command/puppeteer.py` | `ufo/automator/puppeteer.py` | 去 COM | ✅ 已实现 |
| `command/executor.py` | `ufo/automator/action_execution.py` | 替换 import | ✅ 已实现 |
| `control/controller.py` | `ufo/automator/ui_control/controller.py` | 配置解耦 | ✅ 已实现 |
| `control/inspector.py` | `ufo/automator/ui_control/inspector.py` | 原样复制 | ✅ 已实现 |
| `perception/provider.py` | — | 新建 (参考 UFO 融合逻辑) | ✅ 已实现 |
| `perception/state.py` | — | 新建 | ✅ 已实现 |
| `perception/vision_provider.py` | — | 新建 (Tier 2 视觉检测) | ✅ 已实现 |
| `perception/structural_provider.py` | — | 新建 (Tier 1+ 结构推理) | ✅ 已实现 |
| `cli/app.py` | — | 新建 (参考 browser-use CLI) → 增加 daemon 路由 | ✅ 已实现 |
| `cli/session.py` | — | 新建 | ✅ 已实现 |
| `cli/session_dispatch.py` | — | 新建 (命令分发层，传输无关) | ✅ 已实现 |
| `cli/session_server.py` | — | 新建 (TCP 服务器 + PID file) | ✅ 已实现 |
| `cli/session_client.py` | — | 新建 (TCP 客户端 + 自动启动) | ✅ 已实现 |
| `cli/formatter.py` | — | 新建 | ✅ 已实现 |
| `screenshot/photographer.py` | `ufo/.../screenshot.py` | 提取核心 + 配置解耦 | ✅ 已实现 |
| `SKILL.md` | — | 新建 → E2E 后全面重写 | ✅ 已实现 |

## 实现顺序

| 步骤 | 文件 | 依赖 | 状态 |
|---|---|---|---|
| 1 | pyproject.toml, _version.py | — | ✅ |
| 2 | config.py | — | ✅ |
| 3 | _utils.py | — | ✅ |
| 4 | targets.py | — | ✅ |
| 5 | models.py | targets.py | ✅ |
| 6 | command/basic.py | — | ✅ |
| 7 | command/puppeteer.py | basic.py | ✅ |
| 8 | control/controller.py | basic.py, puppeteer.py, config.py | ✅ |
| 9 | command/executor.py | models.py, puppeteer.py, _utils.py | ✅ |
| 10 | control/inspector.py | — | ✅ |
| 11 | screenshot/photographer.py | config.py, targets.py | ✅ |
| 12 | perception/provider.py | inspector.py, targets.py, screenshot (可选) | ✅ |
| 13 | perception/state.py | provider.py, targets.py | ✅ |
| 14 | cli/formatter.py | state.py | ✅ |
| 15 | cli/session.py | provider.py, state.py, puppeteer.py, controller.py | ✅ |
| 16 | cli/app.py | session.py, formatter.py | ✅ |
| 17 | 各 __init__.py | 全部 | ✅ |
| 18 | SKILL.md | cli 完成后 | ✅ |
| 19 | perception/vision_provider.py | anthropic, photographer.py | ✅ |
| 20 | perception/structural_provider.py | provider.py, anthropic | ✅ |
| 21 | SKILL.md (全面重写) | E2E 测试后 | ✅ |
| 22 | cli/session_dispatch.py | session.py | ✅ |
| 23 | cli/session_server.py | session_dispatch.py | ✅ |
| 24 | cli/session_client.py | session_server.py | ✅ |
| 25 | cli/app.py (daemon 路由) | session_client.py | ✅ |
| 26 | tests/test_session_daemon.py | session_dispatch, server, client | ✅ |
| 27 | perception/state.py (compact state) | — | ✅ |
| 28 | SKILL.md (session 模式更新) | Phase 10 完成后 | ✅ |

## 关键注意事项

1. **`_command_registry` 隔离**: `ControlReceiver` 必须定义自己的 `_command_registry: Dict = {}` — ✅ 已落实
2. **`@ReceiverManager.register` 副作用**: `__init__.py` 必须 import `controller.py` 触发注册 — ✅ 已落实
3. **pywinauto timing**: 改为 `_ensure_pywinauto_timings()` 延迟初始化 — ✅ 已落实
4. **Session 持久化**: 通过临时 JSON 文件 + window handle 重连实现跨命令状态 — ✅ 已落实
5. **索引生命周期**: 每次 `state` 重新分配索引，旧索引失效。CLI 文档和 SKILL.md 中必须强调这一点 — ✅ 已落实
6. **StateProvider 可插拔**: v1 只实现 `UIAStateProvider`，但接口设计支持后续接入 OmniParser 等视觉感知源 — ✅ 已落实（接口已预留，尚无视觉 Provider 实现）

## 验证

### Step 1: Library import — ✅ 已通过
```bash
cd D:\src\Kilodex\winactions
pip install -e ".[dev]"
python -c "from winactions import ActionConfig, ControlReceiver, AppPuppeteer, ActionCommandInfo; print('OK')"
```

### Step 2: Perception layer — ✅ 已通过
```python
from winactions.perception.provider import UIAStateProvider
from winactions.control.inspector import ControlInspectorFacade
inspector = ControlInspectorFacade()
windows = inspector.get_desktop_windows()
provider = UIAStateProvider(inspector)
targets, controls = provider.detect(windows[0])
for t in targets:
    print(f"[{t.id}] [{t.type}] \"{t.name}\"")
```

### Step 3: CLI smoke test — ✅ 已通过
```bash
winctl windows                    # 列举窗口
winctl state                      # 索引化控件列表
winctl click 1                    # 点击第一个控件
winctl state --json               # JSON 格式
```

### Step 4: End-to-end (Notepad) — ✅ 已通过
```bash
winctl launch notepad.exe
winctl state
# [1] [Edit] ""  [2] [Menu] "File" ...
winctl input 1 "Hello from winctl"
winctl state
winctl keys "ctrl+s"
```

### Step 5: 自动化测试 — ✅ 27 tests passed
```bash
cd D:\src\Kilodex\winactions
pytest tests/ -v
```

## 实现过程中的 Bug 修复

以下问题在原始 plan 中未预见，在实现和测试过程中发现并修复：

### Bug 1: Windows 控制台 GBK 编码错误
- **症状**: `winctl windows` 输出包含 Unicode 字符（如 Braille ⠐）时抛出 `UnicodeEncodeError: 'gbk' codec can't encode character`
- **原因**: Windows 控制台默认使用 GBK 编码，无法输出部分 Unicode 字符
- **修复**:
  - `formatter.py` 新增 `_safe_print()` 函数，捕获 `UnicodeEncodeError` 后 fallback 到 `errors="replace"`
  - `cli/app.py` 的 `main()` 新增 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`

### Bug 2: `winctl state` 输出空控件列表
- **症状**: `winctl state` 只显示窗口标题，不显示任何控件
- **原因**: plan 中 `UIAStateProvider` 默认 `control_type_list or []`，空数组传给 UIA `CreateOrConditionFromArray` 返回零结果
- **修复**: 新增 `DEFAULT_CONTROL_TYPES` 列表（34 种 UIA 控件类型），作为默认值

---

## E2E 测试中发现的 Bug 和增强

以下问题在 E2E 端到端测试中发现并修复（详见 `tests/end2end/E2E_ISSUES.md`）：

### Bug 3: `keyboard_input` 在 `control` 为 None 时崩溃

- **文件**: `control/controller.py`
- **症状**: `winctl keys` 通过 `execute_global` 调用时抛出 `AttributeError: 'NoneType' object has no attribute 'set_focus'`
- **原因**: `keyboard_input()` 默认 `control_focus=True` 但未检查 `self.control is not None`
- **修复**: `if control_focus:` → `if control_focus and self.control is not None:`

### Bug 4: `drag_on_coordinates` 对绝对坐标应用分数变换

- **文件**: `control/controller.py`
- **症状**: 坐标 (-2058, 979) 被变换为 (-5930322, 1513885)，破坏表格
- **原因**: `drag_on_coordinates()` 将绝对像素坐标传入 `transform_point()` (按 0.0-1.0 分数处理)
- **修复**: 直接使用 `int(float(...))` 转换坐标。同步移除 `DragCommand` 中的 `transfrom_absolute_point_to_fractional`

### Bug 5: `keyboard_input` 转义 pywinauto 按键序列

- **文件**: `control/controller.py`
- **症状**: `winctl keys "ctrl+z"` 输入 literal "ctrl{+}z" 而非 Ctrl+Z
- **原因**: `TextTransformer.transform_text(keys, "all")` 将 `+` → `{+}`, `^` → `{^}` 等
- **修复**: 从 `keyboard_input()` 中移除 TextTransformer。在 CLI 层新增 `_translate_keys()` 将人类友好格式 ("ctrl+a" → "^a") 转换

### Bug 6: `click-at` 和 `drag-at` 不尊重 `--window` 选项

- **文件**: `cli/app.py`
- **症状**: `drag-at` 在终端窗口而非目标应用上拖拽
- **原因**: 直接调用 pyautogui，未先调用 `_ensure_window(ctx)` 聚焦目标窗口
- **修复**: 在两个命令开头添加 `_ensure_window(ctx)` 调用

### 增强 1: `--window` 全局选项

- **问题**: 每次 `winctl` 调用是独立进程，终端在命令间抢回焦点
- **修复**: `winctl --window outlook state` 在同一进程内先聚焦目标窗口再执行命令

### 增强 2: `drag-at` 命令

- **问题**: `winctl drag INDEX X2 Y2` 从控件中心拖拽，无法精确指定起始坐标
- **修复**: `winctl drag-at X1 Y1 X2 Y2` 支持绝对坐标拖拽

### 增强 3: `_translate_keys()` 人类友好按键格式

- **问题**: pywinauto 按键格式 `^a` 对 agent 不直观
- **修复**: CLI 层 `_translate_keys()` 支持 `ctrl+a` → `^a`, `Enter` → `{ENTER}`, `alt+f4` → `%{F4}`

### 增强 4: `--return-state` 添加 UI 稳定延迟

- **问题**: 执行命令后立即 `refresh_state()` 可能扫描到 UI 中间态
- **修复**: `_output_with_return_state()` 中添加 0.5 秒延迟等待 UI 稳定

### 重构: `click-at`/`drag-at` 路由到 controller 层

- **问题**: `click-at` 和 `drag-at` 在 `app.py` 中直接调用 pyautogui，绕过 controller
- **修复**:
  - `click-at` → `session.execute_global("click_on_coordinates", ...)`
  - `drag-at` → `session.execute_global("drag_on_coordinates", ...)`
  - `click_on_coordinates()` 从分数坐标改为绝对坐标（与 `drag_on_coordinates` 一致）
  - `ClickCommand`/`DoubleClickCommand` 移除 `transfrom_absolute_point_to_fractional` 往返转换
  - `app.py` 不再直接 `import pyautogui`

### CLI 命令补充

```
新增命令:
─────────────────────────────────────────────────────  ──────
winctl --window <name> ...        聚焦目标窗口后执行    ✅ 已实现
winctl drag-at <x1> <y1> <x2> <y2>  绝对坐标拖拽      ✅ 已实现
```

---

## Phase 8: Vision Detection (VisionStateProvider) — 已实现

基于之前可行性分析的结论，使用 Anthropic Claude API 实现了视觉感知 Provider。

### 8.1 perception/vision_provider.py — 已实现

```python
class VisionStateProvider:
    """Tier 2 — 多模态模型视觉检测 UIA 不可见元素"""

    def __init__(self, api_key=None, base_url=None,
                 model="claude-sonnet-4-5-20250929", max_tokens=4096):
        ...

    def detect(self, window) -> tuple[List[TargetInfo], List[Optional[UIAWrapper]]]:
        # 1. 截图 → resize 到 ≤1568px 长边 + base64
        # 2. 窗口 rect 用于坐标转换
        # 3. 调用 Anthropic API (图像 + detection prompt)
        # 4. 解析 JSON 响应 → TargetInfo (绝对屏幕坐标)
        #    模型返回图片空间坐标 → scale_x/scale_y → + win_rect.left/top
        # 5. controls = [None] * len(targets)  (无 UIAWrapper)
```

实现要点：
- **图像缩放**: 截图超过 1568px 时自动 resize，记录 scale_x/scale_y 用于坐标还原
- **坐标转换**: 模型返回缩放后图像空间坐标 → 乘以 scale → 加窗口 left/top = 绝对屏幕坐标
- **检测 prompt**: 聚焦 UIA 不可见元素（resize handle、column border、icon-only button、canvas element、splitter、custom control）
- **JSON 解析**: 支持 markdown 代码围栏剥离
- **静默失败**: 异常时返回空列表，不影响 UIA 检测
- **无缓存**: 每次调用都重新检测（之前的文件缓存已移除，简化了架构）

### 8.2 Provider 组合架构 — 已实现

```
--vision 单独:
  CompositeStateProvider(
    primary=UIAStateProvider,       # 精确 UIA 控件
    additional=VisionStateProvider,  # 视觉补充
    iou_threshold=0.1               # IOU 去重
  )

执行策略:
  UIA 元素 (control ≠ None) → execute_on_target (pywinauto, 精确)
  视觉元素 (control == None) → click_on_coordinates (坐标, ±10-30px)
```

### 8.3 CLI 集成

- `winctl --vision state`: 启用 Tier 2，UIA + 视觉融合
- `winctl --vision click <index>`: 视觉元素自动坐标回退
- `--vision-api-key` / `--vision-base-url`: API 配置（或环境变量）

---

## Phase 9: Tier 1+ Structural Inference (StructuralInferenceProvider) — 已实现

E2E 测试揭示：对于常见 UI 模式（表格、面板），UIA 数据本身已包含足够的结构信息来推断隐藏交互元素的存在和精确坐标。本 phase 将 agent 的手动空间推理（Tier 3a: inspect cell rect → 推理 → drag-at）自动化为 Tier 1+。

### 9.1 perception/structural_provider.py — 已实现

```python
class StructuralInferenceProvider:
    """Tier 1+ — wraps UIAStateProvider, adds LLM-inferred hidden elements"""

    def __init__(self, uia_provider, api_key=None, base_url=None,
                 model="claude-haiku-4-5-20251001", max_tokens=4096,
                 min_confidence=0.7):
        ...

    def detect(self, window) -> tuple[List[TargetInfo], List[Optional[UIAWrapper]]]:
        # 1. uia_provider.detect(window) → (uia_targets, uia_controls)
        # 2. _format_uia_data(uia_targets) → 纯文本
        # 3. _call_model(text) → JSON → 过滤 confidence ≥ 0.7 → TargetInfo
        # 4. 返回 (uia + inferred, controls + [None]*len)
        # 5. 异常时仅返回 UIA 结果（静默失败）
```

实现要点：
- **纯文本输入**: UIA 控件列表格式 `[id] [Type] "Name" rect=[l,t,r,b]`，比截图便宜 10 倍
- **LLM 模型**: claude-haiku-4-5（成本 ~$0.001-0.003/次，延迟 0.5-1.5s）
- **推理知识**: 表格列边框（HeaderItem 边界）、resize handle（Table 右下角）、行边框（DataItem 边界）、splitter（Pane 边界）
- **坐标精度**: 来自 UIA rect 精确计算（如 `border_x = HeaderItem.rect[2]`），非视觉估算
- **confidence 过滤**: 默认 ≥ 0.7 才保留，过滤 LLM 幻觉
- **JSON 解析**: 支持 markdown 代码围栏剥离 + 提取最外层 `[...]` 括号对（修复 LLM 在 JSON 后追加推理文本的问题）
- **静默失败**: 推理异常时仅返回 UIA 结果

### 9.2 Provider 组合架构 — 已实现

```
--infer 单独:
  StructuralInferenceProvider(wraps UIAStateProvider)
  → 返回 UIA + 推断元素

--infer --vision 组合:
  CompositeStateProvider(
    primary=StructuralInferenceProvider,  # UIA + 推断
    additional=VisionStateProvider,       # 视觉补充
  )
  → 三层融合: UIA + 结构推断 + 视觉检测
```

### 9.3 对比 Vision (Tier 2)

| 维度 | Tier 1+ (Structural) | Tier 2 (Vision) |
|------|---------------------|-----------------|
| 输入 | 纯文本 (UIA 列表) | 截图图像 |
| 坐标精度 | 像素级 (UIA rect 计算) | ±10-30px (视觉估算) |
| 成本 | ~$0.001-0.003 | ~$0.01 |
| 延迟 | 0.5-1.5s | 2-5s |
| 覆盖 | 表格/面板等结构化 UI 模式 | 任何可视元素 |
| 适用 | 有 UIA 锚点的隐藏元素 | 完全无 UIA 表示的元素 |

### 9.4 perception/__init__.py — 已更新

```python
# VisionStateProvider 和 StructuralInferenceProvider 都依赖可选的 'anthropic' 包
try:
    from winactions.perception.vision_provider import VisionStateProvider
except ImportError:
    VisionStateProvider = None
try:
    from winactions.perception.structural_provider import StructuralInferenceProvider
except ImportError:
    StructuralInferenceProvider = None
```

---

## E2E 测试结果

详细测试日志: `tests/end2end/e2e_run_20260218.md`

### Test Case 1: Compose Email with Table Operations — 9/9 PASS

| Step | Action | Result |
|------|--------|--------|
| 1 | `winctl --window outlook state` | 375 elements detected |
| 2 | `winctl click 71` (New mail) | Compose pane opened |
| 3 | `winctl click 278` (message body) | Editor focused |
| 4 | Insert tab → Table button | Table dropdown grid appeared |
| 5 | `winctl click 292` (3x3 grid) | 3x3 table inserted |
| 6 | `winctl keys "ctrl+a"` | Table selected |
| 7 | `winctl --infer state` → `drag-at` | **Tier 1+ ResizeHandle inferred** → table resized |
| 8 | Table ribbon → Insert below | Row added (3x3→4x3) |
| 9 | `winctl click 262` (Discard) | Compose dismissed |

### Test Case 4: Session Daemon Mode — 9/9 PASS

使用 `--session outlook` 运行 Test Case 1 全部步骤。三个 pitfall 全部消除。

详细日志: `tests/end2end/E2E_ISSUES.md` — "Test Case 4: Session Daemon Mode"

### 发现的 Bug 和增强 (E2E 测试期间)

详见 `tests/end2end/E2E_ISSUES.md` — 共 7 个问题修复 (4 核心 + 3 视觉/推理)。

---

## Phase 10: Session Daemon Mode（TCP 持久会话）— 已实现

借鉴 browser-use 的 thin CLI + persistent server 模式：Agent 的调用方式不变（`winctl --session outlook click 5`），但后台由 TCP daemon 持有持久 DesktopSession，消除了 CLI-per-invocation 架构的三个核心问题。

### 10.1 问题背景

winactions 原本是 CLI-per-invocation 架构，每次 `winctl` 调用是独立进程。这导致三个 pitfall：

1. **焦点漂移**: 每次 CLI 退出后 OS 焦点可能漂回终端，下一次 `keys` 命令打到错误控件
2. **索引混用**: 进程间无法共享 `control_map`，`--return-state` 返回的索引无法直接用于下一条命令（下一条命令会重新枚举）
3. **坐标过时**: `inspect` 获取的坐标在多次 CLI 调用之间可能因 UI 变化而失效

### 10.2 架构

```
winctl --session my click 5     winctl --session my keys ctrl+a
         │                                │
         ▼                                ▼
    [thin CLI]                       [thin CLI]
    main() 检测到 --session              同上
         │                                │
         ├─ daemon 在跑？                  │
         │   TCP connect 127.0.0.1:port    │
         │   → 发送 JSON request           │
         │   → 收 JSON response            │
         │   → 输出 result                 │
         │                                │
         ├─ daemon 没跑？                  │
         │   → 自动启动 daemon 子进程       │
         │   → 等待 READY                  │
         │   → 然后同上                     │
         └────────────────────────────────┘
                       │
                       ▼
         [winctl _serve --session-name my --port 49xxx]
               (后台持久进程, DETACHED_PROCESS)
               DesktopSession（持久）
               control_map / UIAWrapper refs（持久）
               OS 焦点状态（连续）
```

**无 `--session` 时**：`main()` → `_extract_session_flag()` 返回 None → `cli()` → 和以前完全一样。

### 10.3 技术栈

| 组件 | 技术 | 依赖 |
|------|------|------|
| TCP 服务器 | `socket` 模块，单线程 accept-dispatch-close | stdlib |
| 协议 | JSON Lines over TCP（每个连接一个 request-response） | stdlib `json` |
| 端口 | session name SHA256 哈希映射到 49152-65535 | stdlib `hashlib` |
| 生命周期 | PID file (`%TEMP%/winctl_{name}.pid`) + TCP ping | stdlib `os` + `psutil`（已有依赖） |
| 进程隔离 | `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` (Windows) | stdlib `subprocess` |

**零额外依赖**——全部 stdlib + 已有依赖。

### 10.4 Protocol

**连接模型**: 每个 CLI 调用：connect → send 1 JSON line → recv 1 JSON line → disconnect。UI 自动化天然串行，无并发需求。

**Request (CLI → Server)**:
```json
{
  "command": "click",
  "args": {"index": "5", "right": false},
  "flags": {"return_state": true, "window": "outlook", "vision": false, "infer": false}
}
```

**Response (Server → CLI)**:
```json
{"status": "ok", "result": "OK", "state": {"window": "...", "targets": [...]}}
```

**特殊命令**:
- `{"command": "_ping"}` → `{"status": "ok", "result": "pong", "session": "my"}` — 健康检查
- `{"command": "_shutdown"}` → 服务器优雅退出

### 10.5 新文件

#### cli/session_dispatch.py（~520 行）— 命令分发（传输无关）

```python
class SessionDispatch:
    """命令分发：request dict → response dict。不抛异常。"""

    def __init__(self, session: DesktopSession, *,
                 default_vision: bool = False, default_infer: bool = False):
        self.session = session
        self._dispatch = self._build_dispatch_table()  # 24 个命令

    def handle(self, request: dict) -> dict:
        """处理单个 request，返回 response dict。"""
        # 内置: _ping, _shutdown
        # 自动: _maybe_switch_provider(), _maybe_switch_window()
        # 分发: handler(args, flags)

    # --- 24 个 handler（全部命令覆盖）---
    # state, windows, inspect, screenshot
    # click, dblclick, rightclick, input, type, keys, scroll, select, drag
    # click-at, drag-at
    # focus, launch, close
    # get text, get rect, get value
    # wait

    def _maybe_switch_provider(self, flags) -> None:
        """动态切换 provider——仅当 vision/infer 组合与当前不同时重建。"""

    def _action_response(self, result, flags) -> dict:
        """构建动作响应，可选附加 fresh state（相当于 --return-state）。"""
```

核心设计：
- **传输无关**: 不依赖 TCP/socket，纯 dict→dict，未来可接 MCP stdio transport
- **Provider 动态切换**: `_maybe_switch_provider()` 检测 `vision`/`infer` flags 变化，只在需要时重建 provider
- **Window 自动切换**: `_maybe_switch_window()` 检测 `--window` 变化，按需切换
- **keys 翻译**: 内置 `_translate_keys()`（从 app.py 复制），保持 dispatch 自包含

#### cli/session_server.py（~220 行）— TCP 服务器

```python
class SessionServer:
    """单线程 TCP 服务器。每个连接处理一个 request-response 后关闭。"""
    def serve_forever(self) -> None:  # bind → listen → accept loop (1s timeout)
    def _handle_connection(self, conn): # readline → json.loads → dispatch.handle → sendall

def session_port(name: str) -> int:  # SHA256 → 49152-65535
def write_pid_file(name: str, port: int) -> str:  # JSON: {pid, port, session_name}
def read_pid_file(name: str) -> Optional[dict]:
def is_server_alive(name: str) -> bool:  # PID file + psutil + TCP ping (含 session_name 碰撞检测)
```

#### cli/session_client.py（~150 行）— TCP 客户端

```python
def send_command(port: int, request: dict, timeout: float = 30.0) -> dict:
    """connect → send JSON line → recv JSON line → close。"""

def ensure_server(session_name: str, *, vision, infer, ...) -> int:
    """确保 daemon 在跑（不在就 Popen 启动），返回端口号。
    Windows: CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    等待 READY: 轮询 TCP ping (0.3s 间隔, 最多 10s)。"""
```

### 10.6 app.py 修改

**main() 拦截（~10 行）**:
```python
def main():
    if os.name == "nt":
        _setup_utf8_io()
    args = sys.argv[1:]
    session_name = _extract_session_flag(args)
    if session_name and not _is_serve_command(args):
        _daemon_forward(session_name, args)
        return
    cli()  # ← 无 --session 时，和以前完全一样
```

**新增函数**:
- `_extract_session_flag(args)` — 从 argv 提取 `--session` 值
- `_is_serve_command(args)` — 检测 `_serve` 内部命令
- `_parse_args_for_daemon(args)` — argv → JSON request dict（全局 flags + 命令 + 参数）
- `_parse_command_args(command, cmd_args)` — 命令特定参数解析（24 个命令的 positional mapping）
- `_daemon_forward(session_name, args)` — ensure_server → send_command → 输出
- `_serve_cmd` — 隐藏 Click 命令，启动 daemon 服务器

**现有命令函数零改动**: click_cmd, state, keys 等所有命令函数体不变。

**`state` 命令新增 `--verbose` 选项**: 传递给 `to_text(verbose=verbose)` 和 `to_json(verbose=verbose)`。

### 10.7 对现有代码的影响

**零改动文件**:
- `session.py`（DesktopSession 类本身不改）
- `formatter.py`, `__main__.py`
- `perception/*.py`（所有 provider）
- `control/*.py`, `command/*.py`
- `targets.py`

**修改文件**:
- `app.py` — main() 拦截 + _serve 命令 + argv 解析 + daemon 转发 + state --verbose
- `perception/state.py` — to_text()/to_json() 增加 verbose 参数

### 10.8 测试

**tests/test_session_daemon.py（~200 行, 13 tests）**:

| 测试类 | 测试内容 |
|--------|----------|
| `TestSessionDispatch` | state, windows, click, keys, inspect, unknown command, click-at, drag-at, _ping, _shutdown |
| `TestSessionProtocol` | JSON round-trip, 错误处理 |
| `TestSessionClient` | send_command, ensure_server, _try_ping |

所有 77 个测试通过（含 13 个 daemon 测试 + 64 个已有测试）。

### 10.9 E2E 验证 (Test Case 4)

使用 `--session outlook` 模式完整跑通 Test Case 1 的 9 个步骤，全部 PASS。

| 指标 | 非 Session 模式 | Session 模式 |
|------|----------------|-------------|
| TC1 步骤通过率 | 9/9 | 9/9 |
| Unique 调用次数 | 9 | 11 |
| 焦点漂移问题 | 需 `keys --target` 缓解 | 彻底消除 |
| 索引一致性 | 每次重枚举 | Daemon 持久 control_map |
| 坐标稳定性 | 需即时 inspect | Daemon 保持窗口 focus |

详细测试日志: `tests/end2end/E2E_ISSUES.md` — "Test Case 4: Session Daemon Mode"

---

## 未实现条目汇总

原始 plan 中提到的主要未实现功能现在**全部已实现**：

| 条目 | 状态 | 实现位置 |
|---|---|---|
| 视觉感知 Provider | ✅ **已实现** | Phase 8: `perception/vision_provider.py` |
| 结构推理 Provider | ✅ **已实现** | Phase 9: `perception/structural_provider.py` |
| Provider 组合 (--infer --vision) | ✅ **已实现** | `cli/session.py` provider 组合逻辑 |

### 依赖

```toml
[project.optional-dependencies]
vision = ["anthropic>=0.40"]  # 同时用于 VisionStateProvider 和 StructuralInferenceProvider
```
