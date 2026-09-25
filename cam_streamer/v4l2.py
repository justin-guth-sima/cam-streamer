import errno
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Callable, List, Set, Tuple

from cam_streamer.modes import Mode

VIDIOC_QUERYCAP = 0x80685600
VIDIOC_ENUM_FMT = 0xC0405602
VIDIOC_ENUM_FRAMESIZES = 0xC02C564A
VIDIOC_ENUM_FRAMEINTERVALS = 0xC034564B
V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_META_CAPTURE = 0x00800000
V4L2_CAP_DEVICE_CAPS = 0x80000000
V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
TYPE_DISCRETE = 1
QUERYCAP_SIZE = 104
FMTDESC_SIZE = 64
FRMSIZE_SIZE = 44
FRMIVAL_SIZE = 52
COMMON_RATES = (30, 15)
FOURCC_TO_FFMPEG = {
    "MJPG": "mjpeg", "YUYV": "yuyv422", "UYVY": "uyvy422", "NV12": "nv12",
    "YU12": "yuv420p", "RGB3": "rgb24", "BGR3": "bgr24", "GREY": "gray", "H264": "h264",
}


@dataclass(frozen=True)
class Camera:
    path: str
    name: str
    modes: List[Mode] = field(default_factory=list)
    note: str = ""


def _ioctl(fd: int, request: int, buf: bytearray) -> None:
    import fcntl  # POSIX-only; imported here so the module loads on every platform
    fcntl.ioctl(fd, request, buf, True)


def _enumerate(ioctl: Callable, fd: int, request: int, size: int, prefill: bytes):
    """Yield filled buffers for index 0, 1, ... until the driver answers EINVAL."""
    index = 0
    while True:
        buf = bytearray(size)
        buf[4:4 + len(prefill)] = prefill
        struct.pack_into("<I", buf, 0, index)
        try:
            ioctl(fd, request, buf)
        except OSError as exc:
            if exc.errno == errno.EINVAL:
                return
            raise
        yield buf
        index += 1


def _device_caps(ioctl: Callable, fd: int) -> int:
    buf = bytearray(QUERYCAP_SIZE)
    ioctl(fd, VIDIOC_QUERYCAP, buf)
    capabilities, device_caps = struct.unpack_from("<II", buf, 84)
    return device_caps if capabilities & V4L2_CAP_DEVICE_CAPS else capabilities


def _rates(ioctl: Callable, fd: int, pixel_format: int, width: int, height: int) -> Set[int]:
    prefill = struct.pack("<III", pixel_format, width, height)
    rates = set()
    for buf in _enumerate(ioctl, fd, VIDIOC_ENUM_FRAMEINTERVALS, FRMIVAL_SIZE, prefill):
        kind = struct.unpack_from("<I", buf, 16)[0]
        if kind == TYPE_DISCRETE:
            num, den = struct.unpack_from("<II", buf, 20)
            if num:
                rates.add(round(den / num))
        else:
            min_n, min_d, max_n, max_d = struct.unpack_from("<IIII", buf, 20)
            # Intervals are seconds per frame; fps is the inverse, so the bounds swap.
            bounds = sorted((min_d / min_n if min_n else 0, max_d / max_n if max_n else 0))
            rates.update(r for r in COMMON_RATES if bounds[0] <= r <= bounds[1])
            break
    return rates


def _sizes(ioctl: Callable, fd: int, pixel_format: int) -> List[Tuple[int, int]]:
    sizes = []
    for buf in _enumerate(ioctl, fd, VIDIOC_ENUM_FRAMESIZES, FRMSIZE_SIZE, struct.pack("<I", pixel_format)):
        kind = struct.unpack_from("<I", buf, 8)[0]
        if kind == TYPE_DISCRETE:
            sizes.append(struct.unpack_from("<II", buf, 12))
        else:
            _min_w, max_w, _sw, _min_h, max_h, _sh = struct.unpack_from("<IIIIII", buf, 12)
            sizes.append((max_w, max_h))
            break
    return sizes


def _modes(ioctl: Callable, fd: int) -> List[Mode]:
    modes = []
    for buf in _enumerate(ioctl, fd, VIDIOC_ENUM_FMT, FMTDESC_SIZE, struct.pack("<I", V4L2_BUF_TYPE_VIDEO_CAPTURE)):
        pixel_format = struct.unpack_from("<I", buf, 44)[0]
        name = FOURCC_TO_FFMPEG.get(struct.pack("<I", pixel_format).decode("ascii", "replace"))
        if not name:
            continue
        for width, height in _sizes(ioctl, fd, pixel_format):
            for fps in sorted(_rates(ioctl, fd, pixel_format, width, height), reverse=True):
                modes.append(Mode(name, width, height, fps))
    return modes


def _node_number(node: str) -> int:
    match = re.search(r"(\d+)$", node)
    return int(match.group(1)) if match else -1


def _display_name(raw: str) -> str:
    """Collapse a sysfs name of the form "X: X" (kernel joining identical USB product and card names) to "X"."""
    head, sep, tail = raw.partition(": ")
    if sep and head.strip() == tail.strip():
        return head.strip()
    return raw


def discover(sysfs_root: str = "/sys/class/video4linux", dev_root: str = "/dev",
             ioctl: Callable = _ioctl, opener: Callable = os.open) -> List[Camera]:
    cameras = []
    try:
        nodes = sorted(os.listdir(sysfs_root), key=_node_number)
    except OSError:
        return cameras
    for node in nodes:
        path = os.path.join(dev_root, node)
        if not os.path.exists(path):
            continue
        try:
            with open(os.path.join(sysfs_root, node, "name"), encoding="utf-8", errors="replace") as fh:
                name = _display_name(fh.read().strip()) or node
        except OSError:
            name = node
        try:
            fd = opener(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno == errno.EBUSY:
                cameras.append(Camera(path, name, [], "busy"))
            elif exc.errno in (errno.EACCES, errno.EPERM):
                cameras.append(Camera(path, name, [], "no permission"))
            continue
        try:
            caps = _device_caps(ioctl, fd)
            if not caps & V4L2_CAP_VIDEO_CAPTURE or caps & V4L2_CAP_META_CAPTURE:
                continue
            cameras.append(Camera(path, name, _modes(ioctl, fd), ""))
        except OSError:
            continue
        finally:
            os.close(fd)
    return cameras
