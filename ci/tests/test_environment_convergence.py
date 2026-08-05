"""Regression guard: ONE dependency resolve, validated where it ships.

WHY THIS FILE EXISTS
--------------------
This repository resolved its dependencies in more than one way at once, and the
resolve the test suite validated was not the resolve that shipped. Measured on
2026-08-05, four distinct installed sets existed simultaneously:

  * the ``test`` jobs in ``test.yml`` and ``publish.yml`` installed with a
    floating ``pip install -e ".[dev]"`` — which ignores ``uv.lock`` entirely
    and resolves whatever is newest at the moment the job runs;
  * the ``swiss-ephemeris`` job and ``Dockerfile.test`` installed
    ``uv sync --frozen --extra dev``;
  * the shipped ``Dockerfile`` installs ``uv sync --frozen --no-dev``;
  * a developer checkout's ``.venv`` was a fourth resolve again, on a
    different interpreter entirely.

The floating set differed from the shipped set on **13 packages that ship**
(fastapi 0.141.1 vs 0.136.3, uvicorn 0.52.1 vs 0.48.0, anyio, certifi, cffi,
charset-normalizer, click, idna, pytz, typing-extensions, websockets,
annotated-types, annotated-doc). So the suite that gates every merge was
certifying a dependency set no deployment ever ran.

WHY IT IS AN ACCURACY GUARD, NOT HOUSEKEEPING
---------------------------------------------
``uv.lock`` is not one resolve — it FORKS. Measured: of 73 locked package
names, exactly two resolve to more than one version, both split on the same
boundary, ``python_full_version`` 3.11:

    numpy            2.2.6  (< 3.11)   /  2.4.6  (>= 3.11)
    timezonefinder   8.2.0  (< 3.11)   /  8.2.5  (>= 3.11)

``numpy`` is not incidental here. It is imported inside the served compute
path — ``jhora/horoscope/chart/strength.py`` rounds and sums Shadbala and
Bhavabala through ``np.floor``/``np.rint``/``np.around``, and
``jhora/horoscope/chart/ashtakavarga.py`` sums the varga tables through
``np.asarray(...).sum(axis=0)`` and ``np.multiply``. A change of numpy is
therefore capable of moving a number this service exists to produce.

The consequence is the part worth internalising: **changing the interpreter is
not a version change, it is a numerics change**, and it arrives with no diff
that looks like one. ``.github/dependabot.yml`` watches the ``docker``
ecosystem weekly precisely so the pinned base image gets CVE-driven bumps — so
a routine, well-intentioned ``python:3.10-slim`` -> ``python:3.11-slim`` bump
would cross that fork and swap the numpy under every Shadbala value, with
``pyproject.toml`` and ``uv.lock`` both untouched.

WHAT THIS GUARD ASSERTS
-----------------------
1. The running interpreter is the pinned one (``.python-version``) — because a
   lock that forks is only one resolve once the interpreter is fixed.
2. Every installed distribution matches what ``uv.lock`` resolves FOR THAT
   INTERPRETER, and the comparison provably included the packages that can
   move a served number.
3. Every CI job that runs the suite installs from the frozen lock and takes
   its interpreter from ``.python-version`` — so the divergence cannot be
   reintroduced in a workflow edit.
4. Both Dockerfiles' base images are on that same pinned interpreter — so the
   base-image bump above cannot silently cross the fork.

WHY STRUCTURE AND MEASUREMENT, NOT A LINE-SCAN
----------------------------------------------
Every assertion below reads a parsed representation: ``uv.lock`` and
``pyproject.toml`` through a TOML parser, the workflows through PyYAML, the
Dockerfiles through the instruction parser already proven by
``test_image_bakes_ephemeris.py`` (reused by path, not imported as a package —
``ci/`` has no ``__init__.py`` by design). A ``grep`` for ``uv sync --frozen``
matches the string in a comment, including the comment explaining this guard.

And the comparison itself is asserted to be non-empty: "no differences found"
is vacuously true of a comparison that examined nothing, so the number of
distributions compared is printed and floored against the project's own
declared runtime dependencies.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
from importlib import metadata

import pytest
import yaml
from packaging.markers import Marker
from packaging.utils import canonicalize_name

# `packaging` is deliberately NOT declared in the dev extras the way `tomli`
# is: it is a hard dependency of pytest itself, so it cannot be absent in any
# environment capable of running this file. `tomli` has no such relationship —
# it arrived only transitively, via pip-audit — which is why that one is
# declared explicitly in pyproject.toml.
try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # Python 3.10 — the interpreter this repo pins
    import tomli as tomllib

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_LOCK = _REPO_ROOT / "uv.lock"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_PYTHON_VERSION_FILE = _REPO_ROOT / ".python-version"
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"
_DOCKERFILES = (_REPO_ROOT / "Dockerfile", _REPO_ROOT / "Dockerfile.test")

# The four pins that can move a served number: the ephemeris itself, the chart
# library that owns the lunar-node levers, and the two packages the lock forks
# on. If the comparison below ever runs without these in it, it is not checking
# the thing it exists to check — so their presence is asserted, not assumed.
_ACCURACY_CRITICAL = frozenset(
    {"pyswisseph", "pyjhora", "numpy", "timezonefinder"}
)

# Installer plumbing a virtualenv may carry without it appearing in the lock.
# Deliberately tiny: anything else installed-but-unlocked means the environment
# is not the one the lock describes.
_NOT_LOCKED_OK = frozenset({"setuptools", "wheel", "uv", "pkg-resources"})


# ---------------------------------------------------------------------------
# Reuse the proven Dockerfile parser rather than writing a second one.
# ---------------------------------------------------------------------------
def _load_dockerfile_parser():
    """Load ``parse_dockerfile`` from its sibling guard BY PATH.

    ``ci/`` has no ``__init__.py`` by design, so ``from ci.tests… import …``
    raises under the bare ``pytest`` console script that CI actually invokes
    (it does not put the CWD on ``sys.path``; ``python -m pytest`` does). Path
    loading works under both, which is the documented pattern for this tree.
    """
    module_path = pathlib.Path(__file__).with_name("test_image_bakes_ephemeris.py")
    spec = importlib.util.spec_from_file_location(
        "_image_bake_guard_for_convergence", module_path
    )
    assert spec is not None and spec.loader is not None, (
        f"could not load the Dockerfile parser from {module_path}"
    )
    module = importlib.util.module_from_spec(spec)
    # Register before executing: the sibling defines a @dataclass, and
    # dataclasses resolves `sys.modules[cls.__module__]` while processing the
    # class. A module executed from a spec without being registered has no
    # entry there, and the decorator raises on Python 3.10.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.parse_dockerfile


parse_dockerfile = _load_dockerfile_parser()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _pinned_python() -> tuple[int, int]:
    """The (major, minor) every environment here must run, from the one file
    that declares it."""
    assert _PYTHON_VERSION_FILE.is_file(), (
        f"{_PYTHON_VERSION_FILE.name} is missing. It is the single declaration "
        "of the interpreter this project resolves against — uv reads it, "
        "actions/setup-python reads it via python-version-file, and the "
        "Dockerfile base images are checked against it. Without it, uv.lock's "
        "3.11 fork (numpy 2.2.6/2.4.6, timezonefinder 8.2.0/8.2.5) is "
        "unresolved and 'the locked set' names two different sets of numbers."
    )
    raw = _PYTHON_VERSION_FILE.read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", raw)
    assert match, (
        f"{_PYTHON_VERSION_FILE.name} must hold a plain 'MAJOR.MINOR' (or "
        f"'MAJOR.MINOR.PATCH') version; found {raw!r}."
    )
    return int(match.group(1)), int(match.group(2))


def _lock_data() -> dict:
    return tomllib.loads(_LOCK.read_text(encoding="utf-8"))


def _declared_runtime_dependencies() -> set[str]:
    """Canonical names of the project's own ``[project].dependencies``.

    Used as a DERIVED floor for the comparison below — a hard-coded count would
    rot the moment a dependency is added or removed, and a floor that rots is a
    floor that stops failing.
    """
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    names = set()
    for requirement in data["project"]["dependencies"]:
        # Strip extras, markers and version specifiers: "uvicorn[standard]>=0.29"
        name = re.split(r"[\[<>=!~; ]", requirement.strip(), maxsplit=1)[0]
        if name:
            names.add(canonicalize_name(name))
    return names


def _locked_versions_for_this_interpreter() -> dict[str, str]:
    """Resolve ``uv.lock`` down to ONE version per package for the interpreter
    that is running right now, by evaluating each entry's resolution markers."""
    resolved: dict[str, str] = {}
    for package in _lock_data()["package"]:
        name = canonicalize_name(package["name"])
        markers = package.get("resolution-markers") or []
        # No markers => the entry applies to every interpreter in range.
        if markers and not any(Marker(expression).evaluate() for expression in markers):
            continue
        previous = resolved.get(name)
        assert previous is None or previous == package["version"], (
            f"uv.lock resolves {name} ambiguously for this interpreter: both "
            f"{previous} and {package['version']} select. A forked package "
            "whose markers overlap means 'the locked version' is not a single "
            "value, which is exactly the condition this guard exists to "
            "prevent."
        )
        resolved[name] = package["version"]
    return resolved


