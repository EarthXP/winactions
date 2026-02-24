"""Perception layer — UI state detection and indexing."""

from winactions.perception.provider import (
    StateProvider,
    UIAStateProvider,
    CompositeStateProvider,
)
from winactions.perception.state import UIState

# VisionStateProvider requires the optional 'anthropic' package.
# Import conditionally so the rest of the package works without it.
try:
    from winactions.perception.vision_provider import VisionStateProvider
except ImportError:
    VisionStateProvider = None  # type: ignore[assignment,misc]

# StructuralInferenceProvider also requires 'anthropic'.
try:
    from winactions.perception.structural_provider import StructuralInferenceProvider
except ImportError:
    StructuralInferenceProvider = None  # type: ignore[assignment,misc]

__all__ = [
    "StateProvider",
    "UIAStateProvider",
    "CompositeStateProvider",
    "VisionStateProvider",
    "StructuralInferenceProvider",
    "UIState",
]
