"""Regression guard: CI keeps actually running the suite against real Swiss data (CALC-1).

``tests/test_swiss_ephemeris.py`` SKIPS when no ephemeris data files are present —
deliberately, because the fast ``test`` job and the default local loop genuinely have
none. That skip is only safe because one CI job does supply the data and sets
``REQUIRE_SWISS_EPHEMERIS=1``, which converts the skip into a hard failure.

Delete that job, drop its fetch step, or lose that env var, and the accuracy tests
quietly become three permanent skips while the workflow still looks like it covers
them. That is the fail-open shape this file exists to prevent — the same class of
defect as the "docker-test-image only BUILDS the test image, it never runs the suite
against real data" gap that made CALC-1 look closed when it was not.

WHY A PARSER, NOT A LINE-SCAN
-----------------------------
``grep REQUIRE_SWISS_EPHEMERIS .github/workflows/test.yml`` matches the string in a
comment, in a commented-out step, in another job's ``run:`` body, or in this very
file's name — none of which means the job sets it. PyYAML resolves the workflow to a
data structure in which a comment cannot exist, so every assertion below is about
structure: a job exists, its steps include one whose ``run`` invokes the fetcher, and
its pytest step's ``env`` mapping carries the flag. ``test_parser_discriminates``
feeds that logic the fixtures a textual scanner gets wrong, and the real assertions
re-invoke it so an unproven parser can never certify the workflow green.
"""
from __future__ import annotations

import shlex
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TEST_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "test.yml"
_JOB = "swiss-ephemeris"
_FLAG = "REQUIRE_SWISS_EPHEMERIS"
_FETCHER = "ci/fetch_swiss_ephemeris.py"


# ---------------------------------------------------------------------------
# Structural helpers — take a PARSED document, never raw text
# ---------------------------------------------------------------------------


def _job(document: dict, name: str) -> dict | None:
    jobs = document.get("jobs") or {}
    job = jobs.get(name)
    return job if isinstance(job, dict) else None


def _steps(job: dict) -> list[dict]:
    return [step for step in (job.get("steps") or []) if isinstance(step, dict)]


def _runs(job: dict) -> list[str]:
    return [str(step["run"]) for step in _steps(job) if "run" in step]


def _commands(run: str) -> list[list[str]]:
    """Tokenise a ``run:`` body into individual shell commands.

    A ``run:`` body is shell script, so structure alone cannot answer "does this
    invoke pytest" — but a substring search cannot either: `echo "pytest tests/"`
    contains the string and runs nothing. Tokenising with shlex (comments stripped)
    puts the words in argv positions, so a quoted string collapses to ONE token and
    can no longer masquerade as a command. Commands are split on the shell operators
    that start a new one.
    """
    commands: list[list[str]] = []
    for line in run.splitlines():
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            # Unbalanced quoting — treat the line as opaque rather than guessing.
            continue
        current: list[str] = []
        for token in tokens:
            if token in {"&&", "||", ";", "|", "&"}:
                if current:
                    commands.append(current)
                current = []
                continue
            current.append(token)
        if current:
            commands.append(current)
    return commands


def _invokes(run: str, program: str, *required_tokens: str) -> bool:
    """True when some command in ``run`` has ``program`` and all required tokens as ARGV tokens."""
    for tokens in _commands(run):
        if program not in tokens:
            continue
        if all(any(required in token for token in tokens) for required in required_tokens):
            return True
    return False


def _has_token_sequence(run: str, *sequence: str) -> bool:
    """True when some command contains the given consecutive argv tokens (e.g. `uv sync --frozen`)."""
    width = len(sequence)
    for tokens in _commands(run):
        for index in range(len(tokens) - width + 1):
            if tuple(tokens[index : index + width]) == sequence:
                return True
    return False


def _job_invokes_fetcher(job: dict) -> bool:
    return any(_invokes(run, "python", _FETCHER) for run in _runs(job))


