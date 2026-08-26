#!/usr/bin/env python3
"""Boundary gate: this public, AGPL calc-service must never name its proprietary
downstream consumer.

This repository is a generic, standalone Vedic (BPHS) calculation service. It
must not reference the proprietary product that consumes it over HTTP, that
product's issue tracker, or its internals — the two are kept at arm's length, in
separate repos, on purpose. Leaking the consumer's name into this public history
is what this check prevents.

It scans this repository's files AND commit messages in the pushed range against
a LIST of forbidden patterns.

The FILE scan's scope comes from ``git ls-files -z --cached --others
--exclude-standard`` — everything that ships, plus everything one ``git add``
away from shipping, minus everything git is told to ignore. It used to come from
a filesystem walk with a directory-name blacklist, which was wrong in both
directions at once: a tracked file under any blacklisted NAME shipped unscanned,
and the walk read whatever else happened to be on the disk (measured in this
repository's shared root checkout: 221 files walked against 106 real ones, 111
of the extras belonging to another branch's working tree). A walk is kept only
as the fallback for a tree git cannot describe, and a scan that examines zero
files is a REFUSAL rather than a clean verdict.

And EVERY enumerated file is scanned. There is no longer an extension
allowlist. The set used to be ``{.py .md .yml .yaml .toml .txt .sh .ps1 .cfg
.ini .json .dockerfile}`` plus the exact name ``Dockerfile``, and an allowlist is
fail-OPEN in the same shape the directory blacklist was: a file whose extension
nobody thought of ships unscanned, and the gate prints ``OK:`` about a file it
never opened. Measured 2026-08-26 against this repository, that left EIGHT
tracked files outside the scan -- ``Dockerfile.test`` (a suffix the exact-name
``Dockerfile`` test does not match), ``.env.example``, ``.github/CODEOWNERS``,
``.gitattributes``, ``.gitignore``, ``.python-version``, ``LICENSE`` and
``uv.lock``. The first three are prose a human writes about the deployment and
about who owns which path -- exactly the register in which a downstream consumer
gets named.

LOCK FILES ARE IN, deliberately. ``uv.lock`` is 375,480 bytes over 1,881 lines;
it is tracked, and a raw file view publishes it identically to the code. It
records dependency NAMES and index URLs, so a private package name or a private
index host would land there -- machine-generated from a file this gate already
scans, which means such a leak can arrive without a human ever typing it into a
scanned file. Measured on this tree: it decodes strictly and matches nothing, so
including it costs one more regex pass.

BINARY IS A REFUSAL, NEVER A SKIP. Dropping the allowlist means a file the gate
cannot read as text now reaches the decode, and the answer there is the one the
rest of this gate already gives: an undecodable file joins ``refusals``, is named
on stderr, and exits non-zero. It is NOT skipped, because "I could not read it"
must never be recorded as "there was nothing in it" -- that is the same silent
pass the strict decode below exists to remove. Measured 2026-08-26 over the whole
scan set: 0 files carry a NUL byte in their first 8 KiB and 0 fail a strict UTF-8
decode, so strictness costs nothing today. When a genuinely binary file does
arrive the gate reddens, and the operator makes a deliberate decision rather than
inheriting an exemption from an extension list nobody re-reads.

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

The optional positional argument is a git commit range, and it wins when given:
an operator naming a range means exactly that range. With no argument the gate
DERIVES one from the GitHub event (``resolve_commit_range``), which is why the
workflow passes none and interpolates no ``${{ }}`` expression into its ``run:``
body. Outside a GitHub Actions run there is no event to read and the gate scans
HEAD's message, as it always has.

That derivation replaced ``${{ github.event.before }}..${{ github.sha }}``,
which was measured scanning ONE commit message on a branch's first push and
ZERO author-written ones on a pull request — see ``resolve_commit_range`` for
the measurements and for what the derivation deliberately does not claim. The
number of messages actually read is printed on every run, because a range naming
six commits and a scan that read one look identical at exit 0 otherwise.

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

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

# Standalone "astro" token, but not the Swiss Ephemeris URL "astro.com". This is
# the legacy base pattern — always active regardless of env configuration.
_LEGACY_PATTERN = re.compile(r"(?i)\bastro\b(?!\.com)")

# Git's all-zero "before" sentinel for events with no real prior commit (e.g. a
# branch's first push, or a non-push CI event) — never a resolvable range.
_NULL_SHA = "0" * 40

# A git OBJECT NAME, and nothing else. Hex only: no leading dash for `git log`
# to read as an option, no `..` to smuggle a second revision, no whitespace.
# Both SHA-1 (40) and SHA-256 (64) fit, and an abbreviated name is accepted from
# 7 characters because that is git's own minimum for `--abbrev-commit`.
_OBJECT_NAME = re.compile(r"[0-9a-fA-F]{7,64}")

# A branch name safe to splice after `origin/`. Same reasoning as above; this
# one is deliberately narrower than git's real refname grammar, because the only
# values it ever sees come from a payload field naming a default branch.
_REF_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")

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

# Only the WALK fallback below uses these. The primary enumeration asks git,
# which needs no directory blacklist because `--exclude-standard` already knows
# what this repository ignores.
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__",
              ".pytest_cache", "data", ".mypy_cache", ".ruff_cache"}
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


def _git_scan_paths(root: Path) -> list[str] | None:
    """The scan set, asked of git — or ``None`` when git cannot answer.

    ``--cached --others --exclude-standard`` is the union this gate needs, and
    each third of it is load-bearing:

    * ``--cached`` — everything this repository ships. A filesystem walk misses
      any of it that sits under a directory whose NAME is blacklisted, and a
      name-keyed blacklist cannot tell a build artefact from a tracked file.
    * ``--others`` — everything one ``git add`` away from shipping. The gate's
      whole purpose is to run before the push that would publish a leak, and at
      that moment the offending file is usually still untracked, so dropping
      this half would be a regression against the walk it replaces.
    * ``--exclude-standard`` — minus whatever this repository is configured to
      ignore, because an ignored file is not published and a verdict that
      depends on the disk's build artefacts is not reproducible.

    ``-z`` is not optional. Without it git QUOTES any path holding non-ASCII
    bytes (``core.quotePath`` defaults on), wrapping it in literal double quotes
    and rendering each byte as a backslash-octal escape — the measured fail-open
    that ``ci/check_pytest_collection.py`` shipped. ``-z`` NUL-separates exact
    records instead, whatever bytes a path holds.

    Bytes mode, decoded here: a text-mode read hands the decode to a reader
    thread where a ``UnicodeDecodeError`` kills the thread and returns
    ``stdout=None``, and a filename need not be valid UTF-8. An undecodable
    listing therefore answers ``None`` — "git cannot answer" — which the caller
    turns into a walk, never into an empty scan.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, check=False, cwd=root,
        )
    except OSError:
        return None  # no git on this machine; the caller falls back to the walk
    if result.returncode != 0:
        return None  # not a checkout, or git refused: same answer, same fallback
    try:
        listing = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # With -z the records are exact: never strip them, since a leading or
    # trailing space is a legal filename character.
    return [record for record in listing.split("\0") if record]


