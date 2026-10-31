"""STT → LLM → TTS. HTTP only to rails/services."""
from __future__ import annotations
import json, struct, urllib.request
import numpy as np
from . import config as C

# Fast-path sources for the User-at-chair
_USER_CHAIR_SOURCES = frozenset({"voice_emb", "sole_present"})


def user_at_chair_ok(stamp: dict | None, presence: dict | None = None) -> bool:
    """True when stamp identifies the user with a strong path and the user is on roster.

    Used only when RAIL_VOICE_AUTH_GATE is on. No face hunt, no oracle.
    """
    stamp = stamp if isinstance(stamp, dict) else {}
    presence = presence if isinstance(presence, dict) else (stamp.get("presence") or {})
    pid = (stamp.get("person_id") or "").strip().lower()
    if pid != "user":
        return False
    src = (stamp.get("source") or "").strip().lower()
    if src not in _USER_CHAIR_SOURCES:
        return False
    roster = stamp.get("roster")
    if not isinstance(roster, list):
        roster = [
            p.get("name")
            for p in (presence.get("people") or [])
            if isinstance(p, dict) and p.get("name")
        ]
    names = {str(n).strip().lower() for n in roster if n}
    return "user" in names


def stt_pcm16(pcm: bytes, sr: int = 16000) -> dict:
    """Full STT JSON including ear-join stamp/person_id when rail-stt provides it."""
    req = urllib.request.Request(
        C.STT_URL, data=pcm, method="POST",
        headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": str(sr)},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())

def _strip_think_tags(text: str) -> str:
    """Never speak thinking — strip <think>…</think> and loose tags (the User)."""
    if not text:
        return ""
    import re

    text = re.sub(r"<think>[\s\S]*?</think>", " ", text, flags=re.I)
    text = re.sub(r"<thinking>[\s\S]*?</thinking>", " ", text, flags=re.I)
    text = re.sub(r"<reasoning>[\s\S]*?</reasoning>", " ", text, flags=re.I)
    text = re.sub(r"</?(?:think|thinking|reasoning)>", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def extract_spoken_content(message: dict | None) -> str:
    """Pull speakable text from vLLM/ops message (thinking models may fill reasoning only)."""
    if not isinstance(message, dict):
        return ""
    content = _strip_think_tags(message.get("content") or "")
    if content:
        return content
    # Never use reasoning fields as speech when content empty — thinking is
    # internal only. (Older fallbacks that mined reasoning are removed.)
    return ""


def llm_reply(user_text: str, *, person_id: str | None = None, presence: dict | None = None) -> str:
    # Facts in context; model discretion (second law) — no Do-Not list
    sys = C.SYSTEM
    if person_id or presence:
        bits = []
        if person_id:
            bits.append(f"Likely speaker person_id={person_id}.")
        if presence:
            names = [
                p.get("name")
                for p in (presence.get("people") or [])
                if isinstance(p, dict) and p.get("name")
            ]
            bits.append(
                f"Room roster: headcount={presence.get('headcount')} "
                f"names={names} trust={presence.get('count_trust')}."
            )
        bits.append("If uncertain who spoke, ask briefly rather than invent.")
        sys = sys + "\n" + " ".join(bits)
    # enable_thinking=False is required for ops/ThinkingCap short spoken answers
    payload = {
        "model": C.LLM_MODEL,
        "max_tokens": int(getattr(C, "LLM_MAX_TOKENS", 128) or 128),
        "temperature": 0.3,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": user_text},
        ],
    }
    headers = {"Content-Type": "application/json"}
    if getattr(C, "LLM_KEY", ""):
        headers["Authorization"] = f"Bearer {C.LLM_KEY}"
    req = urllib.request.Request(
        C.LLM_URL, data=json.dumps(payload).encode(), method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode())
    msg = (d.get("choices") or [{}])[0].get("message") or {}
    return extract_spoken_content(msg)

def tts_pcm(text: str, voice: str | None = None) -> tuple[bytes, int]:
    """Return s16le mono PCM and sample rate from Cosy /stream.

    Cosy uses Transfer-Encoding: chunked with a long synth; urllib often raises
    IncompleteRead. Use a raw socket + manual dechunk for reliability.
    """
    import socket
    from urllib.parse import urlparse

    voice = voice or C.TTS_VOICE
    payload = json.dumps({"text": text, "voice": voice}).encode()
    u = urlparse(C.TTS_URL)
    host = u.hostname or "127.0.0.1"
    port = int(u.port or (443 if u.scheme == "https" else 80))   # the URL's own port (was the house voice's)
    path = u.path or "/stream"
    req = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Content-Type: application/json\r\n"
        "Connection: close\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n"
    ).encode() + payload
    sock = socket.create_connection((host, port), timeout=30)
    sock.settimeout(180)
    try:
        sock.sendall(req)
        data = b""
        while True:
            try:
                chunk = sock.recv(65536)
            except Exception:
                break
            if not chunk:
                break
            data += chunk
    finally:
        try:
            sock.close()
        except Exception:
            pass
    sep = data.find(b"\r\n\r\n")
    headers = data[:sep].decode("latin-1", errors="replace") if sep >= 0 else ""
    body = data[sep + 4 :] if sep >= 0 else data
    sr = 24000
    for line in headers.split("\r\n"):
        if line.lower().startswith("x-sample-rate:"):
            try:
                sr = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    pcm = _dechunk(body)
    return pcm, sr


def _dechunk(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        j = data.find(b"\r\n", i)
        if j < 0:
            if not out and data:
                return data  # already raw PCM
            break
        try:
            n = int(data[i:j], 16)
        except ValueError:
            return data
        i = j + 2
        if n == 0:
            break
        out.extend(data[i : i + n])
        i += n + 2
    return bytes(out) if out else data

def turn_pcm16(pcm: bytes, sr: int = 16000) -> dict:
    stt = stt_pcm16(pcm, sr)
    text = (stt.get("text") or "").strip()
    person_id = stt.get("person_id")
    stamp = stt.get("stamp") or {}
    presence = stamp.get("presence")
    if not text:
        return {
            "text": "",
            "reply": "",
            "pcm": b"",
            "sr": 24000,
            "reason": "empty_stt",
            "person_id": person_id,
            "stamp": stamp,
        }
    if getattr(C, "AUTH_GATE", False) and not user_at_chair_ok(stamp, presence):
        reply = getattr(
            C,
            "AUTH_GATE_REFUSE",
            "I need the owner at the chair before I take that. Who is speaking?",
        )
        try:
            audio, asr = tts_pcm(reply)
        except OSError:
            audio, asr = b"", 24000
        return {
            "text": text,
            "reply": reply,
            "pcm": audio,
            "sr": asr,
            "reason": "auth_gate",
            "person_id": person_id,
            "stamp": stamp,
        }
    reply = llm_reply(text, person_id=person_id, presence=presence)
    audio, asr = tts_pcm(reply)
    return {
        "text": text,
        "reply": reply,
        "pcm": audio,
        "sr": asr,
        "reason": "ok",
        "person_id": person_id,
        "stamp": stamp,
    }

def play_pcm(pcm: bytes, sr: int) -> None:
    import subprocess, tempfile, os
    if not pcm:
        return
    # paplay raw
    subprocess.run(
        ["paplay", f"--raw", f"--rate={sr}", "--channels=1", "--format=s16le", "/dev/stdin"],
        input=pcm, check=False,
    )
