"""Guards on ci/check_pytest_collection.py — the one check that lives OUTSIDE pytest.

Why that script exists at all is measured, not assumed. Appending three lines to
``pyproject.toml`` on this branch and running ``pytest ci/tests/ -q``:

    addopts = "-k not_corpus"      -> exit 5, 88 deselected  (pytest itself fails)
    addopts = "-k 'not corpus'"    -> exit 1                 (the pytest guard reds)
    addopts = "-m 'not nothing'"   -> exit 1                 (the pytest guard reds)
    addopts = "-k 'not narrow'"    -> exit 0, 87 passed, 1 deselected
    addopts = "--collect-only"     -> exit 0, 88 collected, 0 executed

The last two are fail-open, and no pytest-resident guard can close them: under both,
the guard is among the tests that did not run. ``--collect-only`` is the sharper of
the two — it deselects nothing, so every reachability check still answers "yes, the
job reaches every gated module", truthfully, while not one assertion executes.

These tests exercise the checker against temporary trees rather than by mutating the
real ``pyproject.toml``, so a failure here can never leave the repository's own
configuration in a narrowed state.

Loaded by path via importlib, never ``from ci import ...``: ``ci/`` has no
``__init__.py`` by design, and CI runs the bare ``pytest ci/tests/ -q`` console
script, which does not put the CWD on sys.path (see CLAUDE.md).
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

_CI_DIR = pathlib.Path(__file__).resolve().parent.parent
_REPO = _CI_DIR.parent
_CHECKER = _CI_DIR / "check_pytest_collection.py"

_BASE_PYPROJECT = '[project]\nname = "x"\nversion = "1.0.0"\n'


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_pytest_collection", _CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(tmp_path: pathlib.Path, pyproject_tail: str = "") -> pathlib.Path:
    (tmp_path / "pyproject.toml").write_text(
        _BASE_PYPROJECT + pyproject_tail, encoding="utf-8", newline="\n"
    )
    return tmp_path


# Each of these removes assertions from every pytest run in the repo.
_NARROWING = (
    '\n[tool.pytest.ini_options]\naddopts = "-k not_corpus"\n',
    "\n[tool.pytest.ini_options]\naddopts = \"-k 'not corpus'\"\n",
    "\n[tool.pytest.ini_options]\naddopts = \"-m 'not accuracy'\"\n",
    '\n[tool.pytest.ini_options]\naddopts = "--collect-only"\n',
    '\n[tool.pytest.ini_options]\naddopts = "--co"\n',
    '\n[tool.pytest.ini_options]\naddopts = "--ignore=tests/test_swiss_ephemeris.py"\n',
    '\n[tool.pytest.ini_options]\naddopts = "--ignore-glob=tests/test_*accuracy*.py"\n',
    '\n[tool.pytest.ini_options]\naddopts = ["-q", "--deselect", "tests/x.py::test_y"]\n',
    # One list entry holding two arguments, which pytest shell-splits.
    '\n[tool.pytest.ini_options]\naddopts = ["-k not_corpus"]\n',
)

# Each of these is legitimate and must NOT be refused. A checker that reds on
# ordinary configuration is a checker the next person deletes.
_CLEAN = (
    "",
    "\n[tool.pytest.ini_options]\n",
    '\n[tool.pytest.ini_options]\naddopts = "-q --strict-markers"\n',
    '\n[tool.pytest.ini_options]\naddopts = ["-q", "--color=no"]\n',
    '\n[tool.pytest.ini_options]\naddopts = "--maxfail=1 -x"\n',
    # A COMMENT, which is the entire reason this parses rather than greps.
    '\n[tool.pytest.ini_options]\n# addopts = "-k not_corpus"\n',
    # Defining markers is not selecting on them.
    '\n[tool.pytest.ini_options]\nmarkers = ["accuracy: needs swiss data"]\n',
    # A path that merely contains a flag's spelling is not that flag.
    '\n[tool.pytest.ini_options]\ntestpaths = ["tests", "ci/tests"]\n',
)


@pytest.mark.parametrize("tail", _NARROWING)
def test_narrowing_addopts_is_refused(tmp_path: pathlib.Path, tail: str) -> None:
    checker = _load_checker()
    found = checker.problems(_tree(tmp_path, tail), environ={})
    assert found, f"narrowing addopts was permitted: {tail!r}"
    assert "addopts" in found[0]


@pytest.mark.parametrize("tail", _CLEAN)
def test_legitimate_configuration_is_not_refused(
    tmp_path: pathlib.Path, tail: str
) -> None:
    checker = _load_checker()
    assert checker.problems(_tree(tmp_path, tail), environ={}) == [], (
        f"legitimate configuration was refused: {tail!r}"
    )


@pytest.mark.parametrize("name", ("pytest.ini", "tox.ini", "setup.cfg", ".pytest.ini"))
def test_a_config_file_this_checker_does_not_parse_is_refused(
    tmp_path: pathlib.Path, name: str
) -> None:
    """Fail closed on an unparsed config file rather than covering less than claimed.

    pytest honours `addopts` in all of these. Only pyproject.toml is parsed, so the
    honest response to another one appearing is to refuse until someone extends the
    checker deliberately — not to report clean about a file nothing opened.
    """
    checker = _load_checker()
    root = _tree(tmp_path)
    (root / name).write_text("[pytest]\naddopts = -k not_corpus\n", encoding="utf-8")
    found = checker.problems(root, environ={})
    assert found, f"{name} was ignored entirely"
    assert name in found[0]


@pytest.mark.parametrize("name", ("pytest.ini", "tox.ini", "setup.cfg", ".pytest.ini"))
@pytest.mark.parametrize("directory", ("tests", "ci/tests"))
def test_a_config_file_in_a_SUBDIRECTORY_is_refused(
    tmp_path: pathlib.Path, name: str, directory: str
) -> None:
    """The root is not the only place pytest reads a config from.

    MEASURED ON THE REAL REPO, before this arm existed. pytest resolves its
    inifile by walking UP from the common ancestor of its arguments, so a config
    dropped in `tests/` wins over the root pyproject.toml for `pytest tests/`:

        tests/pytest.ini  ==  [pytest]\\naddopts = --collect-only
        python ci/check_pytest_collection.py
            -> "OK: pytest configuration does not narrow collection.", exit 0
        REQUIRE_SWISS_EPHEMERIS=1 python -m pytest tests/ -q
            -> "937 tests collected", 0 executed, exit 0

    That is the exact fail-open this checker exists to close, one directory
    down, and the root-only scan reported clean about it.
    """
    checker = _load_checker()
    root = _tree(tmp_path)
    target = root / directory
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_text("[pytest]\naddopts = --collect-only\n", encoding="utf-8")

    found = checker.problems(root, environ={})
    assert found, f"{directory}/{name} was ignored entirely"
    assert any(name in problem for problem in found), (
        f"{directory}/{name} was not named in the refusal: {found}"
    )


def test_a_nested_pyproject_declaring_pytest_options_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A second pyproject.toml is an ini source too — and only the root one is parsed.

    Measured on the real repo the same way: `tests/pyproject.toml` carrying
    `[tool.pytest.ini_options] addopts = "--collect-only"` left the checker
    saying "OK" while `pytest tests/test_coord_bounds.py` collected 43 and
    executed none.
    """
    checker = _load_checker()
    root = _tree(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "--collect-only"\n', encoding="utf-8"
    )

    found = checker.problems(root, environ={})
    assert found, "a nested pyproject.toml declaring pytest ini options was ignored"
    assert any("pyproject.toml" in problem for problem in found)


