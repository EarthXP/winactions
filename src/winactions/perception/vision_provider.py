"""VisionStateProvider — multimodal model-based UI element detection.

Uses the Anthropic Claude API to analyze a screenshot and detect
interactive UI elements that are invisible to UIA (resize handles,
column borders, icon-only buttons, canvas elements, etc.).

Design note (UFO Tier-2 strategy):
  Vision-detected elements are wrapped as TargetInfo with an assigned
  index number.  The agent only ever outputs the index — the framework
  computes the bbox centre coordinate and executes the click.  The agent
  never predicts coordinates directly.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import platform
from io import BytesIO
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from winactions.targets import TargetInfo, TargetKind

if TYPE_CHECKING or platform.system() == "Windows":
    from pywinauto.controls.uiawrapper import UIAWrapper
else:
    UIAWrapper = Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detection prompt — tells the model what to look for
# ---------------------------------------------------------------------------

_DETECTION_PROMPT = """\
You are a UI element detector for Windows desktop applications.

Analyze this screenshot and identify **interactive UI elements that are \
typically NOT exposed through Windows UI Automation (UIA) accessibility APIs**.

Focus on these element categories:
1. Resize handles — small squares (■) at corners/edges of selected tables, \
images, or text boxes that can be dragged to resize the object. \
Also window edges/corners and panel grips.
2. Column / row borders — thin vertical or horizontal lines inside tables \
or list headers that can be dragged to resize columns/rows. Look carefully \
at any visible table for draggable border lines between columns and rows.
3. Icon-only buttons or toolbar icons with no text label
4. Canvas elements (drawing surfaces, chart data points, diagram nodes)
5. Splitter bars between panes
6. Custom-drawn controls (toggle switches, colour pickers, non-standard sliders)
7. Clickable status indicators or visual badges
8. Drop zones or drag targets

IMPORTANT: If you see a table in the screenshot, always check for:
- A resize handle (small square ■) at the bottom-right corner of the table
- Draggable column borders (vertical lines between column headers)
- Draggable row borders (horizontal lines between rows)
These are critical interactive elements invisible to accessibility APIs.

Do NOT include elements that standard accessibility APIs already expose:
- Buttons with visible text labels
- Text input fields / edit boxes
- Menu items and menu bars
- Tab items with text
- List items, tree items, data items
- Standard checkboxes, radio buttons, combo boxes, scroll bars

For each element found, return a JSON array of objects:
```json
[
  {
    "name": "short descriptive name",
    "type": "ResizeHandle | ColumnBorder | IconButton | CanvasElement | Splitter | CustomControl | StatusIndicator | DragTarget",
    "rect": [left, top, right, bottom]
  }
]
```

Coordinates are **pixel positions relative to the screenshot image** \
(0, 0 is the top-left corner of the image).

