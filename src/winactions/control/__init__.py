"""Control layer — UI control operations and inspection.

Importing this module triggers registration of ControlReceiver commands
via the @ControlReceiver.register decorators in controller.py.
"""

from winactions.control.controller import ControlReceiver, TextTransformer
from winactions.control.inspector import ControlInspectorFacade, BackendFactory

__all__ = [
    "ControlReceiver",
    "TextTransformer",
    "ControlInspectorFacade",
    "BackendFactory",
]
