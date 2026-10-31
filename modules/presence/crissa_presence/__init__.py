"""Room presence rail — schema + service. No Crissa imports."""

__version__ = "0.0.1"
SCHEMA = "room_presence/v1"

# THE CAMERA SWITCH (2026-10-10): with the camera off (CRISSA_NO_CAMERA=1, the launcher's --no-camera, or
# RAIL_PRESENCE_CAMERA=off) no request from any process that loads this package reaches a camera address. The
# explicit no-ops (camera.py's "off" mode, the service's door aim and grabber posts) answer first; this is the
# backstop for the rest of the copied rail. With a camera on, nothing is wrapped.
from . import ports as _ports  # noqa: E402

_ports.guard_camera()
