import platform
import shutil
from typing import Optional

import click

from cam_streamer import __version__
from cam_streamer.target import DEFAULT_HOST, resolve_port


def run_app(host: str, port: int, ffmpeg: str, version: str) -> None:
    from cam_streamer.app import CamApp
    CamApp(host=host, port=port, ffmpeg=ffmpeg, ffmpeg_version=version).run()


@click.command(name="cam-streamer", context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__)
@click.option("--host", default=DEFAULT_HOST, show_default=True, help="Insight host to publish to.")
@click.option("--port", type=int, default=None,
              help="Insight RTSP port. For a loopback host, defaults to the port a local container "
                   "publishes for 8554/tcp (the Neat SDK container running Insight), else 8554.")
def main(host: str, port: Optional[int]) -> None:
    """Stream webcams from this machine into Insight source slots (src1..src48)."""
    if platform.system() != "Linux":
        raise click.ClickException("cam-streamer supports Linux hosts only for now.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise click.ClickException("ffmpeg was not found on PATH; install it and try again.")
    from cam_streamer.stream import ffmpeg_version
    run_app(host, resolve_port(port, host), ffmpeg, ffmpeg_version(ffmpeg))
