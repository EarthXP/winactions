"""StructuralInferenceProvider — Tier 1+ LLM-based structural reasoning.

Wraps UIAStateProvider and adds LLM-inferred hidden interactive elements
by analyzing the UIA control list (pure text). The LLM uses knowledge of
common UI patterns (tables, panels, splitters) to infer the existence and
precise coordinates of hidden elements like column borders, resize handles,
and splitter bars.

Key advantages over Vision (Tier 2):
  - More precise: coordinates derived from UIA rects, not visual estimation
  - Cheaper: text-only ~$0.001-0.003 vs image ~$0.01
  - Faster: 0.5-1.5s vs 2-5s
  - More general than hardcoded rules: LLM knows many UI patterns
"""

from __future__ import annotations

import json
import logging
import os
import platform
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from winactions.targets import TargetInfo, TargetKind

if TYPE_CHECKING or platform.system() == "Windows":
    from pywinauto.controls.uiawrapper import UIAWrapper
else:
    UIAWrapper = Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inference prompt — tells the model how to reason about UIA structure
# ---------------------------------------------------------------------------

_INFERENCE_PROMPT = """\
You are a Windows UI structure analyst. Given a list of UIA (UI Automation) \
controls from a desktop application window, infer hidden interactive elements \
that exist but are NOT exposed through the UIA accessibility API.

## Input format

Each line describes one UIA control:
[index] [ControlType] "Name" rect=[left, top, right, bottom]

## What to infer

Based on common Windows UI patterns, infer these hidden elements:

1. **Column borders** — Vertical draggable borders between table/list column \
headers (HeaderItem). Located at the boundary between adjacent HeaderItems. \
Typical width: 4px (2px on each side of the boundary).

2. **Row borders** — Horizontal draggable borders between DataItem rows. \
Located at the boundary between vertically adjacent DataItems. \
Typical height: 4px.

3. **Table resize handles** — Small draggable squares at the bottom-right \
corner of a Table or DataGrid control. Typical size: 8x8px.

4. **Splitter bars** — Draggable dividers between adjacent Pane controls. \
Located at the gap between two Panes sharing an edge. Typical width: 4-6px.

5. **Panel resize grips** — Draggable edges of resizable Pane controls. \
Located at the edge of a Pane that borders another Pane or empty space.

## Rules

- Coordinates MUST be derived from the UIA rect values using arithmetic. \
For each inferred element, provide the `derived_from` field showing the \
exact formula used (e.g., "border_x = A.rect[2], using A=[3] HeaderItem").
- Only infer elements where the UI pattern strongly supports their existence.
- Set confidence to reflect how certain you are (0.0 to 1.0):
  - 0.95+: Adjacent HeaderItems in a Table → column borders almost certain
  - 0.85-0.95: Adjacent DataItems → row borders very likely
  - 0.75-0.85: Table present → resize handle likely
  - 0.60-0.75: Panes with small gap → splitter possible
  - Below 0.60: speculative, do not include

## Output format

Return ONLY a JSON array:
```json
[
  {
    "name": "short descriptive name (e.g. 'Column border between Subject and Date')",
    "type": "ColumnBorder | RowBorder | ResizeHandle | Splitter | PanelGrip",
    "rect": [left, top, right, bottom],
    "confidence": 0.95,
    "derived_from": "formula description referencing control indices and rect fields"
  }
]
```

If no hidden elements can be confidently inferred, return `[]`.

## UIA controls

"""

