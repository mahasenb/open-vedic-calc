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

Text encoding is pinned at BOTH ends of the pipe, and neither half is optional:

- Producer: ``git -c i18n.logOutputEncoding=UTF-8``. Git transcodes each commit
  message from its declared encoding into that one, so a contributor carrying a
  legacy codepage in their own git config cannot change what this gate reads.
- Consumer: the raw bytes are decoded here, as UTF-8, STRICTLY. Decoding inside
  ``subprocess`` (``text=True``) would use the *locale* codepage — cp1252 on a
  Windows checkout — and, worse, would raise on a reader thread where this code
  cannot catch it, handing back ``stdout=None``.

A decode failure is a REFUSAL (loud, non-zero exit), never a lossy scan. Lossy
decoding is unsafe *specifically for this gate* because it scans for tokens: a
brand token spelled with a non-ASCII character does not survive replacement
characters, so a lossy read could report "clean" on a real leak. Refusing is the
fail-closed answer; being unable to read a commit message is not evidence of
absence.
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

# The encoding this gate reads commit messages in, pinned at both ends of the
# pipe. `-c` beats repository config, so this holds whatever the contributor's
# own git is configured to emit.
_COMMIT_ENCODING = "utf-8"
_LOG_OUTPUT_ENCODING_PIN = "i18n.logOutputEncoding=UTF-8"


class ScanDecodeError(RuntimeError):
    """Something this gate must scan could not be decoded, so it will not pass.

    Deliberately NOT an OSError subclass, and neither is either subclass:
    main() absorbs OSError in two places — so a missing git degrades quietly,
    and an unreadable file is skipped — and a decode refusal must take neither
    of those exits. Being unable to READ something is the one thing this gate
    may never treat as absence of a token.
    """


class CommitMessageDecodeError(ScanDecodeError):
    """git produced commit-message bytes that are not valid UTF-8."""


class FileDecodeError(ScanDecodeError):
    """A file in the scan set holds bytes that are not valid UTF-8.

    The file scan used to take ``errors="replace"``. Its encoding was already
    pinned, so it was never locale-dependent — but lossy is not the same as
    safe, and the argument that decided the commit-message policy applies here
    unchanged: replacement cannot hide an ASCII token (UTF-8 is
    self-synchronising, and U+FFFD is not a word character), but it destroys a
    token spelled with a non-ASCII character, which is exactly what a brand
    token arriving from a secret this repo cannot read may be. Measured: the
    latin-1 bytes of a non-ASCII synthetic token decode with U+FFFD mid-word and
    stop matching their own pattern.

    Measured before the switch: of the 104 files in this gate's scan set, 0
    failed a strict UTF-8 decode — so strictness costs nothing on a healthy
    tree, and a failure is genuinely a signal rather than routine noise.
    """


def _build_brand_patterns(env: dict[str, str] | None = None) -> list[re.Pattern[str]]:
    """Compile the brand token(s) read from PROPRIETARY_REF_TOKENS (comma-separated)
    — the names that must never appear anywhere in this public repo. The env var's
    value is never written to this repo's source; only its name appears here."""
    if env is None:
        env = os.environ  # type: ignore[assignment]

    patterns: list[re.Pattern[str]] = []
    raw = env.get("PROPRIETARY_REF_TOKENS", "")
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        patterns.append(re.compile(re.escape(token), re.IGNORECASE))
    return patterns


def _build_forbidden_patterns(env: dict[str, str] | None = None) -> list[re.Pattern[str]]:
    """Patterns for scanning FILE content: the legacy base token plus the brand
    tokens. The legacy 'astro' word is a code-hygiene concern in source
    identifiers, so it is included here but deliberately NOT in the commit-message
    scan (see _BRAND_PATTERNS) — 'astro' is unavoidably common in this astrology
    repo's prose and would false-positive on every commit that discusses the gate
    or the domain."""
    return [_LEGACY_PATTERN, *_build_brand_patterns(env)]


# Built at import time from the current environment; tests reload the module
# after setting/clearing PROPRIETARY_REF_TOKENS to exercise both branches.
# _FORBIDDEN scans files (legacy + brand); _BRAND_PATTERNS scans commit messages
# (brand only — a leak buried in a message means a real product name, never the
# legacy 'astro' word which litters legitimate astrology commit prose).
_FORBIDDEN: list[re.Pattern[str]] = _build_forbidden_patterns()
_BRAND_PATTERNS: list[re.Pattern[str]] = _build_brand_patterns()

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


def _scan_text(
    label: str,
    text: str,
    out: list[str],
    patterns: list[re.Pattern[str]] | None = None,
) -> None:
    active = _FORBIDDEN if patterns is None else patterns
    for i, line in enumerate(text.splitlines(), 1):
        for pattern in active:
            if pattern.search(line):
                out.append(f"{label}:{i}: {line.strip()}")
                break


