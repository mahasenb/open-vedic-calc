#!/usr/bin/env python3
"""Boundary gate: this public, AGPL calc-service must never name its proprietary
downstream consumer.

This repository is a generic, standalone Vedic (BPHS) calculation service. It
must not reference the proprietary product that consumes it over HTTP, that
product's issue tracker, or its internals — the two are kept at arm's length, in
separate repos, on purpose. Leaking the consumer's name into this public history
is what this check prevents.

It scans tracked source files AND the latest commit message for the standalone
token ``astro`` (the consumer's name). Legitimate domain words are allowed:
``astrology``/``astronomy``/``astronomical`` (a different word — the trailing
letters mean ``\\bastro\\b`` never matches them) and ``astro.com`` (the Swiss
Ephemeris site).

Run locally:  python ci/check_no_proprietary_refs.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Standalone "astro" token, but not the Swiss Ephemeris URL "astro.com".
_FORBIDDEN = re.compile(r"(?i)\bastro\b(?!\.com)")
_SCAN_EXT = {
    ".py", ".md", ".yml", ".yaml", ".toml", ".txt", ".sh", ".ps1",
    ".cfg", ".ini", ".json", ".dockerfile",
}
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__",
              ".pytest_cache", "data", ".mypy_cache"}
# The gate itself necessarily names the forbidden token to describe it.
_SELF = Path(__file__).resolve()


def _scan_text(label: str, text: str, out: list[str]) -> None:
    for i, line in enumerate(text.splitlines(), 1):
        if _FORBIDDEN.search(line):
            out.append(f"{label}:{i}: {line.strip()}")


def main() -> int:
    violations: list[str] = []

    for path in Path(".").rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.resolve() == _SELF:
            continue  # don't scan the gate's own description of the token
        if path.suffix.lower() not in _SCAN_EXT and path.name.lower() != "dockerfile":
            continue
        try:
            _scan_text(str(path), path.read_text(encoding="utf-8", errors="replace"), violations)
        except OSError:
            continue

    # Latest commit message (cheap guard against it creeping into history).
    try:
        msg = subprocess.run(
            ["git", "log", "-1", "--format=%B"],
            capture_output=True, text=True, check=False,
        ).stdout
        _scan_text("<latest commit message>", msg, violations)
    except OSError:
        pass

    if violations:
        sys.stderr.write(
            "ERROR: proprietary consumer reference(s) found in this public repo:\n"
        )
        for v in violations:
            sys.stderr.write(f"  {v}\n")
        sys.stderr.write(
            '\nThis repo must not name the proprietary consumer. Use generic terms '
            '("the caller", "the HTTP client", "the consumer").\n'
        )
        return 1

    print("OK: no proprietary consumer references found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
