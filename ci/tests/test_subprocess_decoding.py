"""Repo-wide guard: a subprocess read may never decode where failure is invisible.

WHY THIS EXISTS
===============
``subprocess`` in text mode decodes the child's output *inside subprocess* — on
Windows, on a reader thread. A ``UnicodeDecodeError`` there kills the thread; it
does not propagate. The caller is handed ``stdout=None`` with ``returncode=0``,
and whatever it does next is not a verdict about anything.

That was measured on the commit before this guard landed, on the locked
interpreter (CPython 3.11.15, win32, preferred encoding cp1252) and reproduced
identically on 3.12.10, running a child that emits
``b"before\\xff\\xfe\\x80\\x81\\x8d\\x90after\\n"`` — bytes that are neither
valid UTF-8 nor defined in cp1252 (``0x81``/``0x8d``/``0x90`` are undefined in
cp1252; ``0xff``/``0xfe``/``0x80`` are invalid UTF-8 start bytes):

===================================================== ======================
call shape                                            measured result
===================================================== ======================
``text=True``                                         rc=0, ``stdout=None``
``text=True, encoding="utf-8"``                       rc=0, ``stdout=None``
``text=True, encoding="utf-8", errors="strict"``      rc=0, ``stdout=None``
``encoding="utf-8"`` alone (implies text mode)        rc=0, ``stdout=None``
``universal_newlines=True``                           rc=0, ``stdout=None``
``text=True, encoding="utf-8", errors="replace"``     rc=0, ``'before' + 6x U+FFFD + 'after\\n'``
bytes mode, then ``raw.decode("utf-8")`` in-process   **catchable** ``UnicodeDecodeError``
===================================================== ======================

The second row is the load-bearing one, and it is why this guard's contract is
not the obvious "every ``text=True`` needs an ``encoding=``". **An explicit
encoding does not remove the hazard — it only changes which codec dies on the
reader thread.** A guard that required an encoding and stopped there would have
certified every one of rows 2 and 3 as fixed.

What separates the safe rows from the unsafe ones is not the codec and not the
identity of the calling helper. It is whether the decode *can raise where nobody
is listening*. Two shapes satisfy that, and this guard admits exactly those:

**(A) Decode in-process.** Bytes mode — no ``text``, ``universal_newlines``,
``encoding`` or ``errors``. The caller decodes, so ``UnicodeDecodeError`` is
raised in the caller's own frame where it can be caught, refused or reported.
This is the shape ``ci/check_no_proprietary_refs.py`` takes, because a gate that
scans for tokens must refuse an unreadable input rather than scan a lossy
rendering of it.

**(B) Make the reader-thread decode infallible.** An explicit ``encoding=``
*and* an ``errors=`` policy that cannot raise. The thread cannot die, so
``stdout`` cannot come back ``None``.

Refused, therefore:

* text mode with no explicit ``encoding=`` — the decode uses the *locale*
  codepage, so the verdict depends on the machine it ran on;
* text mode with a *raising* ``errors=`` policy (including the default, which
  is ``strict``) — rows 2 and 3 above;
* ``subprocess.getoutput`` / ``getstatusoutput`` in any form — they decode with
  the locale and accept no ``encoding`` parameter at all, so neither shape is
  reachable;
* a ``**kwargs`` splat on a subprocess call — the property cannot be proved by
  reading the call, and a guard that cannot prove its property must fail closed.

THE LOSSY REGISTER (arm 2)
==========================
Shape (B) is crash-free but **lossy**, and lossiness is a semantic risk this
guard cannot adjudicate: a U+FFFD substituted into text that is being *scanned
for a token* can destroy the very token being looked for (measured in PR #75: a
latin-1 ``café``-style token decodes with a replacement character mid-word and
stops matching, while an ASCII token survives intact because UTF-8 is
self-synchronising and U+FFFD is not a word character).

So every shape-(B) site is enumerated in ``_LOSSY_DECODE_REGISTER`` below with
the reason lossiness cannot change *that* site's verdict. A new lossy site reds
until someone writes that reason down. The register is keyed by
``(path, enclosing function)`` resolved by AST, and it is **self-expiring**: an
entry that no longer resolves to a shape-(B) site fails as stale, so it cannot
outlive the code it was written for.

Note the register vets the *lossy choice*, not the identity of a helper. Helper
identity is not a safety property — measured, ``errors="strict"`` inside a
carefully written helper dies on the reader thread exactly as it does anywhere
else, and ``errors="replace"`` outside one is exactly as crash-free.

KNOWN BOUNDS (documented, not assumed away)
===========================================
* Scope is ``git ls-files '*.py'`` — the tracked tree, not a directory walk.
* The guard reads the *call site*. A subprocess call whose keyword arguments are
  assembled elsewhere and splatted in is refused rather than analysed.
* It does not police what the caller does with bytes afterwards. ``bytes.decode()``
  defaults to UTF-8 strict by language guarantee, not by locale, so shape (A) is
  safe whether or not the caller spells the encoding out.
* It reads keyword *literals*. A non-literal ``errors=<expr>`` cannot be proved
  non-raising and is refused.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

# The subprocess entry points that can put this process into text mode.
_DISPATCH = frozenset(
    {"run", "check_output", "check_call", "call", "Popen"}
)
# These two decode with the locale and take no `encoding` parameter at all, so
# neither admissible shape is reachable through them.
_LOCALE_ONLY_DISPATCH = frozenset({"getoutput", "getstatusoutput"})

# Keywords that put the call into text mode.
_TEXT_FLAGS = ("text", "universal_newlines")

# `errors` policies that cannot raise on the reader thread. Anything else --
# including the default, `strict` -- can, which is rows 2 and 3 of the table.
_NON_RAISING_ERRORS = frozenset(
    {"replace", "ignore", "backslashreplace", "surrogateescape"}
)

# Floors. "No violations found" is vacuously true of a sweep that examined
# nothing, so every arm of this guard asserts it actually looked at something.
_MIN_PY_FILES = 30
_MIN_CALL_SITES = 40
_MIN_DECODING_SITES = 14


# ---------------------------------------------------------------------------
# The register: every lossy-but-crash-free (shape B) site, with its reason.
# ---------------------------------------------------------------------------
# Keyed by (posix path, enclosing function qualname). The value is why a
# replacement character cannot change that site's verdict.
_LOSSY_DECODE_REGISTER: dict[tuple[str, str], str] = {
    (
        "ci/tests/test_check_no_proprietary_refs.py",
        "TestCommitRangeScanning.test_get_commit_messages_accepts_a_range",
    ): (
        "Reads `git rev-parse HEAD` only. A commit SHA is 40 hex characters -- "
        "ASCII by git's construction -- and the verdict is a token search in the "
        "message text fetched separately by the gate itself, not in this output."
    ),
    (
        "ci/tests/test_check_no_proprietary_refs.py",
        "_commit_with_raw_message",
    ): (
        "Reads `git symbolic-ref HEAD`, `git rev-parse --verify HEAD` and "
        "`git write-tree`: a ref name and two object SHAs, all ASCII by git's "
        "construction. The deliberately undecodable bytes this helper writes go "
        "in through `input=` in bytes mode and are never read back through here."
    ),
    (
        "ci/tests/test_check_no_proprietary_refs.py",
        "TestCommitMessageDecodingIsPinned.test_the_range_branch_refuses_instead_of_crashing",
    ): (
        "Reads `git rev-parse HEAD` only -- hex SHAs used to build a range "
        "argument. The undecodable message this test is about is read by the "
        "gate under test, which decodes strictly in-process and refuses."
    ),
    (
        "ci/tests/test_pytest_collection_check.py",
        "test_the_real_script_refuses_under_the_reviewers_exact_reproduction",
    ): (
        "Verdicts are the child's exit status and the presence of the ASCII "
        "marker `PYTEST_ADDOPTS` in its output. UTF-8 is self-synchronising, so "
        "ASCII bytes always decode to themselves and no substitution elsewhere "
        "in the stream can hide or fabricate an ASCII marker. The remaining use "
        "is a diagnostic assertion message."
    ),
    (
        "ci/tests/test_reference_corpus_fetcher.py",
        "test_the_committed_corpus_is_not_overwritten_by_a_default_invocation",
    ): (
        "Verdicts are the child's exit status, the presence of the ASCII marker "
        "`UPDATE_INDEPENDENT_CORPUS`, the ABSENCE of the ASCII phrase 'fetching "
        "the independent reference corpus', and a byte comparison of the corpus "
        "file made independently of this decode. Both the positive and the "
        "negative marker are pure ASCII, which survives replacement intact."
    ),
    (
        "tests/test_lunar_node_model.py",
        "test_importing_the_package_is_what_pins_the_model",
    ): (
        "The verdict is parsed from a `RESULT <int> <int> <int>` line the child "
        "prints itself -- ASCII by construction. Anything else in the stream is "
        "a traceback interpolated into an assertion message for a human."
    ),
    (
        "tests/test_position_flags.py",
        "test_importing_the_package_is_what_pins_the_serving_global",
    ): (
        "The verdict is parsed from a `RESULT <int>` line the child prints "
        "itself -- ASCII by construction. Anything else is diagnostic."
    ),
    (
        "tests/test_rise_set_flags.py",
        "test_importing_the_package_is_what_pins_the_serving_globals",
    ): (
        "The verdict is parsed from a `RESULT <int> <int>` line the child prints "
        "itself -- ASCII by construction. Anything else is diagnostic."
    ),
    (
        "tests/test_swiss_ephemeris.py",
        "test_the_recording_arm_itself_refuses_under_an_empty_ci",
    ): (
        "Verdicts are the child pytest run's exit status and the presence of an "
        "ASCII refusal marker in its output. pytest output can carry non-ASCII "
        "(test ids, paths, docstrings), which is precisely why this site must "
        "not decode strictly on the reader thread; the ASCII marker it reads is "
        "unaffected by substitution elsewhere in the stream."
    ),
    (
        "tests/test_line_endings.py",
        "_git",
    ): (
        "Pre-existing, decided in PR #75: every field this guard reaches a "
        "verdict from (`i/<eol>`, `w/<eol>`, `attr/...`) is ASCII by git's own "
        "construction, so no replacement character can turn a CRLF blob into a "
        "clean one. The path is used for identity and the offender report only."
    ),
    (
        "ci/tests/test_stable_anchor_citations.py",
        "_git",
    ): (
        "Pre-existing. Reads tracked-file listings and blob contents whose "
        "citation anchors are resolved by AST against the file on disk, not "
        "from this text; a substituted character can only affect the human-"
        "readable report."
    ),
}


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------
def _tracked_python_files() -> list[pathlib.Path]:
    """Every tracked ``*.py``, from ``git ls-files`` -- the source of truth.

    ``-z`` so no path is quoted or newline-split, and bytes mode so the decode
    happens here: this guard may not commit the defect it polices.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z", "*.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        "`git ls-files` failed in "
        f"{_REPO_ROOT}: {result.stderr.decode('utf-8', 'replace').strip()}. "
        "This guard derives its scope from the tracked-file list and fails "
        "closed rather than silently checking nothing."
    )
    names = result.stdout.decode("utf-8").split("\0")
    return [_REPO_ROOT / name for name in names if name]


