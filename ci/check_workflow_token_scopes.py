#!/usr/bin/env python3
"""Workflow-token least-privilege gate for this repository's CI.

Asserts, over every file in .github/workflows/:

  1. TOP_LEVEL_MISSING     — a top-level `permissions:` block is declared. Without
                             one, every job inherits the repository default token
                             scope. That default is `read` here, but `read` means
                             read on EVERY scope (actions, checks, issues,
                             packages, pull-requests, statuses, ...) — not
                             `contents: read`.
  2. TOP_LEVEL_WRITE       — every scope in that block is read-only. A workflow-level
                             write applies to EVERY job in the file, including any
                             job later added on the wider `pull_request` trigger
                             surface. Job-level blocks REPLACE this default (they
                             never merge with it), so the write belongs on the one
                             job that needs it — where it also stays visible.
     TOP_LEVEL_SHORTHAND   — and it is an explicit mapping, not the `read-all` /
                             `write-all` shorthand, which grants every scope at once.
  3. WIF_WITHOUT_ID_TOKEN  — every job containing a google-github-actions/auth step
                             has an effective `id-token: write`. This is the
                             fail-closed half: because a job-level block REPLACES
                             the workflow default rather than merging, scoping the
                             grant down is exactly the edit that can silently drop
                             it — which would break OIDC/WIF at deploy time. It
                             must break HERE instead.
  4. ID_TOKEN_WITHOUT_WIF  — and no job holds `id-token: write` without such a step,
                             i.e. no job can mint an OIDC identity token against the
                             deploy identity that it does not use.

Note that only the WORKFLOW-level block is required to be read-only. A job-level
`packages: write` (publishing) or `id-token: write` (WIF) is the correct shape and
must keep passing — the point of the gate is that a wider scope is granted per-job,
never as the file-wide default.

WHY A PARSER, NOT A LINE-SCAN
-----------------------------
Guards of this shape have repeatedly, and recently, gone
comment-blind. The most recent: a line-scanning version of this very gate had its
top-level entry regex anchored to end-of-line, so `id-token: write  # required for
WIF` — the exact idiom used in the workflows it was guarding — did not match, was
silently dropped, and the guard reported "present + read-only" and exited 0. Its
own "comment-blindness" self-checks all still passed with comment-stripping
removed entirely, because the fixtures never exercised the stripping they
advertised.

The lesson, and the reason this file exists: comment *stripping* is not what makes
a guard comment-blind — *parsing* is. PyYAML resolves comments, quoting, flow
mappings (`permissions: {contents: read}`), anchors and block scalars to a data
structure in which a comment cannot exist and a `permissions:` string inside a
`run:` body is just text. None of the four assertions above can be defeated by how
a workflow is worded.

The `--self-test` fixtures below are chosen to DISCRIMINATE: each one is input that
a textual scanner gets wrong, plus the inverse cases where a violation must still
be raised. Neutering the parser (swapping it back for a line-scan) makes the
self-test go red. `top_level_permissions` and `jobs_of` deliberately take RAW TEXT
so a mutation harness can substitute a textual implementation and prove exactly
that.

The self-test runs BEFORE the scan (both in the CLI and in the accompanying test),
so a broken guard fails loudly rather than emitting a vacuous "all PASSED" — the
failure mode a reviewer demonstrated on the line-scan version, which reported
every workflow green at exit 0 while a real regression sat in the tree.

Usage:
  python ci/check_workflow_token_scopes.py              # self-test, then scan
  python ci/check_workflow_token_scopes.py --self-test   # fixture suite only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - environment problem, must fail loud
    sys.stderr.write(
        "::error::check_workflow_token_scopes.py requires PyYAML and it is not "
        "installed. This gate fails CLOSED rather than skipping: a missing parser "
        "is a misconfiguration, not a reason to pass green. It is declared in this "
        "project's dev dependencies — install them (`pip install -e '.[dev]'`).\n"
    )
    raise SystemExit(2)

READ_ONLY_VALUES = frozenset({"read", "none"})
WIF_ACTION = "google-github-actions/auth"

# Sentinel distinguishing "no permissions key at all" from "permissions: {}",
# which is a legitimate, maximally-restrictive declaration (deny everything).
MISSING = object()


# --------------------------------------------------------------------------
# Parsing. Both take RAW TEXT: the whole contract of this gate is that the
# text is interpreted by a YAML parser, so a mutation harness can swap either
# for a textual implementation and watch the self-test fail.
# --------------------------------------------------------------------------
def top_level_permissions(text: str):
    """Return the workflow-level `permissions` value, or MISSING if undeclared."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or "permissions" not in doc:
        return MISSING
    value = doc["permissions"]
    # `permissions:` with an empty value parses as None and means "no scopes".
    return {} if value is None else value


