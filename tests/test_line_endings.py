"""Guard: line endings are pinned to LF, and the index agrees with the pin.

``.gitattributes`` declares ``* text=auto eol=lf``. The declaration on its own
is worth nothing, for two independent reasons this guard closes:

1. A rule that does not actually match a path leaves that path's line endings
   at the mercy of each contributor's ``core.autocrlf`` (Git for Windows
   defaults it to ``true``). A ``.gitattributes`` that is present but does not
   cover the repo looks like protection and is not.
2. A CRLF blob already in the index stays CRLF until something re-adds it, at
   which point git renormalises the *whole file*. Every such blob is an armed
   whole-file diff waiting to land on whoever next touches it -- which is the
   exact failure the pin exists to prevent, merely deferred and misattributed.

So both halves are asserted: the attribute is in force for every tracked path,
and no tracked blob is stored CRLF.

The source of truth is ``git ls-files --eol`` -- git's own view of what is in
the index and which attributes are in force. Not a filesystem walk: a walk sees
the working tree, and the working tree is precisely where the index/worktree
divergence at issue is invisible.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# `git ls-files --eol` emits: "i/<eol>  w/<eol>  attr/<attrs><pad>\t<path>".
# The attrs field can contain spaces ("text=auto eol=lf"), and the path is
# separated from the header by the final TAB, so split on the TAB, not on
# whitespace. Paths containing a literal TAB are C-quoted by git (the default
# core.quotePath), so the TAB stays unambiguous.
_LINE = re.compile(r"^i/(\S+)\s+w/(\S+)\s+attr/(.*?)\s*\t(.*)$")

# Index states this repo's policy permits:
#   lf     -- a text blob stored with LF, the declared intent.
#   none   -- an empty file; it has no line ending to get wrong.
#   -text  -- a blob git treats as binary and copies byte-for-byte, never
#             converting. Both an explicit `binary` attribute and `text=auto`'s
#             own binary detection land here.
# Anything else (crlf, mixed) is the defect.
_ALLOWED_INDEX_EOL = frozenset({"lf", "none", "-text"})


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=_ROOT, capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, (
        f"`git {' '.join(args)}` exited {proc.returncode}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _entries() -> list[tuple[str, str, str, str]]:
    """Return (index_eol, worktree_eol, attrs, path) for every tracked file."""
    parsed: list[tuple[str, str, str, str]] = []
    for line in _git("ls-files", "--eol").splitlines():
        if not line:
            continue
        match = _LINE.match(line)
        assert match is not None, f"unparsed `git ls-files --eol` line: {line!r}"
        parsed.append(match.group(1, 2, 3, 4))

    # Cross-check the enumeration against the plain file list. Without this, a
    # git invocation that returned nothing, or a regex that silently matched
    # nothing, would make every assertion below vacuously true -- a guard that
    # cannot fail is not a guard.
    tracked = {line for line in _git("ls-files").splitlines() if line}
    assert tracked, "`git ls-files` listed no files -- not a git checkout?"
    assert {path for *_, path in parsed} == tracked, (
        "`git ls-files --eol` did not enumerate the same paths as `git ls-files`; "
        "the parse above is unreliable, so its verdict cannot be trusted"
    )
    return parsed


def test_every_tracked_path_is_pinned_to_lf() -> None:
    """No tracked path may fall through .gitattributes to autocrlf's mercy."""
    offenders = [
        (path, attrs or "<matches no .gitattributes rule>")
        for _index_eol, _worktree_eol, attrs, path in _entries()
        # A path declared `binary` reports attrs "-text": it is pinned to
        # byte-for-byte, which is the correct pin for a binary file.
        if "-text" not in attrs and "eol=lf" not in attrs
    ]
    assert not offenders, (
        f"{len(offenders)} tracked path(s) are not pinned to LF, so a contributor "
        f"with core.autocrlf=true (the Git for Windows default) or a tool that "
        f"regenerates them can flip their line endings and turn a small change "
        f"into a whole-file diff: {offenders}"
    )


def test_no_tracked_blob_is_stored_crlf() -> None:
    """The index must already agree with the pin, not merely promise to."""
    offenders = [
        (path, index_eol)
        for index_eol, _worktree_eol, _attrs, path in _entries()
        if index_eol not in _ALLOWED_INDEX_EOL
    ]
    assert not offenders, (
        f"{len(offenders)} blob(s) are stored with non-LF line endings. Each one "
        f"renormalises wholesale the next time it is staged, producing exactly the "
        f"whole-file diff the pin exists to prevent. Fix with `git add "
        f"--renormalize .` in its own reviewed commit: {offenders}"
    )