class _Site:
    """One subprocess invocation, with everything needed to judge it."""

    def __init__(self, relpath: str, node: ast.Call, dispatch: str, qualname: str):
        self.relpath = relpath
        self.line = node.lineno
        self.dispatch = dispatch
        self.qualname = qualname
        self.splat = any(kw.arg is None for kw in node.keywords)
        self.kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}

    def __str__(self) -> str:
        return f"{self.relpath}:{self.line} ({self.qualname} -> subprocess.{self.dispatch})"

    def literal(self, key: str):
        """The literal value of a keyword, or the sentinel ``_NOT_LITERAL``."""
        if key not in self.kwargs:
            return None
        try:
            return ast.literal_eval(self.kwargs[key])
        except (ValueError, SyntaxError):
            return _NOT_LITERAL

    @property
    def in_text_mode(self) -> bool:
        """Does this call decode inside subprocess at all?

        Any of the four keywords puts the child's streams into text mode --
        ``encoding``/``errors`` do so implicitly, which is exactly how a site
        can be locale-dependent while carrying no ``text=True`` to grep for.
        """
        if any(self.literal(flag) is True for flag in _TEXT_FLAGS):
            return True
        return "encoding" in self.kwargs or "errors" in self.kwargs


_NOT_LITERAL = object()