def _walked_scan_paths(root: Path) -> list[str]:
    """Fallback enumeration for a tree git cannot describe.

    This is the gate's original scope and it is kept ONLY as a fallback: it
    answers "what is on this disk", which is a different question from "what
    does this repository publish". Measured 2026-08-26 in this repository's
    shared root checkout, it enumerated 221 scannable files against a
    tracked-plus-untracked set of 106, 111 of the extras belonging to an
    unrelated branch's working tree under ``.claude/worktrees/``.
    """
    found: list[str] = []
    for path in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        found.append(path.relative_to(root).as_posix())
    return found


def scan_paths(root: Path) -> tuple[list[str], str]:
    """(relative paths, which enumerator answered: ``"git"`` or ``"walk"``).

    The source is returned rather than logged so a test can assert WHICH
    enumeration ran. A fallback nobody can observe is how a guard ends up
    measuring something other than what it claims to.
    """
    from_git = _git_scan_paths(root)
    if from_git is not None:
        return from_git, "git"
    return _walked_scan_paths(root), "walk"


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


class CommitRangeDecision(NamedTuple):
    """What the commit-message half will scan, and how that was decided.

    ``provenance`` is printed, always. A range naming six commits and a scan
    that read one are indistinguishable at exit 0 unless the decision says so,
    and that indistinguishability is exactly how the defect below survived.
    """

    commit_range: str | None
    provenance: str
    refusal: str | None


def _object_name(value: object) -> str | None:
    """*value* if it is a git object name, else ``None`` -- never a guess.

    ``git log`` reads ``--flags`` positionally, so a field spliced into a range
    without validation is argument injection rather than merely a wrong answer.
    The event payload is the one input to this gate that arrives from outside
    the tree, so it is validated rather than trusted: hex only, no leading dash
    to be read as an option, no separator to smuggle a second revision.

    The all-zero sentinel is hex and is rejected here anyway -- it is git's
    "there is no prior commit" marker, never a resolvable revision.
    """
    if not isinstance(value, str) or not _OBJECT_NAME.fullmatch(value):
        return None
    return None if set(value) == {"0"} else value