def jobs_of(text: str) -> dict:
    """Return the `jobs` mapping (empty when absent or malformed)."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        return {}
    jobs = doc.get("jobs")
    return jobs if isinstance(jobs, dict) else {}


# --------------------------------------------------------------------------
# Assertions
# --------------------------------------------------------------------------
def _job_uses_wif(job: dict) -> bool:
    steps = job.get("steps") if isinstance(job, dict) else None
    if not isinstance(steps, list):
        return False  # reusable-workflow call (`uses:` at job level) has no steps
    for step in steps:
        if isinstance(step, dict) and str(step.get("uses", "")).startswith(WIF_ACTION):
            return True
    return False


def _grants_id_token_write(permissions) -> bool:
    if permissions is MISSING or permissions is None:
        return False
    if isinstance(permissions, str):
        return permissions == "write-all"
    if isinstance(permissions, dict):
        return str(permissions.get("id-token", "")) == "write"
    return False


def check_workflow_text(text: str, name: str) -> list[tuple[str, str]]:
    """Return a list of (code, message) violations for one workflow document."""
    violations: list[tuple[str, str]] = []

    top = top_level_permissions(text)
    if top is MISSING:
        violations.append((
            "TOP_LEVEL_MISSING",
            f"{name}: no top-level `permissions:` block — every job inherits the "
            f"repository default token scope (read on EVERY scope, not contents: read). "
            f"Declare `permissions:` with `contents: read`.",
        ))
    elif isinstance(top, str):
        violations.append((
            "TOP_LEVEL_SHORTHAND",
            f"{name}: top-level `permissions: {top}` uses the all-scopes shorthand. "
            f"Declare an explicit mapping (`contents: read`) instead.",
        ))
    elif isinstance(top, dict):
        for scope, value in top.items():
            if str(value) not in READ_ONLY_VALUES:
                violations.append((
                    "TOP_LEVEL_WRITE",
                    f"{name}: top-level `permissions` grants `{scope}: {value}` — a "
                    f"workflow-level write applies to EVERY job in the file. Move it to "
                    f"the job that needs it (a job-level block REPLACES this default, "
                    f"so restate every scope that job needs).",
                ))
    else:
        violations.append((
            "TOP_LEVEL_SHORTHAND",
            f"{name}: top-level `permissions` is neither a mapping nor a known "
            f"shorthand ({top!r}).",
        ))

    for job_name, job in jobs_of(text).items():
        if not isinstance(job, dict):
            continue
        own = job.get("permissions", MISSING)
        effective = own if own is not MISSING else top
        has_id_token = _grants_id_token_write(effective)
        uses_wif = _job_uses_wif(job)

        if uses_wif and not has_id_token:
            violations.append((
                "WIF_WITHOUT_ID_TOKEN",
                f"{name}: job `{job_name}` calls {WIF_ACTION} but has no effective "
                f"`id-token: write` — WIF/OIDC auth will fail. Declare it in that job's "
                f"own permissions block (which replaces, not merges with, the "
                f"workflow default — so restate `contents: read` there too).",
            ))
        if has_id_token and not uses_wif:
            violations.append((
                "ID_TOKEN_WITHOUT_WIF",
                f"{name}: job `{job_name}` holds `id-token: write` but has no "
                f"{WIF_ACTION} step — it can mint an OIDC identity token against the "
                f"deploy identity that it does not use. Scope the grant to the WIF "
                f"job(s) only.",
            ))

    return violations


# --------------------------------------------------------------------------
# Self-test fixtures. Every one is input a TEXTUAL scanner gets wrong (or a
# violation it must still raise), so this suite fails if the parser is ever
# swapped back for a line-scan.
# --------------------------------------------------------------------------
FIXTURES: list[tuple[str, str, set[str]]] = [
    (
        # The exact defect a review found in the line-scan version: an
        # end-of-line-anchored regex drops this entry and reports the file clean.
        "trailing comment on a top-level write grant",
        """
