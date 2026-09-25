import asyncio
import unittest

from textual.widgets import Input, Label

from cam_streamer import app as cam_app
from cam_streamer.modes import Mode
from cam_streamer.stream import StreamState, StreamSet
from cam_streamer.v4l2 import Camera

CAMS = [
    Camera("/dev/video0", "HP 5MP Camera", [Mode("mjpeg", 1280, 720, 30), Mode("yuyv422", 640, 480, 30)]),
    Camera("/dev/video2", "HP IR Camera", [Mode("yuyv422", 640, 480, 30)]),
    Camera("/dev/video4", "Locked Cam", [], "no permission"),
]


class FakeStream:
    def __init__(self, camera, mode, slot, url, **kwargs):
        self.camera, self.mode, self.slot, self.url = camera, mode, slot, url
        self.kind = "starting"
        self.stopped = False
        self.done = asyncio.Event()
        self.state_override = None

    @property
    def state(self):
        if self.state_override is not None:
            return self.state_override
        return StreamState(self.kind, 29.9, 2100.0)

    async def start(self):
        self.kind = "streaming"

    async def stop(self):
        self.stopped = True
        self.kind = "stopped"
        self.done.set()


def make_app(cams=CAMS):
    ss = StreamSet("127.0.0.1", 8554, stream_factory=FakeStream)
    app = cam_app.CamApp(host="127.0.0.1", port=8554, ffmpeg="/usr/bin/ffmpeg", ffmpeg_version="6.1.1",
                         discover=lambda: cams, stream_set=ss)
    return app, ss


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class TestCamApp(unittest.TestCase):
    def test_header_shows_url_pattern_and_version(self):
        async def go():
            app, _ = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                text = str(app.query_one("#header").renderable)
                self.assertIn("rtsp://127.0.0.1:8554/src<N>", text)
                self.assertIn("ffmpeg 6.1.1", text)
        run(go())

    def test_enter_default_slot_starts_stream_and_q_stops_it(self):
        async def go():
            app, ss = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("enter")          # opens slot prompt for camera 0
                await pilot.pause()
                await pilot.press("enter")          # accept default slot 1
                await pilot.pause()
                self.assertEqual([s.slot for s in ss.streams], [1])
                self.assertEqual(ss.streams[0].mode, Mode("mjpeg", 1280, 720, 30))
                table = app.query_one("#streams")
                self.assertEqual(table.row_count, 1)
                started = ss.streams[0]
                await pilot.press("q")
                await pilot.pause()
            self.assertTrue(started.stopped)
            self.assertEqual(ss.streams, [])
        run(go())

    def test_invalid_slot_keeps_prompt_open(self):
        async def go():
            app, ss = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                for text in ("0", "49", "abc", ""):
                    app.screen.query_one("#slot", Input).value = ""
                    for ch in text:
                        await pilot.press(ch)
                    await pilot.press("enter")
                    await pilot.pause()
                    self.assertEqual(ss.streams, [])
                    self.assertIsInstance(app.screen, cam_app.SlotPrompt)
                    self.assertIn("1 to 48", str(app.screen.query_one("#error").renderable))
                await pilot.press("escape")
        run(go())

    def test_camera_without_modes_shows_message(self):
        async def go():
            app, ss = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                item = app.query_one("#cameras").children[2]
                self.assertIn("no permission", str(item.query_one(Label).renderable))
                await pilot.press("down", "down", "enter")
                await pilot.pause()
                self.assertEqual(ss.streams, [])
                self.assertIn("no permission", str(app.query_one("#message").renderable))
        run(go())

    def test_no_free_slot_message(self):
        async def go():
            app, ss = make_app()
            for slot in range(1, 49):
                ss._streams[slot] = FakeStream(Camera("/dev/x%d" % slot, "x", []), CAMS[0].modes[0], slot, "u")
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, cam_app.SlotPrompt)
                self.assertIn("no free slot", str(app.query_one("#message").renderable))
        run(go())

    def test_format_picker_starts_with_chosen_mode(self):
        async def go():
            app, ss = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("f")
                await pilot.pause()
                self.assertIsInstance(app.screen, cam_app.ModePicker)
                await pilot.press("down", "enter")   # second-best mode
                await pilot.pause()
                await pilot.press("enter")           # accept default slot
                await pilot.pause()
                self.assertEqual(ss.streams[0].mode, Mode("yuyv422", 640, 480, 30))
        run(go())

    def test_s_stops_highlighted_camera_stream(self):
        async def go():
            app, ss = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("enter", "enter")
                await pilot.pause()
                self.assertEqual(len(ss.streams), 1)
                await pilot.press("s")
                await pilot.pause()
                self.assertEqual(ss.streams, [])
                self.assertEqual(app.query_one("#streams").row_count, 0)
        run(go())

    def test_rescan_reloads_cameras(self):
        async def go():
            calls = []
            def discover():
                calls.append(1)
                return CAMS[:len(calls)]
            ss = StreamSet("127.0.0.1", 8554, stream_factory=FakeStream)
            app = cam_app.CamApp(host="h", port=1, ffmpeg="f", ffmpeg_version="v", discover=discover, stream_set=ss)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                self.assertEqual(len(app.query_one("#cameras").children), 1)
                await pilot.press("r")
                await pilot.pause()
                self.assertEqual(len(app.query_one("#cameras").children), 2)
        run(go())

    def test_q_in_slot_prompt_does_not_quit(self):
        async def go():
            app, ss = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                await pilot.press("q")
                await pilot.pause()
                self.assertIsInstance(app.screen, cam_app.SlotPrompt)
                self.assertFalse(app._exit)
                await pilot.press("escape")
        run(go())

    def test_unmount_stops_streams(self):
        async def go():
            app, ss = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("enter", "enter")
                await pilot.pause()
                started = ss.streams[0]
                app.exit()
                await pilot.pause()
            self.assertTrue(started.stopped)
            self.assertEqual(ss.streams, [])
        run(go())

    def test_format_on_running_stream_restarts_in_same_slot(self):
        async def go():
            app, ss = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("enter", "enter")
                await pilot.pause()
                first = ss.streams[0]
                await pilot.press("f")
                await pilot.pause()
                await pilot.press("down", "enter")
                await pilot.pause()
                self.assertTrue(first.stopped)
                self.assertEqual([(s.slot, s.mode) for s in ss.streams], [(1, Mode("yuyv422", 640, 480, 30))])
        run(go())


    def test_details_follow_highlight_and_show_stream_url(self):
        async def go():
            app, ss = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                details = app.query_one("#details")
                self.assertEqual(str(details.renderable),
                                 "HP 5MP Camera   /dev/video0   2 modes   auto mjpeg 1280x720 30fps")
                await pilot.press("down")
                await pilot.pause()
                self.assertEqual(str(details.renderable),
                                 "HP IR Camera   /dev/video2   1 mode   auto yuyv422 640x480 30fps")
                await pilot.press("down")
                await pilot.pause()
                self.assertEqual(str(details.renderable), "Locked Cam   /dev/video4   no permission")
                await pilot.press("up", "up", "enter", "enter")
                await pilot.pause()
                self.assertEqual(str(details.renderable),
                                 "HP 5MP Camera   /dev/video0   2 modes   auto mjpeg 1280x720 30fps"
                                 "   slot 1   rtsp://127.0.0.1:8554/src1")
        run(go())

    def test_camera_list_shows_name_only(self):
        async def go():
            app, _ = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                labels = [str(item.query_one(Label).renderable) for item in app.query_one("#cameras").children]
                self.assertEqual(labels, ["HP 5MP Camera", "HP IR Camera", "Locked Cam  (no permission)"])
        run(go())


    def test_arrows_switch_panes_and_legend_says_so(self):
        async def go():
            app, ss = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                cameras = app.query_one("#cameras")
                streams = app.query_one("#streams")
                self.assertTrue(cameras.has_focus)
                await pilot.press("right")
                await pilot.pause()
                self.assertTrue(streams.has_focus)
                await pilot.press("left")
                await pilot.pause()
                self.assertTrue(cameras.has_focus)
                await pilot.press("tab")
                await pilot.pause()
                self.assertTrue(streams.has_focus)
                legend = str(app.query_one("#legend").renderable)
                self.assertIn("tab / ← → switch pane", legend)
        run(go())

    def test_details_follow_stream_row_when_table_focused(self):
        async def go():
            app, ss = make_app()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("down", "enter", "enter")   # HP IR Camera -> slot 1
                await pilot.pause()
                await pilot.press("up", "right")              # highlight HP 5MP in list, then focus table
                await pilot.pause()
                self.assertTrue(app.query_one("#streams").has_focus)
                self.assertTrue(str(app.query_one("#details").renderable).startswith("HP IR Camera   /dev/video2"))
        run(go())


    def test_periodic_rescan_shows_hotplug_and_keeps_highlight(self):
        async def go():
            present = [CAMS[0], CAMS[1]]
            calls = []
            def discover():
                calls.append(1)
                return list(present)
            ss = StreamSet("127.0.0.1", 8554, stream_factory=FakeStream)
            app = cam_app.CamApp(host="h", port=1, ffmpeg="f", ffmpeg_version="v", discover=discover,
                                 stream_set=ss, rescan_seconds=0.05)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                cameras = app.query_one("#cameras")
                await pilot.press("down")
                await pilot.pause()
                first_items = list(cameras.children)
                await pilot.pause(0.2)
                self.assertGreater(len(calls), 2)
                self.assertEqual(list(cameras.children), first_items)   # unchanged set: no rebuild
                self.assertEqual(app.highlighted_camera(), CAMS[1])
                present.append(CAMS[2])                                  # plug in
                await pilot.pause(0.2)
                self.assertEqual([i.camera.path for i in cameras.children], ["/dev/video0", "/dev/video2", "/dev/video4"])
                self.assertEqual(app.highlighted_camera(), CAMS[1])
                present.remove(CAMS[0])                                  # unplug the one above the highlight
                await pilot.pause(0.2)
                self.assertEqual([i.camera.path for i in cameras.children], ["/dev/video2", "/dev/video4"])
                self.assertEqual(app.highlighted_camera(), CAMS[1])
                present.remove(CAMS[1])                                  # unplug the highlighted one
                await pilot.pause(0.2)
                self.assertEqual(app.highlighted_camera(), CAMS[2])
                del present[:]
                await pilot.pause(0.2)
                self.assertEqual(list(cameras.children), [])
                self.assertIn("No cameras found", str(app.query_one("#message").renderable))
        run(go())


