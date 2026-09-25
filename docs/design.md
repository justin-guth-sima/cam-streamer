---
> Written on 2026-09-24 while this tool was planned as a `sima-cli` subcommand; it is now the standalone `cam-streamer` project, so read `sima-cli util cam` and `sima-cli neat cam` as `cam-streamer`, and the Neat SDK container port lookup as any container publishing 8554/tcp. Error logs live in `~/.cam-streamer/`, not `~/.sima-cli/cam/`.
---

# `sima-cli util cam` — webcam streaming into Insight source slots

Date: 2026-09-24
Status: approved design, awaiting implementation plan

## Purpose

Insight turns uploaded media files into RTSP sources (`rtsp://<host>:8554/srcN`,
N = 1..48) that Neat applications read during development. It cannot yet
capture a webcam itself, but it accepts any external RTSP publisher on a slot
and shows that slot as **External**. Today a developer who wants a live webcam
enumerates `/dev/video*` by hand, checks modes with `v4l2-ctl`, and types a
long `ffmpeg` command with the right host, port and slot.

`sima-cli util cam` replaces that with a full-screen terminal application that
lists the host's cameras, streams one or more of them to chosen slots, and shows
compact live status. It handles URLs, ports and the ffmpeg recipe. The user
deals only in cameras and slot numbers.

This is a stopgap until Insight gains native webcam support, so scope is kept
deliberately small.

## Decisions taken during brainstorming

| Question | Decision |
| --- | --- |
| Where does it live | A subcommand in sima-cli, under a new `util` group for host-side developer utilities (moved out of `neat`, whose other commands are all installers). sima-cli has no extension or alias mechanism, and building one costs more than the tool. |
| Host operating systems | Linux only. Other OSes get a one-line "not supported yet" message. |
| Interaction model | Full-screen TUI built on Textual. |
| Talks to Insight's HTTP API | No. ffmpeg only. Insight's rejection of a busy slot is read from ffmpeg's stderr. |
| Capture format | Auto-picked per camera with a per-camera override from the camera's real mode list. |
| Encoding | Always re-encode to H.264. Native H.264 or MJPEG pass-through is out of scope. |
| Camera and mode discovery | Direct v4l2 ioctls from Python. No `v4l-utils` prerequisite. |

## Prerequisites and environment

- Linux host with `ffmpeg` on `PATH`. Checked before the screen opens.
- Python 3.8 or newer, as for the rest of sima-cli.
- New dependency: `textual>=3.7,<4`. Textual 3.x is the last line that supports
  Python 3.8 and the `rich==14.0.0` sima-cli pins. Textual 4+ needs Python 3.9
  and rich 14.2.
- Insight reachable on an RTSP port: inside a Neat SDK container on the same
  host (the default) or on a DevKit (via flags).

## Command surface

```
sima-cli util cam [--host HOST] [--port PORT]
```

- `--host`: Insight host. Default `127.0.0.1`.
- `--port`: RTSP port. Default: when `--host` is loopback (the default), the
  host port published for container port `8554/tcp` by the running Neat SDK
  container, else `8554`. For any other host, `8554`.

Before starting the screen the command checks, in order, and prints exactly one
sentence and exits non-zero on the first failure:

1. Platform is Linux.
2. `ffmpeg` is on `PATH`.

## Screen

Rendered at 100 columns:

```
 Insight webcam streamer                        rtsp://127.0.0.1:8554/src<N>   ffmpeg 6.1
 ┌ Cameras ───────────────────────┐ ┌ Streams ──────────────────────────────────────────┐
 │ > HP 5MP Camera    /dev/video0 │ │ Slot  Camera          Mode              Status     │
 │   HP IR Camera     /dev/video2 │ │  3    HP 5MP Camera   1280x720 30fps    ● 29.9 fps 2.1 Mb/s │
 │                                │ │  7    HP IR Camera    640x480 30fps     ✗ slot in use     │
 │                                │ │                                                   │
 └────────────────────────────────┘ └───────────────────────────────────────────────────┘
 enter start  s stop  f format  r rescan  q quit  tab / ← → switch pane
```

- Header: title, the URL pattern with the resolved host and port, ffmpeg
  version. The URL pattern is what the user pastes into their application.
- Cameras pane: every video-capture-capable device by its human name only;
  the device path and the rest live in the details line so long names are
  not clipped. Metadata nodes and other non-capture nodes are hidden. A camera
  whose device node cannot be opened is listed with a note (`no permission` for
  EACCES, `busy` for EBUSY) and no modes. Note that a camera held by another
  program usually still opens and enumerates on Linux; that conflict surfaces
  only when ffmpeg starts and is reported on the stream row instead.
