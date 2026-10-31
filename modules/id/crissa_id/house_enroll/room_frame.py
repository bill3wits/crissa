"""Room frame — two clocks. Do not smash them.

Monitor / mic / VoiceOnly dial (USB cord = 12, **stays at the top of the icon**):
  User sits at **6**, looks at **12**, door mark at **10**. Bearings are not
  rotated so 12 becomes 10 — the door is a label, not a remumber.

Camera / sitters / patrol / VL (`site_stations/v2`):
  User desk **8**, a regular **10**, door rest **12**. Fable retracted the
  full seat remumber (user_desk_6). Next house-init must not write it back.

The puck's native 12 is its USB cord. Cord at the screen ⇒ offset 0.

First boot programs the files the rails read:

- `house-id/site.json` `stations` (`door_beam` = camera sitter, `heading.door_hour` = dial)
- `<presence data>/room_geometry.json` (legal/blocked/heading/door rest)
- `<presence data>/camera-stops.json` station labels (pans stay measured)
"""

from __future__ import annotations

from .. import paths as _paths

import json
import os
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .paths import site_config_path

# Init-clock defaults (sitters / patrol). User 6, door 10. Hour 12 is a down seat.
DEFAULT_USER_BEAM = 6
DEFAULT_DOOR_BEAM = 10
DEFAULT_WALL = [1, 2, 3, 4, 5]
# Monitor-frame defaults (dial). 12 stays at the top of the icon.
DEFAULT_HEADING_DOOR = 10
SCREEN_FORWARD_HOUR = 12
FRAME = "camera_clock_center"
SCHEMA = "site_stations/v2"


def _user_beam(st: dict[str, Any] | None) -> int:
    st = st or {}
    try:
        h = int(st.get("user_beam") or 0)
        if 1 <= h <= 12:
            return h
    except (TypeError, ValueError):
        pass
    for hour, meta in (st.get("sitters") or {}).items():
        if isinstance(meta, dict) and meta.get("role") == "user_desk":
            try:
                return int(hour)
            except (TypeError, ValueError):
                pass
    return DEFAULT_USER_BEAM


def _camera_door(st: dict[str, Any] | None) -> int:
    st = st or {}
    try:
        h = int(st.get("camera_door_beam") or 0)
        if 1 <= h <= 12:
            return h
    except (TypeError, ValueError):
        pass
    for hour, meta in (st.get("sitters") or {}).items():
        if isinstance(meta, dict) and meta.get("role") == "door_line":
            try:
                return int(hour)
            except (TypeError, ValueError):
                pass
    return DEFAULT_DOOR_BEAM


def _heading_door(st: dict[str, Any] | None) -> int:
    """Dial label only. Never fall back to stations.door_beam (camera sitter)."""
    st = st or {}
    heading = st.get("heading") if isinstance(st.get("heading"), dict) else {}
    try:
        h = int(heading.get("door_hour") or 0)
        if 1 <= h <= 12:
            return h
    except (TypeError, ValueError):
        pass
    return DEFAULT_HEADING_DOOR


def _tty() -> bool:
    return sys.stdin.isatty()


def _ask_hour(prompt: str, *, default: int | None = None) -> int | None:
    """Ask for a clock hour 1–12. None if skipped / not a terminal."""
    if not _tty():
        return default
    hint = f" [{default}]" if default else " [1-12]"
    for _ in range(3):
        try:
            raw = input(f"{prompt}{hint} ").strip()
        except EOFError:
            return default
        if not raw:
            return default
        try:
            h = int(raw.lstrip("0") or "0")
        except ValueError:
            print("    a number 1-12, please.")
            continue
        if 1 <= h <= 12:
            return h
        print("    a number 1-12, please.")
    return default


def _ask_hours(prompt: str, *, default: list[int] | None = None) -> list[int]:
    """Comma-separated hours, or enter to keep default."""
    default = list(default or [])
    if not _tty():
        return default
    hint = f" [{','.join(str(x) for x in default) or 'none'}]"
    try:
        raw = input(f"{prompt}{hint} ").strip()
    except EOFError:
        return default
    if not raw:
        return default
    out: list[int] = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        try:
            h = int(part)
        except ValueError:
            continue
        if 1 <= h <= 12 and h not in out:
            out.append(h)
    return out


