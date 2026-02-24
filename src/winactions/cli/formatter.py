"""Output formatting for CLI — text (human) and JSON (agent) modes."""

from __future__ import annotations

import io
import json
import os
import sys
from typing import Any, Dict, List


def _safe_print(text: str, file=None) -> None:
    """Print text, handling encoding errors on Windows consoles (e.g. GBK).

    Falls back through progressively more robust strategies:
    1. Normal print (works when stdout is UTF-8)
    2. Encode→decode with replacement (handles GBK codec failures)
    3. Raw UTF-8 bytes to underlying buffer (last resort)
    """
    file = file or sys.stdout
    try:
        print(text, file=file)
    except UnicodeEncodeError:
        # Stream encoding can't represent some chars — replace them
        encoding = getattr(file, "encoding", "utf-8") or "utf-8"
        safe = text.encode(encoding, errors="replace").decode(encoding)
        try:
            print(safe, file=file)
        except Exception:
            _write_bytes_fallback(safe + "\n", file)
    except Exception:
        # Broken pipe, closed stream, etc.
        _write_bytes_fallback(text + "\n", file)


def _write_bytes_fallback(text: str, file) -> None:
    """Write UTF-8 bytes directly to the file's underlying buffer."""
    try:
        buf = getattr(file, "buffer", None)
        if buf is None:
            buf = getattr(sys.stderr, "buffer", None)
        if buf is not None:
            buf.write(text.encode("utf-8", errors="replace"))
            buf.flush()
    except Exception:
        pass  # Nothing more we can do


def output(data: Any, as_json: bool = False) -> None:
    """Print data to stdout in the requested format."""
    if as_json:
        if isinstance(data, str):
            try:
                parsed = json.loads(data)
                _safe_print(json.dumps(parsed, indent=2, ensure_ascii=False))
            except (json.JSONDecodeError, TypeError):
                _safe_print(json.dumps({"message": data}, ensure_ascii=False))
        elif isinstance(data, dict) or isinstance(data, list):
            _safe_print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            _safe_print(json.dumps({"result": str(data)}, ensure_ascii=False))
    else:
        if isinstance(data, str):
            _safe_print(data)
        elif isinstance(data, dict):
            for k, v in data.items():
                _safe_print(f"{k}: {v}")
        elif isinstance(data, list):
            for item in data:
                _safe_print(str(item))
        else:
            _safe_print(str(data))


def output_error(message: str, as_json: bool = False) -> None:
    """Print an error message to stderr."""
    if as_json:
        _safe_print(
            json.dumps({"error": message}, ensure_ascii=False),
            file=sys.stderr,
        )
    else:
        _safe_print(f"Error: {message}", file=sys.stderr)


def format_windows_list(
    windows: List[Dict[str, Any]], as_json: bool = False
) -> str:
    """Format a list of windows for display."""
    if as_json:
        return json.dumps(windows, indent=2, ensure_ascii=False)
    lines = []
    for w in windows:
        idx = w.get("id", "?")
        title = w.get("title", "")
        process = w.get("process", "")
        lines.append(f"[{idx}] {title} ({process})")
    return "\n".join(lines)
