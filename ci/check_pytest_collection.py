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

3. A narrowing ``PYTEST_ADDOPTS`` in **this process's own environment**. A
   workflow- or job-level ``env:`` block lands here too, so an injection that
   silences every pytest run in the job also kills this step — loudly, and before
   the suite it would have silenced. ``--self-test`` refuses first for the same
   reason: it must not certify a detector from inside an already-narrowed
   environment.

4. ``PYTEST_ADDOPTS`` **anywhere in the workflow files** — every job, all three
   ``env:`` levels, and **any mention at all inside a ``run:`` body**. This is the
   placement (3) cannot see: a step-level ``env:``, or a ``$GITHUB_ENV`` write, on
   a *sibling* step never enters this process.

   (3) is narrowing-only; (4) is presence-only. Deliberate: a contributor's shell
   may legitimately carry ``PYTEST_ADDOPTS=-q``, and redding on that would teach
   people to distrust this check — but a workflow file has no legitimate reason to
   name the variable at all, and judging an arbitrary value's narrowing-ness means
   reimplementing pytest's argument parser.

   THE RUN-BODY RULE IS PRESENCE BECAUSE MODELLING FAILED. An earlier version
   analysed argv position — a leading ``VAR=value`` prefix, or an argument to
   ``export``. Both of those are shell-LOCAL: every ``run:`` is a fresh shell, so
   neither escapes its own body. The form that actually reaches a later step is
   GitHub Actions' own cross-step mechanism, ``echo "VAR=value" >> "$GITHUB_ENV"``,
   where the assignment is an argument to ``echo`` and the position check said no.
   Measured with one such line appended to the swiss job's install step: this
   checker exited 0 "OK", all 149 guard tests passed, and the accuracy step would
   then have run under ``--collect-only``. See ``run_body_names_addopts``.

KNOWN BOUND, stated rather than left implicit: this scans the workflow files in
``.github/workflows``. A composite or reusable action pulled in with ``uses:`` can
write ``$GITHUB_ENV`` from a file outside this repository, and nothing here reads
that. Closing it would mean resolving and fetching third-party action source — a
different kind of check. Recorded as a bound of this control, not a claim of
completeness.

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
import os
import shlex
import sys
from collections.abc import Mapping
from pathlib import Path

try:  # Python >= 3.11 — the interpreter this repo pins in .python-version
    import tomllib
except ModuleNotFoundError:  # Python 3.10 — still within the requires-python floor
    import tomli as tomllib

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - declared in the dev extras
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

# The environment variable pytest reads extra arguments from. Round 1 detected this
# at all three GitHub Actions `env:` levels — but from inside a pytest test, which
# the variable itself disables. See the PYTEST_ADDOPTS section below.
ADDOPTS_ENV = "PYTEST_ADDOPTS"

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


# ---------------------------------------------------------------------------
# Shell tokenising — the single definition, imported by
# ci/tests/test_swiss_ephemeris_job.py rather than copied.
# ---------------------------------------------------------------------------


def join_shell_continuations(run: str) -> list[str]:
    """Split a ``run:`` body into LOGICAL lines, joining backslash-continuations.

    ``shlex.split`` RAISES on a line ending in a lone backslash
    (``ValueError: No escaped character``); tokenising line-by-line therefore drops
    the first line of every continued command and reads the rest as commands of
    their own. Only an ODD number of trailing backslashes continues a line — a
    trailing ``\\\\`` is an escaped backslash, a real final token.
    """
    logical: list[str] = []
    pending = ""
    for physical in run.splitlines():
        line = physical.rstrip("\r")
        trailing_backslashes = len(line) - len(line.rstrip("\\"))
        if trailing_backslashes % 2 == 1:
            pending += line[:-1]
            continue
        logical.append(pending + line)
        pending = ""
    if pending:
        # A continuation with nothing after it. Keep what was accumulated rather
        # than dropping the command — dropping it is the failure this removes.
        logical.append(pending)
    return logical


def shell_commands(run: str) -> list[list[str]]:
    """Tokenise a ``run:`` body into individual shell commands (argv token lists).

    A substring search cannot answer "does this invoke pytest": ``echo "pytest
    tests/"`` contains the string and runs nothing. shlex puts the words in argv
    positions, so a quoted string collapses to ONE token.
    """
    commands: list[list[str]] = []
    for line in join_shell_continuations(run):
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            # Unbalanced quoting — treat the line as opaque rather than guessing.
            continue
        current: list[str] = []
        for token in tokens:
            if token in {"&&", "||", ";", "|", "&"}:
                if current:
                    commands.append(current)
                current = []
                continue
            current.append(token)
        if current:
            commands.append(current)
    return commands