def _ask_yes(prompt: str, *, default_yes: bool = True) -> bool:
    if not _tty():
        return default_yes
    hint = "[Y/n]" if default_yes else "[y/N]"
    try:
        ans = input(f"{prompt} {hint} ").strip().lower()
    except EOFError:
        return default_yes
    if not ans:
        return default_yes
    return ans in ("y", "yes")


def _load(root: Path) -> tuple[Path, dict[str, Any]]:
    p = site_config_path(root)
    return p, json.loads(p.read_text(encoding="utf-8"), object_pairs_hook=OrderedDict)


def _save(p: Path, d: dict[str, Any]) -> None:
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def explain() -> None:
    print(
        """
  ── How to label this room ──────────────────────────────────
  Sit at your desk. Face the monitor. That is the only pose
  that counts.

  1. Put the mic where it will LIVE. Rotate it now.
     Do not move it after you answer.

  2. The puck's 12 is the USB port (the cord). Point USB at
     the monitor. USB = 12 = the TOP of the icon. Do not
     remap that top mark to 10.

  3. On THIS clock you sit at 6, you look at 12, the door
     is a mark at 10. The door hour is a LABEL. Bearings
     stay as the mic reports them.

           12  you look (monitor)
       11        1
     10 door        2
      9      ·      3
      8            4
        7        5
            6  you sit

  4. Camera seats are a different clock (the picture):
     you 8, a regular 10, door 12. Do not remumber them
     just because the dial door is 10.
  ────────────────────────────────────────────────────────────
"""
    )


def ask_user(root: Path, *, yes: bool = False) -> dict[str, str]:
    """The User's name is first-class init, not a leftover template id."""
    from .person import format_display_name, format_person_id

    path, d = _load(root)
    enroll = d.setdefault("enroll", OrderedDict())
    prev = str(enroll.get("user_id") or "").strip()
    prev_disp = str((d.get("stations") or {}).get("user_display") or prev).strip()
    if yes or not _tty():
        pid = format_person_id(prev) or "user"
        disp = format_display_name(prev_disp or prev) or pid
        enroll["user_id"] = pid
        st0 = d.setdefault("stations", OrderedDict())
        st0["user_display"] = disp
        st0.setdefault("user_beam", _user_beam(st0))
        _save(path, d)
        return {"user_id": pid, "display": disp, "user_beam": _user_beam(st0)}

    print("\n  You are the User. On the dial you sit at 6 and look at 12.")
    print("  Camera desk hour stays whatever site.json already has (this office 8).")
    hint = f" [{prev_disp or prev}]" if (prev_disp or prev) else ""
    try:
        raw = input(f"  What should we call you?{hint} ").strip()
    except EOFError:
        raw = ""
    if not raw:
        raw = prev_disp or prev or "user"
    pid = format_person_id(raw) or "user"
    disp = format_display_name(raw) or pid
    enroll["user_id"] = pid
    st = d.setdefault("stations", OrderedDict())
    st["user_display"] = disp
    seat = _user_beam(st)
    if not yes and _tty():
        asked = _ask_hour(
            "  Camera-clock hour of your desk? (this office 8 — not the dial 6)",
            default=seat,
        )
        if asked:
            seat = asked
    st["user_beam"] = seat
    _save(path, d)
    print(f"  ✓ User {disp}  (id {pid}) camera desk {seat}  (dial you = 6)")
    return {"user_id": pid, "display": disp, "user_beam": seat}


