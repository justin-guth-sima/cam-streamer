# cam-streamer

A terminal UI that streams the webcams of a Linux machine into
[SiMa Insight](https://developer.sima.ai/software/tools/insight/) RTSP source
slots (`rtsp://<host>:8554/src1` … `src48`), so a Neat application under test
can read a live camera instead of a file.

It lists the cameras it finds, lets you pick a slot and a capture mode, runs
ffmpeg for you, and shows a compact live status per stream. Insight shows the
slot as **External** as soon as the stream arrives.

## Prerequisites

- Linux (video is captured through V4L2). Other systems are refused with a
  one-line message.
- `ffmpeg` on `PATH`.
- Python 3.8 or newer.
- Insight reachable on its RTSP port: in a Neat SDK container on the same
  machine (the default), or on a DevKit via `--host`.

## Install

```bash
pipx install git+https://github.com/justin-guth-sima/cam-streamer
```

or, into a virtual environment:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install git+https://github.com/justin-guth-sima/cam-streamer
```

## Use

```bash
cam-streamer                          # Insight on this machine
cam-streamer --host 10.0.0.42         # Insight on a DevKit, port 8554
cam-streamer --host 10.0.0.42 --port 18554
```

When `--host` is left at its loopback default and no `--port` is given, the
port is taken from the local container that publishes `8554/tcp`, which is the
Neat SDK container running Insight, else `8554`.

The screen has a camera list on the left and a stream table on the right, a
detail line for the highlighted camera with the exact URL to paste into your
application, and a message line.

| Key | Action |
| --- | --- |
| `enter` | start the highlighted camera: asks for a slot (default: lowest free), uses the auto capture mode |
| `f` | pick a capture mode from the camera's real list; restarts a running stream with it |
| `s` | stop the highlighted stream (or the highlighted camera's stream) |
| `r` | rescan cameras now; the list also rescans itself every two seconds |
| `tab`, `←`, `→` | switch between the two panes |
| `q`, `Ctrl-C` | stop every stream and quit |

Status per stream: `… starting`, `● 30.0 fps` while streaming (`● no frames`
when progress stalls), `✗ <reason>` on error. Error reasons are short and
concrete: `slot 3 already in use`, `camera in use by another program`,
`Insight not reachable at host:port`, `camera disconnected`, `mode not
accepted, choose another with f`. The last ffmpeg lines of a failed stream are
written to `~/.cam-streamer/src<N>.log`.

## How it works

- Cameras and their modes come straight from V4L2 ioctls; no `v4l-utils`
  needed. Metadata nodes are filtered out, so each physical camera appears once.
- The auto mode prefers MJPEG, then YUYV, at the largest size up to 1280x720
  and up to 30 fps; staying inside those caps outranks the format preference
  because they bound host CPU.
- Every stream is one ffmpeg process: V4L2 in, libx264 (`veryfast`,
  `zerolatency`, one keyframe per second) out, RTSP over TCP. Insight's
  preview needs the short keyframe interval to show a first frame quickly.
- One camera can feed one slot. Two processes cannot capture from the same
  V4L2 device; fanning one camera out to several slots would need a single
  ffmpeg with the tee muxer and is not implemented.
- Quitting, and any other exit path, stops every ffmpeg the tool started.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
```

The tests need neither a camera nor ffmpeg nor docker: the V4L2 layer is fed
recorded struct bytes, the stream engine runs a small Python stand-in for
ffmpeg, and the screen is driven headlessly with Textual's pilot.

The design document, written when the tool was first planned, is in
[`docs/design.md`](docs/design.md).

## License

Apache License 2.0, like the SiMa Neat projects this tool works with. See [`LICENSE`](LICENSE).
