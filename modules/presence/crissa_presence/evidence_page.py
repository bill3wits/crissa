"""What Argus and FaceID were actually handed, per door event, live.

the User 2026-08-16:

    "i need to see a page with all video presented as evidence. I need to be
    sure the fix is not a simple editing error on the frames. Please let me see
    live time what argus and FaceID are presented."

and, on why a replay will not do:

    "your backtests do not match with live because that is a perfect subset of
    clips recorded properly"

That is the whole design constraint. Every measurement offered all day —
"Argus said enter", "FaceID scored 0.5589" — was a number about frames nobody
looked at, and replaying the event afterwards reads only the raw that survived
pruning, which is a survivor-biased subset rather than what the model saw. So
this page renders bytes captured at fire time by door_fanout and nothing else:
no re-derivation, no reconstruction, no second opinion.

It answers one question: was the verdict wrong, or were the frames wrong?
"""

from __future__ import annotations

import html
import json
from typing import Any

from .door_fanout import EVIDENCE_DIR, EVIDENCE_KEEP


def list_events(limit: int = 12) -> list[dict[str, Any]]:
    """Newest first. Each entry is one door event's captured evidence."""
    out: list[dict[str, Any]] = []
    if not EVIDENCE_DIR.is_dir():
        return out
    try:
        dirs = sorted(
            (d for d in EVIDENCE_DIR.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )[:limit]
    except OSError:
        return out
    for d in dirs:
        meta: dict[str, Any] = {}
        mp = d / "meta.json"
        if mp.is_file():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        try:
            names = sorted(p.name for p in d.iterdir() if p.suffix in (".jpg", ".mp4"))
        except OSError:
            names = []
        out.append({
            "event_id": d.name,
            "mtime": d.stat().st_mtime,
            "meta": meta,
            "argus_frames": [n for n in names if n.startswith("argus_") and n.endswith(".jpg")],
            "faceid_frames": [n for n in names if n.startswith("faceid_")
                               and n.endswith(".jpg")
                               and not n.startswith(("faceid_chosen", "faceid_crop"))],
            "mp4": "argus.mp4" if (d / "argus.mp4").is_file() else None,
            "chosen": "faceid_chosen.jpg" if (d / "faceid_chosen.jpg").is_file() else None,
            "crop": "faceid_crop.jpg" if (d / "faceid_crop.jpg").is_file() else None,
        })
    return out


def asset(event_id: str, name: str) -> tuple[bytes, str] | None:
    """One stored file. Path-confined to the evidence dir on purpose."""
    if not event_id or not name or "/" in name or ".." in name or ".." in event_id:
        return None
    p = EVIDENCE_DIR / event_id / name
    try:
        p = p.resolve()
        if EVIDENCE_DIR.resolve() not in p.parents:
            return None
        if not p.is_file():
            return None
        data = p.read_bytes()
    except OSError:
        return None
    ct = "video/mp4" if name.endswith(".mp4") else "image/jpeg"
    return data, ct


def render() -> str:
    evs = list_events()
    rows = []
    for e in evs:
        m = e["meta"] or {}
        a = m.get("argus") or {}
        b = m.get("faceid") or {}
        ap = m.get("applied") or {}
        eid = e["event_id"]
        import time as _t

        when = _t.strftime("%H:%M:%S", _t.localtime(e["mtime"]))

        def _im(n: str, cls: str = "") -> str:
            return (f'<img class="fr {cls}" loading="lazy" '
                    f'src="/v1/evidence/asset?e={eid}&f={n}" title="{n}">')

        strip = "".join(_im(n) for n in e["argus_frames"])
        bstrip = "".join(_im(n) for n in e["faceid_frames"])
        vid = (f'<video class="vid" controls preload="none" '
               f'src="/v1/evidence/asset?e={eid}&f=argus.mp4"></video>'
               if e["mp4"] else '<span class="none">no mp4 captured</span>')
        chosen = _im(e["chosen"], "big") if e["chosen"] else ""
        crop = _im(e["crop"], "crop") if e["crop"] else '<span class="none">no crop</span>'

        av = a.get("event") or a.get("reason") or "—"
        acls = "ok" if a.get("event") in ("enter", "exit") else "warn"
        bv = b.get("name") or b.get("reason") or "—"
        bcls = "ok" if b.get("name") else "warn"
        applied = ap.get("applied")
        apcls = "ok" if applied else ("warn" if applied is False else "none")

        sys_txt = ""
        user_txt = ""
        try:
            sp = EVIDENCE_DIR / eid / "system.txt"
            if sp.is_file():
                sys_txt = sp.read_text(encoding="utf-8")[:4000]
            pp = EVIDENCE_DIR / eid / "prompt.txt"
            if pp.is_file():
                user_txt = pp.read_text(encoding="utf-8")[:2000]
            elif not sys_txt:
                old = EVIDENCE_DIR / eid / "soul.txt"
                if old.is_file():
                    sys_txt = old.read_text(encoding="utf-8")[:4000]
        except OSError:
            pass
        prompt_html = ""
        if sys_txt or user_txt:
            bits = []
            if sys_txt:
                bits.append("<b>system</b>\n" + html.escape(sys_txt))
            if user_txt:
                bits.append("<b>user</b>\n" + html.escape(user_txt))
            prompt_html = (
                '<div class="lab">→ PROMPT &nbsp;'
                '<span class="sub">what Argus was asked</span></div>'
                f'<div class="say">{chr(10).join(bits)}</div>'
            )

        rows.append(f"""
<article class="ev">
  <header class="evh">
    <span class="t">{when}</span>
    <span class="eid">{eid}</span>
    <span class="seq">trigger seq {m.get('trigger_seq')}</span>
    <span class="pill {acls}">argus {av}</span>
    <span class="pill {bcls}">faceid {bv}{(' ' + str(b.get('sim'))) if b.get('sim') else ''}</span>
    <span class="pill {apcls}">applied {applied} {ap.get('reason') or ''}</span>
  </header>

  <div class="panel">
    <div class="lab">→ ARGUS &nbsp;<span class="sub">{len(e['argus_frames'])} frames encoded, in order</span></div>
    <div class="strip">{strip or '<span class="none">no frames captured</span>'}</div>
    <div class="vidrow">{vid}</div>
    <div class="say">{html.escape((a.get('reply') or a.get('clothes') or '')[:400])}</div>
    {prompt_html}
  </div>

  <div class="panel">
    <div class="lab">→ FACEID &nbsp;<span class="sub">{len(e['faceid_frames'])} frames scanned · face_px {b.get('face_px')} · det {b.get('det_score')} · door_view {b.get('door_view')}</span></div>
    <div class="strip">{bstrip or '<span class="none">no frames captured</span>'}</div>
    <div class="chosenrow">
      <div><div class="mini">winning frame</div>{chosen or '<span class="none">none</span>'}</div>
      <div><div class="mini">crop matched against the bank</div>{crop}</div>
    </div>
  </div>
</article>""")

    body = "".join(rows) or '<p class="none big">No events captured yet. Walk through the door.</p>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>House · Door evidence</title>
<meta http-equiv="refresh" content="15">
<style>
:root{{--bg:#0c0d10;--card:#14161c;--bd:#252833;--tx:#e8eaed;--mut:#8b93a7;
      --ok:#3dd68c;--warn:#e6b84d;--bad:#f07178;--acc:#7eb8ff}}
*{{box-sizing:border-box}}
body{{background:var(--bg);color:var(--tx);margin:0;padding:18px 16px 60px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}}
h1{{font-size:16px;margin:0 0 4px;letter-spacing:.02em}}
.hint{{color:var(--mut);margin:0 0 18px;font-size:12px;line-height:1.5;max-width:80ch}}
.ev{{background:var(--card);border:1px solid var(--bd);border-radius:4px;
  margin:0 0 16px;padding:12px 14px}}
.evh{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 10px;
  padding-bottom:8px;border-bottom:1px solid var(--bd)}}
.t{{font-weight:600}} .eid{{color:var(--mut)}} .seq{{color:var(--mut)}}
.pill{{padding:2px 8px;border-radius:3px;border:1px solid var(--bd);font-size:11px;
  white-space:nowrap}}
.pill.ok{{color:var(--ok);border-color:var(--ok)}}
.pill.warn{{color:var(--warn);border-color:var(--warn)}}
.pill.none{{color:var(--mut)}}
.panel{{margin:10px 0 0}}
.lab{{color:var(--acc);font-size:11px;letter-spacing:.1em;margin:0 0 6px}}
.sub{{color:var(--mut);letter-spacing:0;text-transform:none}}
.strip{{display:flex;gap:3px;overflow-x:auto;padding:2px 0 6px}}
.fr{{height:82px;border:1px solid var(--bd);border-radius:2px;flex:0 0 auto;background:#000}}
.fr.big{{height:180px}} .fr.crop{{height:180px;border-color:var(--ok)}}
.vidrow{{margin:4px 0 6px}}
.vid{{max-width:520px;width:100%;border:1px solid var(--bd);border-radius:3px;background:#000}}
.say{{color:var(--mut);line-height:1.5;margin:4px 0 0;white-space:pre-wrap;
  font-family:ui-sans-serif,system-ui,sans-serif;font-size:12.5px}}
.chosenrow{{display:flex;gap:18px;flex-wrap:wrap;margin:6px 0 0}}
.mini{{color:var(--mut);font-size:11px;margin:0 0 4px}}
.none{{color:var(--mut);font-style:italic}}
.none.big{{font-size:14px;display:block;padding:40px 0}}
</style></head><body>
<h1>Door evidence — what each witness was handed</h1>
<p class="hint">Captured at fire time by door_fanout, newest first, refreshing every 15s.
Nothing here is reconstructed: raw is pruned within the hour, so a replay would show only
the frames that survived rather than the ones the model saw.
Keeping the last {EVIDENCE_KEEP} events.</p>
{body}
</body></html>"""
