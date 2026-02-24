"""Screenshot capture and annotation module (optional, requires Pillow)."""

from winactions.screenshot.photographer import (
    PhotographerFacade,
    PhotographerFactory,
    AnnotationDecorator,
    ControlPhotographer,
    DesktopPhotographer,
)

__all__ = [
    "PhotographerFacade",
    "PhotographerFactory",
    "AnnotationDecorator",
    "ControlPhotographer",
    "DesktopPhotographer",
]
