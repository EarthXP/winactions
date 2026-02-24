# winctl Perception Tiers — 功能与原理

## 概述

winctl 的感知系统 (Perception) 负责**发现和定位 UI 元素**。不同的 UI 元素对标准可访问性 API (UIA) 的可见程度不同：

- **UIA 可见**（>90%）：按钮、输入框、列表项、标签页等标准控件
- **UIA 不可见**：列边框、行边框、resize handle、splitter、icon-only 按钮、canvas 元素等

为了覆盖所有元素类型，winctl 提供多种感知策略。这些策略沿两个正交维度组织。

---

## 二维 Tier 框架

|  | **坐标来源：UIA rect 推导** | **坐标来源：截图视觉估算** |
|---|---|---|
| **工具自动化**（集成在 `state` 输出中） | **Tier 1 / 1+**  `--infer` | **Tier 2**  `--vision` |
| **Agent 手动推理**（多步 CLI 调用） | **Tier 3a**  `inspect` → 推理 → `drag-at` | **Tier 3b**  截图 → 估坐标 → `click-at` |

### 两个维度

**维度一：坐标来源**
- **UIA rect 推导**：坐标从 UIA API 返回的控件边界矩形 (rect) 通过算术计算得出。精度 = 像素级。
- **截图视觉估算**：坐标从截图像素分析得出（LLM vision 或 Agent 目测）。精度 = ±10-50px。

**维度二：谁执行推理**
- **工具自动化**：感知逻辑封装在 `winctl state` 内部，输出直接包含推断元素和坐标。Agent 只看到结果。
- **Agent 手动推理**：Agent 自己调用 `inspect`/`screenshot` 获取原始数据，自己计算坐标，自己调 `drag-at`/`click-at`。

---

## 各 Tier 详解

### Tier 1 — 标准 UIA 控件（默认）

**Flag**: 无（默认行为）
**成本**: 免费
**精度**: 像素级
**延迟**: <100ms

**原理**:

```
winctl state
    → UIAStateProvider.detect(window)
        → inspector.find_control_elements_in_descendants(window)
            → pywinauto 遍历 UIA 控件树
            → 筛选 30+ 种标准控件类型 (Button, Edit, TabItem, ...)
        → 为每个控件分配 1-indexed ID
    → 输出: [1] [Button] "Save"  [2] [Edit] "Name"  ...
```

**实现**: `UIAStateProvider` (`perception/provider.py`)

```python
class UIAStateProvider:
    def detect(self, window) -> tuple[List[TargetInfo], List[UIAWrapper]]:
        controls = self.inspector.find_control_elements_in_descendants(window)
        targets = [TargetInfo(id=str(i+1), ...) for i, c in enumerate(controls)]
        return targets, controls
```

每个 target 都有对应的 `UIAWrapper` 句柄，执行层可以直接调用 `control.click_input()`、`control.set_edit_text()` 等 pywinauto 方法。

**覆盖**: >90% 的日常操作。

---

### Tier 1+ — LLM 结构推断（`--infer`）

**Flag**: `--infer`
**成本**: ~$0.001-0.003/次
**精度**: 像素级（坐标从 UIA rect 算术推导）
**延迟**: 0.5-1.5s

**原理**:

```
winctl --infer state
    → StructuralInferenceProvider.detect(window)
        → Step 1: uia_provider.detect(window)  ← 先获取 Tier 1 数据
        → Step 2: _format_uia_data(uia_targets)
            → 每行: [1] [Button] "Save" rect=[100,130,200,177]
        → Step 3: _call_model(text)
            → 纯文本 prompt + UIA 数据 → Anthropic API (Haiku)
            → LLM 用 UI 模式知识推断隐藏元素
            → 返回 JSON: [{name, type, rect, confidence, derived_from}]
        → Step 4: 按 confidence >= 0.7 过滤
        → Step 5: 合并 UIA + 推断，重新编号
    → 输出: [1] [Button] "Save"  ...  [305] [ColumnBorder] "Column 1-2" rect=[...]
```

**实现**: `StructuralInferenceProvider` (`perception/structural_provider.py`)

**关键特性 — 纯文本推理，不看截图**:

LLM 收到的输入只有 UIA 控件属性的文本列表（含 rect 坐标），没有截图。LLM 运用 UI 设计的常识模式推断隐藏元素：

| 推断类型 | 推断依据 | 坐标公式 |
|---------|---------|---------|
| ColumnBorder | 相邻 HeaderItem | `border_x = headerA.rect.right` |
| RowBorder | 垂直相邻 DataItem | `border_y = rowA.rect.bottom` |
| ResizeHandle | Table/DataGrid 存在 | `(table.rect.right-8, table.rect.bottom-8, table.rect.right, table.rect.bottom)` |
| Splitter | 相邻 Pane 间隙 | 两个 Pane 共享边的中间位置 |
| PanelGrip | Pane 可拖拽边缘 | Pane 边缘坐标 |