Return ONLY the JSON array. If no such elements are found, return `[]`."""

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_MAX_TOKENS = 4096

# Maximum dimension (longest side) for the image sent to the vision API.
# Large screenshots are resized to this limit before sending, and the
# model's returned coordinates are scaled back to the original resolution.
# Claude internally resizes images exceeding ~1568px; we do it ourselves
# so we know the exact scale factor for coordinate conversion.
_MAX_IMAGE_LONG_SIDE = 1568

class VisionStateProvider:
    """Perception source using a multimodal model API for visual element detection.

    Detects UI elements invisible to UIA by sending a window screenshot to
    the Anthropic Claude API and parsing the structured response.

    Detected elements include rect coordinates in the ``state`` output so
    the agent can use ``click-at`` / ``drag-at`` directly.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Anthropic API key required for vision detection. "
                "Set the ANTHROPIC_API_KEY environment variable or pass "
                "api_key= to VisionStateProvider."
            )
        self._base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL")
        self._model = model
        self._max_tokens = max_tokens
        self._client = None  # lazy-initialized

    @property
    def client(self):
        """Lazy-initialize the Anthropic client."""
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError(
                    "The 'anthropic' package is required for vision detection. "
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
        """Detect visual-only UI elements via multimodal model.

        Returns ``(targets, controls)`` where *controls* is a list of
        ``None`` values — vision-detected elements have no UIAWrapper
        handle.  The ``state`` output includes rect coordinates so the
        agent can use ``click-at`` / ``drag-at`` directly.
        """
        try:
            # 1. Capture window screenshot → raw base64 + scale factors
            image_b64, scale_x, scale_y = self._capture_screenshot(window)

            # 2. Window rect for coordinate conversion
            #    (screenshot coords are window-relative; TargetInfo.rect
            #     must be absolute screen coordinates)
            win_rect = window.rectangle()

            # 3. Call the vision model
            raw_elements = self._call_model(image_b64)

            # 4. Convert to TargetInfo with absolute screen coordinates
            #    Model returns coords in the (possibly resized) image space.
            #    Scale back to original screenshot resolution, then offset
            #    by the window position on screen.
            targets: List[TargetInfo] = []
            for elem in raw_elements:
                rect = elem.get("rect")
                if rect and len(rect) == 4:
                    abs_rect = [
                        int(round(rect[0] * scale_x)) + win_rect.left,
                        int(round(rect[1] * scale_y)) + win_rect.top,
                        int(round(rect[2] * scale_x)) + win_rect.left,
                        int(round(rect[3] * scale_y)) + win_rect.top,
                    ]
                else:
                    abs_rect = None

                targets.append(
                    TargetInfo(
                        kind=TargetKind.CONTROL,
                        name=elem.get("name", ""),
                        type=elem.get("type", "VisionElement"),
                        rect=abs_rect,
                    )
                )

            controls: List[Optional[UIAWrapper]] = [None] * len(targets)
            logger.info(
                "Vision detection found %d elements (scale %.2fx%.2f)",
                len(targets), scale_x, scale_y,
            )

            return targets, controls

        except Exception as e:
            logger.warning("Vision detection failed, returning empty: %s", e)
            return [], []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _capture_screenshot(
        self, window: UIAWrapper
    ) -> tuple[str, float, float]:
        """Capture the window, resize if needed, return (base64, scale_x, scale_y).

        The image is resized so that its longest side does not exceed
        ``_MAX_IMAGE_LONG_SIDE``.  The scale factors map the resized
        image coordinates back to the original screenshot resolution:

            original_x = resized_x * scale_x

        This is necessary because the vision API may internally resize
        large images, making the model's returned coordinates inconsistent
        with the original screenshot resolution.  By resizing ourselves we
        know the exact mapping.
        """
        from winactions.screenshot.photographer import PhotographerFacade

        facade = PhotographerFacade()
        image = facade.capture_app_window_screenshot(window)

        orig_w, orig_h = image.size
        long_side = max(orig_w, orig_h)

        if long_side > _MAX_IMAGE_LONG_SIDE:
            ratio = _MAX_IMAGE_LONG_SIDE / long_side
            new_w = int(round(orig_w * ratio))
            new_h = int(round(orig_h * ratio))
            from PIL import Image as PILImage

            image = image.resize((new_w, new_h), PILImage.LANCZOS)
            scale_x = orig_w / new_w
            scale_y = orig_h / new_h
            logger.debug(
                "Screenshot resized %dx%d → %dx%d (scale %.2fx%.2f)",
                orig_w, orig_h, new_w, new_h, scale_x, scale_y,
            )
        else:
            scale_x = 1.0
            scale_y = 1.0

        buffered = BytesIO()
        if image.mode not in ("RGB", "RGBA", "L", "P"):
            image = image.convert("RGB")
        image.save(buffered, format="PNG", optimize=True)
        b64 = base64.b64encode(buffered.getvalue()).decode("ascii")
        return b64, scale_x, scale_y

    def _call_model(self, image_b64: str) -> List[Dict[str, Any]]:
        """Send the screenshot to the Anthropic API and parse the response."""
        message = self.client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": _DETECTION_PROMPT,
                        },
                    ],
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

        # Strip markdown code fences (```json … ``` or ``` … ```)
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
                    "Vision model returned non-list type: %s", type(elements).__name__
                )
                return []
            return elements
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse vision model response: %s", exc)
            logger.debug("Raw response (first 500 chars): %s", text[:500])
            return []
