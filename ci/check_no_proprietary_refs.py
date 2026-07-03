#!/usr/bin/env python3
"""Boundary gate: this public, AGPL calc-service must never name its proprietary
downstream consumer.

This repository is a generic, standalone Vedic (BPHS) calculation service. It
must not reference the proprietary product that consumes it over HTTP, that
product's issue tracker, or its internals — the two are kept at arm's length, in
separate repos, on purpose. Leaking the consumer's name into this public history
is what this check prevents.

It scans tracked source files AND commit messages in the pushed range against a
LIST of forbidden patterns:

- A legacy base pattern, the standalone token ``astro`` (case-insensitive, word
  boundary, excluding the ``astro.com`` Swiss Ephemeris URL). Legitimate domain
  words are allowed: ``astrology``/``astronomy``/``astronomical`` (a different
  word — the trailing letters mean ``\\bastro\\b`` never matches them).
- Zero or more additional brand tokens, supplied ONLY via the
  ``PROPRIETARY_REF_TOKENS`` environment variable (comma-separated) — injected
  in CI from a GitHub Actions secret. This keeps the actual brand literal out of
  this public repo's tracked source entirely: the gate knows the *name* of the
  env var, never its value. When the env var is unset, the gate runs in
  legacy-only mode and does not fail — an absent optional token is not itself a
  leak.

Run locally:  python ci/check_no_proprietary_refs.py [before_sha..after_sha]

The optional positional argument is a git commit range (as produced by, e.g.,
``${{ github.event.before }}..${{ github.sha }}`` in a GitHub Actions push
event). When omitted, or when the range is invalid (e.g. the ``before`` SHA is
the all-zero sentinel git uses for a branch's first push), the gate falls back
to scanning just the current HEAD commit message.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# Standalone "astro" token, but not the Swiss Ephemeris URL "astro.com". This is
# the legacy base pattern — always active regardless of env configuration.
_LEGACY_PATTERN = re.compile(r"(?i)\bastro\b(?!\.com)")

# Git's all-zero "before" sentinel for events with no real prior commit (e.g. a
# branch's first push, or a non-push CI event) — never a resolvable range.
_NULL_SHA = "0" * 40


def _build_forbidden_patterns(env: dict[str, str] | None = None) -> list[re.Pattern[str]]:
    """Build the list of compiled forbidden-reference patterns: the legacy base
    pattern plus any additional brand token(s) read from PROPRIETARY_REF_TOKENS
    (comma-separated). The env var's value is never written to this repo's
    source — only its name appears here."""
    if env is None:
        env = os.environ  # type: ignore[assignment]

    patterns: list[re.Pattern[str]] = [_LEGACY_PATTERN]

    raw = env.get("PROPRIETARY_REF_TOKENS", "")
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        patterns.append(re.compile(re.escape(token), re.IGNORECASE))

    return patterns


# Built at import time from the current environment; tests reload the module
# after setting/clearing PROPRIETARY_REF_TOKENS to exercise both branches.
_FORBIDDEN: list[re.Pattern[str]] = _build_forbidden_patterns()

_SCAN_EXT = {
    ".py", ".md", ".yml", ".yaml", ".toml", ".txt", ".sh", ".ps1",
    ".cfg", ".ini", ".json", ".dockerfile",
}
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__",
              ".pytest_cache", "data", ".mypy_cache"}
# The gate itself necessarily names the forbidden legacy token to describe it.
_SELF = Path(__file__).resolve()
# Anchor the scan to the repo root (parent of ci/), not the process CWD — running
# the gate from a subdirectory must still scan app/ and bphs_core/.
_REPO_ROOT = _SELF.parent.parent


def _scan_text(label: str, text: str, out: list[str]) -> None:
    for i, line in enumerate(text.splitlines(), 1):
        for pattern in _FORBIDDEN:
            if pattern.search(line):
                out.append(f"{label}:{i}: {line.strip()}")
                break


def _get_commit_messages(commit_range: str | None, cwd: Path = _REPO_ROOT) -> list[str]:
    """Return the list of commit messages covered by commit_range (a
    'before..after' git range). Falls back to just HEAD's message when
    commit_range is None, empty, contains the null-SHA sentinel (first push /
    non-push event), or otherwise fails to resolve (e.g. shallow clone missing
    the 'before' commit) — a gate that crashes on an edge case is worse than one
    that silently degrades to single-commit scanning."""
    if commit_range and _NULL_SHA not in commit_range:
        result = subprocess.run(
            ["git", "log", commit_range, "--format=%B%x00"],
            capture_output=True, text=True, check=False, cwd=cwd,
        )
        if result.returncode == 0 and result.stdout.strip():
            return [m for m in result.stdout.split("\x00") if m.strip()]

    # Fallback: legacy single-commit behaviour.
    result = subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        capture_output=True, text=True, check=False, cwd=cwd,
    )
    return [result.stdout] if result.stdout else []


def main(commit_range: str | None = None) -> int:
    if commit_range is None and len(sys.argv) > 1:
        commit_range = sys.argv[1]

    violations: list[str] = []

    for path in _REPO_ROOT.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.resolve() == _SELF:
            continue  # don't scan the gate's own description of the legacy token
        if path.suffix.lower() not in _SCAN_EXT and path.name.lower() != "dockerfile":
            continue
        try:
            label = str(path.relative_to(_REPO_ROOT))
            _scan_text(label, path.read_text(encoding="utf-8", errors="replace"), violations)
        except OSError:
            continue

    # Commit message(s) in the pushed range (cheap guard against a leak buried
    # in an intermediate commit of a multi-commit push, not just HEAD).
    try:
        for msg in _get_commit_messages(commit_range):
            _scan_text("<commit message>", msg, violations)
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