def _job_self_tests_fetcher(job: dict) -> bool:
    return any(_invokes(run, "python", _FETCHER, "--self-test") for run in _runs(job))


def _uses(job: dict) -> list[str]:
    return [str(step["uses"]) for step in _steps(job) if "uses" in step]


def _step_env_flag_values(job: dict, flag: str) -> list[str]:
    """Every value the given env key takes across the job's steps and job-level env."""
    values: list[str] = []
    job_env = job.get("env")
    if isinstance(job_env, dict) and flag in job_env:
        values.append(str(job_env[flag]))
    for step in _steps(job):
        env = step.get("env")
        if isinstance(env, dict) and flag in env:
            values.append(str(env[flag]))
    return values


def _runs_the_test_suite(run: str) -> bool:
    """True when a command in ``run`` really invokes pytest over the tests/ tree."""
    for tokens in _commands(run):
        if "pytest" not in tokens:
            continue
        if any(token == "tests" or token.startswith("tests/") or token.startswith("tests\\") for token in tokens):
            return True
    return False


def _runs_the_suite_with_the_flag(job: dict) -> bool:
    """A step must BOTH invoke pytest over tests/ AND carry the flag=1 in scope."""
    job_env = job.get("env")
    job_level = isinstance(job_env, dict) and str(job_env.get(_FLAG)) == "1"
    for step in _steps(job):
        run = step.get("run")
        if not run or not _runs_the_test_suite(str(run)):
            continue
        env = step.get("env")
        step_level = isinstance(env, dict) and str(env.get(_FLAG)) == "1"
        # Also accept the inline `FLAG=1 pytest ...` shell form — as an argv token,
        # so the same string inside a quoted echo argument does not count.
        inline = any(f"{_FLAG}=1" in tokens for tokens in _commands(str(run)))
        if job_level or step_level or inline:
            return True
    return False


# ---------------------------------------------------------------------------
# Discrimination fixtures — inputs a grep over the raw YAML gets WRONG
# ---------------------------------------------------------------------------

_FIXTURE_GOOD = """
jobs:
  swiss-ephemeris:
    steps:
      - run: python ci/fetch_swiss_ephemeris.py
      - env:
          REQUIRE_SWISS_EPHEMERIS: "1"
        run: pytest tests/ -q
"""

_FIXTURE_GOOD_INLINE = """
jobs:
  swiss-ephemeris:
    steps:
      - run: python ci/fetch_swiss_ephemeris.py
      - run: REQUIRE_SWISS_EPHEMERIS=1 pytest tests/ -q
"""

_FIXTURE_GOOD_JOB_LEVEL_ENV = """
jobs:
  swiss-ephemeris:
    env:
      REQUIRE_SWISS_EPHEMERIS: "1"
    steps:
      - run: python ci/fetch_swiss_ephemeris.py
      - run: pytest tests/ -q
"""

_FIXTURE_FLAG_ONLY_IN_A_COMMENT = """
jobs:
  swiss-ephemeris:
    steps:
      - run: python ci/fetch_swiss_ephemeris.py
      # REQUIRE_SWISS_EPHEMERIS: "1"
      - run: pytest tests/ -q
"""

_FIXTURE_STEP_COMMENTED_OUT = """
jobs:
  swiss-ephemeris:
    steps:
      - run: python ci/fetch_swiss_ephemeris.py
      # - env:
      #     REQUIRE_SWISS_EPHEMERIS: "1"
      #   run: pytest tests/ -q
"""

_FIXTURE_FLAG_SET_BUT_SUITE_NOT_RUN = """
jobs:
  swiss-ephemeris:
    env:
      REQUIRE_SWISS_EPHEMERIS: "1"
    steps:
      - run: python ci/fetch_swiss_ephemeris.py
      - run: echo "pytest tests/ would go here"
"""

_FIXTURE_FLAG_ZERO = """
jobs:
  swiss-ephemeris:
    steps:
      - run: python ci/fetch_swiss_ephemeris.py
      - env:
          REQUIRE_SWISS_EPHEMERIS: "0"
        run: pytest tests/ -q
"""

