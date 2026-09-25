from dataclasses import dataclass
from typing import List, Optional, Tuple

MAX_WIDTH = 1280
MAX_HEIGHT = 720
MAX_FPS = 30
FORMAT_RANK = {"mjpeg": 0, "yuyv422": 1}


@dataclass(frozen=True)
class Mode:
    pixel_format: str
    width: int
    height: int
    fps: int

    @property
    def label(self) -> str:
        return "{}x{} {}fps".format(self.width, self.height, self.fps)

    @property
    def area(self) -> int:
        return self.width * self.height


def _sort_key(mode: Mode) -> Tuple:
    over_cap = mode.width > MAX_WIDTH or mode.height > MAX_HEIGHT
    over_fps = mode.fps > MAX_FPS
    # Staying inside the caps outranks the format preference: the caps bound host CPU.
    area = -mode.area if not over_cap else mode.area
    fps = -mode.fps if not over_fps else mode.fps
    return (over_cap, over_fps, FORMAT_RANK.get(mode.pixel_format, 2), area, fps)


def sort_best_first(modes: List[Mode]) -> List[Mode]:
    return sorted(modes, key=_sort_key)


def pick_default(modes: List[Mode]) -> Optional[Mode]:
    ordered = sort_best_first(modes)
    return ordered[0] if ordered else None