def test_a_nested_pyproject_without_pytest_options_is_not_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A subpackage manifest is legitimate and must not red.

    Positive control, and the reason the pyproject arm PARSES rather than
    refusing on sight the way pytest.ini/tox.ini/setup.cfg do: those four have
    no legitimate reason to exist here, a second project manifest might, and a
    check that cries wolf is one people learn to route around.
    """
    checker = _load_checker()
    root = _tree(tmp_path)
    (root / "subpkg").mkdir()
    (root / "subpkg" / "pyproject.toml").write_text(
        '[project]\nname = "subpkg"\nversion = "0.1.0"\n', encoding="utf-8"
    )

    assert checker.problems(root, environ={}) == [], (
        "an ordinary nested project manifest was refused"
    )


def test_the_real_repository_configuration_is_clean() -> None:
    """The check this repo's CI actually performs, performed here too."""
    checker = _load_checker()
    assert checker.problems(_REPO, environ={}) == []


def test_the_real_repository_tree_is_scanned_not_just_its_root() -> None:
    """The clean verdict above must come from a scan that could have found something.

    `test_the_real_repository_configuration_is_clean` passes both before and
    after this arm exists — an empty result proves nothing about coverage. This
    plants a config file in the real repo's own `tests/` directory, asserts the
    refusal fires, and removes it again, so the clean verdict is known to be a
    measurement rather than a blind spot.
    """
    checker = _load_checker()
    planted = _REPO / "tests" / "pytest.ini"
    assert not planted.exists(), f"{planted} already exists; refusing to overwrite it"
    planted.write_text("[pytest]\naddopts = --collect-only\n", encoding="utf-8")
    try:
        found = checker.problems(_REPO, environ={})
    finally:
        planted.unlink()
    assert any("pytest.ini" in problem for problem in found), (
        "a pytest.ini planted in the real repo's tests/ directory was not "
        f"detected — the repo scan does not reach it. Found: {found}"
    )
    assert checker.problems(_REPO, environ={}) == [], "cleanup did not restore the tree"