class TestDescribe(unittest.TestCase):
    def test_streaming_camera_names_slot_and_url(self):
        stream = FakeStream(CAMS[0], CAMS[0].modes[0], 7, "rtsp://10.0.0.5:18554/src7")
        self.assertEqual(str(cam_app.describe(CAMS[0], stream)),
                         "HP 5MP Camera   /dev/video0   2 modes   auto mjpeg 1280x720 30fps   slot 7   rtsp://10.0.0.5:18554/src7")

    def test_noted_camera_shows_note_instead_of_modes(self):
        self.assertEqual(str(cam_app.describe(CAMS[2], None)), "Locked Cam   /dev/video4   no permission")

    def test_nothing_highlighted(self):
        self.assertEqual(str(cam_app.describe(None, None)), "")


class TestRenderStatus(unittest.TestCase):
    def test_bitrate_omitted_when_unknown(self):
        stream = FakeStream(CAMS[0], CAMS[0].modes[0], 1, "u")
        stream.state_override = StreamState("streaming", 30.5, 0.0)
        self.assertEqual(str(cam_app.render_status(stream)), "● 30.5 fps")

    def test_bitrate_shown_when_known(self):
        stream = FakeStream(CAMS[0], CAMS[0].modes[0], 1, "u")
        stream.state_override = StreamState("streaming", 30.5, 2100.0)
        self.assertEqual(str(cam_app.render_status(stream)), "● 30.5 fps 2.1 Mb/s")


if __name__ == "__main__":
    unittest.main()
