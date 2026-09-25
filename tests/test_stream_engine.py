import asyncio
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path

from cam_streamer import stream
from cam_streamer.modes import Mode
from cam_streamer.v4l2 import Camera

FAKE = str(Path(__file__).with_name("fake_ffmpeg.py"))
CAM = Camera("/dev/video0", "Fake Cam", [Mode("mjpeg", 640, 480, 30)])
CAM2 = Camera("/dev/video2", "Other Cam", [Mode("mjpeg", 640, 480, 30)])
MODE = CAM.modes[0]


def fake(*args):
    return [sys.executable, FAKE] + list(args)


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


async def wait_for(predicate, timeout=3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


class TestStream(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, *args, **kwargs):
        return stream.Stream(CAM, MODE, 3, "rtsp://127.0.0.1:8554/src3", argv=fake(*args),
                             log_dir=self.tmp.name, stall_after=0.3, kill_after=0.3, **kwargs)

    def test_starting_then_streaming_with_values(self):
        async def go():
            s = self.make("progress")
            self.assertEqual(s.state.kind, "starting")
            await s.start()
            await wait_for(lambda: s.state.kind == "streaming")
            self.assertAlmostEqual(s.state.fps, 29.97)
            self.assertAlmostEqual(s.state.bitrate_kbps, 2100.5)
            self.assertFalse(s.state.stalled)
            await s.stop()
            self.assertEqual(s.state.kind, "stopped")
        run(go())

    def test_stall_detected_while_alive(self):
        async def go():
            s = self.make("progress-stop")
            await s.start()
            await wait_for(lambda: s.state.kind == "streaming")
            await wait_for(lambda: s.state.stalled, timeout=2.0)
            self.assertEqual(s.state.kind, "streaming")
            await s.stop()
        run(go())

    def test_error_mapped_and_log_written(self):
        async def go():
            s = self.make("stderr", "1", "[rtsp @ 0x1] method ANNOUNCE failed: 400 Bad Request")
            await s.start()
            await s.done.wait()
            self.assertEqual(s.state.kind, "error")
            self.assertEqual(s.state.reason, "slot 3 already in use")
            self.assertEqual(s.state.log_path, os.path.join(self.tmp.name, "src3.log"))
            self.assertIn("400 Bad Request", Path(s.state.log_path).read_text())
        run(go())

    def test_unknown_error_names_log(self):
        async def go():
            s = self.make("stderr", "187", "weird")
            await s.start()
            await s.done.wait()
            self.assertEqual(s.state.reason, "ffmpeg exited (code 187), see " + os.path.join(self.tmp.name, "src3.log"))
        run(go())

    def test_exit_zero_is_disconnect(self):
        async def go():
            s = self.make("exit0")
            await s.start()
            await s.done.wait()
            self.assertEqual((s.state.kind, s.state.reason), ("error", "camera disconnected"))
        run(go())

    def test_sigkill_fallback_is_stopped_not_error(self):
        async def go():
            s = self.make("ignore-sigint")
            await s.start()
            await wait_for(lambda: s.state.kind == "streaming")
            await s.stop()
            self.assertEqual(s.state.kind, "stopped")
            self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "src3.log")))
        run(go())

    def test_missing_binary_is_error(self):
        async def go():
            s = stream.Stream(CAM, MODE, 3, "u", argv=["/nonexistent/ffmpeg"], log_dir=self.tmp.name)
            await s.start()
            await s.done.wait()
            self.assertEqual(s.state.kind, "error")
            self.assertIn("not found", s.state.reason)
        run(go())

    def test_external_kill_is_error(self):
        async def go():
            s = self.make("progress")
            await s.start()
            await wait_for(lambda: s.state.kind == "streaming")
            os.kill(s._proc.pid, signal.SIGKILL)
            await asyncio.wait_for(s.done.wait(), 3.0)
            self.assertEqual((s.state.kind, s.state.reason), ("error", "ffmpeg killed by signal 9"))
        run(go())

    def test_long_stderr_line_does_not_kill_reader(self):
        async def go():
            s = self.make("stderr-long", "1", "[tcp @ 0x1] Connection refused")
            await s.start()
            await asyncio.wait_for(s.done.wait(), 3.0)
            self.assertEqual((s.state.kind, s.state.reason), ("error", "Insight not reachable at 127.0.0.1:8554"))
            self.assertIn("Connection refused", Path(s.state.log_path).read_text())
        run(go())

    def test_cancelled_stop_still_kills_process(self):
        async def go():
            s = self.make("ignore-sigint")
            await s.start()
            await wait_for(lambda: s.state.kind == "streaming")
            pid = s._proc.pid
            task = asyncio.ensure_future(s.stop())
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.2)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        run(go())


