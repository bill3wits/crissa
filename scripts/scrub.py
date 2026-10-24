#!/usr/bin/env python3
"""The public scrub: refuse a private word, a private path or a key before it leaves this machine.

    python3 scripts/scrub.py                 # every tracked file (and its path), the CI check
    python3 scripts/scrub.py --dir DIR       # every text file under DIR (a release, an unpacked download)
    python3 scripts/scrub.py --pre-push      # the git pre-push hook: the pushed trees, commit messages and authors
    python3 scripts/scrub.py --stdin         # any text on stdin

The private words are kept as salted hashes of a word or a word pair (they are never spelled here); keys, private
paths and e-mail addresses are plain patterns. Exit 0 = clean, 1 = hits (each printed as file:line label).
Enable the hook once per clone:  git config core.hooksPath .githooks
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

SALT = "crissa-scrub-1"
WORDS = {
    "person-name": [
        "4422fa50854eab8ce1df1909",
        "a7739c35afcf4ce229ac2e2a",
        "b68a98ea1b5354e28fae7bc9",
        "4d1f07085d584b58b8f4a936",
        "0fb01a23f45afc842e493422"
    ],
    "private-name": [
        "bf04277d2be601085637962c"
    ],
    "household-name": [
        "bcff4e2db3a4e1d7a55019ca",
        "70985bd950017b4e942cfd1c",
        "80f8dc97b3e1f75161a573e7"
    ],
    "house-voice": [
        "89f4142cb505a375fee6e6eb",
        "4811f3bf58a6190484c85f6c",
        "89133637b612e36ee676c530",
        "5b23c95c05767f91afc459ec",
        "8671cb4334344feb7054963e",
        "b76776a05aa919f17bb8ff51",
        "4cdeb272d3bdfba316ef3489"
    ],
    "face-id-engine": [
        "40a7b3759d299510a5847dfb",
        "786fce036d8f4544e08d1600"
    ],
    "framework": [
        "f3f6a21a4f8ad209bb6f41b6",
        "49a485f3dbbc08659ebe78d5"
    ]
}
ALLOW_LINES = [
    "\"krew_url\": \"https://github.com/bill3wits/krew\",",
    "- Hermes Agent, by Nous Research, the agent framework Crissa grew up alongside: https://github.com/NousResearch/hermes-agent",
    "Crissa works with any agent that speaks the OpenAI chat API, local or cloud. krew is a Hermes Agent plugin: it runs on its own as an operational crew, or plugs into Crissa."
]
ALLOW_TEXT = [
    "ding@rastersoft.com"
]
# a pushed commit's author and committer address must be one of these (or a GitHub no-reply address)
ALLOW_EMAILS = []
GITHUB_NOREPLY = re.compile(r"^[\w.+-]+@users\.noreply\.github\.com$")
PATTERNS = [
    ("key", re.compile(r"(?<![A-Za-z0-9])sk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}")),
    ("key", re.compile(r"(?<![A-Za-z0-9])xai-[A-Za-z0-9]{20,}")),
    ("key", re.compile(r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("key", re.compile(r"whsec_[A-Za-z0-9]{16,}")),
    ("key", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}")),
    ("key", re.compile(r"hf_[A-Za-z0-9]{30,}")),
    ("key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("email", re.compile(r"(?<![\w.+-])\w[\w.+-]*@[\w-]+\.[a-z]{2,}", re.I)),
    # one machine's paths: a directory under the data root or a user's home (a relative lib/data/ is not one)
    ("machine-path", re.compile(r"(?<![\w.~])/data/[\w.-]|/home/[a-z_][a-z0-9_-]*/")),
    ("lan-address", re.compile(r"(?<![\d.])192\.168\.(?!0\.0/)\d{1,3}\.\d{1,3}")),
]
TEXT_SUFFIXES = {".py", ".json", ".jsonl", ".md", ".txt", ".html", ".js", ".css", ".sh", ".yaml", ".yml", ".toml",
                 ".cfg", ".ini", ".service", ".desktop", ".webmanifest", ".svg", ".in", ".lock", ".patch", ""}
TOKEN = re.compile(r"[a-z0-9]+")


def _h(s: str) -> str:
    return hashlib.sha256((SALT + s).encode("utf-8")).hexdigest()[:24]


HASHED = {h: label for label, hs in WORDS.items() for h in hs}


def hits_in(text: str) -> list[tuple[int, str]]:
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        if line.strip() in ALLOW_LINES:
            continue
        probe = line
        for a in ALLOW_TEXT:
            probe = probe.replace(a, "")
        for label, rx in PATTERNS:
            if rx.search(probe):
                out.append((n, label))
        toks = TOKEN.findall(probe.lower())
        for i, t in enumerate(toks):
            for cand in {t, t.rstrip("0123456789")} | ({t + " " + toks[i + 1]} if i + 1 < len(toks) else set()):
                if cand and _h(cand) in HASHED:
                    out.append((n, HASHED[_h(cand)]))
    return out


SELF = "scripts/scrub.py"   # its own allow lists spell what they allow: here only the key patterns apply


def scan(name: str, text: str | None, report: list[str]) -> None:
    if name == SELF and text is not None:
        report += ["%s:%d: key" % (name, n) for n, line in enumerate(text.split("\n"), 1)
                   if any(rx.search(line) for label, rx in PATTERNS if label == "key")]
        return
    for _, label in hits_in(name):
        report.append("%s: (its path) %s" % (name, label))
    if text is not None:
        for n, label in hits_in(text):
            report.append("%s:%d: %s" % (name, n, label))


def is_text(name: str) -> bool:
    return Path(name).suffix.lower() in TEXT_SUFFIXES


def read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def scan_tree_at(rev: str, report: list[str]) -> None:
    for name in git("ls-tree", "-r", "--name-only", rev).splitlines():
        text = None
        if is_text(name):
            raw = subprocess.run(["git", "show", "%s:%s" % (rev, name)], capture_output=True).stdout
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = None
        scan(name, text, report)


def main(argv: list[str]) -> int:
    report: list[str] = []
    if "--dir" in argv:
        root = Path(argv[argv.index("--dir") + 1])
        for f in sorted(root.rglob("*")):
            if f.is_file():
                rel = str(f.relative_to(root))
                scan(rel, read(f) if is_text(rel) else None, report)
    elif "--stdin" in argv:
        for n, label in hits_in(sys.stdin.read()):
            report.append("stdin:%d: %s" % (n, label))
    elif "--pre-push" in argv:
        zero = "0" * 40
        for line in sys.stdin.read().splitlines():
            parts = line.split()
            if len(parts) != 4 or parts[1] == zero:
                continue
            local, remote = parts[1], parts[3]
            scan_tree_at(local, report)
            rng = local if remote == zero else "%s..%s" % (remote, local)
            for rec in git("log", "--format=%H%x01%an%x01%ae%x01%cn%x01%ce%x01%B%x00", rng).split("\x00"):
                if not rec.strip():
                    continue
                sha, an, ae, cn, ce, msg = rec.strip("\n").split("\x01", 5)
                for who, mail in (("author", ae), ("committer", ce)):
                    if mail not in ALLOW_EMAILS and not GITHUB_NOREPLY.match(mail):
                        report.append("commit %s: the %s address is not one this repository publishes" % (sha[:8], who))
                for _, label in hits_in(an + "\n" + cn + "\n" + msg):
                    report.append("commit %s (message or names): %s" % (sha[:8], label))
    else:
        for name in git("ls-files").splitlines():
            scan(name, read(Path(name)) if is_text(name) else None, report)
    for r in report:
        print(r)
    if report:
        print("scrub: %d hit(s); nothing private may leave this machine" % len(report), file=sys.stderr)
        return 1
    print("scrub: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