def test_main_exit_codes(monkeypatch, tmp_path: pathlib.Path) -> None:
    """0 when clean, 1 when refused — a checker that exits 0 on a finding is not one."""
    checker = _load_checker()

    monkeypatch.setattr(checker, "REPO_ROOT", _tree(tmp_path))
    assert checker.main([], environ={}) == 0

    monkeypatch.setattr(
        checker,
        "REPO_ROOT",
        _tree(tmp_path, '\n[tool.pytest.ini_options]\naddopts = "--collect-only"\n'),
    )
    assert checker.main([], environ={}) == 1


def test_self_test_discriminates_and_passes() -> None:
    """`--self-test` must actually exercise both directions, and must pass today.

    The count in its own output is the non-vacuity floor: a self-test that
    discriminated zero fixtures would "pass" while proving nothing.
    """
    checker = _load_checker()
    assert checker.main(["--self-test"]) == 0


def test_self_test_fails_when_the_detector_stops_discriminating(monkeypatch) -> None:
    """Proof the self-test is not decorative.

    With the narrowing-flag table emptied, every narrowing fixture stops being
    detected — and `--self-test` must go non-zero rather than announcing OK. Without
    this, a gutted table would leave the self-test green and the CI step would
    certify the real configuration on a detector that detects nothing.
    """
    checker = _load_checker()
    monkeypatch.setattr(checker, "NARROWING_FLAGS", ())
    assert checker.main(["--self-test"]) == 1


# ---------------------------------------------------------------------------
# PYTEST_ADDOPTS — the arm round 1 left inside pytest (PR #66 review, blocking)
# ---------------------------------------------------------------------------
#
# Round 1 moved the pyproject arm out of pytest on the argument that "a pytest test
# cannot police a pytest setting that stops pytest running tests" — and then left
# the PYTEST_ADDOPTS detector as a pytest test. The same measurement defeats it.
# Reproduced on this branch before the fix:
#
#   PYTEST_ADDOPTS='--collect-only' pytest ci/tests/ -q
#       -> exit 0, 119 tests collected, 0 executed
#          (test_swiss_job_does_not_inject_pytest_addopts, the guard for this exact
#           vector, is among the tests collected and never run)
#   PYTEST_ADDOPTS='--collect-only' pytest tests/ -q   (REQUIRE_SWISS_EPHEMERIS=1)
#       -> exit 0, 912 tests collected, 0 executed
#          (the fail-closed flag never evaluates — fixtures do not run during
#           collection, so the accuracy gate asserts nothing and reports success)
#   PYTEST_ADDOPTS='--collect-only' python ci/check_pytest_collection.py
#       -> "OK", exit 0   <- the checker never read its own environment
#
# One `env:` block in test.yml, whole workflow green, nothing asserted anywhere.
#
# TWO ARMS, because neither alone is enough:
#   * the RUNTIME read catches a workflow- or job-level injection, because those
#     land in the checker step's own environment — read what the engine applies;
#   * the WORKFLOW-FILE parse catches a placement the runtime read cannot see, e.g.
#     a step-level `env:` on a *sibling* pytest step, which never reaches this
#     process at all.