- Streams pane: one row per stream with slot, camera, mode, status.
- Details line, under the panes: one line about the highlighted camera (the
  stream row's camera when the table has focus, else the list item): full
  name, device path, mode count and the auto mode, or the note; and, while
  it streams, its slot and the exact URL to paste. Refreshes on highlight
  changes and on the status tick.
- Footer: the key legend, including the pane-switching keys.

Keys:

- **enter** on a camera: prompt for a slot number 1..48, default the lowest
  slot not already used by this tool; start with the auto mode.
- **f** on a camera: show its real mode list, best first, auto choice marked.
  Picking one starts a stream with that mode, or restarts a running stream of
  that camera with it.
- **s** on a stream row: stop it and remove the row.
- **r**: rescan cameras now. The list also rescans itself every two seconds,
  so hot-plugged cameras appear and unplugged ones disappear on their own.
- **q** or Ctrl-C: stop every stream, then exit.
- **tab**, **←**, **→**: move focus between the Cameras and Streams panes. The
  legend names them so nobody has to guess.

Status column, four states:

| State | Rendering |
| --- | --- |
| Starting | grey `… starting` |
| Streaming | green `● <fps> fps <bitrate>`, or `● <fps> fps` when ffmpeg reports no bitrate (it cannot for RTSP output); yellow `● no frames` if progress stalls |
| Error | red `✗ <short reason>`; stays until the row is removed with **s** |
| Stopped | dim `stopped` (transient, row is removed) |

There is no log pane. On error the last ffmpeg lines are written to a log file
and the reason includes where.

## Modules

New package `sima_cli/cam/`. Only `app.py` imports Textual.

### `v4l2.py` — camera discovery

Lists `/sys/class/video4linux/*`, reads each `name`, opens `/dev/<node>` and
issues three ioctls with the standard library (`fcntl`, `struct`, `ctypes`):

1. `VIDIOC_QUERYCAP`: keep only nodes whose device capabilities include video
   capture and exclude metadata capture.
2. `VIDIOC_ENUM_FMT`: pixel formats.
3. `VIDIOC_ENUM_FRAMESIZES` and `VIDIOC_ENUM_FRAMEINTERVALS`: discrete sizes
   and rates per format. Stepwise or continuous ranges are reduced to the
   range's maximum size and the common rates 30 and 15 that fall inside it.

Output: `Camera(path, name, modes, note)` where `note` is empty, `busy` or
`no permission`, and a mode is
`Mode(pixel_format, width, height, fps)`. `pixel_format` is the ffmpeg
`-input_format` name (`mjpeg`, `yuyv422`, ...); formats ffmpeg has no name for
are dropped.

The ioctl call goes through one small function taking a file descriptor,
request and buffer, so tests can substitute it.

### `modes.py` — mode selection

- `pick_default(modes)`: staying inside the caps outranks the format preference, because the caps bound host CPU. Order: modes at or below 1280x720 and at or below 30 fps first; among those, prefer `mjpeg`, then `yuyv422`, then anything else; then largest area; then highest fps. Modes above a cap come last, closest to the cap first. So an in-cap `yuyv422` mode beats an over-cap `mjpeg` mode.
- `sort_best_first(modes)`: the same ordering, for the picker.

Pure functions.

### `stream.py` — streaming engine

- `Stream(camera, mode, slot, url)`: owns one ffmpeg child started with
  `asyncio.create_subprocess_exec`. Reads the progress pipe on stdout for `fps`
  and `bitrate`, keeps the last 40 stderr lines in a ring buffer, and exposes
  `state`: `Starting`, `Streaming(fps, bitrate, stalled)`, `Error(reason,
  log_path)`, `Stopped`. ffmpeg reports `bitrate=N/A` for RTSP output, so the bitrate is usually 0 and the screen omits it.
- A progress gap over five seconds while ffmpeg is alive sets `stalled`.
- `stop()`: SIGINT, wait two seconds, then SIGKILL.
- `StreamSet`: streams keyed by slot. Refuses a second stream on a slot or a
  second stream from the same camera. `stop_all()` stops every stream
  concurrently.
- Error log: on `Error`, the ring buffer is written to
  `~/.sima-cli/cam/src<N>.log`, overwriting.

### `target.py` — URL resolution

- `resolve_port(explicit, host)`: explicit flag, else `8554` for a non-loopback
  host, else the host port published for `8554/tcp` by the running Neat SDK
  container (parsed from the docker `Ports` string), else `8554`.
- `slot_url(host, port, slot)`: `rtsp://{host}:{port}/src{slot}`.
- `url_pattern(host, port)`: the header text with `src<N>`.

### `app.py` — Textual screen

The screen from above. Holds no logic beyond key handling and rendering. Polls
each stream's state twice a second. The slot prompt and mode picker are modal
Textual screens.

### `commands.py`

Registers `cam` on the existing `neat` click group in
`sima_cli/vulcan/commands.py`, next to `install` and `sdk`. Performs the
prerequisite checks, resolves the port, then runs the app.

## ffmpeg recipe

For MJPEG 1280x720 at 30 fps to slot 3:

```
ffmpeg -hide_banner -nostdin -loglevel warning
  -f v4l2 -input_format mjpeg -video_size 1280x720 -framerate 30 -i /dev/video0
  -c:v libx264 -preset veryfast -tune zerolatency -g 30 -pix_fmt yuv420p
  -f rtsp -rtsp_transport tcp
  -progress pipe:1
  rtsp://127.0.0.1:8554/src3
```

- Input format, size and rate always come from the picked mode, so ffmpeg never
  negotiates something other than what the screen shows.
- `-g` equals the frame rate: one keyframe per second, which Insight's preview
  needs to show a first frame quickly.
- No audio.
- `-loglevel warning` keeps stderr to real problems.

## Error mapping

stderr pattern to status reason:

Patterns are anchored to their context on purpose: a bare three-digit number or a generic `Invalid argument` from another component must not be mistaken for these errors.

| Pattern | Reason |
| --- | --- |
| `Device or resource busy` on the input | `camera in use by another program` |
| an RTSP method line `... failed: 4xx`, ffmpeg's `Server returned 4xx`, or `already publishing` | `slot N already in use` |
| `Connection refused`, `Connection timed out`, `No route to host` | `Insight not reachable at host:port` |
| `Not a video capture device`, or `Invalid argument` on a `video4linux2` / `/dev/videoN` line | `mode not accepted, choose another with f` |
| ffmpeg exits with code 0 on its own | `camera disconnected` |
| anything else | `ffmpeg exited (code N), see ~/.sima-cli/cam/srcN.log` |

Patterns are matched against the ring buffer when ffmpeg exits with a non-zero
code, first match wins in table order.

## Lifecycle

- Discovery runs at start, on **r**, and every two seconds on a timer. The
  list is rebuilt only when the set of cameras changed, and the highlight
  stays on the same device across a rebuild (or moves to the first camera when
  that device is gone). A stream whose camera was unplugged keeps its Error
  row; only the list entry disappears.
- Start: camera + mode + slot into `StreamSet`, which builds the URL and spawns
  ffmpeg.
- Quit and Ctrl-C call `stop_all()` before the screen closes, so no ffmpeg is
  orphaned.
- Nothing is persisted between runs except error logs.

## Testing

Unit tests under `tests/unit/`, none needing a camera or ffmpeg:

- **v4l2**: recorded ioctl struct bytes from a real camera; capture node kept,
  metadata node dropped, EBUSY and EACCES on open produce the note,
  enumeration terminates.
- **modes**: table-driven cases for `pick_default` and `sort_best_first`,
  including the "nothing under the cap" fallback.
- **stream**: a small Python script stands in for ffmpeg and, by argument,
  emits progress blocks then exits, prints a chosen stderr line then exits with
  a code, or ignores SIGINT. Cases: every state transition, every error mapping
  row, stall detection, log written on error, one-per-slot and one-per-camera
  refusal, `stop_all` cleanliness.
- **target**: docker `Ports` strings to port, no container to 8554, flag
  overrides, URL building.
- **commands**: `cam` is registered under `neat`; non-Linux and missing ffmpeg
  produce the one-line message and a non-zero exit.
- **app**: a Textual pilot smoke test with a fake camera list: press enter,
  accept the default slot, a row appears; press q, every stream was stopped.

Manual check before completion: stream this machine's camera into a running
Insight and confirm the slot turns External with the shown resolution; publish
to a slot already in use and see the error row; unplug the camera and see
`camera disconnected`.

## Out of scope

- macOS and Windows capture.
- Reading or controlling Insight slot state through its HTTP API.
- Native H.264 or MJPEG pass-through without re-encoding.
- Audio.
- Persisting camera-to-slot assignments between runs.
- A non-interactive mode. The engine is UI-free so one can be added later.

## Implementation notes

- "`fcntl` is imported inside the ioctl helper and the `stream` module is imported lazily by the command, so `import sima_cli.cli` works on every platform; only `neat cam` itself refuses non-Linux hosts."
- "An ffmpeg killed by an outside signal is reported as `ffmpeg killed by signal N`; only a stop requested by the tool yields the Stopped state."
- "stdout and stderr are read in chunks and split on `\r` and `\n`, so an over-long line can never stall the pipe."
- "Discovery collapses a sysfs name of the form `X: X` to `X`; the kernel reports the USB product and card name joined, and they are often identical."
- "The screen guards its timer and worker callbacks with `App.is_running`, because Textual removes widgets before it stops timers during shutdown."