class _CallCollector(ast.NodeVisitor):
    """Collect subprocess call sites, resolving module aliases and qualnames."""

    def __init__(self, relpath: str):
        self.relpath = relpath
        self.sites: list[_Site] = []
        self._scope: list[str] = []
        # Names bound to the subprocess MODULE (`import subprocess [as x]`).
        self._module_aliases: set[str] = set()
        # Names bound directly to a dispatch function
        # (`from subprocess import run [as r]`) -> the real dispatch name.
        self._direct: dict[str, str] = {}

    # -- binding resolution: aliasing must not be an escape hatch -----------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "subprocess":
                self._module_aliases.add(alias.asname or "subprocess")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "subprocess":
            for alias in node.names:
                if alias.name in _DISPATCH or alias.name in _LOCALE_ONLY_DISPATCH:
                    self._direct[alias.asname or alias.name] = alias.name
        self.generic_visit(node)

    # -- qualname tracking --------------------------------------------------
    def _push(self, node) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_FunctionDef = _push
    visit_AsyncFunctionDef = _push
    visit_ClassDef = _push

    # -- the calls themselves ----------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        dispatch = self._resolve(node.func)
        if dispatch is not None:
            self.sites.append(
                _Site(self.relpath, node, dispatch, ".".join(self._scope) or "<module>")
            )
        self.generic_visit(node)

    def _resolve(self, func: ast.expr) -> str | None:
        if isinstance(func, ast.Attribute):
            value = func.value
            if isinstance(value, ast.Name) and value.id in self._module_aliases:
                if func.attr in _DISPATCH or func.attr in _LOCALE_ONLY_DISPATCH:
                    return func.attr
            return None
        if isinstance(func, ast.Name):
            return self._direct.get(func.id)
        return None


