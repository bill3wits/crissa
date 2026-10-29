"""Low-res motion vision for analysis — ghosts + heading, not face ID.

the User (2026-07-24): smooth video scan at ~30 fps low-res for analysis;
snaps/facefind only on unknown bodies. Motion ghosts show heading
(enter vs leave / room transit direction).

Parse law: several frames during a pan = **camera movement** (ego-motion),
NOT multiple bodies. Trails must not invent headcount from smear.

Uses the sole-owner ring buffer frames (already JPEG). Does not open V4L.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def decode_lowres(
    jpegs: list[bytes],
    *,
    scale: float = 0.35,
    max_side: int = 480,
) -> list[Any]:
    """Decode JPEGs to BGR and downscale for analysis cost."""
    import cv2
    import numpy as np

    out: list[Any] = []
    for raw in jpegs:
        arr = np.frombuffer(raw, dtype=np.uint8)
        im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if im is None:
            continue
        h, w = im.shape[:2]
        s = float(scale)
        if max(h, w) * s > max_side:
            s = max_side / float(max(h, w))
        if s < 0.99:
            im = cv2.resize(
                im,
                (max(1, int(w * s)), max(1, int(h * s))),
                interpolation=cv2.INTER_AREA,
            )
        out.append(im)
    return out


def temporal_trail(frames: list[Any]) -> Any | None:
    """Alpha-composite older→newer so movers leave translucent ghosts.

    Newer frames are more opaque — trail points toward current position
    (heading cue for VLMs / CODE without optical-flow jargon).
    """
    import numpy as np

    if not frames:
        return None
    if len(frames) == 1:
        return frames[0].copy()
    n = len(frames)
    acc = frames[0].astype(np.float32)
    for i, im in enumerate(frames[1:], 1):
        # linear ramp: last frame fully present, early frames ghosted
        a = 0.12 + 0.88 * (i / (n - 1))
        acc = acc * (1.0 - a) + im.astype(np.float32) * a
    return acc.astype(np.uint8)


def motion_heat(frames: list[Any], base: Any | None = None) -> Any | None:
    """Max frame-to-frame absdiff as heat overlay on last (or base) frame."""
    import cv2
    import numpy as np

    if len(frames) < 2:
        return (base if base is not None else frames[0]).copy() if frames else None
    diffs = [cv2.absdiff(a, b) for a, b in zip(frames[:-1], frames[1:])]
    diffmax = np.max(np.stack(diffs, axis=0), axis=0)
    gray = cv2.cvtColor(diffmax, cv2.COLOR_BGR2GRAY)
    norm = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    heat = cv2.applyColorMap(norm, cv2.COLORMAP_HOT)
    bg = base if base is not None else frames[-1]
    return cv2.addWeighted(bg, 0.55, heat, 0.45, 0)


def heading_from_motion(frames: list[Any]) -> dict[str, Any]:
    """Coarse heading from motion-mass centroid path (screen coords).

    Returns unit-ish dx/dy in image space (x right, y down) plus a
    label: left|right|up|down|still|unknown. Door FOV: right↔left often
    maps to room-side vs hall-side depending on mount — report vector
    honestly; geometry mapping is caller's job.
    """
    import cv2
    import numpy as np

    empty: dict[str, Any] = {
        "ok": False,
        "dx": 0.0,
        "dy": 0.0,
        "label": "unknown",
        "energy": 0.0,
        "n_centroids": 0,
    }
    if len(frames) < 3:
        return empty
    cents: list[tuple[float, float, float]] = []
    thr = 18
    for a, b in zip(frames[:-1], frames[1:]):
        d = cv2.absdiff(a, b)
        g = cv2.cvtColor(d, cv2.COLOR_BGR2GRAY)
        g = cv2.GaussianBlur(g, (5, 5), 0)
        _, m = cv2.threshold(g, thr, 255, cv2.THRESH_BINARY)
        ys, xs = np.where(m > 0)
        if len(xs) < 40:
            continue
        energy = float(g[m > 0].mean()) if ys.size else 0.0
        cents.append((float(xs.mean()), float(ys.mean()), energy))
    if len(cents) < 2:
        empty["label"] = "still"
        empty["ok"] = True
        return empty
    # displacement of motion centroid over the window
    x0, y0, _ = cents[0]
    x1, y1, _ = cents[-1]
    dx = x1 - x0
    dy = y1 - y0
    energy = float(np.mean([c[2] for c in cents]))
    # normalize by frame diagonal
    h, w = frames[0].shape[:2]
    diag = float((w * w + h * h) ** 0.5) or 1.0
    ndx, ndy = dx / diag, dy / diag
    mag = (ndx * ndx + ndy * ndy) ** 0.5
    if mag < 0.02 or energy < 4.0:
        label = "still"
    elif abs(ndx) >= abs(ndy):
        label = "right" if ndx > 0 else "left"
    else:
        label = "down" if ndy > 0 else "up"
    return {
        "ok": True,
        "dx": round(float(ndx), 4),
        "dy": round(float(ndy), 4),
        "mag": round(float(mag), 4),
        "label": label,
        "energy": round(energy, 2),
        "n_centroids": len(cents),
        "screen": "x_right_y_down",
    }


def annotate_heading(img: Any, heading: dict[str, Any]) -> Any:
    """Draw heading arrow + label on a BGR image (copy)."""
    import cv2
    import numpy as np

    out = img.copy()
    h, w = out.shape[:2]
    cx, cy = w // 2, h // 2
    label = str(heading.get("label") or "?")
    dx = float(heading.get("dx") or 0.0)
    dy = float(heading.get("dy") or 0.0)
    # arrow length ~ 1/4 of min side, scaled by mag (cap)
    mag = float(heading.get("mag") or 0.0)
    scale = min(h, w) * 0.28 * min(1.0, mag / 0.08 if mag > 1e-6 else 0.0)
    if label == "still" or scale < 4:
        cv2.putText(
            out,
            f"heading={label} e={heading.get('energy', 0)}",
            (8, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (180, 220, 180),
            1,
            cv2.LINE_AA,
        )
        return out
    # direction from normalized dx,dy
    L = (dx * dx + dy * dy) ** 0.5 or 1.0
    ex = int(cx + scale * dx / L)
    ey = int(cy + scale * dy / L)
    cv2.arrowedLine(out, (cx, cy), (ex, ey), (0, 220, 255), 2, tipLength=0.25)
    cv2.putText(
        out,
        f"heading={label} mag={mag:.3f}",
        (8, 22),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 220, 255),
        1,
        cv2.LINE_AA,
    )
    return out


def write_lowres_mp4(
    frames: list[Any],
    path: Path | str,
    *,
    fps: float = 15.0,
) -> str | None:
    """Write a short low-res MP4 for models that accept video paths."""
    import cv2

    if not frames:
        return None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = frames[0].shape[:2]
    # try mp4v then avc1
    for fourcc_name in ("mp4v", "avc1", "XVID"):
        fourcc = cv2.VideoWriter_fourcc(*fourcc_name)
        vw = cv2.VideoWriter(str(path), fourcc, float(fps), (w, h))
        if not vw.isOpened():
            vw.release()
            continue
        for im in frames:
            if im.shape[0] != h or im.shape[1] != w:
                im = cv2.resize(im, (w, h))
            vw.write(im)
        vw.release()
        if path.is_file() and path.stat().st_size > 64:
            return str(path)
    return None


def build_motion_pack(
    jpegs: list[bytes],
    *,
    out_dir: Path | str,
    scale: float = 0.35,
    write_video: bool = True,
    fps: float = 15.0,
) -> dict[str, Any]:
    """From ring JPEGs → trail ghost, heat, heading, optional lowres mp4.

    Snapshots/facefind stay separate — this is analysis product only.
    """
    import cv2

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {
        "ok": False,
        "n_in": len(jpegs),
        "n_frames": 0,
        "trail": None,
        "heat": None,
        "heading_img": None,
        "video": None,
        "heading": {},
    }
    frames = decode_lowres(jpegs, scale=scale)
    meta["n_frames"] = len(frames)
    if not frames:
        meta["error"] = "no decodable frames"
        return meta
    trail = temporal_trail(frames)
    heat = motion_heat(frames, base=trail if trail is not None else frames[-1])
    heading = heading_from_motion(frames)
    meta["heading"] = heading
    headed = annotate_heading(trail if trail is not None else frames[-1], heading)

    trail_p = out_dir / "ghost_trail.jpg"
    heat_p = out_dir / "ghost_heat.jpg"
    head_p = out_dir / "ghost_heading.jpg"
    if trail is not None:
        cv2.imwrite(str(trail_p), trail, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
        meta["trail"] = str(trail_p)
    if heat is not None:
        cv2.imwrite(str(heat_p), heat, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
        meta["heat"] = str(heat_p)
    cv2.imwrite(str(head_p), headed, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
    meta["heading_img"] = str(head_p)

    if write_video:
        vid = write_lowres_mp4(frames, out_dir / "scan_lowres.mp4", fps=fps)
        meta["video"] = vid

    meta["ok"] = True
    meta["scale"] = scale
    meta["shape"] = list(frames[0].shape)
    meta["parse_rule"] = "frames_are_camera_motion_not_multiple_bodies"
    return meta