def _read_event_payload(environ: Mapping[str, str]) -> tuple[dict, str | None]:
    """The GitHub event payload, or ``({}, why not)``.

    GitHub writes the whole payload to a JSON file and names it in
    ``GITHUB_EVENT_PATH``. Reading it directly is what keeps the derivation OUT
    of the workflow: no ``${{ }}`` expression is interpolated into a ``run:``
    body, and the decision lands in Python where it is testable against a
    synthetic payload.

    Decoded through ``_decode`` like everything else this gate reads -- strict
    UTF-8, in-process -- so the encoding policy has one definition here too.
    """
    path = environ.get("GITHUB_EVENT_PATH")
    if not path:
        return {}, "GITHUB_EVENT_PATH is not set"
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return {}, f"the event payload at {path} could not be read ({exc})"
    try:
        payload = json.loads(_decode(raw, f"the event payload at {path}"))
    except (ScanDecodeError, json.JSONDecodeError) as exc:
        return {}, f"the event payload at {path} could not be parsed ({exc})"
    return (payload, None) if isinstance(payload, dict) else ({}, "the event payload is not an object")


def resolve_commit_range(
    environ: Mapping[str, str] | None = None,
    cwd: Path = _REPO_ROOT,
    explicit: str | None = None,
) -> CommitRangeDecision:
    """Decide which commits the message scan covers, from the EVENT.

    WHY THIS IS NOT `${{ github.event.before }}..${{ github.sha }}`
    ==============================================================
    That is what the workflow used to interpolate, and measured on five real
    runs of this repository's own boundary job it produced, verbatim:

    * ``0000...0..<sha>`` on a branch's FIRST push -- the null sentinel, which
      is not a resolvable range, so the scan fell back to HEAD alone. The first
      push is the one carrying every commit on the branch: measured here, six
      commits on one branch and four on another, ONE message read each.
    * ``..<sha>`` on a pull_request ``opened``/``reopened``/``edited``, because
      ``before`` exists only on ``synchronize``. That is git for ``HEAD..<sha>``
      and ``actions/checkout`` has put HEAD *at* ``github.sha`` (the run log
      reads "HEAD is now at <merge> Merge <head> into <base>"), so the range is
      empty and the fallback reads the synthetic MERGE commit -- a message with
      no author-written text in it at all. ZERO real messages, not one.

    So neither field is used as the workflow used them. On a pull_request the
    range comes from the payload's own ``base``/``head`` (``github.sha`` is the
    merge commit and must not appear in it); on a push a real ``before`` wins,
    and the null sentinel falls back to the repository's default branch, which
    is what a branch's first push is measured against.

    KNOWN BOUNDS, stated rather than claimed away
    =============================================
    * ``base..head`` on a pull request over-scans when the branch has merged the
      base branch into itself: those commits are reachable from head and not
      from ``base.sha``. Over-scanning is the fail-SAFE direction and costs one
      git object walk; under-scanning is the defect this replaces.
    * The derivation builds a STRING. Whether git can resolve it depends on the
      clone (``fetch-depth: 0`` is asserted by
      ``ci/tests/test_check_no_proprietary_refs.py::TestTheWorkflowWiresTheDerivation``).
      A range that does not resolve still degrades to HEAD's message, as it
      always has -- what changes is that the printed count and provenance make
      that degradation legible instead of silent.
    * The default branch falls back to ``main`` when the payload does not name
      one. That is this repository's default branch; a fork whose default is
      named otherwise gets the payload value, which is why it is read at all.
    """
    if explicit:
        return CommitRangeDecision(
            explicit, f"the commit-range argument {explicit!r}", None
        )

    env = os.environ if environ is None else environ
    event = env.get("GITHUB_EVENT_NAME", "")
    if not event:
        return CommitRangeDecision(
            None, "HEAD's commit message (no GitHub event in the environment)", None
        )

    payload, payload_problem = _read_event_payload(env)

    def refuse(why: str) -> CommitRangeDecision:
        """Fail closed where it costs something.

        Under GitHub Actions on a push or pull request, every fact needed here
        is one GitHub always supplies -- so failing to derive a range means
        something is wrong, and scanning HEAD alone while printing OK is the
        silent degradation this function exists to end. Outside Actions the
        same failure is ordinary: a developer running the gate by hand has no
        event payload and never did, and refusing there would break the local
        pre-push gate for nothing.
        """
        detail = f"could not derive the commit range for this {event} event: {why}"
        if env.get("GITHUB_ACTIONS"):
            return CommitRangeDecision(None, detail, detail)
        return CommitRangeDecision(None, f"HEAD's commit message ({why})", None)

    if event == "pull_request":
        pull = payload.get("pull_request")
        pull = pull if isinstance(pull, dict) else {}
        base = _object_name((pull.get("base") or {}).get("sha"))
        head = _object_name((pull.get("head") or {}).get("sha"))
        if base and head:
            return CommitRangeDecision(
                f"{base}..{head}",
                f"the pull request's own commits ({base[:7]}..{head[:7]}); "
                f"github.sha is the merge commit and is deliberately not used",
                None,
            )
        return refuse(
            payload_problem
            or "the payload carried no usable pull_request.base.sha / head.sha"
        )

    if event == "push":
        after = _object_name(env.get("GITHUB_SHA"))
        if not after:
            return refuse(payload_problem or "GITHUB_SHA is absent or is not an object name")
        before = _object_name(payload.get("before"))
        if before:
            return CommitRangeDecision(
                f"{before}..{after}",
                f"the pushed range ({before[:7]}..{after[:7]})",
                None,
            )
        repository = payload.get("repository")
        branch = (repository or {}).get("default_branch")
        default_branch = branch if isinstance(branch, str) and _REF_NAME.fullmatch(branch) else "main"
        return CommitRangeDecision(
            f"origin/{default_branch}..{after}",
            f"every commit this branch adds to origin/{default_branch} "
            f"(this push carried no prior commit, so it is a branch's first)",
            None,
        )

    return CommitRangeDecision(
        None, f"HEAD's commit message (a {event} event carries no pushed range)", None
    )