class TestStreamSet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def make_set(self, *args):
        def factory(camera, mode, slot, url, **kwargs):
            kwargs.update(argv=fake(*args), stall_after=0.3, kill_after=0.3)
            return stream.Stream(camera, mode, slot, url, **kwargs)
        return stream.StreamSet("127.0.0.1", 8554, log_dir=self.tmp.name, stream_factory=factory)

    def test_start_builds_url_and_orders_by_slot(self):
        async def go():
            ss = self.make_set("progress")
            b = await ss.start(CAM2, MODE, 7)
            a = await ss.start(CAM, MODE, 3)
            self.assertEqual(a.url, "rtsp://127.0.0.1:8554/src3")
            self.assertEqual([s.slot for s in ss.streams], [3, 7])
            self.assertIs(ss.stream_for("/dev/video2"), b)
            await ss.stop_all()
            self.assertEqual(ss.streams, [])
        run(go())

    def test_refuses_second_stream_on_slot_and_camera(self):
        async def go():
            ss = self.make_set("progress")
            await ss.start(CAM, MODE, 3)
            with self.assertRaises(stream.SlotInUse):
                await ss.start(CAM2, MODE, 3)
            with self.assertRaises(stream.CameraInUse):
                await ss.start(CAM, MODE, 4)
            await ss.stop_all()
        run(go())

    def test_errored_stream_keeps_slot_until_stopped(self):
        async def go():
            ss = self.make_set("stderr", "1", "Connection refused")
            s = await ss.start(CAM, MODE, 3)
            await s.done.wait()
            self.assertEqual([x.slot for x in ss.streams], [3])
            with self.assertRaises(stream.SlotInUse):
                await ss.start(CAM2, MODE, 3)
            await ss.stop(3)
            self.assertEqual(ss.streams, [])
        run(go())

    def test_restart_with_new_mode(self):
        async def go():
            ss = self.make_set("progress")
            first = await ss.start(CAM, MODE, 3)
            second = await ss.restart(3, Mode("yuyv422", 640, 480, 15))
            self.assertEqual(first.state.kind, "stopped")
            self.assertEqual(second.mode.fps, 15)
            self.assertEqual([s.slot for s in ss.streams], [3])
            await ss.stop_all()
        run(go())

    def test_stream_stays_registered_until_stop_completes(self):
        async def go():
            ss = self.make_set("ignore-sigint")
            s = await ss.start(CAM, MODE, 3)
            await wait_for(lambda: s.state.kind == "streaming")
            task = asyncio.ensure_future(ss.stop(3))
            await asyncio.sleep(0.05)
            self.assertEqual([x.slot for x in ss.streams], [3])
            await task
            self.assertEqual(ss.streams, [])
            self.assertEqual(s.state.kind, "stopped")
        run(go())

    def test_next_free_slot(self):
        async def go():
            ss = self.make_set("progress")
            self.assertEqual(ss.next_free_slot(), 1)
            await ss.start(CAM, MODE, 1)
            self.assertEqual(ss.next_free_slot(), 2)
            await ss.stop_all()
        run(go())

    def test_next_free_slot_none_when_all_used(self):
        ss = stream.StreamSet("127.0.0.1", 8554, log_dir=self.tmp.name)
        for slot in range(1, 49):
            ss._streams[slot] = object()
        self.assertIsNone(ss.next_free_slot())


if __name__ == "__main__":
    unittest.main()