def run_body_names_addopts(run: str) -> bool:
    """True when a ``run:`` body mentions ``PYTEST_ADDOPTS`` at all. PRESENCE, not
    semantics — and the plainest substring test, deliberately.

    TOKENISING WAS THE BUG (PR #66 round-2 review, blocking finding). The previous
    version of this check analysed argv POSITION: it accepted a leading
    ``VAR=value`` prefix or an argument to ``export`` as an assignment, and rejected
    anything else as "just an echo". Both forms it recognised are shell-LOCAL —
    every ``run:`` is a fresh shell, so neither escapes its own body. The form that
    actually reaches a later step is GitHub Actions' own cross-step mechanism::

        echo "PYTEST_ADDOPTS=--collect-only" >> "$GITHUB_ENV"

    ``shell_commands`` renders that as
    ``['echo', 'PYTEST_ADDOPTS=--collect-only', '>>', '$GITHUB_ENV']`` — the
    assignment is an ARGUMENT to echo, so the position check returned False. Measured
    with that one line appended to the swiss job's install step: the checker exited 0
    "OK", all 149 guard tests passed, and the accuracy step would then have run under
    ``--collect-only`` (912 collected, 0 executed, ``REQUIRE_SWISS_EPHEMERIS`` never
    evaluating). The checker caught the weak forms and missed the effective one.

    So this does not model shell semantics at all. Telling a harmless echo from a
    poisoning one requires modelling redirection, heredocs, ``printf``, ``tee``,
    variable indirection — an open surface, and modelling is precisely what failed.
    A ``run:`` body has no more legitimate reason to name this variable than an
    ``env:`` block does; if one ever genuinely needs to, that is an exception to
    record here deliberately, not a rule to relax.
    """
    return ADDOPTS_ENV in run


# ---------------------------------------------------------------------------
# PYTEST_ADDOPTS — TWO arms, because neither alone is enough
# ---------------------------------------------------------------------------
#
# THE ROUND-1 DEFECT, measured and reproduced (PR #66 review, blocking finding).
# Round 1 moved the pyproject arm out of pytest on the argument that "a pytest test
# cannot police a pytest setting that stops pytest running tests" — then left the
# PYTEST_ADDOPTS detector as a pytest test. The same measurement defeats it:
#
#   PYTEST_ADDOPTS='--collect-only' pytest ci/tests/ -q
#       -> exit 0, 119 collected, 0 executed. The guard for this exact vector
#          (test_swiss_job_does_not_inject_pytest_addopts) is among them.
#   PYTEST_ADDOPTS='--collect-only' pytest tests/ -q   with REQUIRE_SWISS_EPHEMERIS=1
#       -> exit 0, 912 collected, 0 executed. The fail-closed flag never evaluates:
#          fixtures do not run during collection, so the accuracy gate asserts
#          nothing and reports success.
#   PYTEST_ADDOPTS='--collect-only' python ci/check_pytest_collection.py
#       -> "OK", exit 0. The checker never read its own environment.
#
# One `env:` block in test.yml and the whole workflow is green having asserted
# nothing, anywhere.
#
#   ARM 1 — RUNTIME. Read what the engine applies: this script runs as a plain
#   python step in the same job, so a workflow- or job-level injection lands in
#   THIS process's environment. It cannot be deselected; it is not a test.
#   Narrowing-only, because a contributor's shell may legitimately carry `-q`.
#
#   ARM 2 — DECLARATION. A step-level `env:` on a SIBLING pytest step never
#   reaches this process, so arm 1 cannot see it. This one parses the workflow
#   files themselves. PRESENCE, not narrowing-ness: a workflow has no legitimate
#   reason to declare the variable at all, and judging an arbitrary value's
#   narrowing-ness means reimplementing pytest's argument parser.


def narrowing_env_addopts(environ: Mapping[str, str]) -> list[str]:
    """Narrowing flags in a ``PYTEST_ADDOPTS`` value from the live environment."""
    raw = environ.get(ADDOPTS_ENV)
    if not raw:
        return []
    try:
        tokens = shlex.split(raw, comments=True)
    except ValueError:
        # Unbalanced quoting. pytest would fail on it too, but refuse rather than
        # guess — an unreadable value is not an absent one.
        return [raw]
    return narrowing_addopts(tokens)


