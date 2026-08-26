"""Guard on ``ci/workflow_scope.py`` -- the ONE workflow enumeration.

Its docstring claims that EVERY reader in this repository takes its scope from
it. That sentence used to carry a hand-typed cardinality ("all SIX readers"),
which is an inventory that cannot drift -- and an inventory that cannot drift
cannot detect drift. The claim is checked here instead, over the tracked tree,
so a seventh reader that enumerates the directory itself reds on the commit that
adds it rather than quietly making the docstring false.

THE DETECTOR RESOLVES A BINDING, AND HAD TO
===========================================
The first version of this sweep read only the source text of the enumerator's
RECEIVER, so it caught ``(root / ".github" / "workflows").iterdir()`` and missed

    directory = root / ".github" / "workflows"
    for path in sorted(directory.iterdir()):

-- which is the shape BOTH migrated checkers actually shipped. A guard that
cannot see the defect it was written for is worse than no guard, because it
reports clean. Its own offender probe caught that before it was committed, which
is the argument for writing the probes down as test data rather than trusting
the matcher by eye.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import subprocess

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_SCOPE_MODULE = _REPO_ROOT / "ci" / "workflow_scope.py"

# The calls that enumerate a directory. `os.walk` is deliberately absent: it is
# the neutrality gate's announced fallback for a tree git cannot describe, and
# `ci/check_pytest_collection.py` uses it for the CONFIG scan, neither of which
# is a workflow enumeration.
_ENUMERATORS = frozenset({"iterdir", "glob", "rglob"})

# The path component that makes a directory THE workflow directory.
_MARKER = "workflows"


def _load_scope():
    spec = importlib.util.spec_from_file_location(
        "_workflow_scope_under_test", _SCOPE_MODULE
    )
    assert spec is not None and spec.loader is not None, _SCOPE_MODULE
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow_directory_enumerations(source: str) -> list[tuple[int, str]]:
    """Every ``<workflow dir>.iterdir()``-shaped call in *source*.

    Returns ``(line, rendered receiver)`` pairs. AST, never grep: this module's
    own prose names all three enumerators, and a comment must not be able to
    satisfy -- or defeat -- the sweep.

    A name assigned from an expression that mentions the marker is treated as
    holding the directory, so the two-statement spelling both migrated checkers
    used is resolved rather than missed. Over-flagging is the safe direction
    here and is bounded by the near-miss probe below: the cost of a false
    positive is one deliberate exemption, the cost of a false negative is a
    second enumeration nobody notices.
    """
    tree = ast.parse(source)

    # Every `name = <expr>` in the file, as (name, source of expr).
    assignments: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            targets, value = [node.target], node.value
        if value is None:
            continue
        segment = ast.get_source_segment(source, value) or ""
        for target in targets:
            if isinstance(target, ast.Name):
                assignments.append((target.id, segment))

    # Fixed point, so a REBINDING resolves: `d = <...workflows...>` then
    # `d2 = d`. A single pass sees only the first, and the probe below caught
    # exactly that -- the same transitive gap the subprocess collector had.
    bound: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, segment in assignments:
            if name in bound:
                continue
            if _MARKER in segment or segment.strip() in bound:
                bound.add(name)
                changed = True

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in _ENUMERATORS:
            continue
        receiver = ast.get_source_segment(source, func.value) or ""
        is_marked = _MARKER in receiver
        is_bound = isinstance(func.value, ast.Name) and func.value.id in bound
        if is_marked or is_bound:
            found.append((node.lineno, f"{receiver}.{func.attr}(...)"))
    return found


def _tracked_python_files() -> list[pathlib.Path]:
    """Scope from git, never a walk -- the rule this module exists to enforce.

    ``-z`` and bytes mode for the reasons ``ci/workflow_scope.py`` records: git
    quotes a non-ASCII path without it, and a text-mode read moves the decode to
    a reader thread where its failure is invisible.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z", "*.py"],
        cwd=_REPO_ROOT, capture_output=True, check=False,
    )
    assert result.returncode == 0, (
        "`git ls-files` failed, so this guard cannot know what to sweep and "
        "fails closed rather than checking nothing: "
        f"{result.stderr.decode('utf-8', 'replace').strip()}"
    )
    names = result.stdout.decode("utf-8").split("\0")
    return [_REPO_ROOT / name for name in names if name]


