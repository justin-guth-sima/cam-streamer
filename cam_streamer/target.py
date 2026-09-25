import json
import re
import shutil
import subprocess
from typing import Callable, List, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8554
MIN_SLOT = 1
MAX_SLOT = 48
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_RTSP_MAPPING = re.compile(r":(\d+)->8554/tcp")


def slot_url(host: str, port: int, slot: int) -> str:
    return "rtsp://{}:{}/src{}".format(host, port, slot)


def url_pattern(host: str, port: int) -> str:
    return "rtsp://{}:{}/src<N>".format(host, port)


def port_from_ports_string(ports: str) -> Optional[int]:
    match = _RTSP_MAPPING.search(ports or "")
    return int(match.group(1)) if match else None


def _running_containers() -> List[dict]:
    """`docker ps` rows as dicts; empty when docker is missing, unreachable or slow."""
    if not shutil.which("docker"):
        return []
    try:
        result = subprocess.run(["docker", "ps", "--format", "{{json .}}"],
                                capture_output=True, text=True, timeout=3, check=True)
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in result.stdout.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def resolve_port(explicit: Optional[int] = None, host: str = DEFAULT_HOST,
                 containers: Callable[[], List[dict]] = _running_containers) -> int:
    """The RTSP port: the flag, else the port a local container publishes for 8554/tcp, else 8554.

    Insight runs inside the Neat SDK container, which may publish its RTSP port under
    another host port; that lookup only makes sense when the host is this machine.
    """
    if explicit:
        return explicit
    if host not in LOOPBACK_HOSTS:
        return DEFAULT_PORT
    try:
        for container in containers():
            port = port_from_ports_string(container.get("Ports", ""))
            if port:
                return port
    except Exception:
        pass
    return DEFAULT_PORT