def _all_sites() -> tuple[list[_Site], int]:
    sites: list[_Site] = []
    files = _tracked_python_files()
    for path in files:
        relpath = path.relative_to(_REPO_ROOT).as_posix()
        source = path.read_bytes().decode("utf-8")
        collector = _CallCollector(relpath)
        collector.visit(ast.parse(source, filename=str(path)))
        sites.extend(collector.sites)
    return sites, len(files)


def _verdict(site: _Site) -> tuple[str, str]:
    """Classify a site as ('bytes'|'lossy'|'violation', explanation)."""
    if site.dispatch in _LOCALE_ONLY_DISPATCH:
        return "violation", (
            f"`subprocess.{site.dispatch}` decodes with the locale codepage and "
            "accepts no `encoding` parameter, so neither admissible shape is "
            "reachable through it. Use `subprocess.run` in bytes mode and decode "
            "in-process."
        )
    if site.splat:
        return "violation", (
            "a `**kwargs` splat means the decoding keywords cannot be read at "
            "the call site, so this guard cannot prove the call is safe. Pass "
            "the decoding keywords explicitly."
        )
    if not site.in_text_mode:
        return "bytes", "bytes mode -- the caller decodes, where failure is catchable"

    encoding = site.literal("encoding")
    if encoding is None:
        return "violation", (
            "text mode with no explicit `encoding=`: the decode uses the LOCALE "
            "codepage (cp1252 on a Windows checkout), so the result depends on "
            "the machine. Measured: an undecodable byte kills the reader thread "
            "and the caller is handed stdout=None with returncode=0."
        )
    if encoding is _NOT_LITERAL:
        return "violation", (
            "`encoding=` is not a literal, so this guard cannot confirm which "
            "codec is used. Spell it out at the call site."
        )

    errors = site.literal("errors")
    if errors is None:
        return "violation", (
            f"text mode with `encoding={encoding!r}` but no `errors=` policy, so "
            "the decode is STRICT and can raise on the reader thread. Measured: "
            "an explicit encoding does NOT remove the crash -- it only changes "
            "which codec dies, and the caller still gets stdout=None with "
            "returncode=0. Either drop to bytes mode and decode in-process, or "
            "choose a non-raising `errors=` policy and register the reason."
        )
    if errors is _NOT_LITERAL or errors not in _NON_RAISING_ERRORS:
        return "violation", (
            f"`errors={errors!r}` can raise on the reader thread, where the "
            "exception kills the thread instead of propagating. Non-raising "
            f"policies: {sorted(_NON_RAISING_ERRORS)}."
        )
    return "lossy", f"encoding={encoding!r}, errors={errors!r}"