def test_nothing_else_enumerates_the_workflow_directory() -> None:
    """``ci/workflow_scope.py`` is the only file that lists that directory.

    This is the mechanical form of that module's "every reader takes its scope
    from here". It replaced a hand-typed "all SIX readers", which nothing
    recounted and which a seventh reader would have made silently false.
    """
    offenders: list[str] = []
    for path in _tracked_python_files():
        if path == _SCOPE_MODULE:
            continue
        source = path.read_bytes().decode("utf-8")
        relative = path.relative_to(_REPO_ROOT).as_posix()
        offenders += [
            f"{relative}:{line}: {rendered}"
            for line, rendered in workflow_directory_enumerations(source)
        ]

    assert not offenders, (
        "file(s) enumerate the workflow directory themselves instead of asking "
        "ci/workflow_scope.py:\n  " + "\n  ".join(offenders)
        + "\nA second enumeration is a second answer, and the two drift: the "
        "migrated checkers both read the right SUFFIXES and still lacked "
        "--exclude-standard, --others and a fail-closed reply."
    )


def test_the_detector_sees_the_shape_that_actually_shipped() -> None:
    """The sweep is worth nothing if its matcher has stopped matching.

    The first probe is the VERBATIM shape both checkers carried before the
    migration -- bound to a name on one line, enumerated on the next. The first
    version of this detector read only the receiver's source text and missed it,
    reporting clean over the exact defect it exists to catch.
    """
    shipped = (
        'directory = root / ".github" / "workflows"\n'
        "for path in sorted(directory.iterdir()):\n"
        "    pass\n"
    )
    inline = 'for p in (root / ".github" / "workflows").iterdir():\n    pass\n'
    globbed = 'list((repo / ".github" / "workflows").glob("*.yml"))\n'
    rebound = (
        'd = root / ".github" / "workflows"\n'
        "d2 = d\n"
        "list(d2.rglob('*'))\n"
    )

    for label, source in (
        ("the shape that shipped", shipped),
        ("the inline spelling", inline),
        ("a glob rather than iterdir", globbed),
        ("a rebinding", rebound),
    ):
        assert workflow_directory_enumerations(source), (
            f"the detector no longer recognises {label}:\n{source}"
        )


def test_the_detector_does_not_fire_on_an_unrelated_listing() -> None:
    """The near-miss half. A guard that flags everything is not a guard, and
    this repository already runs ``glob``/``rglob`` over other directories --
    ``tests/`` in the Swiss-job guard, the tree in the neutrality gate's
    announced fallback."""
    for label, source in (
        ("a tests/ glob", 'for p in (root / "tests").glob("**/*.py"):\n    pass\n'),
        ("an os.walk", "import os\nfor a, b, c in os.walk(root):\n    pass\n"),
        ("a bare rglob", 'list(root.rglob("*"))\n'),
        (
            "the word in a comment only",
            "# workflows are enumerated elsewhere\nlist(other.iterdir())\n",
        ),
    ):
        assert not workflow_directory_enumerations(source), (
            f"the detector fires on {label}, which is not a workflow enumeration"
        )


def test_the_sweep_actually_examined_the_tree() -> None:
    """"Nothing else enumerates it" is vacuously true of a sweep of no files."""
    files = _tracked_python_files()
    assert len(files) >= 50, (
        f"only {len(files)} tracked *.py files were enumerated -- the scope "
        "derivation is broken, not the tree clean"
    )


def test_the_module_still_asks_git_for_both_suffixes() -> None:
    """The two properties the whole module exists for, pinned directly."""
    scope = _load_scope()
    assert scope.WORKFLOW_SUFFIXES == frozenset({".yml", ".yaml"})
    source = _SCOPE_MODULE.read_text(encoding="utf-8")
    for flag in ("--cached", "--others", "--exclude-standard", "-z"):
        assert f'"{flag}"' in source, f"the enumeration no longer passes {flag}"
