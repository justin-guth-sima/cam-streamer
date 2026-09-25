import errno
import os
import struct
import tempfile
import unittest
from pathlib import Path

from cam_streamer import v4l2
from cam_streamer.modes import Mode


def fourcc(s):
    return struct.unpack("<I", s.encode("ascii"))[0]


class FakeDevice:
    """Answers the four ioctls for one device from plain Python data."""

    def __init__(self, caps, formats):
        # formats: {fourcc_str: {(w, h): [fps, ...]}}; a (w, h) of ("step", (min_w, max_w, min_h, max_h)) marks stepwise sizes
        self.caps = caps
        self.formats = formats

    def ioctl(self, fd, request, buf):
        if request == v4l2.VIDIOC_QUERYCAP:
            struct.pack_into("<32s", buf, 16, b"Fake Cam")
            struct.pack_into("<I", buf, 84, self.caps | v4l2.V4L2_CAP_DEVICE_CAPS)
            struct.pack_into("<I", buf, 88, self.caps)
            return
        if request == v4l2.VIDIOC_ENUM_FMT:
            index = struct.unpack_from("<I", buf, 0)[0]
            names = list(self.formats)
            if index >= len(names):
                raise OSError(errno.EINVAL, "end")
            struct.pack_into("<I", buf, 44, fourcc(names[index]))
            return
        if request == v4l2.VIDIOC_ENUM_FRAMESIZES:
            index, pf = struct.unpack_from("<II", buf, 0)
            sizes = list(self.formats[self._name(pf)])
            if index >= len(sizes):
                raise OSError(errno.EINVAL, "end")
            size = sizes[index]
            if size[0] == "step":
                min_w, max_w, min_h, max_h = size[1]
                struct.pack_into("<I", buf, 8, 3)
                struct.pack_into("<IIIIII", buf, 12, min_w, max_w, 1, min_h, max_h, 1)
            else:
                struct.pack_into("<I", buf, 8, 1)
                struct.pack_into("<II", buf, 12, size[0], size[1])
            return
        if request == v4l2.VIDIOC_ENUM_FRAMEINTERVALS:
            index, pf, w, h = struct.unpack_from("<IIII", buf, 0)
            rates = self.formats[self._name(pf)].get((w, h))
            if rates is None:
                # stepwise size: report a continuous interval range 1/60 .. 1/5
                if index > 0:
                    raise OSError(errno.EINVAL, "end")
                struct.pack_into("<I", buf, 16, 2)
                struct.pack_into("<IIIIII", buf, 20, 1, 60, 1, 5, 1, 1)
                return
            if index >= len(rates):
                raise OSError(errno.EINVAL, "end")
            struct.pack_into("<I", buf, 16, 1)
            struct.pack_into("<II", buf, 20, 1, rates[index])
            return
        raise AssertionError("unexpected ioctl %r" % request)

    def _name(self, pf):
        return struct.pack("<I", pf).decode("ascii")


class DiscoverTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sysfs = Path(self.tmp.name) / "sys"
        self.dev = Path(self.tmp.name) / "dev"
        self.sysfs.mkdir()
        self.dev.mkdir()
        self.devices = {}
        self.open_errors = {}

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, node, name, device=None, open_error=None, create_dev=True):
        d = self.sysfs / node
        d.mkdir()
        (d / "name").write_text(name + "\n")
        if create_dev:
            (self.dev / node).write_bytes(b"")
        if device is not None:
            self.devices[str(self.dev / node)] = device
        if open_error is not None:
            self.open_errors[str(self.dev / node)] = open_error

    def opener(self, path, flags):
        if path in self.open_errors:
            raise OSError(self.open_errors[path], os.strerror(self.open_errors[path]))
        return os.open(path, flags)

    def ioctl(self, fd, request, buf):
        path = os.readlink("/proc/self/fd/%d" % fd)
        return self.devices[path].ioctl(fd, request, buf)

    def discover(self):
        return v4l2.discover(sysfs_root=str(self.sysfs), dev_root=str(self.dev), ioctl=self.ioctl, opener=self.opener)


