import subprocess
import unittest
from unittest.mock import patch

from cam_streamer import stream
from cam_streamer.modes import Mode


class TestBuildCommand(unittest.TestCase):
    def test_recipe(self):
        argv = stream.build_command("ffmpeg", "/dev/video0", Mode("mjpeg", 1280, 720, 30), "rtsp://127.0.0.1:8554/src3")
        self.assertEqual(argv, [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning",
            "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720", "-framerate", "30", "-i", "/dev/video0",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-g", "30", "-pix_fmt", "yuv420p",
            "-f", "rtsp", "-rtsp_transport", "tcp",
            "-progress", "pipe:1",
            "rtsp://127.0.0.1:8554/src3",
        ])

    def test_keyframe_interval_follows_fps(self):
        argv = stream.build_command("ffmpeg", "/dev/video0", Mode("yuyv422", 640, 480, 15), "u")
        self.assertEqual(argv[argv.index("-g") + 1], "15")


class TestParseProgressBlock(unittest.TestCase):
    def test_values(self):
        fps, kbps = stream.parse_progress_block(["frame=61", "fps=29.97", "bitrate=2100.5kbits/s", "progress=continue"])
        self.assertAlmostEqual(fps, 29.97)
        self.assertAlmostEqual(kbps, 2100.5)

    def test_not_available_during_warmup(self):
        self.assertEqual(stream.parse_progress_block(["fps=0.00", "bitrate=N/A", "progress=continue"]), (0.0, 0.0))

    def test_missing_keys(self):
        self.assertEqual(stream.parse_progress_block(["progress=continue"]), (0.0, 0.0))


class TestClassifyExit(unittest.TestCase):
    def classify(self, code, lines):
        return stream.classify_exit(code, lines, slot=3, host="127.0.0.1", port=8554)

    def test_camera_busy(self):
        self.assertEqual(self.classify(1, ["[video4linux2,v4l2 @ 0x1] ioctl(VIDIOC_STREAMON): Device or resource busy"]),
                         "camera in use by another program")

    def test_slot_in_use_status(self):
        self.assertEqual(self.classify(1, ["[rtsp @ 0x1] method ANNOUNCE failed: 400 Bad Request"]), "slot 3 already in use")

    def test_slot_in_use_text(self):
        self.assertEqual(self.classify(1, ["path 'src3' is already publishing"]), "slot 3 already in use")

    def test_slot_in_use_server_returned(self):
        self.assertEqual(self.classify(8, ["[rtsp @ 0x1] Server returned 400 Bad Request"]), "slot 3 already in use")

    def test_unreachable(self):
        for line in ["Connection refused", "Connection timed out", "No route to host"]:
            self.assertEqual(self.classify(1, ["[tcp @ 0x1] " + line]), "Insight not reachable at 127.0.0.1:8554")

    def test_mode_rejected(self):
        self.assertEqual(self.classify(1, ["[video4linux2,v4l2 @ 0x1] Not a video capture device"]),
                         "mode not accepted, choose another with f")
        self.assertEqual(self.classify(1, ["/dev/video0: Invalid argument"]), "mode not accepted, choose another with f")

    def test_clean_exit_is_disconnect(self):
        self.assertEqual(self.classify(0, []), "camera disconnected")

    def test_unknown(self):
        self.assertEqual(self.classify(187, ["something odd"]), "ffmpeg exited (code 187), see log")

    def test_first_match_in_table_order(self):
        lines = ["Connection refused", "Device or resource busy"]
        self.assertEqual(self.classify(1, lines), "camera in use by another program")

    def test_signal_exit_is_not_an_error(self):
        self.assertEqual(self.classify(-9, ["anything"]), "")
        self.assertEqual(self.classify(-2, []), "")

    def test_bare_number_in_warning_is_not_slot_in_use(self):
        lines = ["[rtsp @ 0x1] Non-monotonic DTS in output stream 0:0; previous: 480, current: 479"]
        self.assertEqual(self.classify(1, lines), "ffmpeg exited (code 1), see log")

    def test_generic_invalid_argument_is_unknown(self):
        self.assertEqual(self.classify(1, ["[libx264 @ 0x1] Invalid argument"]), "ffmpeg exited (code 1), see log")


class TestFfmpegVersion(unittest.TestCase):
    def test_parses_first_line(self):
        with patch("cam_streamer.stream.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout="ffmpeg version 6.1.1-3ubuntu5 Copyright\nbuilt with gcc\n")
            self.assertEqual(stream.ffmpeg_version("ffmpeg"), "6.1.1")

    def test_failure_is_unknown(self):
        with patch("cam_streamer.stream.subprocess.run", side_effect=OSError("nope")):
            self.assertEqual(stream.ffmpeg_version("ffmpeg"), "unknown")


if __name__ == "__main__":
    unittest.main()