_FIXTURE_SUITE_ONLY_ECHOED = """
jobs:
  swiss-ephemeris:
    env:
      REQUIRE_SWISS_EPHEMERIS: "1"
    steps:
      - run: python ci/fetch_swiss_ephemeris.py
      - run: echo "pytest tests/ -q"
"""

_FIXTURE_SUITE_IN_ANOTHER_JOB = """
jobs:
  swiss-ephemeris:
    steps:
      - run: python ci/fetch_swiss_ephemeris.py
  other:
    steps:
      - env:
          REQUIRE_SWISS_EPHEMERIS: "1"
        run: pytest tests/ -q
"""


def test_parser_discriminates() -> None:
    """Every fixture below is something a textual scan of the YAML gets wrong."""

    def check(text: str) -> bool:
        job = _job(yaml.safe_load(text), _JOB)
        assert job is not None, "fixture is missing the job itself"
        return _runs_the_suite_with_the_flag(job)

    # Accepted forms — all three are genuinely correct wiring.
    assert check(_FIXTURE_GOOD) is True
    assert check(_FIXTURE_GOOD_INLINE) is True
    assert check(_FIXTURE_GOOD_JOB_LEVEL_ENV) is True

    # Rejected forms — each contains the literal flag string, none of them runs the
    # suite with it in effect.
    assert check(_FIXTURE_FLAG_ONLY_IN_A_COMMENT) is False
    assert check(_FIXTURE_STEP_COMMENTED_OUT) is False
    assert check(_FIXTURE_FLAG_SET_BUT_SUITE_NOT_RUN) is False
    assert check(_FIXTURE_SUITE_ONLY_ECHOED) is False
    assert check(_FIXTURE_FLAG_ZERO) is False
    assert check(_FIXTURE_SUITE_IN_ANOTHER_JOB) is False

    # And the fetcher-step detection must be equally structural: neither a
    # commented-out step nor an echoed mention counts as running the fetcher.
    for decoy in (
        """
jobs:
  swiss-ephemeris:
    steps:
      # - run: python ci/fetch_swiss_ephemeris.py
      - run: pytest tests/ -q
""",
        """
jobs:
  swiss-ephemeris:
    steps:
      - run: echo "run python ci/fetch_swiss_ephemeris.py first"
      - run: pytest tests/ -q
""",
    ):
        job = _job(yaml.safe_load(decoy), _JOB)
        assert job is not None
        assert not _job_invokes_fetcher(job), "a decoy fetcher mention was accepted"
        assert not _job_self_tests_fetcher(job), "a decoy --self-test mention was accepted"

    # A real invocation in any of its plausible spellings must be accepted.
    for real in (
        """
jobs:
  swiss-ephemeris:
    steps:
      - run: python ci/fetch_swiss_ephemeris.py
      - run: python ci/fetch_swiss_ephemeris.py --self-test
"""
    ,
        """
jobs:
  swiss-ephemeris:
    steps:
      - run: |
          python ci/fetch_swiss_ephemeris.py --self-test
          python ci/fetch_swiss_ephemeris.py
""",
    ):
        job = _job(yaml.safe_load(real), _JOB)
        assert job is not None
        assert _job_invokes_fetcher(job), "a real fetcher invocation was rejected"
        assert _job_self_tests_fetcher(job), "a real --self-test invocation was rejected"

    # `uv sync --frozen` must be a consecutive argv sequence, not a substring.
    assert _has_token_sequence("uv sync --frozen --extra dev", "uv", "sync", "--frozen")
    assert not _has_token_sequence('echo "uv sync --frozen"', "uv", "sync", "--frozen")


# ---------------------------------------------------------------------------
# The real workflow
# ---------------------------------------------------------------------------