class TestDiscover(DiscoverTestCase):
    def test_capture_node_listed_with_modes(self):
        self.add("video0", "HP 5MP Camera", FakeDevice(v4l2.V4L2_CAP_VIDEO_CAPTURE, {
            "MJPG": {(1280, 720): [30], (640, 480): [30, 15]},
            "YUYV": {(640, 480): [30]},
        }))
        cams = self.discover()
        self.assertEqual(len(cams), 1)
        cam = cams[0]
        self.assertEqual(cam.path, str(self.dev / "video0"))
        self.assertEqual(cam.name, "HP 5MP Camera")
        self.assertEqual(cam.note, "")
        self.assertIn(Mode("mjpeg", 1280, 720, 30), cam.modes)
        self.assertIn(Mode("mjpeg", 640, 480, 15), cam.modes)
        self.assertIn(Mode("yuyv422", 640, 480, 30), cam.modes)
        self.assertEqual(len(cam.modes), 4)

    def test_metadata_node_dropped(self):
        self.add("video0", "HP 5MP Camera", FakeDevice(v4l2.V4L2_CAP_VIDEO_CAPTURE, {"MJPG": {(640, 480): [30]}}))
        self.add("video1", "HP 5MP Camera", FakeDevice(v4l2.V4L2_CAP_META_CAPTURE, {}))
        cams = self.discover()
        self.assertEqual([c.path for c in cams], [str(self.dev / "video0")])

    def test_busy_and_no_permission_notes(self):
        self.add("video0", "Busy Cam", open_error=errno.EBUSY)
        self.add("video1", "Locked Cam", open_error=errno.EACCES)
        cams = self.discover()
        self.assertEqual([(c.name, c.note, c.modes) for c in cams],
                         [("Busy Cam", "busy", []), ("Locked Cam", "no permission", [])])

    def test_missing_dev_node_skipped(self):
        self.add("video5", "Ghost", create_dev=False)
        self.assertEqual(self.discover(), [])

    def test_unknown_fourcc_dropped(self):
        self.add("video0", "Odd Cam", FakeDevice(v4l2.V4L2_CAP_VIDEO_CAPTURE, {
            "ZZZZ": {(640, 480): [30]},
            "MJPG": {(640, 480): [30]},
        }))
        self.assertEqual(self.discover()[0].modes, [Mode("mjpeg", 640, 480, 30)])

    def test_stepwise_sizes_reduce_to_max_with_common_rates(self):
        self.add("video0", "Step Cam", FakeDevice(v4l2.V4L2_CAP_VIDEO_CAPTURE, {
            "YUYV": {("step", (160, 1920, 120, 1080)): None},
        }))
        self.assertEqual(sorted(self.discover()[0].modes, key=lambda m: m.fps),
                         [Mode("yuyv422", 1920, 1080, 15), Mode("yuyv422", 1920, 1080, 30)])

    def test_sorted_by_node_number(self):
        self.add("video10", "Ten", FakeDevice(v4l2.V4L2_CAP_VIDEO_CAPTURE, {"MJPG": {(640, 480): [30]}}))
        self.add("video2", "Two", FakeDevice(v4l2.V4L2_CAP_VIDEO_CAPTURE, {"MJPG": {(640, 480): [30]}}))
        self.assertEqual([c.name for c in self.discover()], ["Two", "Ten"])

    def test_duplicated_sysfs_name_is_collapsed(self):
        self.add("video0", "HP 5MP Camera: HP 5MP Camera", FakeDevice(v4l2.V4L2_CAP_VIDEO_CAPTURE, {"MJPG": {(640, 480): [30]}}))
        self.add("video2", "HP 5MP Camera: HP IR Camera", FakeDevice(v4l2.V4L2_CAP_VIDEO_CAPTURE, {"GREY": {(400, 400): [15]}}))
        self.assertEqual([c.name for c in self.discover()], ["HP 5MP Camera", "HP 5MP Camera: HP IR Camera"])


if __name__ == "__main__":
    unittest.main()
