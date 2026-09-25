import asyncio
import os
import re
import signal
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

from cam_streamer.modes import Mode
from cam_streamer.target import MAX_SLOT, MIN_SLOT, slot_url
from cam_streamer.v4l2 import Camera

_ERROR_TABLE = [
    (re.compile(r"Device or resource busy"), "camera in use by another program"),
    (re.compile(r"(?:ANNOUNCE|SETUP|RECORD|OPTIONS|DESCRIBE) failed: 4\d\d|Server returned 4\d\d|already publishing"), "slot {slot} already in use"),
    (re.compile(r"Connection refused|Connection timed out|No route to host"), "Insight not reachable at {host}:{port}"),
    (re.compile(r"Not a video capture device|(?:video4linux2|/dev/video\d+)[^\n]*Invalid argument"), "mode not accepted, choose another with f"),
]
_VERSION = re.compile(r"ffmpeg version (\d+(?:\.\d+)*)")


def build_command(ffmpeg: str, device: str, mode: Mode, url: str) -> List[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-f", "v4l2", "-input_format", mode.pixel_format,
        "-video_size", "{}x{}".format(mode.width, mode.height), "-framerate", str(mode.fps), "-i", device,
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-g", str(mode.fps), "-pix_fmt", "yuv420p",
        "-f", "rtsp", "-rtsp_transport", "tcp",
        "-progress", "pipe:1",
        url,
    ]


