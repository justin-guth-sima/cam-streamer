import subprocess
import sys
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from cam_streamer.cli import main


class TestCli(unittest.TestCase):
    def invoke(self, args=()):
        return CliRunner().invoke(main, list(args))

    def test_refuses_non_linux(self):
        with patch("cam_streamer.cli.platform.system", return_value="Darwin"):
            result = self.invoke()
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.output.strip(), "Error: cam-streamer supports Linux hosts only for now.")

    def test_refuses_missing_ffmpeg(self):
        with patch("cam_streamer.cli.platform.system", return_value="Linux"), \
             patch("cam_streamer.cli.shutil.which", return_value=None):
            result = self.invoke()
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.output.strip(), "Error: ffmpeg was not found on PATH; install it and try again.")

    def test_runs_app_with_resolved_port(self):
        with patch("cam_streamer.cli.platform.system", return_value="Linux"), \
             patch("cam_streamer.cli.shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("cam_streamer.cli.resolve_port", return_value=18554) as resolve, \
             patch("cam_streamer.stream.ffmpeg_version", return_value="6.1.1"), \
             patch("cam_streamer.cli.run_app") as run_app:
            result = self.invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        resolve.assert_called_once_with(None, "127.0.0.1")
        run_app.assert_called_once_with("127.0.0.1", 18554, "/usr/bin/ffmpeg", "6.1.1")

    def test_flags_override(self):
        with patch("cam_streamer.cli.platform.system", return_value="Linux"), \
             patch("cam_streamer.cli.shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("cam_streamer.cli.resolve_port", side_effect=lambda explicit, host: explicit) as resolve, \
             patch("cam_streamer.stream.ffmpeg_version", return_value="6.1.1"), \
             patch("cam_streamer.cli.run_app") as run_app:
            result = self.invoke(["--host", "10.0.0.9", "--port", "28554"])
        self.assertEqual(result.exit_code, 0, result.output)
        resolve.assert_called_once_with(28554, "10.0.0.9")
        run_app.assert_called_once_with("10.0.0.9", 28554, "/usr/bin/ffmpeg", "6.1.1")

    def test_version_flag(self):
        result = self.invoke(["--version"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("cam-streamer", result.output)

    def test_cli_module_imports_without_fcntl(self):
        code = (
            "import builtins\n"
            "real_import = builtins.__import__\n"
            "def fake_import(name, *args, **kwargs):\n"
            "    if name == 'fcntl':\n"
            "        raise ModuleNotFoundError(\"No module named 'fcntl'\")\n"
            "    return real_import(name, *args, **kwargs)\n"
            "builtins.__import__ = fake_import\n"
            "import cam_streamer.cli\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