def ask_room_frame(root: Path, *, yes: bool = False) -> dict[str, Any]:
    """Prompt for mic orientation + door hour. Returns what was written."""
    path, d = _load(root)
    st = d.setdefault("stations", OrderedDict())
    explain()

    # --- the mic is the physical key -------------------------------------
    mp = st.setdefault("mic_placement", OrderedDict())
    cord = 12
    if not yes:
        print("  Mic 12 = USB port. Point USB at the monitor before you continue.")
        if not _ask_yes("  Is the mic in its FINAL orientation (you will not rotate it again)?"):
            print("    Rotate it now, then come back. USB should face the monitor.")
            input("    Enter when the mic will not move again. ")
        if _ask_yes("  USB port / cord is facing the monitor (12)?"):
            cord = 12
        else:
            print("    Keep the puck where it is. We will record which hour the USB faces.")
            cord = _ask_hour(
                "    From your chair, which hour does the USB port point to?",
                default=12,
            ) or 12
    mp["with_monitor"] = True
    mp["orientation_key"] = "USB cord = the puck native 12 o clock"
    mp["cord_points_to_hour"] = cord
    # Cord at 12 is the zero case; anything else rotates every bearing we read.
    mp["mic_zero_offset_hours"] = (cord - 12) % 12
    mp["collected_at"] = "init"

    # --- the door is a LABEL on the mic clock (12 stays at the top) ------
    prev = _heading_door(st)
    print("\n  Door = a mark on the mic clock. USB/12 stays at the top of the icon.")
    print("  Do not remap that top mark to 10. This office the door sits at 10.")
    door = _ask_hour(
        "  From the mic (USB=12), which hour is your door?",
        default=prev,
    )
    if door is None:
        door = prev
    heading_door = int(door)
    camera_door = _camera_door(st)
    st["door_beam"] = camera_door
    st["camera_door_beam"] = camera_door
    st["door_beam_source"] = "camera-frame door sitter (v2). Dial label is heading.door_hour."
    st["heading"] = {
        "schema": "room_heading/v1",
        "frame": "monitor",
        "twelve": "monitor",
        "twelve_is": "XVF3800 USB plug — stays at the top of the icon",
        "door_hour": heading_door,
        "user_hour": 6,
        "source": "house-init room phase",
    }

    st["schema"] = st.get("schema") or SCHEMA
    st["frame"] = st.get("frame") or FRAME
    st["user_beam"] = _user_beam(st)
    st["screen_forward_hour"] = SCREEN_FORWARD_HOUR

    _save(path, d)
    print(
        f"\n  ✓ dial door = {heading_door}   camera door = {camera_door}   "
        f"camera you = {_user_beam(st)}"
    )
    if mp["mic_zero_offset_hours"]:
        print(f"  ✓ mic cord at {cord} → offset {mp['mic_zero_offset_hours']}h recorded")
    return {
        "door_beam": heading_door,
        "heading_door": heading_door,
        "camera_door": camera_door,
        "mic_placement": dict(mp),
    }


def ask_family(root: Path, *, yes: bool = False) -> list[dict[str, Any]]:
    """Collect household names and, where they have one, a fixed seat hour.

    Names only. No embeddings, no counts, no guessing — a seat is recorded
    because the User said so, never because someone was seen there. Seats are
    optional: 'if available' means blank is a perfectly good answer.
    """
    path, d = _load(root)
    st = d.setdefault("stations", OrderedDict())
    if yes or not _tty():
        return list(st.get("regulars") or [])

    print("\n  Who else lives or works here? (blank name to finish)")
    print("  A seat is optional — press enter if they move around.")
    rows: list[dict[str, Any]] = list(st.get("regulars") or [])
    known = {str(r.get("person_id", "")).lower() for r in rows}
    while True:
        try:
            name = input("    Name: ").strip()
        except EOFError:
            break
        if not name:
            break
        pid = name.strip().lower().split()[0]
        if pid in known:
            print(f"    {pid} already listed — skipping.")
            continue
        seat = _ask_hour(f"    Which hour does {name} usually sit at? (enter = no fixed seat)",
                         default=None)
        row: dict[str, Any] = OrderedDict(
            [("person_id", pid), ("display", name), ("kind", "regular")]
        )
        if seat is not None:
            row["home_beam"] = int(seat)
            row["prior"] = 1.0
        row["source"] = "init: named by the User"
        rows.append(row)
        known.add(pid)
        print(f"    ✓ {name}" + (f" at {seat} o'clock" if seat is not None else " (no fixed seat)"))

    st["regulars"] = rows
    _save(path, d)
    seated = sum(1 for r in rows if r.get("home_beam") is not None)
    print(f"\n  ✓ {len(rows)} people recorded, {seated} with a seat")
    return rows


