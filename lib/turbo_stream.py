"""Turbo's line, sentence by sentence, on the speaker's side (the User 2026-10-10: "Can it stream so no lag?").

house-turbo-tts cuts a line into pieces (sentences, a very short one joined to the next) and writes each piece as
ONE HTTP chunk the moment it is made. `pieces` is that cut, one copy for the server and house-speak.

`TurboReader` pulls the chunks off the socket on its own thread, as fast as Turbo makes them. house-speak used to
pull them only as fast as the player drained its pipe: at tempo 1.0 there is no ffmpeg stage, so the read loop
ran at playback pace and the whole-line alignment started about two seconds before the line ENDED - the mouth
spoke on the letter-count guess for nearly every live line. Each finished piece is handed to `on_piece` with its
byte range, so the mouth can stamp that sentence while the one before it is still playing.

Cosy streamed audio from inside a sentence, which is why the old rule aligned the whole utterance or nothing; a
Turbo piece is a whole sentence, so aligning it against its own text is the same job on less audio.
"""
from __future__ import annotations

import json
import os
import queue
import re
import socket
import threading
import time
from pathlib import Path

_SPLIT = re.compile(r"(?<=[.!?])\s+")
CONFIG = Path(__file__).resolve().parent.parent / "config" / "house-turbo-tts.json"
# A SENTENCE BEING MADE IS NOT A DEAD VOICE (the User 2026-10-11: "she got to 'ishmael'"). house-speak ends a line when
# no audio comes for HOUSE_SPEAK_TAIL_STARVE_MS (800 on the ear), a rule written for Cosy's continuous stream. Turbo
# makes each sentence whole, so "Call me Ishmael." (1.3 s) followed by a 208-character sentence (~1.4 s to make) read
# as a starve and the reply ended after three words. While the stream is still open, the reader keeps waiting up to
# TURBO_GAP_S since the last sentence arrived; a closed or broken stream still ends at once. 0 = the old rule.
TURBO_GAP_S = float(os.environ.get("HOUSE_SPEAK_TURBO_GAP_S", "20"))


def pieces(text: str, min_chars: int = 24) -> list[str]:
    """Sentences, with a very short one joined to the next (a lone 'Okay.' clips its own prosody)."""
    out: list[str] = []
    for s in (x.strip() for x in _SPLIT.split(text.strip())):
        if not s:
            continue
        if out and len(out[-1]) < min_chars:
            out[-1] = out[-1] + " " + s
        else:
            out.append(s)
    return out


def min_piece_chars(path: Path | None = None) -> int:
    """The server's join length, from its own config (24 when unreadable, the server's default)."""
    try:
        return int(json.loads((path or CONFIG).read_text(encoding="utf-8")).get("min_piece_chars", 24))
    except (OSError, ValueError, TypeError):
        return 24


class TurboReader:
    """A chunked /stream body read on its own thread. `read(n, timeout_s)` serves the bytes in order.

    One HTTP chunk is one piece. `on_piece(index, start, end, pcm)` runs on the reader thread when a piece has
    fully arrived; `start`/`end` are byte offsets into the line. A read that waits past `timeout_s` raises
    TimeoutError, as house-speak's starve rule expects. A broken body ends the line like an early EOF.
    """

    is_turbo_reader = True                           # house-speak's _http_read hands it the starve timeout

    def __init__(self, resp, on_piece=None, on_mark=None):
        self.resp = resp
        self.fp = resp.fp
        self.on_piece = on_piece
        # A PIECE MARK (round 11, 2026-10-12): a voice that cuts the line itself (Pocket) names where each of its pieces
        # ended as a chunk extension, `;piece-end=<i>;text=<percent-encoded>`, on the next chunk-size line (or the last).
        # `on_mark(i, end, text)` runs on this thread with `end` the byte offset where that piece ended. None: ignored.
        self.on_mark = on_mark
        self.marks = 0
        self.cut = 0                                 # round 19: the quiet Pocket's seams dropped so far (bytes)
        self.ends: list[int] = []
        self.total = 0
        self.error: str | None = None
        self.done = threading.Event()
        self._q: queue.Queue = queue.Queue()
        self._pend = bytearray()
        self._eof = False
        self.last_at = time.monotonic()           # when the last sentence (or the stream) arrived
        self.gap_waits = 0                        # reads that outlasted the starve limit while Turbo was still making
        self._t = threading.Thread(target=self._run, daemon=True, name="turbo-reader")
        self._t.start()

    def _marks(self, line: bytes) -> None:
        """Hand each `piece-end` chunk extension on a chunk-size line to on_mark (end = the bytes before this chunk)."""
        if self.on_mark is None or b";" not in line:
            return
        import urllib.parse
        cur = None
        for part in line.split(b";")[1:]:
            k, _, v = part.strip().partition(b"=")
            if k == b"piece-end":
                cur = int(v)
            elif k == b"cut" and v.strip().isdigit():
                self.cut = max(self.cut, int(v))
            elif k == b"text" and cur is not None:
                self.marks += 1
                try:
                    self.on_mark(cur, self.total, urllib.parse.unquote(v.decode("ascii")))
                except Exception:  # noqa: BLE001  the mouth's trouble never stops her voice
                    pass
                cur = None

    def _chunk(self) -> bytes | None:
        line = self.fp.readline(65537)
        if not line:
            return None
        size = int(line.split(b";", 1)[0].strip() or b"0", 16)
        self._marks(line)
        if size == 0:
            while True:                              # trailers, then the blank line
                rest = self.fp.readline(65537)
                if not rest or rest in (b"\r\n", b"\n"):
                    break
            return None
        data = bytearray()
        while len(data) < size:
            got = self.fp.read(size - len(data))
            if not got:
                raise EOFError("body ended inside a chunk")
            data.extend(got)
        self.fp.read(2)
        return bytes(data)

    def _run(self) -> None:
        try:
            # the socket keeps the timeout house-speak opened it with (HOUSE_SPEAK_COSY_TIMEOUT); a timeout ends the line
            while True:
                data = self._chunk()
                if data is None:
                    break
                start = self.total
                self.last_at = time.monotonic()
                self.total += len(data)
                self.ends.append(self.total)
                self._q.put(data)
                if self.on_piece is not None:
                    try:
                        self.on_piece(len(self.ends) - 1, start, self.total, data)
                    except Exception:  # noqa: BLE001  the mouth's trouble never stops her voice
                        pass
        except (OSError, ValueError, EOFError, socket.timeout) as e:
            self.error = "%s: %s" % (type(e).__name__, e)
        finally:
            self.done.set()
            self._q.put(None)

    def read(self, n: int, timeout_s: float | None = None) -> bytes:
        while not self._pend and not self._eof:
            try:
                item = self._q.get(timeout=timeout_s)
            except queue.Empty:
                if (TURBO_GAP_S > 0 and not self.done.is_set()
                        and time.monotonic() - self.last_at < TURBO_GAP_S):
                    self.gap_waits += 1
                    continue                      # the next sentence is still being made
                raise TimeoutError("turbo stream starve") from None
            if item is None:
                self._eof = True
                break
            self._pend.extend(item)
        out = bytes(self._pend[:n])
        del self._pend[:n]
        return out

    def close(self) -> None:
        try:
            self.resp.close()
        except Exception:  # noqa: BLE001
            pass
