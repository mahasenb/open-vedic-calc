"""Regression guard: every workflow in .github/workflows/ keeps a least-privilege
GITHUB_TOKEN, and `id-token: write` stays scoped to the job that actually calls
google-github-actions/auth.

WHY THIS IS A TEST AND NOT ONLY A CI STEP
-----------------------------------------
It runs in this repo's ordinary suite, so it is part of the local pre-push gate as
well as CI — a workflow edit that widens the token cannot get as far as a pushed
branch. The detection logic lives in ci/check_workflow_token_scopes.py (a real YAML
parser, never a line-scan; see that module's docstring for the comment-blindness
incident that motivates it).

FAIL-CLOSED ORDERING
--------------------
`test_guard_fixtures_discriminate` proves the guard can still tell a violation from
a clean file, and the scan test re-asserts that itself rather than relying on
test-execution order. A guard whose own detection is broken must never be able to
certify the real workflows green — the precise failure mode a reviewer demonstrated
on an earlier line-scanning version, which printed "All workflows PASSED" at exit 0
while a genuine regression sat in the tree.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_GATE_PATH = _REPO_ROOT / "ci" / "check_workflow_token_scopes.py"


def _load_gate_module():
    """Import ci/check_workflow_token_scopes.py as a module (it is a standalone
    script, not a package member)."""
    spec = importlib.util.spec_from_file_location("check_workflow_token_scopes", _GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_gate = _load_gate_module()


def test_guard_fixtures_discriminate():
    """The guard's own fixtures must pass.

    Each fixture is input a textual scanner gets wrong (trailing comment on a
    write grant, column-0 comment mid-block, flow mapping, quoted value,
    read-all/write-all shorthand, `permissions: {}`, `permissions:`-shaped text
    inside a run: body) or a violation it must still raise. If this goes red the
    guard's verdict on the real workflows means nothing.
    """
    failures = _gate.fixture_failures()
    assert failures == [], (
        "The workflow-token guard's own self-test fixtures failed, so it cannot be "
        "trusted to judge the real workflows. Fix ci/check_workflow_token_scopes.py "
        "before reading any scan result. Failing fixtures:\n  - "
        + "\n  - ".join(failures)
    )


def test_workflow_directory_is_not_empty():
    """Fail closed: a scan that examined nothing must not read as a pass."""
    files = _gate.workflow_files(_REPO_ROOT)
    assert files, (
        f"No workflow files found under {_REPO_ROOT / '.github' / 'workflows'}. "
        f"Either the path moved (update ci/check_workflow_token_scopes.py) or this "
        f"gate is silently scanning nothing."
    )


def test_workflows_declare_least_privilege_tokens():
    """Every workflow declares a read-only top-level `permissions:` block, and
    `id-token: write` appears only on jobs that call google-github-actions/auth."""
    # Fail-closed ordering, not an execution-order assumption: refuse to certify
    # the real workflows with a guard whose own detection is broken.
    assert _gate.fixture_failures() == [], (
        "Refusing to certify the workflows: the guard's self-test fixtures are "
        "failing (see test_guard_fixtures_discriminate)."
    )

    violations = _gate.scan_violations(_REPO_ROOT)
    assert violations == [], (
        "Workflow token scopes are not least-privilege. A workflow-level write "
        "applies to EVERY job in the file, and a job-level block REPLACES the "
        "workflow default rather than merging with it — so move the wider scope "
        "onto the job that needs it and restate the scopes it still needs there.\n  - "
        + "\n  - ".join(violations)
    )


# ---------------------------------------------------------------------------
# WHERE THE SCOPE COMES FROM
#
# This checker used to enumerate `.github/workflows` with `iterdir()` filtered
# on the two suffixes. That got the SUFFIXES right — measured, both are read, so
# the half-enumeration defect that motivated ci/workflow_scope.py was never
# present here — but it asked the FILESYSTEM rather than git, so its answer had
# no `--exclude-standard` and no `--others` semantics, and it answered "no
# workflows" for a tree git cannot describe.
#
# Measured at the commit before this migration, planting one probe workflow at a
# time under `.github/workflows` and running the checker:
#
#   untracked probe  -> FAIL [TOP_LEVEL_MISSING], "Scanned 4 workflow file(s)"
#   git-ignored probe-> FAIL [TOP_LEVEL_MISSING], "Scanned 4 workflow file(s)"
#
# The first is right and must not change; the second is the behaviour change
# this migration makes, and it is the correct direction — an ignored file is
# never pushed, so GitHub Actions never runs it, and a developer's scratch
# workflow must not decide whether this gate is red.
# ---------------------------------------------------------------------------
def _git_repo(tmp_path: Path) -> Path:
    """A real checkout: the shape this checker actually runs in."""
    for args in (
        ("init", "-q"),
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test"),
    ):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)
    return tmp_path


# No top-level `permissions:` — TOP_LEVEL_MISSING, the loudest violation this
# checker raises, so "was this file scanned?" has an unambiguous answer.
_VIOLATING = (
    "name: probe\non: [push]\njobs:\n  probe:\n    runs-on: ubuntu-latest\n"
    "    steps:\n      - uses: actions/checkout@v7\n"
)
_CLEAN = "name: clean\non: [push]\npermissions:\n  contents: read\njobs: {}\n"


def _put_workflow(root: Path, name: str, body: str) -> Path:
    directory = root / ".github" / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def test_the_scope_is_the_shared_definition_not_a_directory_listing():
    """Delegation, asserted as an EQUALITY against the shared enumeration.

    A re-implementation that drifts — a dropped suffix, a lost flag — fails
    here even if it still happens to return the right answer for today's three
    workflow files, because the two answers are compared rather than eyeballed.
    """
    spec = importlib.util.spec_from_file_location(
        "_workflow_scope_for_token_scope_test", _REPO_ROOT / "ci" / "workflow_scope.py"
    )
    scope = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scope)
    assert _gate.workflow_files(_REPO_ROOT) == scope.workflow_paths(_REPO_ROOT)


def test_an_untracked_workflow_is_still_scanned(tmp_path: Path):
    """`--others`: the cheapest moment to catch a violation is the commit that
    introduces it, and at that moment the file is not yet tracked."""
    root = _git_repo(tmp_path)
    _put_workflow(root, "brand_new.yml", _VIOLATING)
    violations = _gate.scan_violations(root)
    assert any("brand_new.yml" in v for v in violations), (
        "an untracked workflow left the scan set. It is one `git add` away from "
        f"shipping and must stay in scope: {violations}"
    )


def test_a_git_ignored_workflow_is_not_scanned(tmp_path: Path):
    """`--exclude-standard`: an ignored file is never pushed, so GitHub Actions
    never runs it — and this gate's verdict must not depend on what a developer
    happens to have left on disk."""
    root = _git_repo(tmp_path)
    (root / ".gitignore").write_text(
        "/.github/workflows/scratch.yml\n", encoding="utf-8", newline="\n"
    )
    _put_workflow(root, "kept.yml", _CLEAN)
    _put_workflow(root, "scratch.yml", _VIOLATING)
    violations = _gate.scan_violations(root)
    assert violations == [], (
        "a git-ignored scratch workflow decided this gate's verdict: "
        f"{violations}"
    )


def test_a_tree_git_cannot_describe_is_REFUSED_not_reported_clean(tmp_path: Path):
    """Fail closed. "No workflows found" and "no violations found" are the same
    green, and this checker may not reach the second by way of the first.

    Measured before the migration: this exact tree — a workflow directory
    holding a clean file, in a directory that is not a checkout — was answered
    `All workflows PASSED`, having decided the scope from a filesystem listing
    that cannot know whether the file ships.
    """
    root = tmp_path  # deliberately NOT a git repository
    _put_workflow(root, "ci.yml", _CLEAN)
    violations = _gate.scan_violations(root)
    assert violations, (
        "the checker reported a clean tree whose workflow scope git could not "
        "describe. A scope it could not derive is not a scope it verified."
    )
    assert any("git" in v.lower() for v in violations), (
        f"the refusal does not say WHY the scope is unknown: {violations}"
    )


def test_a_tracked_workflow_deleted_from_the_worktree_does_not_crash(tmp_path: Path):
    """`--cached` lists a file the working tree no longer holds.

    A directory listing could never produce that path, so this failure mode
    arrives WITH the migration and is closed in the same change rather than
    discovered on the first branch that deletes a workflow.
    """
    root = _git_repo(tmp_path)
    path = _put_workflow(root, "doomed.yml", _CLEAN)
    _put_workflow(root, "kept.yml", _CLEAN)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    path.unlink()
    assert _gate.scan_violations(root) == []


def test_the_checker_no_longer_enumerates_the_directory_itself():
    """Parsed, not grepped: a comment naming `iterdir` must not satisfy — or
    defeat — this arm."""
    import ast

    tree = ast.parse(_GATE_PATH.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"iterdir", "glob", "rglob"}
    ]
    assert offenders == [], (
        f"{_GATE_PATH.name} walks the filesystem again at line(s) {offenders}. "
        "Workflow scope comes from ci/workflow_scope.py, which asks git."
    )


@pytest.mark.parametrize("suffix", [".yml", ".yaml"])
def test_both_suffixes_stay_in_scope(tmp_path: Path, suffix: str):
    """GitHub Actions runs `.yaml` exactly as it runs `.yml`. Pinned per suffix
    so a one-armed regression cannot hide behind the other."""
    root = _git_repo(tmp_path)
    _put_workflow(root, f"probe{suffix}", _VIOLATING)
    assert any(f"probe{suffix}" in v for v in _gate.scan_violations(root))