_ADDOPTS_NARROWING_VALUES = (
    "--collect-only",
    "--co",
    "-k not_corpus",
    "-k 'not corpus'",
    "-m 'not accuracy'",
    "--ignore=tests/test_swiss_ephemeris.py",
    "--deselect tests/test_swiss_ephemeris.py::test_x",
    "-q --collect-only",
)

_ADDOPTS_CLEAN_VALUES = ("", "-q", "--color=no", "-q --strict-markers", "--maxfail=1")


@pytest.mark.parametrize("value", _ADDOPTS_NARROWING_VALUES)
def test_narrowing_pytest_addopts_in_the_runtime_environment_is_refused(
    tmp_path: pathlib.Path, value: str
) -> None:
    """An injected narrowing PYTEST_ADDOPTS must kill the checker step, loudly.

    This is the arm that makes a workflow- or job-level `env:` injection fatal: the
    checker runs as a plain python step in the same job, so the injected variable is
    in *its* environment too. It cannot be deselected — it is not a test.
    """
    checker = _load_checker()
    found = checker.problems(_tree(tmp_path), environ={"PYTEST_ADDOPTS": value})
    assert found, f"a narrowing PYTEST_ADDOPTS={value!r} was permitted at runtime"
    assert "PYTEST_ADDOPTS" in found[0]


@pytest.mark.parametrize("value", _ADDOPTS_CLEAN_VALUES)
def test_non_narrowing_pytest_addopts_in_the_environment_is_not_refused(
    tmp_path: pathlib.Path, value: str
) -> None:
    """A developer's ordinary PYTEST_ADDOPTS must not red the checker.

    The runtime arm is deliberately NARROWING-only, unlike the workflow-declaration
    arm below which refuses on presence. A local shell may legitimately carry `-q`;
    a workflow file has no legitimate reason to declare the variable at all. Refusing
    presence at runtime would red this check on contributors' machines, and a checker
    that reds on ordinary input is one the next person deletes.
    """
    checker = _load_checker()
    assert checker.problems(_tree(tmp_path), environ={"PYTEST_ADDOPTS": value}) == [], (
        f"a harmless PYTEST_ADDOPTS={value!r} was refused"
    )


def test_problems_reads_the_real_environment_when_none_is_passed(
    monkeypatch, tmp_path: pathlib.Path
) -> None:
    """The default must be os.environ, never an empty mapping.

    Every other test here passes `environ=` explicitly so it is deterministic. That
    makes the DEFAULT the dangerous part: if it silently became `{}`, all of those
    tests would still pass while the real CI step read nothing. This pins the
    default by monkeypatching the process environment and calling with no `environ`.
    """
    checker = _load_checker()
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    found = checker.problems(_tree(tmp_path))
    assert found, (
        "problems() ignored a narrowing PYTEST_ADDOPTS in the real process "
        "environment — the default environ is not os.environ"
    )


def _workflow(root: pathlib.Path, body: str, name: str = "test.yml") -> pathlib.Path:
    directory = root / ".github" / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body, encoding="utf-8", newline="\n")
    return root