def ask_visitors(root: Path, *, yes: bool = False) -> list[dict[str, Any]]:
    """Usual visitors (not occupancy). Optional usual seat if they have one."""
    path, d = _load(root)
    st = d.setdefault("stations", OrderedDict())
    if yes or not _tty():
        return list(st.get("guests") or [])

    print("\n  Usual visitors (guests) — not occupancy.")
    print("  Blank name to finish. Seat optional.")
    rows: list[dict[str, Any]] = list(st.get("guests") or [])
    known = {str(r.get("person_id", "")).lower() for r in rows}
    while True:
        try:
            name = input("    Visitor name: ").strip()
        except EOFError:
            break
        if not name:
            break
        pid = name.strip().lower().split()[0]
        if pid in known:
            print(f"    {pid} already listed — skipping.")
            continue
        seat = _ask_hour(
            f"    Usual seat hour for {name}? (enter = none)",
            default=None,
        )
        row: dict[str, Any] = OrderedDict(
            [("person_id", pid), ("display", name), ("kind", "guest")]
        )
        if seat is not None:
            row["home_beam"] = int(seat)
        row["source"] = "init: usual visitor"
        rows.append(row)
        known.add(pid)
        print(f"    ✓ visitor {name}" + (f" at {seat}" if seat is not None else ""))

    st["guests"] = rows
    st["guests_pool"] = [str(r.get("person_id")) for r in rows if r.get("person_id")]
    _save(path, d)
    print(f"\n  ✓ {len(rows)} usual visitors")
    return rows


def ask_layout(root: Path, *, yes: bool = False) -> dict[str, Any]:
    """Media, furniture, stand, seats-down — hours that are not sit-legal."""
    path, d = _load(root)
    st = d.setdefault("stations", OrderedDict())
    prev_media = st.get("media_hour")
    prev_dress = st.get("furniture_hours") or st.get("dresser_hours")
    prev_stand = st.get("stand_hours")
    prev_down = st.get("seats_down")
    prev_wall = st.get("wall_hours")
    if yes or not _tty():
        return {
            "media_hour": prev_media,
            "furniture_hours": list(prev_dress or []),
            "stand_hours": list(prev_stand or []),
            "seats_down": list(prev_down or []),
            "wall_hours": list(prev_wall or []),
        }

    print("\n  Same mic clock (USB = 12).")
    print("  First: hours a PERSON can be (chair or stand).")
    stand = _ask_hours(
        "  Stand-only hours (people can stand, no chair)",
        default=list(prev_stand or []),
    )
    print("  Then: hours nobody can occupy.")
    print("  Against a wall this is often six hours in a row.")
    wall = _ask_hours(
        "  WALL hours (no stand, no sit)",
        default=list(prev_wall or list(DEFAULT_WALL)),
    )
    media = _ask_hour("  Media / speakers hour?", default=prev_media)
    furniture = _ask_hours(
        "  Furniture hours (dresser, not a person)",
        default=list(prev_dress or []),
    )
    down = _ask_hours(
        "  Seats that are down / unused",
        default=list(prev_down or []),
    )
    st["media_hour"] = media
    st["furniture_hours"] = furniture
    st["stand_hours"] = stand
    st["seats_down"] = down
    st["wall_hours"] = wall
    _save(path, d)
    return {
        "media_hour": media,
        "furniture_hours": furniture,
        "stand_hours": stand,
        "seats_down": down,
        "wall_hours": wall,
    }


def _geom_path() -> Path:
    return Path(
        os.environ.get(
            "ROOM_GEOMETRY",
            str(_paths.presence_data() / "room_geometry.json"),
        )
    )


def _stops_path() -> Path:
    return Path(
        os.environ.get(
            "RAIL_CAMERA_STOPS",
            str(_paths.presence_data() / "camera-stops.json"),
        )
    )


def _read_live_door_pose() -> dict[str, int]:
    """Prefer already-programmed heading, then v4l2, then the camera's own forward (no room's numbers)."""
    out = {"pan": 0, "tilt": 0, "zoom": 100}
    gp = _geom_path()
    if gp.is_file():
        try:
            h = (json.loads(gp.read_text()).get("heading") or {})
            if h.get("door_home_pan") is not None:
                out["pan"] = int(h["door_home_pan"])
            if h.get("door_home_tilt") is not None:
                out["tilt"] = int(h["door_home_tilt"])
            if h.get("door_home_zoom") is not None:
                out["zoom"] = int(h["door_home_zoom"])
        except Exception:
            pass
    try:
        import subprocess

        if _paths.camera_off():                  # no camera: the device is not asked (2026-10-10)
            raise RuntimeError("camera: off")
        raw = subprocess.check_output(
            [
                "v4l2-ctl",
                "-d",
                "/dev/video0",
                "--get-ctrl=pan_absolute",
                "--get-ctrl=tilt_absolute",
                "--get-ctrl=zoom_absolute",
            ],
            text=True,
            timeout=3,
        )
        for line in raw.splitlines():
            if "pan_absolute" in line and ":" in line:
                out["pan"] = int(line.split(":")[-1].strip())
            elif "tilt_absolute" in line and ":" in line:
                out["tilt"] = int(line.split(":")[-1].strip())
            elif "zoom_absolute" in line and ":" in line:
                out["zoom"] = int(line.split(":")[-1].strip())
    except Exception:
        pass
    return out


