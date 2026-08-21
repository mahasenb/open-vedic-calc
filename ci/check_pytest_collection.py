#!/usr/bin/env python3
"""Refuse a pytest configuration that removes tests from every run in this repo.

WHY THIS IS A SCRIPT AND NOT A TEST
-----------------------------------
``ci/tests/test_swiss_ephemeris_job.py`` guards the accuracy job's *run line* and
its *environment*, and it does so as a pytest test. That works for every vector
that lives in the workflow file. It cannot work for this one, and the reason is
worth stating precisely rather than discovering again later:

**a pytest test cannot police a pytest setting that stops pytest running tests.**

``addopts`` in ``pyproject.toml`` applies to EVERY pytest invocation in this
repository — the fast ``test`` job, the ``swiss-ephemeris`` accuracy job, and the
``ci/tests/`` guard suite alike. Measured on this branch, appending three lines to
``pyproject.toml`` and running ``pytest ci/tests/ -q``:

  ``addopts = "-k not_corpus"``      -> exit 5, 88 deselected   (pytest itself fails)
  ``addopts = "-k 'not corpus'"``    -> exit 1                  (the pytest guard reds)
  ``addopts = "-m 'not nothing'"``   -> exit 1                  (the pytest guard reds)
  ``addopts = "-k 'not narrow'"``    -> **exit 0**, 87 passed, 1 deselected
  ``addopts = "--collect-only"``     -> **exit 0**, 88 collected, 0 executed

The last two are fail-open. ``--collect-only`` is the worst of them: it deselects
nothing, so every module is still collected and every reachability check still
answers "yes, the job reaches it" — while not one test body executes and the whole
workflow reports green. The pytest-resident guard cannot catch either case, because
under both it is the thing that did not run.

So this check runs as a plain ``python`` step. Nothing in pytest's configuration can
deselect, skip, or no-op it.

WHAT IT REFUSES
---------------
1. A narrowing ``addopts`` in ``pyproject.toml``: anything that deselects tests
   (``-k``, ``-m``, ``--ignore``, ``--ignore-glob``, ``--deselect``) or that collects
   without running them (``--collect-only``, ``--co``). A non-narrowing ``addopts``
   (``-q``, ``--strict-markers``, ...) is perfectly legitimate and is not flagged —
   this is about what removes assertions from a run, not about whether addopts is used.
2. The presence of any OTHER file pytest reads ini options from. pytest honours
   ``pytest.ini``, ``tox.ini`` and ``setup.cfg`` as well; only ``pyproject.toml``
   exists here and only it is parsed, so a new one is refused rather than silently
   uncovered. Extending this checker to parse it is the deliberate act that clears
   the refusal.

PARSED, NEVER GREPPED. ``# addopts = "-k not_corpus"`` is a comment and must stay
invisible; a grep would red on it and a reader would then learn to distrust this
check. The TOML is parsed and the value is shell-split the way pytest splits it.

Usage
-----
    python ci/check_pytest_collection.py            # 0 clean, 1 refused
    python ci/check_pytest_collection.py --self-test

Exit codes: 0 = no narrowing configuration; 1 = refused (with the reason printed).
"""
from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

try:  # Python >= 3.11 — the interpreter this repo pins in .python-version
    import tomllib
except ModuleNotFoundError:  # Python 3.10 — still within the requires-python floor
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Narrowing flags that DESELECT tests, and that consume a value. Kept separate
# because a value-taking flag's argument must never be mistaken for a positional
# target when a caller parses a command line (see ci/tests/test_swiss_ephemeris_job.py).
VALUE_TAKING_DESELECT_FLAGS = ("--ignore", "--ignore-glob", "--deselect", "-k", "-m")

# Narrowing flags that deselect NOTHING and run nothing. `pytest --collect-only`
# collects every module the accuracy gate needs and executes not one test body.
COLLECTION_ONLY_FLAGS = ("--collect-only", "--co")

# Everything that costs a run its assertions, by either mechanism. THIS IS THE ONE
# DEFINITION — ci/tests/test_swiss_ephemeris_job.py imports it rather than keeping a
# second copy, so a flag added here is covered by both readers at once.
NARROWING_FLAGS = VALUE_TAKING_DESELECT_FLAGS + COLLECTION_ONLY_FLAGS

# Files pytest reads ini options (and therefore `addopts`) from, other than
# pyproject.toml. None exists in this repo; any that appears is refused.
PYTEST_CONFIG_FILENAMES = ("pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg")


def addopts_tokens(document: dict) -> list[str]:
    """``[tool.pytest.ini_options].addopts`` from a PARSED pyproject document.

    pytest accepts either a string (which it shell-splits) or a list of arguments.
    Each list entry is shell-split too, so a single entry holding ``"-k not_corpus"``
    is read as the two arguments pytest would actually see rather than as one opaque
    token that no flag check would ever match.
    """
    ini_options = (
        ((document.get("tool") or {}).get("pytest") or {}).get("ini_options") or {}
    )
    addopts = ini_options.get("addopts")
    if addopts is None:
        return []
    entries = addopts if isinstance(addopts, list) else [addopts]
    tokens: list[str] = []
    for entry in entries:
        tokens.extend(shlex.split(str(entry), comments=True))
    return tokens


