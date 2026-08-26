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
* text mode with an ``encoding=`` other than UTF-8 — shape (B)'s whole
  justification is that ASCII survives a lossy decode, and that is codec
  specific; see ``_ADMITTED_CODECS`` for the cp932/cp936/utf-16 counterexamples;
* ``subprocess.getoutput`` / ``getstatusoutput`` and ``os.popen`` in any form —
  they decode with the locale and accept no ``encoding`` parameter at all, so
  neither shape is reachable;
* a ``**kwargs`` splat on a subprocess call — the property cannot be proved by
  reading the call, and a guard that cannot prove its property must fail closed;
* a call whose text-mode keywords cannot be read — ``text=<expr>`` and friends.
  Text mode is decided by TRUTHINESS, so an unreadable value may well select it;
* more than one positional argument, or a ``*args`` splat — **text mode can be
  selected with no keyword at all.** ``universal_newlines`` is ``Popen``'s 12th
  positional parameter and ``run``/``check_output``/``call``/``check_call``
  forward ``*popenargs`` into it, so a guard reading only ``node.keywords``
  makes an affirmative *"bytes, safe"* claim about a text-mode call. See
  ``_MAX_POSITIONAL_ARGS``.

**Text mode is read as the engine reads it.** CPython's ``Popen.__init__`` sets
``self.text_mode = encoding or errors or text or universal_newlines`` — an
``or`` chain over truth values, not a comparison against ``True``. An earlier
version of this guard tested ``literal(flag) is True`` and so classified
``text=1``, ``text=2``, ``text="yes"``, ``universal_newlines=1`` and any
non-literal ``text=<expr>`` as **bytes mode, i.e. safe**, while the engine put
every one of them in text mode and handed back ``stdout=None``. That is this
guard committing the exact defect it exists to catch. See ``_Site.text_mode``.

KNOWN BOUNDS (documented, not assumed away)
===========================================
Stated because a guard that overclaims its reach is worse than one whose limits
are written down. **Binding forms that ARE resolved:** ``import subprocess``,
``import subprocess as sp``, ``from subprocess import run``,
``from subprocess import run as r``, ``from subprocess import *``, and a plain
rebinding ``sp = subprocess``. **Binding forms that are NOT, and would be
invisible to this guard:**

* ``getattr(subprocess, "run")(...)`` — the attribute name is a runtime value;
* ``importlib.import_module("subprocess").run(...)`` — likewise;
* a rebinding that is not a simple ``name = name`` assignment, one placed
  textually *before* the import it aliases, or one built dynamically.

Those are deliberate-evasion shapes rather than things anyone writes by
accident, and closing them means executing the module rather than parsing it.
They are refused as a *design* boundary, not overlooked. Note this repository
has **no linter at all**, so nothing else would flag them either.

Further bounds:

* Scope is ``git ls-files '*.py'`` — the tracked tree, not a directory walk.
* The guard reads the *call site*. A subprocess call whose keyword arguments are
  assembled elsewhere and splatted in is refused rather than analysed.
* It does not police what the caller does with bytes afterwards. ``bytes.decode()``
  defaults to UTF-8 strict by language guarantee, not by locale, so shape (A) is
  safe whether or not the caller spells the encoding out.
* It reads keyword *literals*. A non-literal value in any decoding keyword is
  refused rather than guessed at.
* Only ``subprocess`` and ``os.popen`` are watched (``_WATCHED_MODULES``). Other
  ways to reach a locale-decoded pipe — ``pty``, ``asyncio.subprocess``, a
  third-party wrapper — are out of scope and would need their own entry.

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

