"""Round 46 (the local review): a store sale's download was held in memory whole before the first byte was written.
It now streams to the part file, and the response closes after the last chunk. Kill CRISSA_IMPORT_STREAM=0."""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_import as I  # noqa: E402


class _Resp(io.BytesIO):
    status = 200
    headers = {"Content-Disposition": 'attachment; filename="x.zip"'}

    def close(self):
        self.closed_by = True
        super().close()


def _fake_urlopen(monkeypatch, resp):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: resp)


def test_the_sale_streams_and_closes(monkeypatch):
    resp = _Resp(b"a" * ((1 << 20) + 5))
    _fake_urlopen(monkeypatch, resp)
    status, name, chunks = I._open_sale("https://store.zoeyos.dev/x")
    assert status == 200 and name == "x.zip" and not isinstance(chunks, list)
    assert not resp.closed, "nothing read yet"
    assert sum(len(c) for c in chunks) == (1 << 20) + 5 and resp.closed


def test_the_kill_reads_it_all(monkeypatch):
    monkeypatch.setenv("CRISSA_IMPORT_STREAM", "0")
    resp = _Resp(b"abc")
    _fake_urlopen(monkeypatch, resp)
    _s, _n, chunks = I._open_sale("https://store.zoeyos.dev/x")
    assert chunks == [b"abc"] and resp.closed