def narrowing_addopts(tokens: list[str]) -> list[str]:
    """The narrowing flags among a parsed ``addopts``, by flag NAME.

    Split on ``=`` first so ``--ignore=path`` and ``--ignore path`` are both caught,
    while ``--color=no`` and a positional path that merely contains a flag's spelling
    are not.
    """
    found: list[str] = []
    for token in tokens:
        if not token.startswith("-"):
            continue
        name = token.split("=", 1)[0]
        if name in NARROWING_FLAGS:
            found.append(name)
    return found


def unparsed_config_files(root: Path) -> list[str]:
    """Config files pytest would honour that this checker does not parse."""
    return [name for name in PYTEST_CONFIG_FILENAMES if (root / name).is_file()]


def problems(root: Path) -> list[str]:
    """Every reason to refuse, as operator-readable lines. Empty means clean."""
    found: list[str] = []

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        tokens = addopts_tokens(document)
        narrowing = narrowing_addopts(tokens)
        if narrowing:
            found.append(
                f"pyproject.toml [tool.pytest.ini_options] addopts carries narrowing "
                f"flag(s) {narrowing} (parsed as {tokens}).\n"
                f"  That applies to EVERY pytest invocation in this repo — including "
                f"the swiss-ephemeris accuracy job — so it removes assertions from the "
                f"suite without touching a single workflow file.\n"
                f"  If tests genuinely need to be excluded, exclude them on the run "
                f"line of the specific job that needs it, where the diff shows it and "
                f"ci/tests/test_swiss_ephemeris_job.py can see it."
            )

    for name in unparsed_config_files(root):
        found.append(
            f"{name} exists, and pytest reads `addopts` from it. This checker only "
            f"parses pyproject.toml, so it would report clean while a narrowing "
            f"addopts sat in a file it never opens. Extend addopts_tokens() and "
            f"PYTEST_CONFIG_FILENAMES to parse {name}, deliberately."
        )

    return found


def _self_test() -> int:
    """Prove the checker still discriminates before it certifies the real repo.

    Same ordering rule as ci/fetch_swiss_ephemeris.py --self-test: a verifier that
    has stopped verifying must never get as far as certifying the real thing.
    """
    narrowing_fixtures = (
        '[tool.pytest.ini_options]\naddopts = "-k not_corpus"\n',
        "[tool.pytest.ini_options]\naddopts = \"-k 'not corpus'\"\n",
        "[tool.pytest.ini_options]\naddopts = \"-m 'not accuracy'\"\n",
        '[tool.pytest.ini_options]\naddopts = "--collect-only"\n',
        '[tool.pytest.ini_options]\naddopts = "--co"\n',
        '[tool.pytest.ini_options]\naddopts = "--ignore=tests/test_swiss_ephemeris.py"\n',
        '[tool.pytest.ini_options]\naddopts = ["-q", "--deselect", "tests/x.py"]\n',
        '[tool.pytest.ini_options]\naddopts = ["-k not_corpus"]\n',
    )
    clean_fixtures = (
        "",
        "[tool.pytest.ini_options]\n",
        '[tool.pytest.ini_options]\naddopts = "-q --strict-markers"\n',
        '[tool.pytest.ini_options]\naddopts = ["-q", "--color=no"]\n',
        '[tool.pytest.ini_options]\n# addopts = "-k not_corpus"\n',
        '[tool.pytest.ini_options]\nmarkers = ["accuracy: needs swiss data"]\n',
    )

    failures: list[str] = []
    for text in narrowing_fixtures:
        if not narrowing_addopts(addopts_tokens(tomllib.loads(text))):
            failures.append(f"NOT DETECTED as narrowing: {text!r}")
    for text in clean_fixtures:
        if narrowing_addopts(addopts_tokens(tomllib.loads(text))):
            failures.append(f"FALSELY flagged as narrowing: {text!r}")

    if failures:
        print("self-test FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(
        f"self-test OK ({len(narrowing_fixtures)} narrowing and {len(clean_fixtures)} "
        "clean fixtures discriminated)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refuse a narrowing pytest configuration.")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="prove the detector still discriminates; check nothing real",
    )
    arguments = parser.parse_args(argv)

    if arguments.self_test:
        return _self_test()

    found = problems(REPO_ROOT)
    if found:
        print(
            "REFUSED: this repository's pytest configuration removes tests from every "
            "run.\n",
            file=sys.stderr,
        )
        for problem in found:
            print(f"  * {problem}\n", file=sys.stderr)
        return 1

    print("OK: pytest configuration does not narrow collection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
