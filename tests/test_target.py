import subprocess
import unittest
from unittest.mock import patch

from cam_streamer import target

PORTS = ("0.0.0.0:8022->8022/tcp, [::]:8022->8022/tcp, 0.0.0.0:9000-9003->9000-9003/udp, "
         "9000-9079/tcp, 0.0.0.0:18554->8554/tcp, [::]:18554->8554/tcp, 0.0.0.0:9900->9900/tcp")


class TestUrls(unittest.TestCase):
    def test_slot_url(self):
        self.assertEqual(target.slot_url("10.0.0.5", 18554, 3), "rtsp://10.0.0.5:18554/src3")

    def test_url_pattern(self):
        self.assertEqual(target.url_pattern("127.0.0.1", 8554), "rtsp://127.0.0.1:8554/src<N>")

    def test_slot_bounds(self):
        self.assertEqual((target.MIN_SLOT, target.MAX_SLOT), (1, 48))


class TestPortFromPortsString(unittest.TestCase):
    def test_finds_mapped_port(self):
        self.assertEqual(target.port_from_ports_string(PORTS), 18554)

    def test_ipv6_only_entry_still_found(self):
        self.assertEqual(target.port_from_ports_string("[::]:28554->8554/tcp"), 28554)

    def test_no_rtsp_mapping(self):
        self.assertIsNone(target.port_from_ports_string("0.0.0.0:9900->9900/tcp, 8554/tcp"))

    def test_empty(self):
        self.assertIsNone(target.port_from_ports_string(""))


class TestResolvePort(unittest.TestCase):
    def test_explicit_wins(self):
        self.assertEqual(target.resolve_port(9999, containers=lambda: [{"Ports": PORTS}]), 9999)

    def test_from_running_container(self):
        self.assertEqual(target.resolve_port(None, containers=lambda: [{"Ports": PORTS}]), 18554)

    def test_no_container_falls_back(self):
        self.assertEqual(target.resolve_port(None, containers=lambda: []), 8554)

    def test_container_without_mapping_falls_back(self):
        self.assertEqual(target.resolve_port(None, containers=lambda: [{"Ports": "8554/tcp"}]), 8554)

    def test_container_lookup_failure_falls_back(self):
        def boom():
            raise RuntimeError("docker down")
        self.assertEqual(target.resolve_port(None, containers=boom), 8554)

    def test_remote_host_ignores_local_container(self):
        called = []
        def containers():
            called.append(1)
            return [{"Ports": PORTS}]
        self.assertEqual(target.resolve_port(None, host="10.0.0.5", containers=containers), 8554)
        self.assertEqual(called, [])

    def test_localhost_names_use_container(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertEqual(target.resolve_port(None, host=host, containers=lambda: [{"Ports": PORTS}]), 18554)

    def test_default_lookup_without_docker_binary(self):
        with patch("cam_streamer.target.shutil.which", return_value=None):
            self.assertEqual(target._running_containers(), [])

    def test_default_lookup_docker_timeout_is_silent(self):
        with patch("cam_streamer.target.shutil.which", return_value="/usr/bin/docker"), \
             patch("cam_streamer.target.subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 3)):
            self.assertEqual(target._running_containers(), [])

    def test_default_lookup_parses_neat_container(self):
        line = '{"Names":"ghcr.io-sima-neat-sdk-v2.1.3.0","Image":"ghcr.io/sima-neat/sdk:v2.1.3.0","Ports":"%s"}' % PORTS
        with patch("cam_streamer.target.shutil.which", return_value="/usr/bin/docker"), \
             patch("cam_streamer.target.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=line + "\n")):
            self.assertEqual([c["Ports"] for c in target._running_containers()], [PORTS])


if __name__ == "__main__":
    unittest.main()
