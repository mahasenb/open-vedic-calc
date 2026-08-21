#!/usr/bin/env python3
"""Scan a pull request's TITLE and BODY for proprietary consumer references.

THE GAP THIS CLOSES
===================
``ci/check_no_proprietary_refs.py`` scans two things: the contents of tracked
files, and the commit messages in the pushed range. A pull request's title and
body are **neither**. They live only in GitHub's database, they can be edited
after the PR is opened, and they are among the most public prose this project
produces — a PR page is world-readable and indexed exactly like the code is.

So the one artifact written most freely, by hand, in the register of "explaining
this change to a reviewer", had nothing checking it at all.

WHY A SEPARATE SCRIPT
=====================
The tree gate's ``main()`` walks the repository and reads git. This reads two
strings out of the environment and needs neither. Keeping them apart means this
check cannot break the tree scan, and the tree gate keeps a single job.

The pattern machinery is IMPORTED from the tree gate rather than reimplemented,
so the two can never disagree about what a forbidden token is. That matters:
a second copy of the matching rules is a second place for them to rot.

INPUT ARRIVES AS DATA, NEVER AS ARGV
====================================
Title and body come from ``PR_TITLE`` / ``PR_BODY`` in the environment. They are
attacker-controlled — anyone may open a pull request against a public repository
— and a GitHub Actions ``${{ ... }}`` expression is **textual substitution
performed before the shell parses the command**, so interpolating a title into a
``run:`` body is a remote code execution vector. ``env:`` hands the value to the
process as data. ``ci/tests/test_pr_text_check.py`` asserts no workflow here
does it the other way.

THE SECRET-AVAILABILITY DECISION (fail closed, with an enumerated exception)
===========================================================================
The brand tokens arrive in ``PROPRIETARY_REF_TOKENS``, a repository Actions
secret. GitHub does not supply it to every run, and "the secret is empty" has
two completely different meanings that must not be conflated:

* **It should have been here.** A same-repo pull request opened by a person gets
  repository secrets. An empty value means the secret was never provisioned or
  has been cleared, and the scan would then check only the legacy pattern while
  reporting a clean verdict about brand tokens it never held. That is a control
  silently running at reduced strength, so it is a **hard failure** (exit 2).

* **GitHub withheld it, and the PR author cannot do anything about that.**
  Two enumerated cases:

  1. **a fork PR** — secrets are deliberately not exposed to workflows running
     from a fork, because the fork controls the code;
  2. **a Dependabot PR** — these are served from the separate Dependabot secret
     store, so a repository Actions secret is absent even though the branch
     lives in this repository and ``head.repo == repository`` is true for it.

  Refusing either would redden pull requests for a reason their author cannot
  fix — every fork contribution, and every weekly dependency bump. So the run
  continues, **loudly announcing what it could not check**.

Case 2 is the one a naive "same repo means secrets" rule gets wrong, and the
carve-out is deliberately the *permissive* direction: if this reading of
Dependabot's secret handling were wrong, the cost is that a Dependabot PR gets
an advisory scan instead of a hard-failing one — whereas omitting the carve-out
while the reading is right would break every scheduled bump.

**Advisory scopes the MISSING SECRET, never the verdict.** The legacy pattern
needs no secret and is live on every run, so a match still fails the job on a
fork exactly as it does anywhere else. An advisory mode that tolerated hits
would be a bypass, not a degradation.

Exit codes: 0 clean · 1 a forbidden reference was found · 2 the token secret is
absent where it should have been present.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import sys
from pathlib import Path
from typing import Mapping

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TREE_GATE = _REPO_ROOT / "ci" / "check_no_proprietary_refs.py"

_SECRET_NAME = "PROPRIETARY_REF_TOKENS"

# Actors GitHub serves from a secret store other than the repository's own, so
# an absent repository secret is expected rather than a provisioning failure.
_ACTORS_WITHOUT_REPOSITORY_SECRETS = frozenset({"dependabot[bot]"})


def _load_tree_gate():
    """Import the tree gate as a module so the patterns have ONE definition.

    By path, not ``from ci import ...``: ``ci/`` has no ``__init__.py`` by
    design, and this runs as ``python ci/check_pr_text.py`` where that import
    form would not resolve.
    """
    spec = importlib.util.spec_from_file_location("_tree_gate_for_pr_text", _TREE_GATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tokens_expected(head_repo: str, repository: str, actor: str) -> bool:
    """Should this run have been given the repository's token secret?

    Unprovable means NOT expected: an empty ``head_repo`` (the field is absent
    on anything that is not a pull request) is treated as a fork rather than
    assumed to be this repository, so a missing fact can never manufacture a
    hard failure.
    """
    if not head_repo or not repository:
        return False
    if head_repo != repository:
        return False  # a fork: GitHub does not expose secrets to it
    if actor in _ACTORS_WITHOUT_REPOSITORY_SECRETS:
        return False  # served from a different secret store
    return True


def scanned_fields(environ: Mapping[str, str]) -> dict[str, str]:
    """The named fields this check reads, so a caller can assert the coverage.

    Returned as a mapping rather than scanned inline because "which fields did
    you actually look at" is the question a reviewer needs answered, and a test
    that can only observe the verdict cannot tell one field from two.
    """
    return {
        "title": environ.get("PR_TITLE", "") or "",
        "body": environ.get("PR_BODY", "") or "",
    }


def _patterns(environ: Mapping[str, str]) -> tuple[list[re.Pattern[str]], int]:
    """(patterns, brand token count) — legacy always, brand tokens when supplied."""
    gate = _load_tree_gate()
    env = dict(environ)
    patterns = gate._build_forbidden_patterns(env)
    raw = env.get(_SECRET_NAME, "") or ""
    token_count = len([t for t in raw.split(",") if t.strip()])
    return patterns, token_count


def main(environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ

    head_repo = env.get("PR_HEAD_REPO", "") or ""
    repository = env.get("GITHUB_REPOSITORY", "") or ""
    actor = env.get("GITHUB_ACTOR", "") or ""

    patterns, token_count = _patterns(env)
    expected = tokens_expected(head_repo, repository, actor)

    if expected and token_count == 0:
        sys.stderr.write(
            f"ERROR: {_SECRET_NAME} is empty on a run that should have it.\n"
            f"  head repository : {head_repo or '<unset>'}\n"
            f"  actor           : {actor or '<unset>'}\n\n"
            "This is a same-repository pull request, so GitHub supplies "
            f"repository secrets to it. An empty {_SECRET_NAME} therefore means "
            "the secret has not been provisioned, or has been cleared. Scanning "
            "would then check only the legacy pattern while reporting a clean "
            "verdict about brand tokens it never held -- a control running "
            "silently at reduced strength.\n\n"
            f"Provision {_SECRET_NAME} as a repository Actions secret "
            "(comma-separated). Its value must never be written into this "
            "repository's tracked source.\n"
        )
        return 2

    if token_count == 0:
        why = (
            "this pull request comes from a fork"
            if head_repo != repository
            else f"'{actor}' is served from a separate secret store"
        )
        print(
            f"ADVISORY: {_SECRET_NAME} is not available to this run because "
            f"{why}, which is GitHub's behaviour and not a fault of this pull "
            "request.\n"
            "  checked        : the legacy pattern (needs no secret)\n"
            "  NOT checked    : the current brand token(s)\n"
            "A match on what IS checked still fails this job -- advisory scopes "
            "the missing secret, never the verdict."
        )

    violations: list[str] = []
    gate = _load_tree_gate()
    for field, text in scanned_fields(env).items():
        if text:
            gate._scan_text(f"PR {field}", text, violations, patterns)

    if violations:
        sys.stderr.write(
            "ERROR: proprietary consumer reference(s) found in this pull "
            "request's text:\n"
        )
        for violation in violations:
            sys.stderr.write(f"  {violation}\n")
        sys.stderr.write(
            "\nA pull request's title and body are as public as this repo's "
            'code. Use generic terms ("the caller", "the HTTP client", "the '
            'consumer"), then EDIT the pull request -- this check re-runs on '
            "edit.\n"
        )
        return 1

    checked = "legacy + brand" if token_count else "legacy only"
    print(
        f"OK: no proprietary consumer references in the PR title or body "
        f"({len(patterns)} pattern(s) applied: {checked})."
    )
    return 0


def self_test() -> int:
    """Prove the detector still discriminates, as the sibling checkers do.

    A checker that cannot fail is not a checker. This runs before the real
    scan in CI, so a detector that has stopped matching stops the job rather
    than certifying the PR text green.
    """
    synthetic = "zzzselftestbrand"
    base = {
        "PR_HEAD_REPO": "o/r",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_ACTOR": "human",
        _SECRET_NAME: synthetic,
        "PR_TITLE": "neutral title",
        "PR_BODY": "neutral body about the caller",
    }
    # (description, callable returning the observed value, expected value)
    probes = [
        ("neutral text is accepted", lambda: main(base), 0),
        ("a token in the body is detected",
         lambda: main(dict(base, PR_BODY=f"leak {synthetic}")), 1),
        ("a token in the title is detected",
         lambda: main(dict(base, PR_TITLE=f"leak {synthetic}")), 1),
        ("an absent-but-expected secret is refused",
         lambda: main(dict(base, **{_SECRET_NAME: ""})), 2),
        ("a fork run is not refused for a secret it cannot have",
         lambda: main(dict(base, **{_SECRET_NAME: "", "PR_HEAD_REPO": "fork/r"})), 0),
        ("the Dependabot carve-out is in force",
         lambda: tokens_expected("o/r", "o/r", "dependabot[bot]"), False),
    ]

    # The negative probes deliberately trip the checker, and their output would
    # otherwise fill a PASSING CI log with ERROR blocks describing nothing real.
    # Captured, not silenced: a probe returning the wrong value still fails this
    # self-test loudly, below.
    failures = []
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        for description, probe, expected_value in probes:
            observed = probe()
            if observed != expected_value:
                failures.append(
                    f"{description}: expected {expected_value!r}, got {observed!r}"
                )

    if failures:
        sys.stderr.write("ERROR: --self-test found the detector broken:\n")
        for failure in failures:
            sys.stderr.write(f"  {failure}\n")
        return 1
    print(f"OK: --self-test passed ({len(probes)} probes; detector discriminates, scoping in force).")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        raise SystemExit(self_test())
    raise SystemExit(main())