def workflow_addopts_declarations(root: Path) -> list[str]:
    """Every ``PYTEST_ADDOPTS`` declaration across the workflow files.

    All three GitHub Actions ``env:`` levels (workflow, job, step) plus inline
    shell assignments in ``run:`` bodies. Every job in every workflow file, not
    just the accuracy job: a declaration anywhere narrows the pytest run in that
    job, and this checker is the only thing outside pytest that can see it.
    """
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        return []

    if yaml is None:  # pragma: no cover - pyyaml is in the dev extras
        return [
            "PyYAML is not installed, so the workflow files could not be scanned for "
            f"{ADDOPTS_ENV} declarations. Refusing rather than reporting clean about "
            "files nothing opened."
        ]

    found: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.suffix not in {".yml", ".yaml"} or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            found.append(
                f"{relative} could not be parsed as YAML ({exc.__class__.__name__}), "
                f"so it could not be checked for {ADDOPTS_ENV}. A workflow this "
                "checker cannot read is exactly where a declaration would hide."
            )
            continue
        if not isinstance(document, dict):
            found.append(
                f"{relative} did not parse to a mapping, so it could not be checked "
                f"for {ADDOPTS_ENV}."
            )
            continue

        workflow_env = document.get("env")
        if isinstance(workflow_env, dict) and ADDOPTS_ENV in workflow_env:
            found.append(
                f"{relative} declares {ADDOPTS_ENV} at WORKFLOW level "
                f"({workflow_env[ADDOPTS_ENV]!r}) — it reaches every job in the file."
            )

        jobs = document.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            job_env = job.get("env")
            if isinstance(job_env, dict) and ADDOPTS_ENV in job_env:
                found.append(
                    f"{relative} job {job_name!r} declares {ADDOPTS_ENV} at JOB level "
                    f"({job_env[ADDOPTS_ENV]!r})."
                )
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                label = step.get("name") or "<unnamed step>"
                step_env = step.get("env")
                if isinstance(step_env, dict) and ADDOPTS_ENV in step_env:
                    found.append(
                        f"{relative} job {job_name!r} step {label!r} declares "
                        f"{ADDOPTS_ENV} ({step_env[ADDOPTS_ENV]!r})."
                    )
                run = step.get("run")
                if run and run_body_names_addopts(str(run)):
                    found.append(
                        f"{relative} job {job_name!r} step {label!r} names "
                        f"{ADDOPTS_ENV} in its run body. Any mention is refused: "
                        f"`echo \"{ADDOPTS_ENV}=...\" >> \"$GITHUB_ENV\"` sets the "
                        "variable for every LATER step in the job, which is how a "
                        "single line in an install step silently disarms the "
                        "accuracy gate that follows it."
                    )
    return found


