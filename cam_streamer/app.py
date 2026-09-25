from typing import Callable, List, Optional

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label, ListItem, ListView, OptionList, Static

from cam_streamer.modes import Mode, pick_default, sort_best_first
from cam_streamer.stream import CameraInUse, SlotInUse, Stream, StreamSet
from cam_streamer.target import MAX_SLOT, MIN_SLOT, url_pattern
from cam_streamer.v4l2 import Camera, discover as discover_cameras

REFRESH_SECONDS = 0.5
RESCAN_SECONDS = 2.0


def render_status(stream: Stream) -> Text:
    state = stream.state
    if state.kind == "starting":
        return Text("… starting", style="grey62")
    if state.kind == "streaming":
        if state.stalled:
            return Text("● no frames", style="yellow")
        if state.bitrate_kbps > 0:
            return Text("● {:.1f} fps {:.1f} Mb/s".format(state.fps, state.bitrate_kbps / 1000.0), style="green")
        return Text("● {:.1f} fps".format(state.fps), style="green")
    if state.kind == "error":
        return Text("✗ " + state.reason, style="red")
    return Text("stopped", style="dim")


def describe(camera: Optional[Camera], stream: Optional[Stream]) -> Text:
    """One line about the highlighted camera: identity, capture modes, and the stream URL when it runs."""
    if camera is None:
        return Text("")
    parts = [camera.name, camera.path]
    if camera.note:
        parts.append(camera.note)
    elif not camera.modes:
        parts.append("no usable modes")
    else:
        auto = pick_default(camera.modes)
        parts.append("{} mode{}".format(len(camera.modes), "" if len(camera.modes) == 1 else "s"))
        parts.append("auto {} {}".format(auto.pixel_format, auto.label))
    if stream is not None:
        parts.append("slot {}".format(stream.slot))
        parts.append(stream.url)
    return Text("   ".join(parts))


class SlotPrompt(ModalScreen):
    """Asks for a slot number; dismisses with an int, or None on escape."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, camera: Camera, default_slot: int):
        super().__init__()
        self.camera = camera
        self.default_slot = default_slot

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Slot for {} (1 to {})".format(self.camera.name, MAX_SLOT))
            yield Input(value=str(self.default_slot), id="slot")
            yield Static("", id="error")

    def on_mount(self) -> None:
        self.query_one("#slot", Input).focus()

    @on(Input.Submitted, "#slot")
    def submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if text.isdigit() and MIN_SLOT <= int(text) <= MAX_SLOT:
            self.dismiss(int(text))
            return
        self.query_one("#error", Static).update(Text("Enter a number from 1 to {}".format(MAX_SLOT), style="red"))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ModePicker(ModalScreen):
    """Lists a camera's modes best first; dismisses with a Mode, or None on escape."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, camera: Camera):
        super().__init__()
        self.camera = camera
        self.modes = sort_best_first(camera.modes)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Format for {}".format(self.camera.name))
            options = []
            for index, mode in enumerate(self.modes):
                suffix = "  (auto)" if index == 0 else ""
                options.append("{:<8} {}{}".format(mode.pixel_format, mode.label, suffix))
            yield OptionList(*options, id="modes")

    def on_mount(self) -> None:
        self.query_one("#modes", OptionList).focus()

    @on(OptionList.OptionSelected, "#modes")
    def select(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.modes[event.option_index])

    def action_cancel(self) -> None:
        self.dismiss(None)


class CameraList(ListView):
    BINDINGS = [Binding("right", "app.focus_streams", "streams", show=False)]


class StreamTable(DataTable):
    BINDINGS = [Binding("left", "app.focus_cameras", "cameras", show=False)]


class CameraItem(ListItem):
    def __init__(self, camera: Camera):
        label = Text(camera.name)
        if camera.note:
            label.append("  ({})".format(camera.note), style="dim")
        super().__init__(Label(label))
        self.camera = camera