# ---------------------------------------------------------------------------
# Arm 0 -- the classifier itself, proved against synthetic sources.
# ---------------------------------------------------------------------------
def _classify_source(source: str) -> tuple[str, str]:
    collector = _CallCollector("<synthetic>")
    collector.visit(ast.parse(source))
    assert len(collector.sites) == 1, (
        f"the synthetic fixture did not yield exactly one site: {source!r}"
    )
    return _verdict(collector.sites[0])


def test_the_classifier_separates_the_measured_shapes() -> None:
    """The seven measured shapes, each classified as the measurement demands.

    Without this, every arm below could be green because the classifier calls
    everything safe. The table mirrors the module docstring's measurements one
    for one.
    """
    cases = [
        # (source, expected verdict)
        ("import subprocess\nsubprocess.run(c, capture_output=True)", "bytes"),
        ("import subprocess\nsubprocess.run(c, text=True)", "violation"),
        ("import subprocess\nsubprocess.run(c, universal_newlines=True)", "violation"),
        ("import subprocess\nsubprocess.run(c, text=True, encoding='utf-8')", "violation"),
        (
            "import subprocess\nsubprocess.run(c, text=True, encoding='utf-8', errors='strict')",
            "violation",
        ),
        (
            "import subprocess\nsubprocess.run(c, text=True, encoding='utf-8', errors='replace')",
            "lossy",
        ),
        ("import subprocess\nsubprocess.run(c, encoding='utf-8')", "violation"),
        ("import subprocess\nsubprocess.run(c, encoding='utf-8', errors='replace')", "lossy"),
        # `errors=` alone still implies text mode -- with the LOCALE codec.
        ("import subprocess\nsubprocess.run(c, errors='replace')", "violation"),
        # the two that can never be made safe
        ("import subprocess\nsubprocess.getoutput(c)", "violation"),
        ("import subprocess\nsubprocess.getstatusoutput(c)", "violation"),
        # unprovable shapes fail closed
        ("import subprocess\nsubprocess.run(c, **kw)", "violation"),
        ("import subprocess\nsubprocess.run(c, text=True, encoding=E)", "violation"),
        (
            "import subprocess\nsubprocess.run(c, text=True, encoding='utf-8', errors=E)",
            "violation",
        ),
        # aliasing is not an escape hatch
        ("import subprocess as sp\nsp.run(c, text=True)", "violation"),
        ("from subprocess import run\nrun(c, text=True)", "violation"),
        ("from subprocess import run as r\nr(c, text=True)", "violation"),
        ("import subprocess as sp\nsp.Popen(c, text=True)", "violation"),
    ]
    for source, expected in cases:
        verdict, why = _classify_source(source)
        assert verdict == expected, (
            f"{source!r} classified {verdict!r}, expected {expected!r} ({why})"
        )
    assert len(cases) >= 18, "the classifier table lost cases"


def test_a_call_to_something_else_named_run_is_not_a_subprocess_site() -> None:
    """The collector must not fire on unrelated names, or the guard is noise."""
    collector = _CallCollector("<synthetic>")
    collector.visit(
        ast.parse(
            "import subprocess\n"
            "import other\n"
            "other.run(c, text=True)\n"
            "run(c, text=True)\n"
            "obj.subprocess.run(c, text=True)\n"
        )
    )
    assert collector.sites == [], (
        "the collector fired on a name that is not the subprocess module: "
        f"{[str(s) for s in collector.sites]}"
    )