def problems(root: Path, environ: Mapping[str, str] | None = None) -> list[str]:
    """Every reason to refuse, as operator-readable lines. Empty means clean.

    ``environ`` defaults to ``os.environ`` deliberately — the CI step passes
    nothing, so a default of ``{}`` would silently drop the runtime arm while every
    test that passes ``environ=`` explicitly kept passing. Pinned by
    ``test_problems_reads_the_real_environment_when_none_is_passed``.
    """
    if environ is None:
        environ = os.environ

    found: list[str] = []

    narrowing_env = narrowing_env_addopts(environ)
    if narrowing_env:
        found.append(
            f"{ADDOPTS_ENV} is set in this process's environment to "
            f"{environ.get(ADDOPTS_ENV)!r}, carrying narrowing flag(s) "
            f"{narrowing_env}.\n"
            f"  pytest reads arguments from that variable, so every pytest run in "
            f"this job — including the accuracy gate — loses assertions without a "
            f"single visible change to its run line.\n"
            f"  Measured: with {ADDOPTS_ENV}='--collect-only', `pytest tests/ -q` "
            f"collects 912 tests, executes NONE, and exits 0 with "
            f"REQUIRE_SWISS_EPHEMERIS=1 still set — fixtures do not run during "
            f"collection, so the fail-closed flag never evaluates.\n"
            f"  If pytest arguments are genuinely needed, put them on the run line "
            f"of the job that needs them, where the diff shows them."
        )

    found.extend(workflow_addopts_declarations(root))

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

    # The runtime-environment arm. Neutering `narrowing_env_addopts` must red here,
    # or the CI step's `--self-test` proves nothing about the arm that catches an
    # injected workflow-level `env:` block.
    env_narrowing = ("--collect-only", "--co", "-k not_corpus", "-m 'not accuracy'",
                     "--ignore=tests/test_swiss_ephemeris.py")
    env_clean = ("", "-q", "--color=no", "-q --strict-markers")

    # The run-body arm. `$GITHUB_ENV` forms come FIRST because they are the ones
    # that persist across steps — and the ones the previous, tokenising version of
    # this check let through.
    inline_declaring = (
        'echo "PYTEST_ADDOPTS=--collect-only" >> "$GITHUB_ENV"',
        "echo PYTEST_ADDOPTS=--co >> $GITHUB_ENV",
        'printf "PYTEST_ADDOPTS=%s" --collect-only >> "$GITHUB_ENV"',
        'PYTEST_ADDOPTS="--collect-only" pytest tests/ -q',
        "export PYTEST_ADDOPTS=-k nothing",
        "REQUIRE_SWISS_EPHEMERIS=1 PYTEST_ADDOPTS=--co pytest tests/",
        # A bare mention, no assignment: still refused. Presence, not semantics.
        'echo "PYTEST_ADDOPTS"',
    )
    inline_clean = (
        "pytest tests/ -q",
        "python ci/check_pytest_collection.py",
        "uv sync --frozen --extra dev\nuv run --frozen pytest tests/ -q",
        'echo "REQUIRE_SWISS_EPHEMERIS=1" >> "$GITHUB_ENV"',
    )

    failures: list[str] = []
    for text in narrowing_fixtures:
        if not narrowing_addopts(addopts_tokens(tomllib.loads(text))):
            failures.append(f"NOT DETECTED as narrowing: {text!r}")
    for text in clean_fixtures:
        if narrowing_addopts(addopts_tokens(tomllib.loads(text))):
            failures.append(f"FALSELY flagged as narrowing: {text!r}")

    for value in env_narrowing:
        if not narrowing_env_addopts({ADDOPTS_ENV: value}):
            failures.append(f"NOT DETECTED as a narrowing {ADDOPTS_ENV}: {value!r}")
    for value in env_clean:
        if narrowing_env_addopts({ADDOPTS_ENV: value}):
            failures.append(f"FALSELY flagged as a narrowing {ADDOPTS_ENV}: {value!r}")

    for run in inline_declaring:
        if not run_body_names_addopts(run):
            failures.append(f"NOT DETECTED as a run body naming {ADDOPTS_ENV}: {run!r}")
    for run in inline_clean:
        if run_body_names_addopts(run):
            failures.append(f"FALSELY flagged as naming {ADDOPTS_ENV}: {run!r}")

    if failures:
        print("self-test FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    total = (
        len(narrowing_fixtures) + len(clean_fixtures)
        + len(env_narrowing) + len(env_clean)
        + len(inline_declaring) + len(inline_clean)
    )
    print(
        f"self-test OK ({total} fixtures discriminated across three arms: "
        f"{len(narrowing_fixtures)}+{len(clean_fixtures)} pyproject addopts, "
        f"{len(env_narrowing)}+{len(env_clean)} runtime {ADDOPTS_ENV}, "
        f"{len(inline_declaring)}+{len(inline_clean)} run-body mention)"
    )
    return 0


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refuse a narrowing pytest configuration.")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="prove the detector still discriminates; check nothing real",
    )
    arguments = parser.parse_args(argv)

    if environ is None:
        environ = os.environ

    if arguments.self_test:
        # The self-test runs BEFORE the real check in CI, so an injected
        # PYTEST_ADDOPTS must stop it here rather than let it certify a detector
        # and only then fail. Checked first for that reason.
        narrowing_env = narrowing_env_addopts(environ)
        if narrowing_env:
            print(
                f"self-test REFUSED: {ADDOPTS_ENV}={environ.get(ADDOPTS_ENV)!r} is set "
                f"in this environment and carries narrowing flag(s) {narrowing_env}. "
                "Refusing to certify anything from inside an already-narrowed "
                "environment.",
                file=sys.stderr,
            )
            return 1
        return _self_test()

    found = problems(REPO_ROOT, environ=environ)
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