class CamApp(App):
    CSS = """
    #header { height: 1; padding: 0 1; }
    #details { height: 1; padding: 0 1; }
    #message { height: 1; padding: 0 1; color: $text-muted; }
    #cameras { width: 36; border: round $primary; }
    #streams { border: round $primary; }
    #dialog { width: 60; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    SlotPrompt, ModePicker { align: center middle; }
    """
    BINDINGS = [
        Binding("s", "stop", "stop"),
        Binding("f", "format", "format"),
        Binding("r", "rescan", "rescan"),
        Binding("q", "quit", "quit"),
        Binding("ctrl+c", "quit", "quit", show=False, priority=True),
    ]

    def __init__(self, host: str, port: int, ffmpeg: str, ffmpeg_version: str,
                 discover: Callable[[], List[Camera]] = discover_cameras,
                 stream_set: Optional[StreamSet] = None, rescan_seconds: float = RESCAN_SECONDS):
        super().__init__()
        self.rescan_seconds = rescan_seconds
        self.host = host
        self.port = port
        self.ffmpeg_version = ffmpeg_version
        self.discover = discover
        self.streams = stream_set or StreamSet(host, port, ffmpeg=ffmpeg)
        self.cameras = []  # type: List[Camera]

    def compose(self) -> ComposeResult:
        header = Text("Insight webcam streamer", style="bold")
        header.append("    {}    ffmpeg {}".format(url_pattern(self.host, self.port), self.ffmpeg_version), style="dim")
        yield Static(header, id="header")
        with Horizontal():
            yield CameraList(id="cameras")
            yield StreamTable(id="streams", cursor_type="row", zebra_stripes=True)
        yield Static("", id="details")
        yield Static("", id="message")
        yield Static(Text(" enter start   s stop   f format   r rescan   q quit   tab / ← → switch pane", style="dim"), id="legend")

    async def on_mount(self) -> None:
        table = self.query_one("#streams", DataTable)
        table.add_columns("Slot", "Camera", "Mode", "Status")
        await self.action_rescan()
        self.set_interval(REFRESH_SECONDS, self.refresh_streams)
        self.set_interval(self.rescan_seconds, self.rescan_if_changed)

    def say(self, text: str, style: str = "") -> None:
        if not self.is_running:
            return
        self.query_one("#message", Static).update(Text(text, style=style))

    def highlighted_camera(self) -> Optional[Camera]:
        item = self.query_one("#cameras", ListView).highlighted_child
        return item.camera if isinstance(item, CameraItem) else None

    def subject(self) -> Optional[Camera]:
        """The camera the user is looking at: the stream row when the table has focus, else the list item."""
        table = self.query_one("#streams", DataTable)
        if table.has_focus and table.row_count:
            slot = int(table.get_row_at(table.cursor_row)[0])
            for stream in self.streams.streams:
                if stream.slot == slot:
                    return stream.camera
        return self.highlighted_camera()

    def refresh_details(self) -> None:
        if not self.is_running:
            return
        camera = self.subject()
        stream = self.streams.stream_for(camera.path) if camera else None
        self.query_one("#details", Static).update(describe(camera, stream))

    @on(ListView.Highlighted, "#cameras")
    @on(DataTable.RowHighlighted, "#streams")
    def on_highlight_changed(self) -> None:
        self.refresh_details()

    def on_descendant_focus(self) -> None:
        self.refresh_details()

    def refresh_streams(self) -> None:
        # Shutdown removes the widgets while timers and workers can still call in.
        if not self.is_running:
            return
        self.refresh_details()
        table = self.query_one("#streams", DataTable)
        cursor = table.cursor_row
        table.clear()
        for stream in self.streams.streams:
            table.add_row(str(stream.slot), stream.camera.name[:22], stream.mode.label, render_status(stream), key=str(stream.slot))
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))

    def action_focus_streams(self) -> None:
        self.query_one("#streams", DataTable).focus()

    def action_focus_cameras(self) -> None:
        self.query_one("#cameras", ListView).focus()

    async def action_rescan(self) -> None:
        await self.rebuild_cameras(self.discover())

    async def rescan_if_changed(self) -> None:
        if not self.is_running:
            return
        cameras = self.discover()
        if cameras != self.cameras:
            await self.rebuild_cameras(cameras)

    async def rebuild_cameras(self, cameras: List[Camera]) -> None:
        """Replace the list, keeping the highlight on the same device when it is still there."""
        view = self.query_one("#cameras", ListView)
        previous = self.highlighted_camera()
        had_focus = view.has_focus or not self.cameras
        self.cameras = cameras
        await view.clear()
        for camera in cameras:
            await view.append(CameraItem(camera))
        if cameras:
            paths = [camera.path for camera in cameras]
            index = paths.index(previous.path) if previous and previous.path in paths else 0
            view.index = min(index, len(cameras) - 1)
            if had_focus:
                view.focus()
            self.say("")
        else:
            self.say("No cameras found. Plug one in.")
        self.refresh_details()

    @on(ListView.Selected, "#cameras")
    def on_camera_selected(self, event: ListView.Selected) -> None:
        camera = event.item.camera
        if camera.note or not camera.modes:
            self.say("{}: {}".format(camera.name, camera.note or "no usable modes"), "yellow")
            return
        self.begin_start(camera, pick_default(camera.modes))

    def action_format(self) -> None:
        camera = self.highlighted_camera()
        if camera is None or not camera.modes:
            self.say("Select a camera with modes first.", "yellow")
            return

        def chosen(mode: Optional[Mode]) -> None:
            if mode is None:
                return
            running = self.streams.stream_for(camera.path)
            if running is not None:
                self.run_worker(self.restart_stream(running.slot, mode), exclusive=False)
                return
            self.begin_start(camera, mode)

        self.push_screen(ModePicker(camera), chosen)

    def begin_start(self, camera: Camera, mode: Mode) -> None:
        if self.streams.stream_for(camera.path) is not None:
            self.say("{} is already streaming; press s to stop it first.".format(camera.name), "yellow")
            return
        default = self.streams.next_free_slot()
        if default is None:
            self.say("no free slot: all {} are in use by this tool.".format(MAX_SLOT), "yellow")
            return

        def chosen(slot: Optional[int]) -> None:
            if slot is not None:
                self.run_worker(self.start_stream(camera, mode, slot), exclusive=False)

        self.push_screen(SlotPrompt(camera, default), chosen)

    async def start_stream(self, camera: Camera, mode: Mode, slot: int) -> None:
        try:
            await self.streams.start(camera, mode, slot)
        except (SlotInUse, CameraInUse) as exc:
            self.say(str(exc), "yellow")
            return
        self.say("")
        self.refresh_streams()

    async def restart_stream(self, slot: int, mode: Mode) -> None:
        try:
            await self.streams.restart(slot, mode)
        except (KeyError, SlotInUse, CameraInUse) as exc:
            self.say("could not restart slot {}: {}".format(slot, exc), "yellow")
            return
        self.say("")
        self.refresh_streams()

    def action_stop(self) -> None:
        table = self.query_one("#streams", DataTable)
        slot = None
        if table.has_focus and table.row_count:
            slot = int(table.get_row_at(table.cursor_row)[0])
        else:
            camera = self.highlighted_camera()
            running = self.streams.stream_for(camera.path) if camera else None
            slot = running.slot if running else None
        if slot is None:
            self.say("Nothing to stop.", "yellow")
            return
        self.run_worker(self.stop_stream(slot), exclusive=False)

    async def stop_stream(self, slot: int) -> None:
        await self.streams.stop(slot)
        self.refresh_streams()

    async def on_unmount(self) -> None:
        await self.streams.stop_all()

    async def action_quit(self) -> None:
        await self.streams.stop_all()
        self.exit()