class StructuralInferenceProvider:
    """Tier 1+ — wraps UIAStateProvider, adds LLM-inferred hidden elements.

    Uses a text-only LLM call to analyze the UIA control list and infer
    hidden interactive elements based on structural UI patterns. Coordinates
    are derived from UIA rect values, not visual estimation.
    """

    def __init__(
        self,
        uia_provider,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "claude-haiku-4-5-20251001",
        max_tokens: int = 4096,
        min_confidence: float = 0.7,
    ):
        self.uia_provider = uia_provider
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Anthropic API key required for structural inference. "
                "Set the ANTHROPIC_API_KEY environment variable or pass "
                "api_key= to StructuralInferenceProvider."
            )
        self._base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL")
        self._model = model
        self._max_tokens = max_tokens
        self._min_confidence = min_confidence
        self._client = None  # lazy-initialized

    @property
    def client(self):
        """Lazy-initialize the Anthropic client."""
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError(
                    "The 'anthropic' package is required for structural inference. "
                    "Install with:  pip install winactions[vision]"
                )
            kwargs: Dict[str, Any] = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    # ------------------------------------------------------------------
    # StateProvider interface
    # ------------------------------------------------------------------

    def detect(
        self, window: UIAWrapper,
    ) -> tuple[List[TargetInfo], List[Optional[UIAWrapper]]]:
        """Detect UIA controls + LLM-inferred hidden elements.

        Returns ``(targets, controls)`` where UIA targets carry UIAWrapper
        handles and inferred targets carry ``None``.  Inferred targets
        include rect coordinates so the agent can use ``drag-at`` /
        ``click-at`` directly from the ``state`` output.
        """
        # 1. Get fresh UIA data
        uia_targets, uia_controls = self.uia_provider.detect(window)

        # 2. Run LLM inference (graceful degradation on failure)
        try:
            inferred = self._infer_elements(uia_targets)
        except Exception as e:
            logger.warning(
                "Structural inference failed, returning UIA-only: %s", e
            )
            return uia_targets, uia_controls

        # 3. Combine UIA + inferred, assign sequential IDs
        merged_targets = list(uia_targets) + inferred
        merged_controls = list(uia_controls) + [None] * len(inferred)
        for i, t in enumerate(merged_targets):
            t.id = str(i + 1)
        return merged_targets, merged_controls

    # ------------------------------------------------------------------
    # LLM inference pipeline
    # ------------------------------------------------------------------

    def _infer_elements(
        self, uia_targets: List[TargetInfo],
    ) -> List[TargetInfo]:
        """Format UIA data, call LLM, parse and filter results."""
        text = self._format_uia_data(uia_targets)
        if not text.strip():
            return []

        raw_elements = self._call_model(text)

        # Filter by confidence threshold
        inferred: List[TargetInfo] = []
        for elem in raw_elements:
            confidence = elem.get("confidence", 0.0)
            if confidence < self._min_confidence:
                logger.debug(
                    "Filtered low-confidence inference: %s (%.2f < %.2f)",
                    elem.get("name", "?"),
                    confidence,
                    self._min_confidence,
                )
                continue

            rect = elem.get("rect")
            if rect and len(rect) == 4:
                rect = [int(v) for v in rect]
            else:
                rect = None

            inferred.append(
                TargetInfo(
                    kind=TargetKind.CONTROL,
                    name=elem.get("name", ""),
                    type=elem.get("type", "InferredElement"),
                    rect=rect,
                )
            )

        logger.info(
            "Structural inference found %d elements (%d passed confidence filter)",
            len(raw_elements),
            len(inferred),
        )

        return inferred

    @staticmethod
    def _format_uia_data(targets: List[TargetInfo]) -> str:
        """Format UIA targets as plain text for the LLM prompt."""
        lines = []
        for t in targets:
            rect_str = f"rect={t.rect}" if t.rect else "rect=None"
            lines.append(f'[{t.id}] [{t.type}] "{t.name}" {rect_str}')
        return "\n".join(lines)

    def _call_model(self, uia_text: str) -> List[Dict[str, Any]]:
        """Send the UIA control list to the LLM and parse the response."""
        prompt = _INFERENCE_PROMPT + uia_text

        message = self.client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        # Extract text from response blocks
        response_text = ""
        for block in message.content:
            if block.type == "text":
                response_text += block.text

        return self._parse_response(response_text)

    @staticmethod
    def _parse_response(response_text: str) -> List[Dict[str, Any]]:
        """Parse the model's JSON response, stripping markdown fences if present."""
        text = response_text.strip()

        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines)

        # Extract the JSON array — the model may append reasoning text after it.
        # Find the outermost [...] bracket pair.
        start = text.find("[")
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "[":
                    depth += 1
                elif text[i] == "]":
                    depth -= 1
                    if depth == 0:
                        text = text[start : i + 1]
                        break

        try:
            elements = json.loads(text)
            if not isinstance(elements, list):
                logger.warning(
                    "Structural inference model returned non-list type: %s",
                    type(elements).__name__,
                )
                return []
            return elements
        except json.JSONDecodeError as exc:
            logger.warning(
                "Failed to parse structural inference response: %s", exc
            )
            logger.debug("Raw response (first 500 chars): %s", text[:500])
            return []

