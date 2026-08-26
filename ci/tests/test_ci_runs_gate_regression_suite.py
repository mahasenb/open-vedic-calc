"""Regression guard: some GitHub Actions workflow must actually execute the
proprietary-reference gate's own regression suite (`ci/tests/`).

`ci/tests/test_check_no_proprietary_refs.py` exists specifically so a future
edit that weakens `_FORBIDDEN` (e.g. reverting it to a single regex, or
dropping the `PROPRIETARY_REF_TOKENS` env-token loop) is caught. That
protection is inert unless some CI workflow actually runs `ci/tests/` —
`pytest tests/` alone never collects it (verified: `ci/tests/` is outside the
`tests/` tree by design, see CLAUDE.md and the module docstring on the
sibling test file).

This test parses the tracked workflow YAML files and asserts at least one
`run:` step's command targets `ci/tests` (or sweeps the whole repo root), or
that `pyproject.toml` widens pytest collection (`testpaths`) to include it —
so `ci/tests/` is never silently skipped by every workflow at once.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_workflow_scope():
    """The ONE definition of "which files are this repo's workflows".

    By path, not ``from ci import ...``: ``ci/`` has no ``__init__.py`` by
    design, and CI runs the console-script ``pytest``, which does not put the
    CWD on ``sys.path``.
    """
    path = _REPO_ROOT / "ci" / "workflow_scope.py"
    spec = importlib.util.spec_from_file_location("ci_workflow_scope", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_workflow_scope = _load_workflow_scope()


def _all_run_commands(repo_root: Path = _REPO_ROOT) -> list[str]:
    """Every ``run:`` body across the workflows, BOTH suffixes, asked of git.

    This used to glob ``.github/workflows/*.yml``. GitHub Actions runs a
    ``.yaml`` file identically, so the glob made an affirmative "no workflow
    runs ci/tests" claim about a file it never opened -- and this assertion
    fails CLOSED, so a repository whose only workflow was named ``.yaml`` would
    have reddened here while the wiring it checks for was in fact present. The
    inverse is the dangerous half: a repository that MOVED its ci/tests step
    into a ``.yaml`` workflow would have looked identical to one that deleted
    it.

    Takes a root so the enumeration can be exercised against a fixture repo:
    a guard that can only ever be run against the tree it ships in cannot be
    shown to discriminate.
    """
    commands: list[str] = []
    for workflow_path in _workflow_scope.workflow_paths(repo_root):
        data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        jobs = data.get("jobs", {}) if data else {}
        for job in jobs.values():
            for step in job.get("steps", []):
                run = step.get("run")
                if run:
                    commands.append(run)
    return commands


def _pyproject_widens_collection_to_ci_tests() -> bool:
    """Cheap textual check for a pytest `testpaths` setting that would sweep
    ci/tests in — avoids a TOML-parser dependency (a two-line scan needs none,
    and requires-python still admits 3.10, before stdlib `tomllib`)."""
    pyproject = _REPO_ROOT / "pyproject.toml"
    if not pyproject.exists():
        return False
    text = pyproject.read_text(encoding="utf-8")
    if "testpaths" not in text:
        return False
    return "ci/tests" in text or "ci\\tests" in text


def test_some_workflow_step_invokes_ci_tests_directory():
    """At least one `run:` step across the tracked workflows must invoke
    `pytest` against `ci/tests` (directly, or by sweeping the whole repo),
    or `pyproject.toml` must widen collection (`testpaths`) to include it.
    Otherwise the gate's own regression suite never executes in CI and a
    future regression in `_FORBIDDEN` would pass CI green."""
    commands = _all_run_commands()

    def _targets_ci_tests(cmd: str) -> bool:
        if "pytest" not in cmd:
            return False
        # Accept an explicit ci/tests invocation, or a bare `pytest` /
        # `pytest .` that would sweep the whole repo including ci/tests.
        stripped = cmd.strip()
        return (
            "ci/tests" in cmd
            or "ci\\tests" in cmd
            or stripped in {"pytest", "pytest .", "pytest ./"}
        )

    assert any(_targets_ci_tests(cmd) for cmd in commands) or _pyproject_widens_collection_to_ci_tests(), (
        "No CI workflow step runs ci/tests/ (the proprietary-reference gate's "
        "own regression suite), and pyproject.toml does not widen pytest "
        "collection to include it either. A future edit that weakens "
        "_FORBIDDEN in ci/check_no_proprietary_refs.py would pass CI green. "
        "Add a `pytest ci/tests/ -q` step (or equivalent) to a workflow."
    )


# ---------------------------------------------------------------------------
# The enumeration itself: GitHub Actions runs `.yaml`, so this guard must read it
# ---------------------------------------------------------------------------
_A_WORKFLOW_RUNNING_CI_TESTS = """\
name: Fixture
on: [push]
jobs:
  t:
    runs-on: ubuntu-latest
    steps:
      - run: uv run --frozen pytest ci/tests/ -q
"""


def _fixture_repo(tmp_path: Path, filename: str) -> Path:
    """A throwaway checkout whose ONLY workflow is *filename*.

    Not committed: ``--others`` is part of the enumeration precisely so an
    untracked workflow is in scope at the commit that introduces it.
    """
    root = tmp_path / "wf-fixture"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / filename).write_text(
        _A_WORKFLOW_RUNNING_CI_TESTS, encoding="utf-8", newline="\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def test_a_yaml_workflow_is_enumerated(tmp_path):
    """RED under the old ``*.yml`` glob: a ``.yaml`` workflow was never opened.

    GitHub Actions runs ``.yaml`` exactly as it runs ``.yml``. This assertion
    fails CLOSED, so the visible symptom of the half-enumeration was backwards
    from the usual one: a repository that MOVED its ``ci/tests`` step into a
    ``.yaml`` workflow looked identical to one that deleted the step. The guard
    would have demanded the wiring be re-added when it was already there --
    and, worse, any sibling guard reading the same half-enumeration to assert
    "no workflow does X" would have passed by not looking.
    """
    root = _fixture_repo(tmp_path, "ci.yaml")
    commands = _all_run_commands(root)
    assert any("ci/tests" in command for command in commands), (
        f"a .yaml workflow was not enumerated: {commands}. GitHub Actions runs "
        "it identically to .yml, so reading one suffix makes an affirmative "
        "claim about a file that was never opened."
    )


def test_the_yml_control_still_holds(tmp_path):
    """Non-vacuity for the test above: the same body under ``.yml`` is found.

    Without this, a broken fixture helper would make the ``.yaml`` assertion
    pass for the wrong reason -- or fail for one, and be "fixed" by widening
    something that was never narrow.
    """
    root = _fixture_repo(tmp_path, "ci.yml")
    commands = _all_run_commands(root)
    assert any("ci/tests" in command for command in commands), commands