def _installed_versions() -> dict[str, str]:
    installed: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata["Name"]
        if not name:
            continue
        installed[canonicalize_name(name)] = distribution.version
    return installed


def _workflow_files() -> list[pathlib.Path]:
    return sorted(_WORKFLOWS_DIR.glob("*.yml")) + sorted(_WORKFLOWS_DIR.glob("*.yaml"))


def _job_steps(job: dict) -> list[dict]:
    return [step for step in (job.get("steps") or []) if isinstance(step, dict)]


def _runs_the_suite(step: dict) -> bool:
    """True when a step invokes pytest against this repo's test trees."""
    command = step.get("run") or ""
    if "pytest" not in command:
        return False
    return any(target in command for target in ("tests/", "tests ", "ci/tests"))


def _suite_jobs() -> list[tuple[str, str, dict]]:
    """Every (workflow file, job name, job) that runs the test suite."""
    found: list[tuple[str, str, dict]] = []
    for path in _workflow_files():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_name, job in (data.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            if any(_runs_the_suite(step) for step in _job_steps(job)):
                found.append((path.name, job_name, job))
    return found


# ---------------------------------------------------------------------------
# 1 — the interpreter is the pinned one
# ---------------------------------------------------------------------------
def test_running_interpreter_matches_the_pinned_python_version() -> None:
    """The lock forks at 3.11. Fixing the interpreter is what collapses it to a
    single resolve — so an environment on any other minor version is resolving
    a different set of numbers, however green it looks."""
    expected = _pinned_python()
    actual = (sys.version_info.major, sys.version_info.minor)
    assert actual == expected, (
        f"this interpreter is {actual[0]}.{actual[1]}, but "
        f"{_PYTHON_VERSION_FILE.name} pins {expected[0]}.{expected[1]}. "
        "uv.lock forks at python_full_version 3.11 — numpy resolves to 2.2.6 "
        "below it and 2.4.6 at or above, timezonefinder to 8.2.0 and 8.2.5 — "
        "and numpy is inside the served Shadbala/Ashtakavarga compute path. "
        "Testing on the other side of that fork validates numbers no "
        "deployment produces. Recreate the environment with "
        "`uv sync --frozen --extra dev` (uv reads .python-version)."
    )


# ---------------------------------------------------------------------------
# 2 — the installed set IS the locked set
# ---------------------------------------------------------------------------
def test_installed_distributions_match_the_lock_for_this_interpreter() -> None:
    """Compare the RESOLVED sets, rather than asserting they match.

    This is deliberately a measurement of the live environment, not a reading
    of configuration: a ``pyproject.toml`` constraint is a declaration, and two
    environments that declare the same thing can and did install different
    versions.
    """
    locked = _locked_versions_for_this_interpreter()
    installed = _installed_versions()

    compared = sorted(set(locked) & set(installed))
    # A comparison that examined nothing reports no differences. Floor it
    # against the project's own declared runtime dependencies, which must all
    # be installed and locked in any environment able to serve a request.
    required = _declared_runtime_dependencies()
    print(
        f"compared {len(compared)} installed distributions against uv.lock "
        f"on python {sys.version_info.major}.{sys.version_info.minor} "
        f"({len(locked)} locked entries select for this interpreter, "
        f"{len(installed)} distributions installed)"
    )

    missing_required = sorted(required - set(compared))
    assert not missing_required, (
        "these declared runtime dependencies were not part of the comparison "
        f"(absent from the environment or from uv.lock): {missing_required}. "
        "A comparison that skips the project's own dependencies proves "
        "nothing about the environment."
    )
    assert len(compared) >= len(required), (
        f"only {len(compared)} distributions were compared against uv.lock, "
        f"fewer than the {len(required)} runtime dependencies this project "
        "declares. The comparison is not seeing the environment."
    )

    missing_critical = sorted(_ACCURACY_CRITICAL - set(compared))
    assert not missing_critical, (
        f"the accuracy-critical pins {missing_critical} were not compared. "
        "These are the packages that can move a served number — the ephemeris, "
        "the chart library that owns the lunar-node levers, and the two "
        "packages uv.lock forks on at python 3.11. A convergence check that "
        "silently omits them is not a convergence check."
    )

    mismatched = [
        f"{name}: installed {installed[name]}, uv.lock resolves {locked[name]}"
        for name in compared
        if installed[name] != locked[name]
    ]
    assert not mismatched, (
        "the installed environment is not the one uv.lock describes for this "
        "interpreter:\n  " + "\n  ".join(mismatched) + "\n"
        "Recreate it with `uv sync --frozen --extra dev`. A floating install "
        "(`pip install -e \".[dev]\"`) resolves whatever is newest today, "
        "which is how the suite came to validate a set no deployment ran."
    )

    unlocked = sorted(set(installed) - set(locked) - _NOT_LOCKED_OK)
    assert not unlocked, (
        f"installed but absent from uv.lock: {unlocked}. The environment has "
        "packages the lock does not describe, so it is not the shipped "
        "resolve. Recreate it with `uv sync --frozen --extra dev`."
    )


# ---------------------------------------------------------------------------
# 3 — no workflow may reintroduce a floating resolve
# ---------------------------------------------------------------------------
def test_every_workflow_job_that_runs_the_suite_installs_from_the_frozen_lock() -> None:
    """Structural, over parsed YAML — the divergence this repo had was a
    workflow edit away, and it is a workflow edit away again."""
    suite_jobs = _suite_jobs()
    print(
        f"checked {len(suite_jobs)} workflow job(s) that run the test suite: "
        + ", ".join(f"{wf}:{job}" for wf, job, _ in suite_jobs)
    )
    # Positive floor: test.yml's `test` and `swiss-ephemeris` jobs and
    # publish.yml's `test` job all run the suite. Zero found would mean the
    # detector stopped detecting, and every assertion below would pass
    # vacuously.
    assert len(suite_jobs) >= 3, (
        f"only {len(suite_jobs)} workflow job(s) were found running the test "
        "suite; at least 3 are expected (test.yml's `test` and "
        "`swiss-ephemeris`, publish.yml's `test`). Either a gate was deleted "
        "or this guard's detector no longer recognises the invocation — both "
        "are failures."
    )

    failures: list[str] = []
    for workflow, job_name, job in suite_jobs:
        steps = _job_steps(job)
        commands = [step.get("run") or "" for step in steps]

        installs_frozen = any(
            ("uv sync" in command and "--frozen" in command)
            or ("uv run" in command and "--frozen" in command)
            for command in commands
        )
        if not installs_frozen:
            failures.append(
                f"{workflow}:{job_name} runs the suite but never installs from "
                "the frozen lock (`uv sync --frozen` / `uv run --frozen`)"
            )

        floating = [
            command
            for command in commands
            if re.search(r"pip\s+install\s+(-e\s+)?[\"']?\.", command)
        ]
        if floating:
            failures.append(
                f"{workflow}:{job_name} installs the project with a floating "
                f"resolve that ignores uv.lock: {floating!r}"
            )

        setup_python = [
            step
            for step in steps
            if isinstance(step.get("uses"), str)
            and step["uses"].startswith("actions/setup-python")
        ]
        for step in setup_python:
            options = step.get("with") or {}
            if "python-version-file" not in options:
                failures.append(
                    f"{workflow}:{job_name} pins its interpreter inline "
                    f"({options.get('python-version')!r}) instead of reading "
                    f"{_PYTHON_VERSION_FILE.name}. A second declaration of the "
                    "interpreter is a second chance to straddle uv.lock's 3.11 "
                    "fork."
                )

    assert not failures, "\n  ".join(["", *failures])


# ---------------------------------------------------------------------------
# 4 — the images are on the pinned interpreter too
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.name)
def test_dockerfile_base_images_are_the_pinned_python_version(
    dockerfile: pathlib.Path,
) -> None:
    """A base-image bump is a numerics change here, so it must not be able to
    cross uv.lock's fork without also changing the pin every environment
    reads."""
    major, minor = _pinned_python()
    instructions = parse_dockerfile(dockerfile.read_text(encoding="utf-8"))

    tags: list[str] = []
    for instruction in instructions:
        if instruction.keyword != "FROM":
            continue
        image = instruction.value.split()[0]
        if image.startswith("python:"):
            tags.append(image.split(":", 1)[1])

    print(f"{dockerfile.name}: found {len(tags)} python base image(s): {tags}")
    assert tags, (
        f"{dockerfile.name} declares no `FROM python:...` stage. Either the "
        "base image stopped being an official python image — in which case "
        "the interpreter this image runs is no longer checked against "
        f"{_PYTHON_VERSION_FILE.name} — or this parser stopped seeing it."
    )

    wrong = [tag for tag in tags if not re.match(rf"^{major}\.{minor}(?:\.\d+)?(?:-|$)", tag)]
    assert not wrong, (
        f"{dockerfile.name} builds on python {wrong}, but "
        f"{_PYTHON_VERSION_FILE.name} pins {major}.{minor}. uv.lock forks at "
        "python_full_version 3.11: crossing it swaps numpy (2.2.6 <-> 2.4.6) "
        "and timezonefinder (8.2.0 <-> 8.2.5) with no change to pyproject.toml "
        "or uv.lock, and numpy is inside the served Shadbala/Ashtakavarga "
        "compute path. dependabot watches the docker ecosystem weekly, so this "
        "is a bump that arrives on its own. Moving the interpreter is a "
        "deliberate, separately-reviewed change that re-records the goldens — "
        "not a base-image tag edit."
    )