每个推断元素必须提供 `derived_from` 字段（如 `"border_x = A.rect[2], using A=[3] HeaderItem"`），确保坐标可追溯。

**推断元素的 controls 槽位是 `None`**：没有 UIAWrapper 句柄，执行层只能通过坐标操作（`click-at`/`drag-at`）。

**与 Tier 3a 的对应关系**：

Tier 1+ 和 Tier 3a 本质上做同一件事（从 UIA rect 推导隐藏元素坐标），区别在于谁来推理：

```
Tier 1+: 工具内置 LLM 自动推断 → 结果直接出现在 state 输出中
Tier 3a: Agent 自己用 inspect 查 rect → 自己算坐标 → 自己调 drag-at
```

---

### Tier 2 — Vision API 视觉检测（`--vision`）

**Flag**: `--vision`
**成本**: ~$0.01/次
**精度**: ±35px（视觉 bbox 估算）
**延迟**: 2-5s

**原理**:

```
winctl --vision state
    → CompositeStateProvider.detect(window)
        → primary.detect(window)  ← Tier 1（或 Tier 1+ if --infer）
        → VisionStateProvider.detect(window)
            → Step 1: 截取窗口截图
            → Step 2: 缩放到 ≤1568px（长边），记录 scale_x/scale_y
            → Step 3: 截图 base64 + prompt → Anthropic API (Sonnet)
                → 多模态 LLM 看截图，识别 UIA 不可见的视觉元素
                → 返回 JSON: [{name, type, rect}]（截图像素坐标）
            → Step 4: 坐标转换: 缩放图坐标 × scale → 原始截图坐标 + window.rect → 屏幕绝对坐标
        → IOU 去重合并两个来源
    → 输出: [1] [Button] "Save"  ...  [308] [ResizeHandle] "..." rect=[...]
```

**实现**: `VisionStateProvider` (`perception/vision_provider.py`) + `CompositeStateProvider` (`perception/provider.py`)

**关键特性 — 看截图，不看 UIA 数据**:

VisionStateProvider 和 StructuralInferenceProvider 完全互补：

| | Tier 1+ (`--infer`) | Tier 2 (`--vision`) |
|---|---|---|
| 输入 | UIA 控件文本列表 | 窗口截图 |
| LLM 类型 | 纯文本 (Haiku) | 多模态 (Sonnet) |
| 坐标来源 | UIA rect 算术推导 | 截图像素估算 |
| 精度 | 像素级 | ±35px |
| 成本 | ~$0.001-0.003 | ~$0.01 |
| 优势 | 精确、便宜、快 | 能发现任何可见元素 |
| 劣势 | 只能推断已知 UI 模式 | 精度不够精细操作 |

**坐标转换流程**:
1. 截图可能很大（如 3840×2160），先缩放到长边 ≤1568px
2. LLM 返回的 rect 在缩放后的图片坐标系中
3. 乘以 `scale_x`/`scale_y` 还原到原始截图尺寸
4. 加上 `window.rectangle().left/top` 转为屏幕绝对坐标

**与 Tier 3b 的对应关系**：

Tier 2 和 Tier 3b 本质上做同一件事（从截图视觉估算坐标），区别在于谁来看图：

```
Tier 2:  工具内置 Vision API 自动检测 → 结果直接出现在 state 输出中
Tier 3b: Agent 自己看截图 → 自己估坐标 → 自己调 click-at
```

---

### Tier 3a — Agent 手动 UIA 锚点推理

**Flag**: 无（Agent 行为模式，不是 CLI flag）
**成本**: 免费
**精度**: 像素级
**延迟**: ~1s（多次 CLI 调用）

**原理**:

```
# Agent 手动执行以下步骤：

# Step 1: 用 inspect 获取相邻 UIA 控件的精确 rect
winctl inspect 265
# → control_rect: (-2599, 850, -2292, 900)   ← 第一列 cell

winctl inspect 266
# → control_rect: (-2176, 850, -1964, 900)   ← 第二列 cell

# Step 2: Agent 空间推理（不需要 LLM 调用，Agent 自己算）
# "列边框在第一列 cell 的右边缘 x=-2292"
# "y 取 cell 垂直中线 = (850+900)/2 = 875"

# Step 3: 用精确坐标执行操作
winctl drag-at -- -2292 875 -2222 875
```