"""

from __future__ import annotations

import ast
import codecs
import json
import pathlib
import subprocess
import sys
import textwrap

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

# The subprocess entry points that can put this process into text mode.
_DISPATCH = frozenset(
    {"run", "check_output", "check_call", "call", "Popen"}
)
# These decode with the locale and take no `encoding` parameter at all, so
# neither admissible shape is reachable through them. `os.popen` is the same
# hazard class wearing a different module name: it returns a text-mode pipe
# opened with the locale encoding and offers no way to pin one.
_LOCALE_ONLY_DISPATCH = frozenset({"getoutput", "getstatusoutput"})
_OS_LOCALE_DISPATCH = frozenset({"popen"})

# The four keywords the engine ORs together to decide text mode, in its own
# order: `encoding or errors or text or universal_newlines`
# (CPython `subprocess.Popen.__init__`). `encoding`/`errors` select text mode
# implicitly, which is how a site can be locale-dependent while carrying no
# `text=True` for a reader to notice.
_TEXT_MODE_KEYS = ("encoding", "errors", "text", "universal_newlines")

# The ONLY codec admitted for shape (B), by canonical codec name.
#
# Shape (B) is crash-free but LOSSY, and every register entry justifies its
# lossiness with "UTF-8 is self-synchronising, so ASCII bytes always decode to
# themselves". That argument is CODEC-SPECIFIC, and the verdict must not accept
# codecs it is false for. Measured on the locked interpreter, decoding
# `b"\x82" + b"UPDATE_INDEPENDENT_CORPUS"` with errors="replace" -- i.e. one
# stray lead byte in front of an ASCII marker a real site asserts on:
#
#     utf-8    -> '�UPDATE_INDEPENDENT_CORPUS'   marker survives
#     cp932    -> '６PDATE_INDEPENDENT_CORPUS'    marker DESTROYED
#     cp936    -> '俇PDATE_INDEPENDENT_CORPUS'    marker DESTROYED
#     utf-16   -> '喂䑐呁...'             marker DESTROYED
#
# The double-byte codecs consume the following ASCII byte as a trail byte, and
# utf-16 re-pairs the whole stream. So a future `encoding="cp932",
# errors="replace"` site would be classified lossy, receive a register entry
# reciting the ASCII-survival argument, and that argument would be FALSE.
#
# Single-byte codecs (cp1252, latin-1) also preserve ASCII, and `utf-8-sig`
# canonicalises separately while behaving identically -- but admitting either
# family buys nothing here and cp1252/latin-1 would reintroduce exactly the
# locale-flavoured decoding this whole guard exists to remove. The allowlist is
# therefore the one codec whose argument is actually proven; anything else must
# extend the argument with its own measurement rather than inherit this one.
_ADMITTED_CODECS = frozenset({"utf-8"})

# Positional arguments beyond the command itself are refused.
#
# `universal_newlines` is a POSITIONAL_OR_KEYWORD parameter of
# `Popen.__init__`, and `run` / `check_output` / `call` / `check_call` forward
# their `*popenargs` straight into `Popen`. So text mode can be selected
# without any keyword at all, and a guard reading `node.keywords` alone makes
# an affirmative "bytes, safe" claim about a call the engine puts in text mode.
#
# Measured on the locked interpreter, `inspect.signature(Popen.__init__)`:
#
#     position 12  universal_newlines   POSITIONAL_OR_KEYWORD
#     position 21  encoding             KEYWORD_ONLY
#     position 22  errors               KEYWORD_ONLY
#     position 23  text                 KEYWORD_ONLY
#
# so `universal_newlines` is the ONLY one of the four reachable positionally --
# and measured end to end, `subprocess.run(cmd, -1, None, None, PIPE, PIPE,
# None, True, False, None, None, True)` returns `stdout='ok\n'` (a `str`: text
# mode), while the same call ending in `False` returns `bytes`.
#
# Resolving positions 2+ against the signature is possible, but refusal is the
# cheaper honest shape and it is the same ground on which a `**kwargs` splat is
# refused: the property cannot be read at the call site. Measured tree-wide
# before choosing it, so the cost is known and not guessed: of 53 watched call
# sites, **every one passes exactly one positional argument** and none uses a
# `*args` splat -- so this refuses nothing that exists today.
_MAX_POSITIONAL_ARGS = 1

# Modules this guard watches, and which of their entry points matter.
# Enumerated in one table so the dispatch is stated once and the docstring's
# claims can be checked against it.
_WATCHED_MODULES: dict[str, frozenset[str]] = {
    "subprocess": _DISPATCH | _LOCALE_ONLY_DISPATCH,
    "os": _OS_LOCALE_DISPATCH,
}

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

    def __init__(
        self,
        relpath: str,
        node: ast.Call,
        dispatch: str,
        qualname: str,
        module: str = "subprocess",
    ):
        self.relpath = relpath
        self.line = node.lineno
        self.dispatch = dispatch
        self.module = module
        self.qualname = qualname
        self.splat = any(kw.arg is None for kw in node.keywords)
        self.kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        # POSITIONAL arguments are part of the decoding contract too -- see
        # `_MAX_POSITIONAL_ARGS`. Reading only `node.keywords` was a measured
        # false-safety claim.
        self.star_args = any(isinstance(arg, ast.Starred) for arg in node.args)
        self.positional = len(node.args)

    def __str__(self) -> str:
        return (
            f"{self.relpath}:{self.line} "
            f"({self.qualname} -> {self.module}.{self.dispatch})"
        )

    def literal(self, key: str):
        """The literal value of a keyword, or the sentinel ``_NOT_LITERAL``."""
        if key not in self.kwargs:
            return None
        try:
            return ast.literal_eval(self.kwargs[key])
        except (ValueError, SyntaxError):
            return _NOT_LITERAL

    def truthiness(self, key: str):
        """``True`` / ``False`` / ``_NOT_LITERAL`` for one text-mode keyword.

        An ABSENT keyword is ``False``: it contributes nothing to the ``or``
        chain the engine evaluates. A present one is judged by its truth value,
        not by identity with ``True`` -- see ``text_mode`` below.
        """
        if key not in self.kwargs:
            return False
        try:
            return bool(ast.literal_eval(self.kwargs[key]))
        except (ValueError, SyntaxError):
            return _NOT_LITERAL

    @property
    def text_mode(self):
        """``True`` / ``False`` / ``_NOT_LITERAL`` -- READ AS THE ENGINE READS IT.

        CPython decides text mode by **truthiness**, in ``Popen.__init__``::

            self.text_mode = encoding or errors or text or universal_newlines

        so this must too. An earlier version of this guard tested
        ``literal(flag) is True`` and therefore **failed open** on every shape
        that is truthy without being the ``True`` singleton. Measured on the
        locked interpreter against the hostile child in the module docstring:

            text=1                -> stdout=None   (engine: text mode)
            text=2                -> stdout=None
            text="yes"            -> stdout=None
            universal_newlines=1  -> stdout=None
            text=0 / False / None -> bytes mode
            encoding=None         -> bytes mode
            errors=None           -> bytes mode

        The identity test called the first four **safe**, which is precisely
        the shape this guard exists to refuse -- the same failure the whole
        change is built around ("an explicit encoding does not remove the
        hazard"), turned on the guard itself.

        Three-valued, and the ordering matters. A keyword that is *definitely
        truthy* settles the question, so an unreadable value elsewhere cannot
        make a certain text-mode call uncertain. Only when nothing is
        definitely truthy does an unreadable value leave the mode unprovable --
        and then this returns ``_NOT_LITERAL`` so the caller can fail closed,
        symmetrically with how ``encoding=`` and ``errors=`` already treat a
        non-literal. ``text=<variable>`` is ordinary Python, not a contrived
        evasion, and this repository has no linter to catch it either.
        """
        states = [self.truthiness(key) for key in _TEXT_MODE_KEYS]
        if any(state is True for state in states):
            return True
        if any(state is _NOT_LITERAL for state in states):
            return _NOT_LITERAL
        return False


_NOT_LITERAL = object()


class _CallCollector(ast.NodeVisitor):
    """Collect subprocess call sites, resolving module aliases and qualnames."""

    def __init__(self, relpath: str):
        self.relpath = relpath
        self.sites: list[_Site] = []
        self._scope: list[str] = []
        # Names bound to a watched MODULE -> the module's real name.
        # `import subprocess`, `import subprocess as sp`, `import os as _os`,
        # and plain rebindings like `sp = subprocess`.
        self._module_aliases: dict[str, str] = {}
        # Names bound directly to a dispatch function
        # (`from subprocess import run [as r]`, `from subprocess import *`)
        # -> (module, real dispatch name).
        self._direct: dict[str, tuple[str, str]] = {}

    # -- binding resolution -------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in _WATCHED_MODULES:
                self._module_aliases[alias.asname or alias.name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in _WATCHED_MODULES:
            watched = _WATCHED_MODULES[node.module]
            for alias in node.names:
                if alias.name == "*":
                    # `from subprocess import *` binds every public name, so
                    # every dispatch name arrives unqualified and un-renamed.
                    for name in watched:
                        self._direct[name] = (node.module, name)
                elif alias.name in watched:
                    self._direct[alias.asname or alias.name] = (node.module, alias.name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """`sp = subprocess` -- a rebinding, not an import, and not exotic.

        Single-level and source-order: the visitor walks a module body in
        order, so a rebinding placed after its import resolves. A rebinding
        that precedes its import, or one built dynamically, does not -- see
        KNOWN BOUNDS in the module docstring.
        """
        source = node.value
        if isinstance(source, ast.Name) and source.id in self._module_aliases:
            real = self._module_aliases[source.id]
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._module_aliases[target.id] = real
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
        resolved = self._resolve(node.func)
        if resolved is not None:
            module, dispatch = resolved
            self.sites.append(
                _Site(
                    self.relpath, node, dispatch,
                    ".".join(self._scope) or "<module>", module,
                )
            )
        self.generic_visit(node)

    def _resolve(self, func: ast.expr) -> tuple[str, str] | None:
        """(module, dispatch) for a watched call, else None."""
        if isinstance(func, ast.Attribute):
            value = func.value
            if isinstance(value, ast.Name) and value.id in self._module_aliases:
                module = self._module_aliases[value.id]
                if func.attr in _WATCHED_MODULES[module]:
                    return module, func.attr
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
    if site.module == "os" and site.dispatch in _OS_LOCALE_DISPATCH:
        return "violation", (
            f"`os.{site.dispatch}` returns a text-mode pipe opened with the "
            "LOCALE encoding and offers no way to pin one, so neither "
            "admissible shape is reachable through it. Use `subprocess.run` in "
            "bytes mode and decode in-process."
        )
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
    if site.star_args:
        return "violation", (
            "a `*args` splat means the number of positional arguments cannot be "
            "read at the call site, so this guard cannot tell whether one of "
            "them is `universal_newlines` (Popen's 12th positional parameter). "
            "Pass the command as a single argument and everything else by "
            "keyword."
        )
    if site.positional > _MAX_POSITIONAL_ARGS:
        return "violation", (
            f"{site.positional} positional arguments. `run`/`check_output`/"
            "`call`/`check_call` forward `*popenargs` into `Popen`, whose 12th "
            "positional parameter is `universal_newlines` -- so text mode can "
            "be selected with no keyword at all, and reading only the keywords "
            "would report this call as bytes mode. Measured: "
            "`run(cmd, -1, None, None, PIPE, PIPE, None, True, False, None, "
            "None, True)` returns a `str`. Pass the command positionally and "
            "everything else by keyword, where both this guard and a human "
            "reader can see it."
        )

    text_mode = site.text_mode
    if text_mode is _NOT_LITERAL:
        return "violation", (
            "whether this call runs in text mode cannot be read at the call "
            "site: one of "
            f"{list(_TEXT_MODE_KEYS)} carries a non-literal value. The engine "
            "decides on TRUTHINESS (`encoding or errors or text or "
            "universal_newlines`), so a truthy value here selects text mode and "
            "the decode moves onto the reader thread. A guard that cannot prove "
            "its property fails closed -- spell the value out at the call site."
        )
    if not text_mode:
        return "bytes", "bytes mode -- the caller decodes, where failure is catchable"

    encoding = site.literal("encoding")
    if not encoding or encoding is _NOT_LITERAL:
        if encoding is _NOT_LITERAL:
            return "violation", (
                "`encoding=` is not a literal, so this guard cannot confirm which "
                "codec is used. Spell it out at the call site."
            )
        return "violation", (
            "text mode with no explicit (or a falsy) `encoding=`: the decode "
            "uses the LOCALE codepage (cp1252 on a Windows checkout), so the "
            "result depends on the machine. Measured: an undecodable byte kills "
            "the reader thread and the caller is handed stdout=None with "
            "returncode=0."
        )
    try:
        canonical = codecs.lookup(encoding).name
    except (LookupError, TypeError):
        return "violation", (
            f"`encoding={encoding!r}` is not a codec Python knows, so this call "
            "would raise at run time rather than decode."
        )
    if canonical not in _ADMITTED_CODECS:
        return "violation", (
            f"`encoding={encoding!r}` (canonically {canonical!r}) is not "
            f"{sorted(_ADMITTED_CODECS)}. A lossy reader-thread decode is only "
            "admitted for a codec where the register's justification actually "
            "holds -- 'ASCII bytes always decode to themselves'. Measured, that "
            "is FALSE for cp932, cp936 and utf-16: a single stray lead byte "
            "consumes the following ASCII character, destroying a marker a test "
            "asserts on. Use utf-8, or drop to bytes mode and decode in-process."
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


# Shapes driven through BOTH the real engine and this guard. One list, so the
# expression the engine evaluates and the expression the guard parses cannot
# drift apart. `EMIT` is a child that writes pure ASCII, so the only question
# asked is str-vs-bytes -- the text-mode decision itself.
_ENGINE_CROSSCHECK_SHAPES = [
    # keyword axis
    "subprocess.run(EMIT, capture_output=True)",
    "subprocess.run(EMIT, capture_output=True, text=True)",
    "subprocess.run(EMIT, capture_output=True, text=1)",
    "subprocess.run(EMIT, capture_output=True, text=2)",
    "subprocess.run(EMIT, capture_output=True, text='yes')",
    "subprocess.run(EMIT, capture_output=True, universal_newlines=1)",
    "subprocess.run(EMIT, capture_output=True, text=0)",
    "subprocess.run(EMIT, capture_output=True, text=False)",
    "subprocess.run(EMIT, capture_output=True, text=None)",
    "subprocess.run(EMIT, capture_output=True, encoding=None)",
    "subprocess.run(EMIT, capture_output=True, errors=None)",
    "subprocess.run(EMIT, capture_output=True, encoding='utf-8')",
    "subprocess.run(EMIT, capture_output=True, errors='replace')",
    # POSITIONAL axis -- the round-2 blocking finding. `universal_newlines` is
    # Popen's 12th positional parameter, so these select text mode with no
    # keyword at all. `capture_output` cannot be combined with an explicit
    # stdout/stderr, hence PIPE passed positionally too.
    "subprocess.run(EMIT, -1, None, None, subprocess.PIPE, subprocess.PIPE,"
    " None, True, False, None, None, True)",
    "subprocess.run(EMIT, -1, None, None, subprocess.PIPE, subprocess.PIPE,"
    " None, True, False, None, None, False)",
]


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
        # --- POSITIONAL axis: text mode with no keyword at all -------------
        # universal_newlines is Popen's 12th positional parameter, forwarded
        # through run/check_output/call/check_call via *popenargs.
        (
            "import subprocess\nsubprocess.run(c, -1, None, None, subprocess.PIPE,"
            " subprocess.PIPE, None, True, False, None, None, True)",
            "violation",
        ),
        (
            "import subprocess\nsubprocess.run(c, -1, None, None, subprocess.PIPE,"
            " subprocess.PIPE, None, True, False, None, None, False)",
            "violation",
        ),
        # two positionals is already unreadable, and refused
        ("import subprocess\nsubprocess.run(c, -1)", "violation"),
        # ...but the ordinary one-positional call must stay clean
        ("import subprocess\nsubprocess.run(c, capture_output=True)", "bytes"),
        ("import subprocess\nsubprocess.Popen(c, stdout=P)", "bytes"),
        # a *args splat hides the positional count entirely
        ("import subprocess\nsubprocess.run(*parts)", "violation"),
        ("import subprocess\nsubprocess.run(c, *rest, text=True)", "violation"),
        # the ones that can never be made safe
        ("import subprocess\nsubprocess.getoutput(c)", "violation"),
        ("import subprocess\nsubprocess.getstatusoutput(c)", "violation"),
        ("import os\nos.popen(c)", "violation"),
        # unprovable shapes fail closed
        ("import subprocess\nsubprocess.run(c, **kw)", "violation"),
        ("import subprocess\nsubprocess.run(c, text=True, encoding=E)", "violation"),
        (
            "import subprocess\nsubprocess.run(c, text=True, encoding='utf-8', errors=E)",
            "violation",
        ),
        # aliasing is not an escape hatch, for the forms KNOWN BOUNDS claims
        ("import subprocess as sp\nsp.run(c, text=True)", "violation"),
        ("from subprocess import run\nrun(c, text=True)", "violation"),
        ("from subprocess import run as r\nr(c, text=True)", "violation"),
        ("import subprocess as sp\nsp.Popen(c, text=True)", "violation"),
        ("from subprocess import *\nrun(c, text=True)", "violation"),
        ("import subprocess\nsp = subprocess\nsp.run(c, text=True)", "violation"),
        ("import os as _o\n_o.popen(c)", "violation"),
        # --- TRUTHINESS, as the engine reads it -----------------------------
        # Measured: each of these puts the ENGINE in text mode (stdout=None).
        # The identity test `literal(flag) is True` called them all bytes-safe.
        ("import subprocess\nsubprocess.run(c, text=1)", "violation"),
        ("import subprocess\nsubprocess.run(c, text=2)", "violation"),
        ("import subprocess\nsubprocess.run(c, text='yes')", "violation"),
        ("import subprocess\nsubprocess.run(c, universal_newlines=1)", "violation"),
        # ...and each of these leaves it in BYTES mode, so they must not red.
        ("import subprocess\nsubprocess.run(c, text=0)", "bytes"),
        ("import subprocess\nsubprocess.run(c, text=False)", "bytes"),
        ("import subprocess\nsubprocess.run(c, text=None)", "bytes"),
        ("import subprocess\nsubprocess.run(c, encoding=None)", "bytes"),
        ("import subprocess\nsubprocess.run(c, errors=None)", "bytes"),
        # A non-literal cannot be proved falsy, so the mode is unprovable.
        ("import subprocess\nsubprocess.run(c, text=flag)", "violation"),
        ("import subprocess\nsubprocess.run(c, text=True if x else False)", "violation"),
        ("import subprocess\nsubprocess.run(c, universal_newlines=flag)", "violation"),
        # ...but a definitely-truthy keyword settles the mode, so an unreadable
        # value elsewhere must not turn a provably-safe call into a refusal.
        (
            "import subprocess\nsubprocess.run(c, text=flag, encoding='utf-8', errors='replace')",
            "lossy",
        ),
        # --- CODEC restriction (shape B's argument is UTF-8 specific) --------
        ("import subprocess\nsubprocess.run(c, encoding='utf8', errors='replace')", "lossy"),
        ("import subprocess\nsubprocess.run(c, encoding='UTF-8', errors='replace')", "lossy"),
        ("import subprocess\nsubprocess.run(c, encoding='cp932', errors='replace')", "violation"),
        ("import subprocess\nsubprocess.run(c, encoding='cp936', errors='replace')", "violation"),
        ("import subprocess\nsubprocess.run(c, encoding='utf-16', errors='replace')", "violation"),
        ("import subprocess\nsubprocess.run(c, encoding='latin-1', errors='replace')", "violation"),
        (
            "import subprocess\nsubprocess.run(c, encoding='not-a-codec', errors='replace')",
            "violation",
        ),
    ]
    for source, expected in cases:
        verdict, why = _classify_source(source)
        assert verdict == expected, (
            f"{source!r} classified {verdict!r}, expected {expected!r} ({why})"
        )
    assert len(cases) >= 47, f"the classifier table lost cases ({len(cases)})"


def test_the_classifier_agrees_with_the_REAL_ENGINE_about_text_mode() -> None:
    """Cross-check the truthiness rule against ``subprocess`` itself.

    Every table above is a CLAIM about the engine, and the blocking defect this
    replaced was exactly a claim that had drifted from it: the guard tested
    ``literal(flag) is True`` while ``Popen.__init__`` evaluates
    ``encoding or errors or text or universal_newlines``. A table can be wrong
    in the same direction as the code it checks; the engine cannot.

    So each shape is really run. The probe emits pure ASCII, which every codec
    decodes, so the question asked is only "did the engine hand back ``str`` or
    ``bytes``" -- the text-mode decision itself, with no dependence on the
    platform-specific way an *undecodable* byte fails (a dead reader thread and
    ``stdout=None`` on Windows; a raise in the caller on POSIX).

    The experiment itself runs in a CHILD interpreter, because varying the
    keywords means splatting a dict -- a shape this guard refuses, and rightly:
    it caught this very test when it was written the other way. The call made
    from here is therefore an ordinary compliant bytes-mode one.
    """
    experiment = textwrap.dedent(
        """
        import json, subprocess, sys

        EMIT = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok\\\\n')"]
        observed = []
        for source in json.loads(sys.stdin.read()):
            done = eval(source, {"subprocess": subprocess, "EMIT": EMIT})
            observed.append([source, isinstance(done.stdout, str)])
        print("RESULT " + json.dumps(observed))
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", experiment],
        input=json.dumps(_ENGINE_CROSSCHECK_SHAPES).encode("utf-8"),
        capture_output=True,
        timeout=300,
    )
    assert completed.returncode == 0, (
        "the engine cross-check child failed:\n"
        + completed.stderr.decode("utf-8", "replace")
    )
    payload = [
        line
        for line in completed.stdout.decode("utf-8").splitlines()
        if line.startswith("RESULT ")
    ]
    assert payload, (
        "the child produced no RESULT line, so nothing was measured:\n"
        + completed.stdout.decode("utf-8", "replace")
    )
    observed = json.loads(payload[-1][len("RESULT "):])
    assert len(observed) == len(_ENGINE_CROSSCHECK_SHAPES), (
        "the child did not report on every shape it was given"
    )

    false_safety = []
    disagreements = []
    for source, engine_text_mode in observed:
        collector = _CallCollector("<engine-crosscheck>")
        collector.visit(ast.parse("import subprocess\n" + source))
        assert len(collector.sites) == 1, source
        site = collector.sites[0]
        verdict, _why = _verdict(site)

        # THE SAFETY INVARIANT. Whatever else the guard concludes, it must never
        # affirmatively call a text-mode call "bytes" -- that is a false claim
        # of safety, and it is exactly what reading only `node.keywords` did to
        # a positionally-selected `universal_newlines`.
        if engine_text_mode and verdict == "bytes":
            false_safety.append(f"  {source}\n      engine: TEXT MODE, guard: 'bytes'")

        # PRECISION, for the shapes the guard can fully read. A call it refuses
        # as unprovable is allowed to disagree -- refusing is not a claim.
        provable = (
            not site.splat
            and not site.star_args
            and site.positional <= _MAX_POSITIONAL_ARGS
        )
        if provable and site.text_mode is not engine_text_mode:
            disagreements.append(
                f"  {source}\n      engine text_mode={engine_text_mode}, "
                f"guard says {site.text_mode!r}"
            )

    assert not false_safety, (
        "the guard made a FALSE SAFETY CLAIM -- it called a call the engine "
        "runs in text mode 'bytes':\n" + "\n".join(false_safety)
    )
    assert not disagreements, (
        "the guard does not read text mode the way the engine applies it:\n"
        + "\n".join(disagreements)
        + "\n\nCPython: `self.text_mode = encoding or errors or text or "
        "universal_newlines` -- truthiness, not identity with True."
    )
    # Both axes must actually be exercised, or this proves less than it looks.
    assert any(s.count(",") >= 11 for s, _ in observed), (
        "no positional shape was measured -- the cross-check is blind on the "
        "axis the round-2 review found the defect on"
    )
    assert len(observed) >= 15, f"the engine cross-check lost shapes ({len(observed)})"


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
    decoding = [s for s in sites if s.text_mode is True]
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
