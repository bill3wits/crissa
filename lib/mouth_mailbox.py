"""The mouth mailbox: one store for the viseme sheet, shared by the wall and
the benches (2026-09-15).

house-speak posts the envelope of what paplay is ABOUT to play. Each frame's
pts is a future instant, so this is a short score, not a report: whoever
paints reads it ahead of the room hearing it and can ease into a syllable.

This class WAS module-level state in scripts/voiceonly-home-server.py. It
moved here when the Composer got a bench conductor of its own: the bench
serves its own /v1/mouth so a Say on the Composer never reaches the glass,
and two copies of a contract with three dated bug fixes in it would drift.
The server keeps module-level `mouth_ingest` / `mouth_snapshot` shims bound
to its instance, so its routes and tests/test_w2l_pcm.py are unchanged.

The three fixes that live in here, so nobody re-opens them:
  * 2026-08-27  retire frames on READ as well as on ingest. Retiring only on
    a POST left the last turn's frames in the ring forever, and every client
    that connected afterwards got a score that ended minutes ago (a freshly
    reloaded wall came up holding 23 frames whose newest stamp was 69 s old).
  * 2026-08-30  a new air_at drops the PCM as well as the envelope. Leaving
    the samples meant the next line's mel window still held the last line's
    audio - her mouth saying the previous sentence over the new one. Two rows
    survived a new air_at before this.
  * the ring is bounded on purpose. This is LOOKAHEAD, not the turn: frames
    retire as they are consumed, so HOLD_S covers a 20 s reply without
    growing, and PCM is a live window, never a recording of the house.
  * 2026-10-14  a CUT ends the line's sheet (round 20, .101: "her lips moved on
    after the cut"). A post carrying "cut": true with air_until drops every row
    (and sample) after air_until for that air_at, and any row posted later for
    the same air_at past it. Only a pack's house-speak sends it on a signal
    (HOUSE_SPEAK_CUT_SHEET); a post without it changes nothing here.
"""
from __future__ import annotations

import threading
import time

PCM_ROWS = 80
HOLD_S = 4.0
ROW_CAP = 600        # envelope rows kept (the wall's own cap is per-POST)
POST_CAP = 512       # rms values accepted from one POST


class MouthMailbox:
    """One turn's score, in flight. ingest() from house-speak, snapshot() to
    whoever paints. Thread-safe; `wake` notifies long-pollers."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.wake = threading.Condition(self.lock)
        self._d: dict = {"air_at": None, "air_until": None, "sr": 24000,
                         "hop_ms": 20.0, "frames": [], "seq": 0, "pcm": [],
                         "speaker": ""}     # who is on the air (2026-09-18)

    # the store itself, for a caller that wants to read a field directly
    @property
    def d(self) -> dict:
        return self._d

    def ingest(self, d: dict) -> dict:
        """One POST from house-speak. Newer air_at starts a new turn."""
        now = time.time()
        m = self._d
        with self.wake:
            air_at = d.get("air_at")
            if air_at is not None and air_at != m["air_at"]:
                m["frames"] = []
                # the samples belong to the turn as much as the envelope does
                m["pcm"] = []
                m["air_at"] = float(air_at)
                m["speaker"] = ""
                m.pop("clock", None)
                m.pop("cut_at", None)
            if d.get("cut") and d.get("air_until") is not None:
                try:
                    m["cut_at"] = float(d["air_until"])
                except (TypeError, ValueError):
                    pass
            if d.get("speaker") is not None:
                m["speaker"] = str(d.get("speaker") or "").strip().lower()
            if isinstance(d.get("clock"), dict):
                # the line's lip clock, as house-speak saw it (round 11, 2026-10-12): numbers and short words only
                m["clock"] = {str(k)[:24]: (v if isinstance(v, (int, float, bool)) or v is None else str(v)[:120])
                              for k, v in list(d["clock"].items())[:16]}
            if d.get("air_until") is not None:
                m["air_until"] = float(d["air_until"])
            elif air_at is not None and air_at != m.get("_seen_at"):
                m["air_until"] = None
            m["_seen_at"] = air_at
            try:
                m["sr"] = int(d.get("sr") or m["sr"])
                m["hop_ms"] = float(d.get("hop_ms") or m["hop_ms"])
            except (TypeError, ValueError):
                pass
            rms = d.get("rms")
            pts0 = d.get("pts0")
            if isinstance(rms, list) and pts0 is not None:
                hop = m["hop_ms"] / 1000.0
                fr = m["frames"]
                vis = d.get("visemes")
                vis = vis if isinstance(vis, list) else []
                for i, v in enumerate(rms[:POST_CAP]):
                    try:
                        row = [round(float(pts0) + i * hop, 4), float(v)]
                        if i < len(vis):
                            try:
                                row.append(int(vis[i]))
                            except (TypeError, ValueError):
                                pass
                        fr.append(row)
                    except (TypeError, ValueError):
                        continue
                # retire what the room has already heard
                cut = now - 0.5
                m["frames"] = [f for f in fr if f[0] >= cut][-ROW_CAP:]
            blob = d.get("pcm16")
            if isinstance(blob, str) and blob and pts0 is not None:
                try:
                    rows = m["pcm"]
                    rows.append([round(float(pts0), 4), blob])
                    keep = now - HOLD_S
                    m["pcm"] = [r for r in rows if r[0] >= keep][-PCM_ROWS:]
                except (TypeError, ValueError):
                    pass
            if m.get("cut_at") is not None:              # a cut line: nothing after the cut (2026-10-14)
                end = m["cut_at"]
                m["frames"] = [f for f in m["frames"] if f[0] < end]
                m["pcm"] = [r for r in m["pcm"] if r[0] < end]
            m["seq"] += 1
            self.wake.notify_all()
            return {"ok": True, "seq": m["seq"], "frames": len(m["frames"])}

    def snapshot(self) -> dict:
        """Also retire on READ, not just on ingest (2026-08-27)."""
        now = time.time()
        m = self._d
        with self.lock:
            fresh = [f for f in m["frames"] if f[0] >= now - 0.5]
            if len(fresh) != len(m["frames"]):
                m["frames"] = fresh
            return {
                "air_at": m["air_at"], "air_until": m["air_until"],
                "sr": m["sr"], "hop_ms": m["hop_ms"],
                "seq": m["seq"], "frames": list(fresh),
                "pcm": [r for r in m["pcm"] if r[0] >= now - HOLD_S],
                "speaker": m.get("speaker") or "",
                **({"clock": dict(m["clock"])} if m.get("clock") else {}),
            }
