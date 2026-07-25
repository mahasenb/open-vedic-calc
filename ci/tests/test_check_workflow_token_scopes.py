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
import sys
from pathlib import Path

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