# The three `env:` levels GitHub Actions resolves into a step's environment, plus
# the inline shell assignment. A step-level declaration on a SIBLING pytest step is
# the case the runtime read cannot see: it never enters the checker's own process.
_WORKFLOW_DECLARATIONS = (
    'env:\n  PYTEST_ADDOPTS: "--collect-only"\njobs:\n  test:\n    steps:\n      - run: pytest tests/ -q\n',
    'jobs:\n  test:\n    env:\n      PYTEST_ADDOPTS: "--collect-only"\n    steps:\n      - run: pytest tests/ -q\n',
    'jobs:\n  test:\n    steps:\n      - env:\n          PYTEST_ADDOPTS: "--collect-only"\n        run: pytest tests/ -q\n',
    'jobs:\n  test:\n    steps:\n      - run: PYTEST_ADDOPTS="--collect-only" pytest tests/ -q\n',
    'jobs:\n  test:\n    steps:\n      - run: export PYTEST_ADDOPTS="-k nothing"\n',
    # THE CROSS-STEP MECHANISM (PR #66 round-2 review, blocking finding).
    # GitHub Actions persists a `>> $GITHUB_ENV` write to every SUBSEQUENT step in
    # the job, so this is the form that actually reaches a sibling pytest step. The
    # two forms round 2 modelled — a leading `VAR=` prefix and `export` — are
    # shell-LOCAL: each `run:` is a fresh shell, so neither ever escapes its own
    # body. The checker caught the weak forms and missed the effective one.
    'jobs:\n  test:\n    steps:\n      - run: echo "PYTEST_ADDOPTS=--collect-only" >> "$GITHUB_ENV"\n',
    'jobs:\n  test:\n    steps:\n      - run: echo PYTEST_ADDOPTS=--co >> $GITHUB_ENV\n',
    'jobs:\n  test:\n    steps:\n      - run: printf "PYTEST_ADDOPTS=%s" --collect-only >> "$GITHUB_ENV"\n',
    # The reviewer's exact placement: one line appended to the swiss job's existing
    # install step, poisoning the accuracy step that follows it.
    'jobs:\n  swiss-ephemeris:\n    steps:\n      - name: Install the FROZEN dependency set\n        run: |\n          python -m pip install --upgrade uv\n          uv sync --frozen --extra dev\n          echo "PYTEST_ADDOPTS=--collect-only" >> "$GITHUB_ENV"\n      - name: Run the full suite against real Swiss data\n        env:\n          REQUIRE_SWISS_EPHEMERIS: "1"\n        run: uv run --frozen python -m pytest tests/ -q\n',
    # A bare MENTION, with no assignment at all, is refused too: presence, not
    # semantics. Distinguishing a harmless echo from a poisoning one means modelling
    # shell redirection, and modelling is exactly what let the $GITHUB_ENV form
    # through. A run body has no more legitimate reason to name the variable than an
    # `env:` block does.
    'jobs:\n  test:\n    steps:\n      - run: echo "PYTEST_ADDOPTS"\n',
    # A non-narrowing VALUE is still refused here: presence is the rule for a
    # workflow declaration, because deciding narrowing-ness of an arbitrary value
    # means reimplementing pytest's argument parser.
    'jobs:\n  test:\n    env:\n      PYTEST_ADDOPTS: "-q"\n    steps:\n      - run: pytest tests/ -q\n',
    # Declared in a DIFFERENT workflow file, and in a job that is not `test`.
    'jobs:\n  anything:\n    env:\n      PYTEST_ADDOPTS: "--co"\n    steps:\n      - run: pytest tests/ -q\n',
)

_WORKFLOW_CLEAN = (
    'jobs:\n  test:\n    steps:\n      - run: pytest tests/ -q\n',
    # Only in a COMMENT — invisible to a parser, which is why this parses.
    'jobs:\n  test:\n    steps:\n      # env:\n      #   PYTEST_ADDOPTS: "--collect-only"\n      - run: pytest tests/ -q\n',
    # A similarly-named variable is not this one — `env:` keys are matched exactly,
    # so the presence rule that now governs RUN BODIES does not leak into env blocks.
    'jobs:\n  test:\n    env:\n      PYTEST_ADDOPTS_NOTES: "see docs"\n    steps:\n      - run: pytest tests/ -q\n',
    # An ordinary run body that happens to invoke pytest is still fine — the rule is
    # about naming the variable, not about running tests.
    'jobs:\n  test:\n    steps:\n      - run: |\n          uv sync --frozen --extra dev\n          uv run --frozen pytest tests/ -q\n',
)