# ---------------------------------------------------------------------------
# Arm 1 -- the sweep floors its own inventory.
# ---------------------------------------------------------------------------
def test_the_sweep_actually_examined_the_tree() -> None:
    """"No violations" is vacuously true of a sweep that examined nothing."""
    sites, file_count = _all_sites()
    assert file_count >= _MIN_PY_FILES, (
        f"only {file_count} tracked *.py files were enumerated (floor "
        f"{_MIN_PY_FILES}) -- the scope derivation is broken, not the tree."
    )
    assert len(sites) >= _MIN_CALL_SITES, (
        f"only {len(sites)} subprocess call sites were found (floor "
        f"{_MIN_CALL_SITES}) -- the collector has stopped matching."
    )
    decoding = [s for s in sites if s.in_text_mode]
    assert len(decoding) >= _MIN_DECODING_SITES, (
        f"only {len(decoding)} decoding sites were found (floor "
        f"{_MIN_DECODING_SITES}). If decoding sites were legitimately removed, "
        "lower the floor deliberately -- do not let this arm pass on zero."
    )


# ---------------------------------------------------------------------------
# Arm 2 -- the contract itself.
# ---------------------------------------------------------------------------
def test_no_subprocess_read_decodes_where_failure_is_invisible() -> None:
    """Every subprocess site is bytes mode, or infallible on the reader thread."""
    sites, _ = _all_sites()
    violations = [
        f"  {site}\n      {why}"
        for site in sites
        for verdict, why in [_verdict(site)]
        if verdict == "violation"
    ]
    assert not violations, (
        f"{len(violations)} subprocess call site(s) decode where a failure is "
        "invisible:\n" + "\n".join(violations) + "\n\n"
        "Measured: text mode with an undecodable byte kills the subprocess "
        "reader thread and hands the caller stdout=None with returncode=0. An "
        "explicit `encoding=` does NOT fix that on its own. Either use bytes "
        "mode and decode in-process (where the failure is catchable), or pair "
        "the encoding with a non-raising `errors=` policy and register why "
        "lossiness is safe at that site."
    )


# ---------------------------------------------------------------------------
# Arm 3 -- the lossy register: complete, and self-expiring.
# ---------------------------------------------------------------------------
def test_every_lossy_decode_site_is_registered_with_a_reason() -> None:
    """A crash-free decode is still a LOSSY one; each needs a stated reason."""
    sites, _ = _all_sites()
    lossy = [s for s in sites if _verdict(s)[0] == "lossy"]
    assert lossy, (
        "no lossy sites were found at all -- either the classifier stopped "
        "matching, or every site moved to bytes mode and this register plus its "
        "floor should be retired deliberately."
    )
    unregistered = [
        f"  {site}" for site in lossy if (site.relpath, site.qualname) not in _LOSSY_DECODE_REGISTER
    ]
    assert not unregistered, (
        "these sites decode lossily on the reader thread without a registered "
        "reason:\n" + "\n".join(unregistered) + "\n\n"
        "A replacement character can destroy a non-ASCII token in text that is "
        "being scanned for one. Add an entry to _LOSSY_DECODE_REGISTER stating "
        "why lossiness cannot change this site's verdict -- or use bytes mode "
        "and decode strictly in-process."
    )


def test_the_lossy_register_does_not_outlive_its_sites() -> None:
    """Self-expiring: an entry whose site is gone is a stale claim, not a pass.

    Without this the register only ever grows, and a reason written for code
    that no longer exists reads as active vetting of code that does.
    """
    sites, _ = _all_sites()
    live = {(s.relpath, s.qualname) for s in sites if _verdict(s)[0] == "lossy"}
    stale = sorted(key for key in _LOSSY_DECODE_REGISTER if key not in live)
    assert not stale, (
        "these _LOSSY_DECODE_REGISTER entries no longer resolve to a lossy "
        "subprocess site:\n"
        + "\n".join(f"  {path} :: {qualname}" for path, qualname in stale)
        + "\n\nThe code moved, was renamed, or was fixed. Remove the entry."
    )


def test_every_registered_reason_is_substantive() -> None:
    """An empty or placeholder reason is a registration that vets nothing."""
    thin = [
        f"  {path} :: {qualname}"
        for (path, qualname), reason in _LOSSY_DECODE_REGISTER.items()
        if len(reason.strip()) < 60 or "TODO" in reason
    ]
    assert not thin, (
        "these register entries carry no real justification:\n" + "\n".join(thin)
    )
    assert len(_LOSSY_DECODE_REGISTER) >= 8, (
        f"the register holds only {len(_LOSSY_DECODE_REGISTER)} entries -- if "
        "sites were legitimately removed, lower this floor deliberately."
    )
