"""Screenshot capture and annotation facilities.

Adapted from ufo/automator/ui_control/screenshot.py with config decoupled.
"""

from __future__ import annotations

import base64
import functools
import logging
import os
import platform
from abc import ABC, abstractmethod
from io import BytesIO
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING, Any

from PIL import Image, ImageDraw, ImageFont, ImageGrab

if TYPE_CHECKING or platform.system() == "Windows":
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.win32structures import RECT
else:
    UIAWrapper = Any
    RECT = Any

if TYPE_CHECKING:
    from winactions.targets import TargetInfo

from winactions._utils import coordinate_adjusted

logger = logging.getLogger(__name__)

DEFAULT_PNG_COMPRESS_LEVEL = 6
DEFAULT_ANNOTATION_FONT_SIZE = 25
DEFAULT_ANNOTATION_COLORS: Dict[str, str] = {}


class Photographer(ABC):
    """Abstract class for the photographer."""

    @abstractmethod
    def capture(self) -> Image.Image:
        pass

    @staticmethod
    def rescale_image(image: Image.Image, scaler: List[int]) -> Image.Image:
        raw_width, raw_height = image.size
        scale_ratio = min(scaler[0] / raw_width, scaler[1] / raw_height)
        new_width = int(raw_width * scale_ratio)
        new_height = int(raw_height * scale_ratio)
        resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        new_image = Image.new("RGB", scaler, (0, 0, 0))
        new_image.paste(resized_image, (0, 0))
        return new_image


class ControlPhotographer(Photographer):
    """Capture a screenshot of a specific control/window."""

    def __init__(self, control: UIAWrapper):
        self.control = control

    def capture(self, save_path: str = None, scalar: List[int] = None) -> Image.Image:
        screenshot = self.control.capture_as_image()
        if scalar is not None:
            screenshot = self.rescale_image(screenshot, scalar)
        if save_path is not None and screenshot is not None:
            screenshot.save(save_path, compress_level=DEFAULT_PNG_COMPRESS_LEVEL)
        return screenshot


class DesktopPhotographer(Photographer):
    """Capture a screenshot of the entire desktop."""

    def __init__(self, all_screens=True) -> None:
        self.all_screens = all_screens

    def capture(self, save_path: str = None, scalar: List[int] = None) -> Image.Image:
        screenshot = ImageGrab.grab(all_screens=self.all_screens)
        if scalar is not None:
            screenshot = self.rescale_image(screenshot, scalar)
        if save_path is not None and screenshot is not None:
            screenshot.save(save_path, compress_level=DEFAULT_PNG_COMPRESS_LEVEL)
        return screenshot


class PhotographerDecorator(Photographer):
    """Decorator base for photographers."""

    def __init__(self, photographer: Photographer) -> None:
        self.photographer = photographer

    def capture(self, save_path=None) -> Image.Image:
        return self.photographer.capture(save_path)


