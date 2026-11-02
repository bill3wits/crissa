"""Shared fakes for the Imagine package tests: no network, no ffmpeg."""
from __future__ import annotations

import io
import json
import struct
import urllib.error
import zlib
from pathlib import Path


def png_bytes(w: int = 320, h: int = 400) -> bytes:
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg_bytes(w: int = 640, h: int = 480) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, h, w, 3) + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def webp_vp8x(w: int = 512, h: int = 512) -> bytes:
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00" + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHTTP:
    """opener(req, timeout) stand-in. routes: list of (method, url_prefix, handler)
    where handler(req) -> (status, body_bytes_or_obj)."""

    def __init__(self):
        self.calls = []
        self.routes = []

    def on(self, method, prefix, handler):
        self.routes.append((method, prefix, handler))
        return self

    def __call__(self, req, timeout=None):
        method = req.get_method()
        url = req.full_url
        self.calls.append({"method": method, "url": url, "headers": dict(req.header_items()),
                           "data": req.data})
        for m, prefix, h in reversed(self.routes):
            if m == method and url.startswith(prefix):
                status, body = h(req)
                if not isinstance(body, (bytes, bytearray)):
                    body = json.dumps(body).encode()
                if status >= 400:
                    raise urllib.error.HTTPError(url, status, "err", {}, io.BytesIO(body))
                return _Resp(bytes(body), status)
        raise AssertionError("unexpected HTTP call: %s %s" % (method, url))


class FakeRunner:
    """subprocess.run stand-in for ffmpeg: writes the output file."""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls = []

    def __call__(self, cmd, capture_output=True, text=True, **kw):
        self.calls.append(list(cmd))
        out = Path(cmd[-1])
        if self.ok:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-video")

        class R:
            returncode = 0 if self.ok else 1
            stderr = "" if self.ok else "boom"
        return R()


def scratch_rule(tmp_path, **over):
    """A scratch pack rule for a test of the OLD machinery (the legacy three-clip package, the house's own
    transition films): the real rule with `legacy_package: allow` and `tiers.enforce: false` unless overridden.
    Returns the env entry to give the service. The real rule stays what config/imagine-pack-rule.json says."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    d = json.loads((root / "config" / "imagine-pack-rule.json").read_text())
    d["legacy_package"] = "allow"
    d["tiers"]["enforce"] = False
    d["addon_roles"] = ["flourish", "slide"]
    for k, v in over.items():
        a, _, b = k.partition(".")
        if b:
            d[a][b] = v
        else:
            d[a] = v
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "pack-rule.json"
    p.write_text(json.dumps(d))
    return {"IMAGINE_PACK_RULE_FILE": str(p)}