def _number(text: str) -> float:
    match = re.match(r"\s*(-?\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else 0.0


def parse_progress_block(lines: List[str]) -> Tuple[float, float]:
    values = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return _number(values.get("fps", "")), _number(values.get("bitrate", ""))


def classify_exit(returncode: int, stderr_lines: List[str], slot: int, host: str, port: int) -> str:
    if returncode < 0:
        return ""
    if returncode == 0:
        return "camera disconnected"
    text = "\n".join(stderr_lines)
    for pattern, reason in _ERROR_TABLE:
        if pattern.search(text):
            return reason.format(slot=slot, host=host, port=port)
    return "ffmpeg exited (code {}), see log".format(returncode)


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    try:
        result = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    match = _VERSION.search(result.stdout or "")
    return match.group(1) if match else "unknown"


STDERR_KEEP = 40
DEFAULT_LOG_DIR = os.path.join(os.path.expanduser("~"), ".cam-streamer")


@dataclass
class StreamState:
    kind: str
    fps: float = 0.0
    bitrate_kbps: float = 0.0
    stalled: bool = False
    reason: str = ""
    log_path: Optional[str] = None


class SlotInUse(ValueError):
    pass


class CameraInUse(ValueError):
    pass


async def _lines(reader, chunk_size: int = 4096):
    """Yield text lines split on \\r or \\n; a line longer than the chunk size never raises."""
    pending = ""
    while True:
        chunk = await reader.read(chunk_size)
        if not chunk:
            if pending:
                yield pending
            return
        pending += chunk.decode("utf-8", "replace")
        parts = pending.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        pending = parts.pop()
        for line in parts:
            yield line


class Stream:
    def __init__(self, camera: Camera, mode: Mode, slot: int, url: str, argv: Optional[List[str]] = None,
                 ffmpeg: str = "ffmpeg", log_dir: Optional[str] = None, host: str = "127.0.0.1", port: int = 8554,
                 stall_after: float = 5.0, kill_after: float = 2.0):
        self.camera = camera
        self.mode = mode
        self.slot = slot
        self.url = url
        self.argv = argv or build_command(ffmpeg, camera.path, mode, url)
        self.log_dir = log_dir or DEFAULT_LOG_DIR
        self.host = host
        self.port = port
        self.stall_after = stall_after
        self.kill_after = kill_after
        self.done = asyncio.Event()
        self._proc = None  # type: Optional[asyncio.subprocess.Process]
        self._kind = "starting"
        self._fps = 0.0
        self._kbps = 0.0
        self._last_progress = 0.0
        self._reason = ""
        self._log_path = None  # type: Optional[str]
        self._stderr = deque(maxlen=STDERR_KEEP)  # type: Deque[str]
        self._stopping = False
        self._tasks = []  # type: List[asyncio.Task]

    @property
    def state(self) -> StreamState:
        stalled = self._kind == "streaming" and (time.monotonic() - self._last_progress) > self.stall_after
        return StreamState(self._kind, self._fps, self._kbps, stalled, self._reason, self._log_path)

    async def start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.argv, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            self._fail("ffmpeg not found ({})".format(exc.strerror or exc))
            return
        self._tasks = [asyncio.ensure_future(self._read_progress()),
                       asyncio.ensure_future(self._read_stderr()),
                       asyncio.ensure_future(self._wait())]

    async def stop(self) -> None:
        self._stopping = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.send_signal(signal.SIGINT)
                await asyncio.wait_for(proc.wait(), self.kill_after)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            except BaseException:
                if proc.returncode is None:
                    proc.kill()
                raise
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._kind != "error":
            self._kind = "stopped"
        self.done.set()

    async def _read_progress(self) -> None:
        block = []  # type: List[str]
        async for line in _lines(self._proc.stdout):
            block.append(line)
            if line.startswith("progress="):
                self._fps, self._kbps = parse_progress_block(block)
                self._last_progress = time.monotonic()
                if self._kind == "starting":
                    self._kind = "streaming"
                block = []

    async def _read_stderr(self) -> None:
        async for line in _lines(self._proc.stderr):
            self._stderr.append(line)

    async def _wait(self) -> None:
        code = await self._proc.wait()
        await asyncio.gather(self._tasks[0], self._tasks[1], return_exceptions=True)
        if self._stopping:
            return
        if code < 0:
            self._fail("ffmpeg killed by signal {}".format(-code))
            return
        reason = classify_exit(code, list(self._stderr), self.slot, self.host, self.port)
        if reason:
            self._fail(reason)

    def _fail(self, reason: str) -> None:
        self._kind = "error"
        self._log_path = self._write_log()
        self._reason = reason.replace("see log", "see " + (self._log_path or "log"))
        self.done.set()

    def _write_log(self) -> Optional[str]:
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            path = os.path.join(self.log_dir, "src{}.log".format(self.slot))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(" ".join(self.argv) + "\n\n" + "\n".join(self._stderr) + "\n")
            return path
        except OSError:
            return None


class StreamSet:
    def __init__(self, host: str, port: int, ffmpeg: str = "ffmpeg", log_dir: Optional[str] = None,
                 stream_factory: Callable = Stream):
        self.host = host
        self.port = port
        self.ffmpeg = ffmpeg
        self.log_dir = log_dir
        self._factory = stream_factory
        self._streams = {}  # type: Dict[int, Stream]

    @property
    def streams(self) -> List[Stream]:
        return [self._streams[slot] for slot in sorted(self._streams)]

    def stream_for(self, camera_path: str) -> Optional[Stream]:
        for s in self._streams.values():
            if s.camera.path == camera_path:
                return s
        return None

    def next_free_slot(self) -> Optional[int]:
        for slot in range(MIN_SLOT, MAX_SLOT + 1):
            if slot not in self._streams:
                return slot
        return None

    async def start(self, camera: Camera, mode: Mode, slot: int) -> Stream:
        if slot in self._streams:
            raise SlotInUse("slot {} already used".format(slot))
        if self.stream_for(camera.path) is not None:
            raise CameraInUse("{} is already streaming".format(camera.name))
        s = self._factory(camera, mode, slot, slot_url(self.host, self.port, slot),
                          ffmpeg=self.ffmpeg, log_dir=self.log_dir, host=self.host, port=self.port)
        self._streams[slot] = s
        await s.start()
        return s

    async def stop(self, slot: int) -> None:
        s = self._streams.get(slot)
        if s is None:
            return
        try:
            await s.stop()
        finally:
            self._streams.pop(slot, None)

    async def restart(self, slot: int, mode: Mode) -> Stream:
        old = self._streams[slot]
        await self.stop(slot)
        return await self.start(old.camera, mode, slot)

    async def stop_all(self) -> None:
        slots = list(self._streams)
        await asyncio.gather(*(self.stop(slot) for slot in slots))