@pytest.mark.parametrize("body", _WORKFLOW_DECLARATIONS)
def test_pytest_addopts_declared_in_a_workflow_is_refused(
    tmp_path: pathlib.Path, body: str
) -> None:
    """Declared at ANY level, in ANY job, in ANY workflow file — refused.

    A step-level `env:` on a sibling pytest step never reaches this process, so the
    runtime arm above cannot see it. This arm reads the workflow files themselves,
    as plain python, so a narrowing declaration cannot hide behind the pytest run it
    is narrowing.
    """
    checker = _load_checker()
    found = checker.problems(_workflow(_tree(tmp_path), body), environ={})
    assert found, f"a workflow PYTEST_ADDOPTS declaration was permitted:\n{body}"
    assert any("PYTEST_ADDOPTS" in problem for problem in found)


@pytest.mark.parametrize("body", _WORKFLOW_CLEAN)
def test_a_workflow_without_an_addopts_declaration_is_clean(
    tmp_path: pathlib.Path, body: str
) -> None:
    checker = _load_checker()
    assert checker.problems(_workflow(_tree(tmp_path), body), environ={}) == [], (
        f"a clean workflow was refused:\n{body}"
    )


def test_workflow_scan_fails_closed_when_a_workflow_cannot_be_parsed(
    tmp_path: pathlib.Path,
) -> None:
    """Unparseable YAML must refuse, not be skipped.

    Silently ignoring a file this checker cannot read is how a declaration hides in
    it. The whole point of this arm is that no workflow goes unexamined.
    """
    checker = _load_checker()
    root = _workflow(_tree(tmp_path), "jobs:\n  test:\n    steps:\n  - bad: [indent\n")
    found = checker.problems(root, environ={})
    assert found, "an unparseable workflow file was silently skipped"


def test_the_real_repository_workflows_declare_no_addopts() -> None:
    """The repo as committed must be clean on this arm too."""
    checker = _load_checker()
    assert checker.workflow_addopts_declarations(_REPO) == []


def test_main_refuses_under_an_injected_environment(
    monkeypatch, tmp_path: pathlib.Path
) -> None:
    """End-to-end through main(), the way the CI step invokes it."""
    checker = _load_checker()
    monkeypatch.setattr(checker, "REPO_ROOT", _tree(tmp_path))
    assert checker.main([], environ={"PYTEST_ADDOPTS": "--collect-only"}) == 1
    assert checker.main([], environ={}) == 0


def test_self_test_covers_the_env_and_workflow_arms(monkeypatch) -> None:
    """`--self-test` must exercise the NEW arms too, not just the pyproject one.

    The CI step runs `--self-test` before the real check precisely so a detector
    that has stopped discriminating cannot certify the repository. That argument
    only holds if the self-test covers every arm: with the env detector neutered,
    or the workflow detector neutered, `--self-test` must go non-zero.
    """
    checker = _load_checker()
    assert checker.main(["--self-test"]) == 0

    monkeypatch.setattr(checker, "narrowing_env_addopts", lambda environ: [])
    assert checker.main(["--self-test"]) == 1, (
        "the self-test passed with the runtime-env detector neutered — it does not "
        "cover that arm, so the CI step's self-test proves nothing about it"
    )

    checker = _load_checker()
    monkeypatch.setattr(checker, "run_body_names_addopts", lambda run: False)
    assert checker.main(["--self-test"]) == 1, (
        "the self-test passed with the run-body detector neutered — it does not "
        "cover the $GITHUB_ENV arm, so the CI step's self-test proves nothing about "
        "the only form that persists across steps"
    )