def main(
    commit_range: str | None = None,
    argv: list[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    if commit_range is None:
        arguments = sys.argv[1:] if argv is None else argv
        if arguments:
            commit_range = arguments[0]
    decision = resolve_commit_range(environ, explicit=commit_range)

    violations: list[str] = []
    refusals: list[str] = []
    # A SUBSET of `refusals`: the ones the encoding guidance below is actually
    # about. Not every refusal is a decode failure -- an underivable commit
    # range and a scan that examined zero files are both refusals too, and
    # telling their reader to "rewrite the offending file as UTF-8" sends them
    # hunting for a problem that is not there. Advice aimed at the wrong defect
    # is worse than no advice.
    decode_refusals: list[str] = []
    if decision.refusal:
        refusals.append(decision.refusal)

    candidates, scan_source = scan_paths(_REPO_ROOT)
    scanned = 0
    for label in candidates:
        path = _REPO_ROOT / label
        if not path.is_file():
            continue  # a tracked path deleted from the working tree
        if path.resolve() == _SELF:
            continue  # don't scan the gate's own description of the legacy token
        scanned += 1
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
            decode_refusals.append(str(exc))
            continue
        _scan_text(label, text, violations)

    if scanned == 0:
        # A new failure mode the git enumeration introduces, so it is closed in
        # the same change: a walk of a populated tree cannot come back empty,
        # but `git ls-files` answers rc 0 with no output in a repository holding
        # nothing yet -- which would otherwise have been a clean verdict over a
        # scan of zero files. "No violations found" is vacuously true of a scan
        # that examined nothing.
        refusals.append(
            f"the file scan examined no files at all (enumerated by the "
            f"{scan_source!r} source under {_REPO_ROOT}), so a clean verdict "
            f"would be vacuous"
        )

    # Commit message(s) in the pushed range (cheap guard against a leak buried
    # in an intermediate commit of a multi-commit push, not just HEAD).
    try:
        messages = _get_commit_messages(decision.commit_range)
    except CommitMessageDecodeError as exc:
        # Ordered before OSError deliberately, and kept off that class entirely:
        # absorbing this would restore exactly the silent pass it replaces.
        messages = []
        refusals.append(str(exc))
        decode_refusals.append(str(exc))
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
    # Only the refusals this guidance is ABOUT get it. A range that could not be
    # derived and a scan that examined zero files are refusals too, and telling
    # their reader to rewrite a file as UTF-8 points them at a defect that is
    # not there.
    if decode_refusals:
        sys.stderr.write(
            "\nThis gate decodes everything it scans — file contents and commit "
            "messages alike — as utf-8 strictly. A lossy decode substitutes "
            "U+FFFD for the offending bytes, and a brand token spelled with a "
            "non-ASCII character would not survive that substitution, so "
            "scanning a lossy decode could report 'clean' on a real leak. "
            "Rewrite the offending file(s) or commit message(s) as UTF-8.\n"
            "\nIf the named file is genuinely BINARY, note that skipping it is "
            "not on offer: this gate scans every file git enumerates, and "
            "'I could not read it' may not be recorded as 'there was nothing "
            "in it'. Either git-ignore it (it is then out of scope because it "
            "is not published) or remove it.\n"
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

    # The COUNT is not decoration. A range naming six commits and a scan that
    # read one are indistinguishable at exit 0 without it -- which is precisely
    # how this gate spent months scanning a single message per CI run while
    # printing the same OK line it prints for a full one.
    print(
        f"OK: no proprietary consumer references found "
        f"({scanned} file(s) scanned, enumerated by {scan_source}; "
        f"{len(messages)} commit message(s) scanned from {decision.provenance})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