class AnnotationDecorator(PhotographerDecorator):
    """Annotate controls on a screenshot with numbered labels."""

    def __init__(
        self,
        screenshot: Image.Image,
        sub_control_list: List[UIAWrapper],
        annotation_type: str = "number",
        color_diff: bool = True,
        color_default: str = "#FFF68F",
    ) -> None:
        super().__init__(screenshot)
        self.sub_control_list = sub_control_list
        self.annotation_type = annotation_type
        self.color_diff = color_diff
        self.color_default = color_default

    @staticmethod
    def draw_rectangles_controls(
        image: Image.Image,
        coordinate: tuple,
        label_text: str,
        botton_margin: int = 5,
        border_width: int = 2,
        font_size: int = 25,
        font_color: str = "#000000",
        border_color: str = "#FF0000",
        button_color: str = "#FFF68F",
    ) -> Image.Image:
        button_img = AnnotationDecorator._get_button_img(
            label_text,
            botton_margin=botton_margin,
            border_width=border_width,
            font_size=font_size,
            font_color=font_color,
            border_color=border_color,
            button_color=button_color,
        )
        image.paste(button_img, (coordinate[0], coordinate[1]))
        return image

    @staticmethod
    @functools.lru_cache(maxsize=2048, typed=False)
    def _get_button_img(
        label_text: str,
        botton_margin: int = 5,
        border_width: int = 2,
        font_size: int = 25,
        font_color: str = "#000000",
        border_color: str = "#FF0000",
        button_color: str = "#FFF68F",
    ):
        font = AnnotationDecorator._get_font("arial.ttf", font_size)
        text_size = font.getbbox(label_text)
        button_size = (text_size[2] + botton_margin, text_size[3] + botton_margin)
        button_img = Image.new("RGBA", button_size, button_color)
        button_draw = ImageDraw.Draw(button_img)
        button_draw.text(
            (botton_margin / 2, botton_margin / 2),
            label_text,
            font=font,
            fill=font_color,
        )
        ImageDraw.Draw(button_img).rectangle(
            [(0, 0), (button_size[0] - 1, button_size[1] - 1)],
            outline=border_color,
            width=border_width,
        )
        return button_img

    @staticmethod
    @functools.lru_cache(maxsize=64, typed=False)
    def _get_font(name: str, size: int):
        return ImageFont.truetype(name, size)

    def get_annotation_dict(self) -> Dict[str, UIAWrapper]:
        annotation_dict = {}
        for i, control in enumerate(self.sub_control_list):
            label_text = str(i + 1)
            annotation_dict[label_text] = control
        return annotation_dict

    def capture_with_annotation_dict(
        self,
        annotation_dict: Dict[str, UIAWrapper],
        save_path: Optional[str] = None,
        path: Optional[str] = None,
        highlight_bbox: bool = False,
    ) -> Image.Image:
        window_rect = self.photographer.control.rectangle()
        if path and os.path.exists(path):
            screenshot_annotated = Image.open(path)
        else:
            screenshot_annotated = self.photographer.capture()

        color_dict = DEFAULT_ANNOTATION_COLORS

        if highlight_bbox:
            overlay = Image.new("RGBA", screenshot_annotated.size, (255, 255, 255, 0))
            overlay_draw = ImageDraw.Draw(overlay)

            for label_text, control in annotation_dict.items():
                control_rect = control.rectangle()
                adjusted_rect = coordinate_adjusted(window_rect, control_rect)

                button_color = (
                    color_dict.get(
                        control.element_info.control_type, self.color_default
                    )
                    if self.color_diff
                    else self.color_default
                )

                if button_color.startswith("#"):
                    rgb = tuple(int(button_color[i : i + 2], 16) for i in (1, 3, 5))
                    rgba_color = rgb + (80,)
                else:
                    rgba_color = (255, 246, 143, 80)

                overlay_draw.rectangle(
                    adjusted_rect,
                    fill=rgba_color,
                    outline=(255, 160, 160, 180),
                    width=2,
                )

            screenshot_annotated = Image.alpha_composite(
                screenshot_annotated.convert("RGBA"), overlay
            ).convert("RGB")

        for label_text, control in annotation_dict.items():
            control_rect = control.rectangle()
            adjusted_rect = coordinate_adjusted(window_rect, control_rect)
            adjusted_coordinate = (adjusted_rect[0], adjusted_rect[1])
            screenshot_annotated = self.draw_rectangles_controls(
                screenshot_annotated,
                adjusted_coordinate,
                label_text,
                font_size=DEFAULT_ANNOTATION_FONT_SIZE,
                button_color=(
                    color_dict.get(
                        control.element_info.control_type, self.color_default
                    )
                    if self.color_diff
                    else self.color_default
                ),
            )

        if save_path is not None and screenshot_annotated is not None:
            screenshot_annotated.save(
                save_path, compress_level=DEFAULT_PNG_COMPRESS_LEVEL
            )
        return screenshot_annotated

    def capture(self, save_path: Optional[str] = None) -> Image.Image:
        annotation_dict = self.get_annotation_dict()
        return self.capture_with_annotation_dict(annotation_dict, save_path)