**没有 Provider 实现**：这不是一个代码模块，而是 Agent 的操作模式。Agent 利用 UIA 可见控件作为"锚点"，通过空间推理计算不可见元素的精确位置。

**与 Tier 1+ 的关系**：Tier 1+ 自动化了 Tier 3a 的过程。如果 `--infer` 的 LLM 推断正确，Agent 不需要手动做 Tier 3a。Tier 3a 是 `--infer` 的降级方案。

---

### Tier 3b — Agent 手动截图坐标估算

**Flag**: 无（Agent 行为模式，不是 CLI flag）
**成本**: 免费（不调 Vision API，Agent 自己看）
**精度**: ±10-50px
**延迟**: 取决于 Agent

**原理**:

```
# Agent 手动执行以下步骤：

# Step 1: 截图
winctl screenshot /tmp/screen.png

# Step 2: Agent 看截图，目测目标位置
# "resize handle 大约在截图的 (800, 600) 位置"

# Step 3: 用估算坐标执行操作
winctl click-at 800 600
```

**没有 Provider 实现**：同 Tier 3a，这是 Agent 行为模式。

**与 Tier 2 的关系**：Tier 2 自动化了 Tier 3b 的过程。如果 `--vision` 的检测结果可用，Agent 不需要手动看截图估坐标。Tier 3b 是最后手段，E2E 测试中实际未使用过。

---

## Provider 组合

`DesktopSession.__init__()` (`cli/session.py`) 根据 CLI flags 组合 Provider：

```
无 flag:          UIAStateProvider                          → Tier 1
--infer:          StructuralInferenceProvider(UIAStateProvider) → Tier 1 + 1+
--vision:         CompositeStateProvider(UIAStateProvider, VisionStateProvider)  → Tier 1 + 2
--infer --vision: CompositeStateProvider(StructuralInferenceProvider, VisionStateProvider) → Tier 1 + 1+ + 2
```

CompositeStateProvider 通过 IOU (Intersection over Union) 阈值 0.1 去重合并多个来源的 targets。

---

## Agent 决策流程

```
需要操作的目标元素
│
├─ winctl state → 元素在 UIA 树中？
│   └─ 是 → 【Tier 1】click/drag <index>（>90% 走这里）
│
└─ 否（UIA 不可见）
    │
    ├─ 【首选】winctl --infer state → 推断出来了？
    │   ├─ 是 → 用 rect 坐标 click-at/drag-at（Tier 1+，像素级精确）
    │   └─ 否 → 继续
    │
    ├─ 【次选】winctl --vision state → 检测到了？
    │   ├─ 是 + 目标 > 20px → click <vision_index>（Tier 2，精度足够）
    │   ├─ 是 + 目标 < 20px → 需要 Tier 3a 精确定位
    │   └─ 否 → 继续
    │
    ├─ 【降级 A】附近有 UIA 控件做锚点？
    │   └─ 是 → inspect <neighbor> → 算坐标 → drag-at（Tier 3a，像素级）
    │
    └─ 【降级 B】无锚点，最后手段
        └─ screenshot → 目测坐标 → click-at（Tier 3b，±10-50px）
```

---

## 成本对比（9 步 E2E 测试）

| 方案 | Vision API 调用 | 成本 | 成功率 | 说明 |
|------|----------------|------|--------|------|
| 纯 Tier 1 | 0 | $0 | 67% (6/9) | 视觉元素步骤无法执行 |
| Tier 1 + Tier 1+ | 0 | ~$0.003 | ~89% | 推断结果取决于 LLM |
| Tier 1 + Tier 2 | 4 | $0.04 | 89% (8/9) | 列边框精度不足 |
| **Tier 1 + Tier 3a** | 0 | **$0** | **100%** | Agent 用 UIA 锚点推理 |
| Tier 1 + Tier 2 + Tier 3a | 1 | $0.01 | 100% | 发现 + 精确操作的最优组合 |

---

## 代码文件映射

| 文件 | Tier | 职责 |
|------|------|------|
| `perception/provider.py` | Tier 1 | `UIAStateProvider` — UIA 控件枚举 |
| `perception/provider.py` | 合并 | `CompositeStateProvider` — IOU 去重合并 |
| `perception/structural_provider.py` | Tier 1+ | `StructuralInferenceProvider` — LLM 结构推断 |
| `perception/vision_provider.py` | Tier 2 | `VisionStateProvider` — Vision API 视觉检测 |
| `perception/state.py` | — | `UIState` — 状态快照数据结构 |
| `targets.py` | — | `TargetInfo` — 统一的元素描述 |
| `cli/session.py` | — | `DesktopSession` — Provider 组合逻辑 |
| `cli/app.py` | — | CLI flags (`--infer`, `--vision`) 到 Provider 的映射 |