def _run_git_log(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """Run ``git log`` with its output encoding pinned, returning RAW BYTES.

    The single place this gate speaks to git, so the pin cannot be applied to
    one invocation and forgotten on the other.

    Bytes, not text: with ``text=True`` the decode happens inside subprocess, on
    a reader thread on Windows, where a ``UnicodeDecodeError`` kills the thread
    and leaves ``stdout`` as ``None`` instead of propagating. Decoding here
    keeps the failure catchable on every platform.
    """
    return subprocess.run(
        ["git", "-c", _LOG_OUTPUT_ENCODING_PIN, "log", *args],
        capture_output=True, check=False, cwd=cwd,
    )


def _decode(
    raw: bytes,
    source: str,
    error_class: type[ScanDecodeError] = CommitMessageDecodeError,
) -> str:
    """Decode scanned bytes as UTF-8, strictly — or refuse.

    ``errors="replace"`` is the wrong policy for anything this gate SCANS
    (though it is the right one for a caller that is not looking for tokens):
    an ASCII token would survive replacement, but a token carrying a non-ASCII
    character would not, so a lossy read could report "clean" on a real leak.

    Both scanned inputs — commit messages and file contents — come through
    here, so the policy cannot be applied to one and forgotten on the other.
    """
    try:
        return raw.decode(_COMMIT_ENCODING)
    except UnicodeDecodeError as exc:
        raise error_class(
            f"{source} is not valid {_COMMIT_ENCODING} ({exc.reason} at byte "
            f"{exc.start}), so it cannot be scanned for forbidden tokens"
        ) from exc


def _get_commit_messages(commit_range: str | None, cwd: Path = _REPO_ROOT) -> list[str]:
    """Return the list of commit messages covered by commit_range (a
    'before..after' git range). Falls back to just HEAD's message when
    commit_range is None, empty, contains the null-SHA sentinel (first push /
    non-push event), or otherwise fails to resolve (e.g. shallow clone missing
    the 'before' commit) — a gate that crashes on an edge case is worse than one
    that silently degrades to single-commit scanning.

    Raises CommitMessageDecodeError when git's output cannot be decoded. That is
    a different thing from an unresolvable range, and it is deliberately NOT
    degraded to the fallback: the fallback exists for "there is no such range",
    not for "there is a message here I could not read".
    """
    if commit_range and _NULL_SHA not in commit_range:
        result = _run_git_log([commit_range, "--format=%B%x00"], cwd)
        if result.returncode == 0 and result.stdout.strip():
            text = _decode(result.stdout, f"a commit message in {commit_range}")
            return [m for m in text.split("\x00") if m.strip()]

    # Fallback: legacy single-commit behaviour.
    result = _run_git_log(["-1", "--format=%B"], cwd)
    if not result.stdout:
        return []
    return [_decode(result.stdout, "HEAD's commit message")]


def main(commit_range: str | None = None) -> int:
    if commit_range is None and len(sys.argv) > 1:
        commit_range = sys.argv[1]

    violations: list[str] = []
    refusals: list[str] = []

    for path in _REPO_ROOT.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.resolve() == _SELF:
            continue  # don't scan the gate's own description of the legacy token
        if path.suffix.lower() not in _SCAN_EXT and path.name.lower() != "dockerfile":
            continue
        label = str(path.relative_to(_REPO_ROOT))
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        # The decode is deliberately OUTSIDE the OSError arm above. An
        # unreadable file is skipped; an UNDECODABLE one is refused, and
        # collecting the refusal rather than raising keeps the remaining files
        # scanned — otherwise one undecodable file would become a way to hide a
        # leak sitting in a readable one later in the walk.
        try:
            text = _decode(raw, label, FileDecodeError)
        except FileDecodeError as exc:
            refusals.append(str(exc))
            continue
        _scan_text(label, text, violations)

    # Commit message(s) in the pushed range (cheap guard against a leak buried
    # in an intermediate commit of a multi-commit push, not just HEAD).
    try:
        messages = _get_commit_messages(commit_range)
    except CommitMessageDecodeError as exc:
        # Ordered before OSError deliberately, and kept off that class entirely:
        # absorbing this would restore exactly the silent pass it replaces.
        messages = []
        refusals.append(str(exc))
    except OSError:
        messages = []

    for msg in messages:
        _scan_text("<commit message>", msg, violations, _BRAND_PATTERNS)

    if refusals:
        sys.stderr.write(
            "ERROR: part of this gate's scan could not be performed, so it "
            "refuses rather than reporting a clean result:\n"
        )
        for refusal in refusals:
            sys.stderr.write(f"  {refusal}\n")
        sys.stderr.write(
            "\nThis gate decodes everything it scans — file contents and commit "
            "messages alike — as utf-8 strictly. A lossy decode substitutes "
            "U+FFFD for the offending bytes, and a brand token spelled with a "
            "non-ASCII character would not survive that substitution, so "
            "scanning a lossy decode could report 'clean' on a real leak. "
            "Rewrite the offending file(s) or commit message(s) as UTF-8.\n"
        )

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

    if violations or refusals:
        return 1

    print("OK: no proprietary consumer references found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