class PhotographerFactory:
    @staticmethod
    def create_screenshot(screenshot_type: str, *args, **kwargs):
        if screenshot_type == "app_window":
            return ControlPhotographer(*args, **kwargs)
        elif screenshot_type == "desktop_window":
            return DesktopPhotographer(*args, **kwargs)
        else:
            raise ValueError("Invalid screenshot type")


class PhotographerFacade:
    """Facade for screenshot capture and annotation."""

    _instance = None
    _empty_image_string = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.screenshot_factory = PhotographerFactory()
        return cls._instance

    def capture_app_window_screenshot(
        self, control: UIAWrapper, save_path=None, scalar: List[int] = None
    ) -> Image.Image:
        screenshot = self.screenshot_factory.create_screenshot("app_window", control)
        return screenshot.capture(save_path, scalar)

    def capture_desktop_screen_screenshot(
        self, all_screens=True, save_path=None
    ) -> Image.Image:
        screenshot = self.screenshot_factory.create_screenshot(
            "desktop_window", all_screens
        )
        return screenshot.capture(save_path)

    def capture_app_window_screenshot_with_annotation(
        self,
        control: UIAWrapper,
        sub_control_list: List[UIAWrapper],
        annotation_type: str = "number",
        color_diff: bool = True,
        color_default: str = "#FFF68F",
        save_path: Optional[str] = None,
    ) -> Image.Image:
        screenshot = self.screenshot_factory.create_screenshot("app_window", control)
        screenshot = AnnotationDecorator(
            screenshot, sub_control_list, annotation_type, color_diff, color_default
        )
        return screenshot.capture(save_path)

    def get_annotation_dict(
        self,
        control: UIAWrapper,
        sub_control_list: List[UIAWrapper],
        annotation_type: str = "number",
    ) -> Dict[str, UIAWrapper]:
        screenshot = self.screenshot_factory.create_screenshot("app_window", control)
        screenshot = AnnotationDecorator(screenshot, sub_control_list, annotation_type)
        return screenshot.get_annotation_dict()

    @staticmethod
    def target_info_iou(target1: "TargetInfo", target2: "TargetInfo") -> float:
        """Calculate IOU overlap between two TargetInfo objects."""
        if not target1.rect or not target2.rect:
            return 0.0

        r1_left, r1_top, r1_right, r1_bottom = target1.rect
        r2_left, r2_top, r2_right, r2_bottom = target2.rect

        left = max(r1_left, r2_left)
        top = max(r1_top, r2_top)
        right = min(r1_right, r2_right)
        bottom = min(r1_bottom, r2_bottom)

        intersection_area = max(0, right - left) * max(0, bottom - top)
        area1 = (r1_right - r1_left) * (r1_bottom - r1_top)
        area2 = (r2_right - r2_left) * (r2_bottom - r2_top)

        union_area = area1 + area2 - intersection_area
        if union_area == 0:
            return 0.0
        return intersection_area / union_area

    @staticmethod
    def merge_target_info_list(
        main_target_list: List["TargetInfo"],
        additional_target_list: List["TargetInfo"],
        iou_overlap_threshold: float = 0.1,
    ) -> List["TargetInfo"]:
        """Merge two TargetInfo lists, removing overlapping targets from additional list."""
        merged = main_target_list.copy()
        for additional in additional_target_list:
            is_overlapping = False
            for main in main_target_list:
                if PhotographerFacade.target_info_iou(additional, main) > iou_overlap_threshold:
                    is_overlapping = True
                    break
            if not is_overlapping:
                merged.append(additional)
        return merged

    @classmethod
    def encode_image(cls, image: Image.Image, mime_type: Optional[str] = None) -> str:
        """Encode an image to base64 data URL string."""
        if image is None:
            return cls._empty_image_string
        try:
            buffered = BytesIO()
            if image.mode not in ["RGB", "RGBA", "L", "P"]:
                image = image.convert("RGB")
            image.save(buffered, format="PNG", optimize=True)
            if mime_type is None:
                mime_type = "image/png"
            encoded_image = base64.b64encode(buffered.getvalue()).decode("ascii")
            return f"data:{mime_type};base64,{encoded_image}"
        except Exception as e:
            logger.error(f"Error encoding image: {e}")
            return cls._empty_image_string
