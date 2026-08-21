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

import ast
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


def _git(*args: str, cwd: Path | None = None) -> str:
    """Run git and decode its output as UTF-8 -- explicitly, never at the
    locale's discretion.

    ``text=True`` alone decodes with the *locale* encoding, which is cp1252 on a
    Windows checkout. Measured on the base commit, that fails two ways: valid
    UTF-8 comes back mojibaked (an em dash as three characters), and a byte
    cp1252 has no mapping for raises ``UnicodeDecodeError`` on a subprocess
    reader thread -- where this code cannot catch it -- so the thread dies and
    ``proc.stdout`` arrives as ``None``. The ``returncode == 0`` assertion below
    then passes and this helper hands its caller ``None``, which surfaces
    downstream as an ``AttributeError`` about ``NoneType``. That is a crash, not
    a verdict about line endings.

    ``errors="replace"``, and deliberately not the strict-and-refuse policy its
    sibling in ``ci/check_no_proprietary_refs.py`` takes. That one SCANS text for
    forbidden tokens, so a replacement character there could hide the very thing
    it is looking for. Nothing of the sort applies here: every field this guard
    reaches a verdict from -- ``i/<eol>``, ``w/<eol>`` and the ``attr/...``
    values -- is ASCII by git's own construction, so no replacement character
    can turn a CRLF blob into a clean one. The path is used for identity and for
    the offender report only, and the cross-check in ``_entries`` compares two
    invocations decoded the same way, so it stays sound. Refusing here would
    redden the suite over a cosmetic rendering of a path git itself considers
    well-formed.
    """
    proc = subprocess.run(
        ["git", *args],
        cwd=_ROOT if cwd is None else cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
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


# ---------------------------------------------------------------------------
# The helper's own decoding
# ---------------------------------------------------------------------------
def _fixture_repo(path: Path, message: str) -> Path:
    """A one-commit repo whose commit message is ``message``, stored as UTF-8."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "a.txt").write_bytes(b"one\n")
    subprocess.run(["git", "add", "a.txt"], cwd=path, check=True)
    msg = path / "COMMIT_MSG"
    msg.write_bytes(message.encode("utf-8"))
    subprocess.run(
        ["git", "commit", "-q", "--cleanup=verbatim", "-F", str(msg)],
        cwd=path, check=True,
    )
    msg.unlink()
    return path


class TestGitOutputDecoding:
    """``subprocess.run(..., text=True)`` decodes with the LOCALE codepage.

    On the cp1252 checkout this repo is developed on that is not UTF-8, and the
    two failure modes were measured on the base commit: valid UTF-8 comes back
    mojibaked (an em dash as three characters), and output carrying a byte
    cp1252 cannot map raises on a subprocess reader thread, leaving
    ``proc.stdout`` as ``None`` -- which sails past this helper's
    ``returncode == 0`` assertion and reaches ``_entries`` as an
    ``AttributeError``. A crash about ``NoneType`` is not a verdict about line
    endings.
    """

    def test_utf8_output_is_not_mojibaked_by_the_locale_codepage(self, tmp_path):
        repo = _fixture_repo(tmp_path / "utf8", "chore: an em dash — and 12°\n")

        out = _git("log", "-1", "--format=%B", cwd=repo)

        assert "—" in out and "°" in out, (
            f"git output was not decoded as UTF-8: {ascii(out)}"
        )

    def test_undecodable_output_yields_a_verdict_not_a_crash(self, tmp_path):
        """Trigger: ``i18n.logOutputEncoding`` pointed at a legacy codepage.

        Measured -- a contributor carrying that in their git config makes
        ``git log`` emit latin-1 bytes, which are not valid UTF-8. This helper
        must still hand back a string. ``errors="replace"`` is safe here (and
        deliberately not the strict-and-refuse policy the token-scanning gate in
        ``ci/check_no_proprietary_refs.py`` takes) because every field this
        guard's verdict reads is ASCII by git's construction -- see ``_git``.
        """
        repo = _fixture_repo(tmp_path / "latin1", "chore: leaked cafébrand\n")

        out = _git(
            "-c", "i18n.logOutputEncoding=ISO-8859-1",
            "log", "-1", "--format=%B", cwd=repo,
        )

        assert isinstance(out, str), (
            f"the helper handed back {type(out).__name__}, not a string -- the "
            f"decode died on a reader thread and stdout came back None"
        )
        assert "chore: leaked caf" in out
        assert "�" in out, (
            f"expected the undecodable byte to be replaced, got {ascii(out)}"
        )

    def test_the_decoding_pin_cannot_be_quietly_removed(self):
        """Structural guard: reverting ``_git`` to a bare ``text=True`` reddens
        this without needing a non-ASCII fixture to reach it.

        AST, never grep -- a comment or a string literal naming the keyword must
        not be able to satisfy this.
        """
        tree = ast.parse(Path(__file__).resolve().read_text(encoding="utf-8"))
        helper = next(
            (
                node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "_git"
            ),
            None,
        )
        assert helper is not None, "the _git helper is gone"

        runs = [
            node for node in ast.walk(helper)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ]
        # Floor: "every call is pinned" is vacuously true of no calls at all.
        assert len(runs) == 1, (
            f"expected exactly one subprocess.run in _git, found {len(runs)}"
        )

        keywords = {kw.arg: kw.value for kw in runs[0].keywords if kw.arg}
        encoding = keywords.get("encoding")
        assert isinstance(encoding, ast.Constant) and encoding.value == "utf-8", (
            "_git must name its decoding explicitly (encoding='utf-8'); without it "
            "the locale codepage decides, and that is cp1252 on Windows"
        )
        errors = keywords.get("errors")
        assert isinstance(errors, ast.Constant) and errors.value == "replace", (
            "_git must state an explicit errors policy alongside the encoding"
        )
