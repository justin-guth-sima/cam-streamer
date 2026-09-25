import unittest

from cam_streamer.modes import Mode, pick_default, sort_best_first


def m(fmt, w, h, fps):
    return Mode(pixel_format=fmt, width=w, height=h, fps=fps)


class TestPickDefault(unittest.TestCase):
    def test_prefers_mjpeg_over_yuyv_at_same_size(self):
        modes = [m("yuyv422", 1280, 720, 30), m("mjpeg", 1280, 720, 30)]
        self.assertEqual(pick_default(modes), m("mjpeg", 1280, 720, 30))

    def test_caps_at_720p(self):
        modes = [m("mjpeg", 1920, 1080, 30), m("mjpeg", 1280, 720, 30), m("mjpeg", 640, 480, 30)]
        self.assertEqual(pick_default(modes), m("mjpeg", 1280, 720, 30))

    def test_caps_fps_at_30(self):
        modes = [m("mjpeg", 1280, 720, 60), m("mjpeg", 1280, 720, 30), m("mjpeg", 1280, 720, 15)]
        self.assertEqual(pick_default(modes), m("mjpeg", 1280, 720, 30))

    def test_largest_area_under_cap_wins_over_format_within_same_format_tier(self):
        modes = [m("mjpeg", 640, 480, 30), m("mjpeg", 1024, 576, 30)]
        self.assertEqual(pick_default(modes), m("mjpeg", 1024, 576, 30))

    def test_mjpeg_small_beats_yuyv_large(self):
        modes = [m("yuyv422", 1280, 720, 30), m("mjpeg", 640, 480, 30)]
        self.assertEqual(pick_default(modes), m("mjpeg", 640, 480, 30))

    def test_falls_back_to_smallest_above_cap(self):
        modes = [m("mjpeg", 2560, 1440, 30), m("mjpeg", 1920, 1080, 30)]
        self.assertEqual(pick_default(modes), m("mjpeg", 1920, 1080, 30))

    def test_unknown_format_used_only_when_nothing_else(self):
        modes = [m("nv12", 1280, 720, 30)]
        self.assertEqual(pick_default(modes), m("nv12", 1280, 720, 30))

    def test_empty_returns_none(self):
        self.assertIsNone(pick_default([]))

    def test_in_cap_mode_beats_preferred_format_over_cap(self):
        modes = [m("mjpeg", 2560, 1440, 30), m("yuyv422", 1280, 720, 30)]
        self.assertEqual(pick_default(modes), m("yuyv422", 1280, 720, 30))


class TestSortBestFirst(unittest.TestCase):
    def test_default_is_first_and_input_untouched(self):
        modes = [m("yuyv422", 640, 480, 30), m("mjpeg", 1920, 1080, 30), m("mjpeg", 1280, 720, 30)]
        ordered = sort_best_first(modes)
        self.assertEqual(ordered[0], pick_default(modes))
        self.assertEqual(len(ordered), 3)
        self.assertEqual(modes[0], m("yuyv422", 640, 480, 30))

    def test_label(self):
        self.assertEqual(m("mjpeg", 1280, 720, 30).label, "1280x720 30fps")


if __name__ == "__main__":
    unittest.main()