def test_the_github_env_idiom_is_refused_in_the_real_workflow_shape(
    tmp_path: pathlib.Path,
) -> None:
    """The round-2 blocking finding, as its own named regression.

    `echo "PYTEST_ADDOPTS=…" >> "$GITHUB_ENV"` is GitHub Actions' STANDARD way to
    set an environment variable for later steps, and it was the one form the
    tokenising detector could not see: `shell_commands` renders it as
    `['echo', 'PYTEST_ADDOPTS=--collect-only', '>>', '$GITHUB_ENV']`, where the
    assignment is an ARGUMENT to echo — neither a leading prefix nor an `export`
    arg. Measured before the fix, with this line appended to the swiss job's install
    step in the real test.yml: checker exit 0 "OK", 149 guard tests passed, and the
    accuracy step would then have run under `--collect-only` (912 collected, 0
    executed, exit 0).

    Kept separate from the parametrised fixtures so the finding cannot be dropped by
    editing a tuple.
    """
    checker = _load_checker()
    poisoned = (
        "jobs:\n"
        "  swiss-ephemeris:\n"
        "    steps:\n"
        "      - name: Install the FROZEN dependency set\n"
        "        run: |\n"
        "          uv sync --frozen --extra dev\n"
        '          echo "PYTEST_ADDOPTS=--collect-only" >> "$GITHUB_ENV"\n'
        "      - name: Run the full suite against real Swiss data\n"
        "        env:\n"
        '          REQUIRE_SWISS_EPHEMERIS: "1"\n'
        "        run: uv run --frozen python -m pytest tests/ -q\n"
    )
    found = checker.problems(_workflow(_tree(tmp_path), poisoned), environ={})
    assert found, (
        "the $GITHUB_ENV cross-step idiom was permitted — this is the round-2 "
        "blocking finding, unfixed"
    )
    assert any("PYTEST_ADDOPTS" in problem for problem in found)


def test_the_real_script_refuses_under_the_reviewers_exact_reproduction() -> None:
    """The reviewer's repro, run as a subprocess against the REAL repository.

    Measured on this branch before the fix, and quoted verbatim in the review:

        PYTEST_ADDOPTS='--collect-only' python ci/check_pytest_collection.py
            -> "OK", exit 0

    A subprocess is the honest form: it proves the SCRIPT refuses as CI invokes it,
    not merely that a function inside it would have. The environment is built
    explicitly so the assertion is about the injected variable and nothing ambient.
    """
    environment = {
        # SYSTEMROOT is required for a Python subprocess to start on Windows.
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT")
        if key in os.environ
    }
    injected = dict(environment, PYTEST_ADDOPTS="--collect-only")

    refused = subprocess.run(
        [sys.executable, str(_CHECKER)],
        cwd=str(_REPO), env=injected, capture_output=True, text=True, timeout=120,
    )
    assert refused.returncode != 0, (
        "the real script exited 0 with a narrowing PYTEST_ADDOPTS injected — this is "
        f"the blocking finding.\nstdout:\n{refused.stdout}\nstderr:\n{refused.stderr}"
    )
    assert "PYTEST_ADDOPTS" in (refused.stdout + refused.stderr)

    # ...and the self-test must fail under the same injection too, so the CI step
    # cannot get as far as certifying anything.
    self_tested = subprocess.run(
        [sys.executable, str(_CHECKER), "--self-test"],
        cwd=str(_REPO), env=injected, capture_output=True, text=True, timeout=120,
    )
    assert self_tested.returncode != 0, (
        "`--self-test` exited 0 with a narrowing PYTEST_ADDOPTS injected. The CI step "
        "runs the self-test FIRST; if the injection does not stop it there, the "
        "injection has already survived the check that runs before the real one."
    )

    # The same script, uninjected, must still pass — or this proves nothing but that
    # the checker refuses everything.
    clean = subprocess.run(
        [sys.executable, str(_CHECKER)],
        cwd=str(_REPO), env=environment, capture_output=True, text=True, timeout=120,
    )
    assert clean.returncode == 0, (
        f"the real repository was refused with a clean environment:\n{clean.stderr}"
    )