def _real_job() -> dict:
    document = yaml.safe_load(_TEST_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{_TEST_WORKFLOW} did not parse to a mapping"
    job = _job(document, _JOB)
    assert job is not None, (
        f"{_TEST_WORKFLOW} has no `{_JOB}` job. That job is the ONLY thing that runs "
        "this engine's suite against real Swiss ephemeris data; without it "
        "tests/test_swiss_ephemeris.py becomes three permanent skips and CI is back to "
        "validating the Moshier fallback only (CALC-1)."
    )
    return job


def test_swiss_job_fetches_the_data_files() -> None:
    test_parser_discriminates()
    job = _real_job()
    runs = _runs(job)
    assert _job_invokes_fetcher(job), (
        f"the `{_JOB}` job no longer invokes {_FETCHER}, so it has no ephemeris data "
        "and can only be running on the Moshier fallback. Steps found:\n  - "
        + "\n  - ".join(runs)
    )


def test_swiss_job_self_tests_the_verifier_before_trusting_it() -> None:
    test_parser_discriminates()
    job = _real_job()
    assert _job_self_tests_fetcher(job), (
        f"the `{_JOB}` job no longer runs `{_FETCHER} --self-test`. A checksum "
        "verifier that has stopped verifying must not be able to certify the real "
        "download. Steps found:\n  - " + "\n  - ".join(_runs(job))
    )


def test_swiss_job_runs_the_suite_with_the_fail_closed_flag() -> None:
    test_parser_discriminates()
    job = _real_job()
    assert _runs_the_suite_with_the_flag(job), (
        f"the `{_JOB}` job does not run `pytest tests/` with {_FLAG}=1 in effect. "
        f"Without that flag tests/test_swiss_ephemeris.py SKIPS when the ephemeris "
        "data is absent, so this job would report success while validating nothing. "
        f"env values seen for {_FLAG}: {_step_env_flag_values(job, _FLAG) or 'none'}; "
        "run steps:\n  - " + "\n  - ".join(_runs(job))
    )


def test_swiss_job_installs_the_frozen_dependency_set() -> None:
    """Golden values are only reproducible against the pinned resolve."""
    test_parser_discriminates()
    runs = _runs(_real_job())
    assert any(_has_token_sequence(run, "uv", "sync", "--frozen") for run in runs), (
        f"the `{_JOB}` job no longer installs with `uv sync --frozen`. The committed "
        "Swiss golden values are reproducible only against the pinned dependency set "
        "(the same one the image ships); a floating resolve turns a dependency's own "
        "numeric change into a mystery golden failure. Steps found:\n  - "
        + "\n  - ".join(runs)
    )


def test_swiss_job_caches_the_data_by_manifest_hash() -> None:
    """A cache keyed on anything but the manifest could satisfy a new pin with old bytes."""
    test_parser_discriminates()
    job = _real_job()
    assert any(step.startswith("actions/cache") for step in _uses(job)), (
        f"the `{_JOB}` job no longer caches data/ephe; every run would re-download "
        "~5 MB from the upstream host"
    )
    for step in _steps(job):
        if not str(step.get("uses", "")).startswith("actions/cache"):
            continue
        key = str((step.get("with") or {}).get("key", ""))
        assert "ci/swiss_ephemeris.json" in key, (
            "the ephemeris cache key no longer includes ci/swiss_ephemeris.json, so a "
            f"stale cache could survive a checksum change. key={key!r}"
        )


def test_manifest_and_goldens_are_committed() -> None:
    assert (_REPO_ROOT / _FETCHER).is_file(), f"{_FETCHER} is missing"
    assert (_REPO_ROOT / "ci" / "swiss_ephemeris.json").is_file(), (
        "ci/swiss_ephemeris.json (the checksum-pinned data manifest) is missing"
    )
    assert (_REPO_ROOT / "tests" / "goldens" / "swiss_ephemeris_goldens.json").is_file(), (
        "tests/goldens/swiss_ephemeris_goldens.json is missing — the Swiss accuracy "
        "assertions have nothing to compare against"
    )
