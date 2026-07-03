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

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"


def _all_run_commands() -> list[str]:
    commands: list[str] = []
    for workflow_path in sorted(_WORKFLOWS_DIR.glob("*.yml")):
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
    ci/tests in — avoids a TOML-parser dependency (this repo's CI runs on
    Python 3.10, before stdlib `tomllib`)."""
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
