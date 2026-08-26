#!/usr/bin/env python3
"""ONE definition of "which files are this repository's GitHub Actions workflows".

WHY THIS EXISTS
===============
Four guards in ``ci/tests/`` need that set, and before this module they answered
it four different ways. Two of them were wrong in the same way, and the shape of
the error is worth naming because it is the third time this workspace has paid
for it: they enumerated ``.github/workflows/*.yml``.

GitHub Actions runs a ``.yaml`` file exactly as it runs a ``.yml`` one. A
``*.yml`` glob therefore makes an affirmative "no violations" claim about a file
it never opened -- the same fail-open shape as an extension allowlist in the
neutrality gate, and as a directory-name blacklist in the scan set before that.
The claim is not "we checked and it was fine"; it is "we did not look".

WHAT IT ASKS, AND WHY EACH PART
===============================
``git ls-files -z --cached --others --exclude-standard -- .github/workflows``:

* **both suffixes** -- ``.yml`` and ``.yaml``, because the runner does;
* **git, not a directory glob** -- ``--exclude-standard`` drops a developer's
  ignored scratch workflow, so a guard's verdict does not depend on what else
  happens to be on the disk, and the scope comes from the source of truth for
  "what does this repository publish" rather than from a filesystem walk;
* **``--others``** -- an UNTRACKED workflow stays in scope. The cheapest moment
  to catch a violation is the commit that introduces it, and at that moment the
  file is not yet tracked; tracked-only would arrive one commit late;
* **``-z``, and bytes mode** -- without ``-z`` git QUOTES a path holding
  non-ASCII bytes and renders each byte as a backslash-octal escape (measured,
  the fail-open ``ci/check_pytest_collection.py`` shipped exactly that), and a
  text-mode read moves the decode onto a reader thread where its failure is
  invisible and the caller is handed ``stdout=None``.

FAILS CLOSED
============
Outside a checkout, or when git refuses, this RAISES rather than returning an
empty list. "Zero workflows found" and "zero violations found" are the same
green, and a guard may not reach the second by way of the first.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# The two suffixes GitHub Actions executes. Not a preference -- the runner reads
# both, so a guard that reads one is silent about half its subject.
WORKFLOW_SUFFIXES = frozenset({".yml", ".yaml"})

WORKFLOWS_DIR = ".github/workflows"


class WorkflowScopeError(RuntimeError):
    """git could not describe the workflow directory, so the scope is unknown.

    Raised rather than degraded to an empty list on purpose: every caller here
    asserts something of the form "no workflow does X", which is vacuously true
    of no workflows at all.
    """


def workflow_paths(repo_root: Path) -> list[Path]:
    """Every workflow file under *repo_root*, both suffixes, asked of git.

    Returns absolute paths, sorted, deduplicated -- a path can be reported by
    both ``--cached`` and ``--others`` in some index states.
    """
    try:
        result = subprocess.run(
            [
                "git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                "--", WORKFLOWS_DIR,
            ],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
    except OSError as exc:  # no git on this machine
        raise WorkflowScopeError(
            f"git could not be run in {repo_root}: {exc}. This guard derives its "
            "scope from git and fails closed rather than reporting no workflows."
        ) from exc
    if result.returncode != 0:
        raise WorkflowScopeError(
            f"`git ls-files` failed in {repo_root}: "
            f"{result.stderr.decode('utf-8', 'replace').strip()}. This guard "
            "derives its scope from git and fails closed rather than reporting "
            "no workflows."
        )
    # Bytes, decoded here: a filename need not be valid UTF-8, and a text-mode
    # read would kill the reader thread instead of raising where we can see it.
    try:
        listing = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowScopeError(
            f"`git ls-files` returned a path that is not valid UTF-8 in "
            f"{repo_root} ({exc.reason} at byte {exc.start}), so the workflow "
            "scope cannot be read."
        ) from exc
    # With -z the records are exact: never strip them, since a leading or
    # trailing space is a legal filename character.
    records = [record for record in listing.split("\0") if record]
    return sorted(
        {
            repo_root / record
            for record in records
            if Path(record).suffix in WORKFLOW_SUFFIXES
        }
    )