name: F
on: [push]
permissions:
  contents: read
  id-token: write  # required for WIF
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
""",
        {"TOP_LEVEL_WRITE", "ID_TOKEN_WITHOUT_WIF"},
    ),
    (
        # A commented-out block must never satisfy the declaration requirement.
        "permissions mentioned only in comments",
        """
name: F
on: [push]
# permissions:
#   contents: read
#   id-token: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
""",
        {"TOP_LEVEL_MISSING"},
    ),
    (
        # A column-0 comment INSIDE the block: a line-scan using a dedent test to
        # find the end of the block stops here and silently drops every entry below.
        "column-0 comment inside the permissions block",
        """
name: F
on: [push]
permissions:
  contents: read
# an aside written at column zero, mid-block
  packages: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
""",
        {"TOP_LEVEL_WRITE"},
    ),
    (
        # Flow mapping + quoted values: valid YAML that a key/value line-scan
        # cannot read at all, and which must be accepted as read-only.
        "flow mapping and quoted values are read correctly",
        """
name: F
on: [push]
permissions: {contents: "read"}
jobs:
  build:
    runs-on: ubuntu-latest
    permissions: {contents: read, id-token: write}
    steps:
      - uses: google-github-actions/auth@v3
""",
        set(),
    ),
    (
        # `permissions:` and `id-token: write` appearing as TEXT inside a run:
        # body are not a declaration. Structurally irrelevant to a parser.
        "a run: body that talks about permissions is not a declaration",
        """
name: F
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: |
          cat <<'YAML'
          permissions:
            contents: read
            id-token: write
          YAML
""",
        {"TOP_LEVEL_MISSING"},
    ),
    (
        # ... and a run: body cannot make a real violation disappear either.
        "a run: body quoting a read-only block does not mask a real write",
        """
name: F
on: [push]
permissions:
  contents: read
  actions: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "the recommended default is:"
          echo "permissions:"
          echo "  contents: read"
""",
        {"TOP_LEVEL_WRITE"},
    ),
    (
        # Job-level block replaces the top-level default; the non-WIF job must
        # NOT be treated as holding id-token. This is the shape this gate exists
        # to keep: read-only default, id-token scoped to the WIF job.
        "job-level block overrides the workflow default",
        """
name: F
on: [push]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write  # required for WIF
    steps:
      - uses: google-github-actions/auth@v3
        with:
          workload_identity_provider: x
""",
        set(),
    ),
    (
        # A job-level write OTHER than id-token (e.g. GHCR publishing) is the
        # correct shape and must not be flagged — only the workflow-level
        # default has to be read-only.
        "job-level packages: write is allowed",
        """
name: F
on: [push]
permissions:
  contents: read
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v7
""",
        set(),
    ),
    (
        # A WIF job that lost its grant must fail closed — this is the failure a
        # careless "scope it down" edit produces.
        "WIF job with no id-token: write",
        """
name: F
on: [push]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: google-github-actions/auth@v3
""",
        {"WIF_WITHOUT_ID_TOKEN"},
    ),
    (
        # The all-scopes shorthands are not least-privilege declarations.
        "read-all shorthand is rejected",
        """
name: F
on: [push]
permissions: read-all
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
""",
        {"TOP_LEVEL_SHORTHAND"},
    ),
    (
        # write-all at JOB level implies id-token: write, with no WIF step.
        "job-level write-all mints an unused id-token",
        """
name: F
on: [push]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    permissions: write-all
    steps:
      - uses: actions/checkout@v7
""",
        {"ID_TOKEN_WITHOUT_WIF"},
    ),
    (
        # Deny-everything is legitimate and must not be confused with "absent".
        "permissions: {} is a valid declaration, not a missing one",
        """
name: F
on: [push]
permissions: {}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
""",
        set(),
    ),
    (
        # `permissions:` with an empty value is also a declaration (no scopes).
        "an empty permissions value is a declaration, not a missing one",
        """