def program_room_globals(
    root: Path,
    *,
    door: int,
    media: int | None,
    furniture: list[int],
    stand: list[int],
    down: list[int],
    seats: list[int] | None = None,
    wall: list[int] | None = None,
    regulars: list[dict[str, Any]],
    visitors: list[dict[str, Any]],
    user_id: str | None = None,
    user_hour: int | None = None,
    camera_door: int | None = None,
) -> dict[str, Any]:
    """Write site.json + room_geometry.json + camera-stops labels.

    `door` is the dial / monitor-frame label (this office 10).
    `camera_door` / `user_hour` are the picture clock (this office 12 / 8).
    Pans are never invented from the hour. Do not remumber sitters to the dial.
    """
    if user_id is None:
        from .local_values import local

        user_id = local("user_id", "user")
    user = int(user_hour) if user_hour else DEFAULT_USER_BEAM
    if not (1 <= user <= 12):
        user = DEFAULT_USER_BEAM
    try:
        heading_door = int(door)
    except (TypeError, ValueError):
        heading_door = DEFAULT_HEADING_DOOR
    if not (1 <= heading_door <= 12):
        heading_door = DEFAULT_HEADING_DOOR
    try:
        cam_door = int(camera_door) if camera_door is not None else DEFAULT_DOOR_BEAM
    except (TypeError, ValueError):
        cam_door = DEFAULT_DOOR_BEAM
    if not (1 <= cam_door <= 12):
        cam_door = DEFAULT_DOOR_BEAM
    door = cam_door
    furniture = [int(x) for x in furniture if 1 <= int(x) <= 12]
    stand = [int(x) for x in stand if 1 <= int(x) <= 12]
    down = [int(x) for x in down if 1 <= int(x) <= 12]
    extra_seats = [int(x) for x in (seats or []) if 1 <= int(x) <= 12]
    wall = [int(x) for x in (wall or []) if 1 <= int(x) <= 12]
    sit: set[int] = {user}
    sitters: dict[str, dict[str, Any]] = {
        str(user): {
            "usual": user_id,
            "kind": "seat",
            "role": "user_desk",
            "prior": 1.0,
            "multi": False,
        }
    }
    roles: dict[str, str] = {str(user): "user_desk"}
    for r in regulars:
        if str(r.get("kind") or "") == "user":
            continue
        hb = r.get("home_beam")
        if hb is None:
            continue
        h = int(hb)
        sit.add(h)
        sitters[str(h)] = {
            "usual": str(r.get("person_id") or "").lower(),
            "kind": "seat",
            "role": "regular",
            "prior": 1.0,
            "multi": False,
        }
        roles[str(h)] = str(r.get("person_id") or "regular")
    for h in stand:
        sitters[str(h)] = {
            "usual": None,
            "kind": "beam_stand",
            "role": "stand",
            "multi": False,
            "furniture": False,
        }
        roles[str(h)] = "stand"
    if media:
        sitters[str(int(media))] = {
            "usual": None,
            "kind": "media",
            "role": "media",
            "multi": False,
        }
        roles[str(int(media))] = "media"
    for h in furniture:
        sitters[str(h)] = {
            "usual": None,
            "kind": "furniture",
            "role": "dresser",
            "multi": False,
        }
        roles[str(h)] = "dresser"
    sitters[str(door)] = {
        "usual": None,
        "kind": "beam_door",
        "role": "door_line",
        "multi": True,
        "furniture": False,
    }
    roles[str(door)] = "door"
    for h in extra_seats:
        sit.add(h)
        sitters.setdefault(
            str(h),
            {
                "usual": None,
                "kind": "seat",
                "role": "seat",
                "multi": False,
            },
        )
        roles.setdefault(str(h), "seat")
    for h in down:
        sitters[str(h)] = {
            "usual": None,
            "kind": "seat_down",
            "role": "seat_down",
            "multi": False,
        }
        roles[str(h)] = "seat_down"

    legal = sorted(sit | {door} | set(stand))
    blanked_set: set[int] = set(furniture) | set(down)
    if media:
        blanked_set.add(int(media))
    blanked = sorted(blanked_set)
    if not wall:
        wall = sorted(set(range(1, 13)) - sit - {door} - set(stand) - blanked_set)
    blocked = sorted(set(wall) | blanked_set)
    guest_beams = sorted(
        set(stand)
        | {
            int(v["home_beam"])
            for v in visitors
            if v.get("home_beam") is not None
        }
    )
    pose = _read_live_door_pose()
    az = 0.0 if heading_door == 12 else float(heading_door) * 30.0
    heading = {
        "schema": "room_heading/v1",
        "frame": "monitor",
        "twelve": "monitor",
        "twelve_is": "XVF3800 USB plug — stays at the top of the icon",
        "door_hour": heading_door,
        "user_hour": 6,
        "door_az_deg": az,
        "door_home_pan": pose["pan"],
        "door_home_tilt": pose["tilt"],
        "door_home_zoom": pose["zoom"],
        "source": "house-init room phase",
    }

    # site.json
    path, d = _load(root)
    st = d.setdefault("stations", OrderedDict())
    st["schema"] = SCHEMA
    st["frame"] = FRAME
    st["user_beam"] = user
    st["screen_forward_hour"] = SCREEN_FORWARD_HOUR
    st["door_beam"] = cam_door
    st["camera_door_beam"] = cam_door
    st["media_hour"] = media
    st["furniture_hours"] = furniture
    st["stand_hours"] = stand
    st["seats_down"] = down
    st["wall_hours"] = wall
    st["legal"] = legal
    st["illegal"] = blocked
    st["sitters"] = sitters
    st["regulars"] = list(regulars)
    st["guests"] = list(visitors)
    st["guests_pool"] = [
        str(v.get("person_id")) for v in visitors if v.get("person_id")
    ]
    st["guest_beams"] = guest_beams
    st["heading"] = heading
    st["programmed_by"] = "house-init program_room_globals"
    _save(path, d)

    # room_geometry.json — seat_slots + room_heading + patrol
    gp = _geom_path()
    geom: dict[str, Any] = {}
    if gp.is_file():
        try:
            geom = json.loads(gp.read_text())
        except Exception:
            geom = {}
    geom.update(
        {
            "schema": "room_geometry/v1",
            "legal_stations": legal,
            "blocked_stations": blocked,
            "blanked_sit": blanked,
            "wall_hours": wall,
            "multi_ok": [door],
            "media_station": media,
            "user_seat": user,
            "camera_door": cam_door,
            "station_roles": roles,
            "heading": heading,
            "source": "house-init",
            "range_default": "limited",
        }
    )
    gp.parent.mkdir(parents=True, exist_ok=True)
    gp.write_text(json.dumps(geom, indent=2) + "\n")

    # Relabel camera-stops hours only — never rewrite pans.
    sp = _stops_path()
    if sp.is_file():
        try:
            stops = json.loads(sp.read_text())
            from .local_values import local

            by_role = {
                "desk": user,
                # A regular's seat stop is named per house (house-local.json).
                local("regular_stop_id", "regular"): next(
                    (
                        int(r["home_beam"])
                        for r in regulars
                        if r.get("home_beam") and str(r.get("kind")) != "user"
                    ),
                    None,
                ),
                "stand_11": 11 if 11 in stand else (stand[-1] if stand else None),
                "door": door,
            }
            for s in stops.get("stops") or []:
                if not isinstance(s, dict):
                    continue
                hid = by_role.get(str(s.get("id") or ""))
                if hid is not None:
                    s["station"] = hid
            if isinstance(stops.get("door"), dict):
                stops["door"]["pan"] = pose["pan"]
                stops["door"]["tilt"] = pose["tilt"]
                stops["door"]["zoom"] = pose["zoom"]
            sp.write_text(json.dumps(stops, indent=2) + "\n")
        except Exception:
            pass

    return {
        "legal": legal,
        "blocked": blocked,
        "door": cam_door,
        "heading_door": heading_door,
        "user": user,
        "heading": heading,
        "geometry": str(gp),
        "site": str(path),
    }