name: F
on: [push]
permissions:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
""",
        set(),
    ),
]


def fixture_failures() -> list[str]:
    """Run the fixture suite; return one description per failing fixture."""
    failures: list[str] = []
    for label, text, expected in FIXTURES:
        actual = {code for code, _ in check_workflow_text(text, "fixture")}
        if actual != expected:
            missed = sorted(expected - actual)
            extra = sorted(actual - expected)
            detail = []
            if missed:
                detail.append(f"not raised: {', '.join(missed)}")
            if extra:
                detail.append(f"unexpected: {', '.join(extra)}")
            failures.append(f"{label} ({'; '.join(detail)})")
    return failures


def workflow_files(repo_root: Path) -> list[Path]:
    workflow_dir = repo_root / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return []
    return sorted(
        p for p in workflow_dir.iterdir()
        if p.is_file() and p.suffix in {".yml", ".yaml"}
    )


def scan_results(repo_root: Path) -> list[tuple[str, list[str]]]:
    """Return (relative path, violation messages) for every workflow file."""
    results: list[tuple[str, list[str]]] = []
    for path in workflow_files(repo_root):
        try:
            rel = path.relative_to(repo_root).as_posix()
        except ValueError:  # pragma: no cover - defensive
            rel = path.name
        try:
            violations = check_workflow_text(path.read_text(encoding="utf-8"), rel)
        except yaml.YAMLError as exc:
            results.append((rel, [f"[UNPARSEABLE] {rel}: not valid YAML: {exc}"]))
            continue
        results.append((rel, [f"[{code}] {message}" for code, message in violations]))
    return results


def scan_violations(repo_root: Path) -> list[str]:
    """Return one message per violation across every workflow file.

    An empty workflow directory is itself reported as a violation: this gate
    fails closed rather than passing green on a scan that examined nothing.
    """
    results = scan_results(repo_root)
    if not results:
        return [
            f"[EMPTY_SCAN] no workflow files found under "
            f"{(repo_root / '.github' / 'workflows')} — this gate fails closed "
            f"rather than passing green on a scan that examined nothing."
        ]
    return [message for _rel, messages in results for message in messages]


def self_test() -> int:
    print("== Guard self-test (discriminating fixtures) ==")
    failures = fixture_failures()
    failed_labels = {f.split(" (")[0] for f in failures}
    for label, _text, _expected in FIXTURES:
        print(f"  {'FAIL' if label in failed_labels else 'PASS'}  {label}")
    print()
    if failures:
        for failure in failures:
            print(f"  FAILED FIXTURE: {failure}", file=sys.stderr)
        print(
            f"::error::{len(failures)} self-test fixture(s) failed — the guard's own "
            f"detection is broken, so its verdict on the real workflows cannot be "
            f"trusted. Fix the guard before reading any scan result.",
            file=sys.stderr,
        )
        return 1
    print(f"All {len(FIXTURES)} self-test fixtures PASSED.")
    return 0


def scan(repo_root: Path) -> int:
    print("== Workflow token scopes ==")
    results = scan_results(repo_root)
    for rel, messages in results:
        print(f"  {'FAIL' if messages else 'PASS'}  {rel}")
    print()
    print(f"Scanned {len(results)} workflow file(s).")

    violations = scan_violations(repo_root)
    if violations:
        for message in violations:
            print(f"  FAIL  {message}", file=sys.stderr)
        print(
            f"::error::{len(violations)} violation(s) — the workflow-level default "
            f"must be read-only (`contents: read`), with every wider scope granted "
            f"per-job. See the rationale comments in .github/workflows/*.yml (publish.yml grants `packages: write` on its publishing job alone).",
            file=sys.stderr,
        )
        return 1
    print("All workflows PASSED.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the fixture suite only, without scanning the repo",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root to scan (default: this script's parent repo)",
    )
    args = parser.parse_args()

    # Self-test ALWAYS runs first: a guard whose own detection is broken must
    # fail loudly rather than print a vacuous "All workflows PASSED".
    rc = self_test()
    if rc or args.self_test:
        return rc
    print()
    return scan(args.repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
