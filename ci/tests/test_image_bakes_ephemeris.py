"""Regression guard: the Swiss ephemeris data is BAKED into the images.

WHY THIS FILE EXISTS
--------------------
The data used to be supplied at runtime — ``Dockerfile`` created an empty
``data/ephe`` and a comment said it "must be volume-mounted or COPY'd
separately". Nothing codified that mount. Measured 2026-07-31 against the
digest staging was actually serving: the directory held 0 files, no ``*.se1``
existed anywhere on the filesystem, and ``probe_ephemeris_source()`` returned
``retflag=65604`` with FLG_MOSEPH set. Baking removes the runtime dependency
entirely: the data becomes immutable with the image digest, so rolling back to
an image is rolling back to its data.

Nothing in the ordinary test suite can see a Dockerfile regression — the suite
runs in a checkout, not in the image — so a later edit that dropped the fetch
would leave every test green while shipping an image on the fallback engine.
That is precisely the shape of the original defect, which is why this guard is
structural rather than behavioural.

WHY A PARSER, NOT A LINE-SCAN
-----------------------------
``grep fetch_swiss_ephemeris Dockerfile`` matches the string in a comment, in a
commented-out instruction, in an unused build stage that the final image never
copies from, and in this file's own name — none of which puts a single byte of
ephemeris data into the shipped image. So every assertion below is about
structure: which stage an instruction belongs to, which stage the final image
copies FROM, and where that copy lands. ``test_parser_discriminates`` feeds
that logic the fixtures a textual scanner gets wrong, and the real assertions
re-invoke the same helpers, so an unproven parser can never certify the
Dockerfile green.
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

import pytest
import yaml

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_DOCKERFILE_TEST = _REPO_ROOT / "Dockerfile.test"
_TEST_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "test.yml"

_FETCHER = "ci/fetch_swiss_ephemeris.py"
# The path the application actually reads. bphs_core/utils.py computes
# EPHE_PATH as <package>/../data/ephe, which under WORKDIR /app is
# /app/data/ephe — so a COPY landing anywhere else is not the fix.
_EPHE_DESTINATIONS = {"data/ephe", "./data/ephe", "/app/data/ephe", "data/ephe/", "./data/ephe/"}


# ---------------------------------------------------------------------------
# Dockerfile parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Instruction:
    stage: str  # the AS-name of the stage, or its index as a string
    keyword: str  # RUN / COPY / FROM / ...
    value: str  # everything after the keyword, continuations joined


def parse_dockerfile(text: str) -> list[Instruction]:
    """Resolve a Dockerfile to instructions, with comments and continuations gone.

    A ``#`` line is dropped outright — including one that comments out a whole
    instruction, which is the single most tempting way to disable this without
    the diff looking like a removal.
    """
    logical: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            # A continuation cannot resume across a comment line in a way that
            # matters here; dropping both is what Docker does with the comment.
            continue
        if buffer:
            buffer += " " + line
        else:
            buffer = line
        if buffer.endswith("\\"):
            buffer = buffer[:-1].rstrip()
            continue
        logical.append(buffer)
        buffer = ""
    if buffer:
        logical.append(buffer)

    instructions: list[Instruction] = []
    stage = "0"
    stage_index = 0
    for entry in logical:
        parts = entry.split(None, 1)
        keyword = parts[0].upper()
        value = parts[1] if len(parts) > 1 else ""
        if keyword == "FROM":
            match = re.search(r"\bAS\s+(\S+)\s*$", value, flags=re.IGNORECASE)
            stage = match.group(1) if match else str(stage_index)
            stage_index += 1
        instructions.append(Instruction(stage=stage, keyword=keyword, value=value))
    return instructions


def split_shell_commands(value: str) -> list[str]:
    """Split a RUN body on shell separators that are NOT inside quotes.

    A naive ``re.split(r"&&|;|\\|\\|", ...)`` tears a quoted argument apart.
    Measured: the real build-time assertion is a single
    ``python -c "import sys; from bphs_core.utils import probe_ephemeris_source; ..."``,
    and splitting on the semicolons INSIDE that string produced fragments where
    one piece had ``python`` and another had the probe, so no single fragment
    had both and the assertion read as absent. The separators that matter to
    the shell are only the unquoted ones.

    Also strips each command at an unquoted ``#`` — a shell comment disables
    everything after it, so text there is named but never executed.
    """
    commands: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(value):
        char = value[index]
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            index += 1
            continue
        if char == "#":
            # Rest of THIS command is a shell comment; drop it and stop here.
            remainder = value[index:]
            separator = re.search(r"&&|\|\||;", remainder)
            if separator is None:
                break
            index += separator.end()
            commands.append("".join(current))
            current = []
            continue
        if value.startswith(("&&", "||"), index):
            commands.append("".join(current))
            current = []
            index += 2
            continue
        if char == ";":
            commands.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    commands.append("".join(current))
    return [c.strip() for c in commands if c.strip()]


def final_stage(instructions: list[Instruction]) -> str:
    """The stage that becomes the shipped image: the last one declared."""
    stages = [i.stage for i in instructions if i.keyword == "FROM"]
    if not stages:
        raise AssertionError("Dockerfile declares no FROM")
    return stages[-1]


def stages_running_the_fetcher(instructions: list[Instruction]) -> set[str]:
    """Stages with a RUN that genuinely invokes the checksum-pinned fetcher.

    ``--self-test`` alone does NOT count: it exercises the verifier's
    discrimination fixtures and downloads nothing. A stage that only self-tests
    has no data in it.
    """
    found: set[str] = set()
    for instruction in instructions:
        if instruction.keyword != "RUN" or _FETCHER not in instruction.value:
            continue
        for command in split_shell_commands(instruction.value):
            if _FETCHER not in command:
                continue
            if "--self-test" in command or "--verify-only" in command:
                continue
            found.add(instruction.stage)
    return found


def copies_into_ephe_from(instructions: list[Instruction], *, into: str) -> set[str]:
    """Stages that ``into`` COPYs ephemeris data FROM (``COPY --from=X … data/ephe``)."""
    sources: set[str] = set()
    for instruction in instructions:
        if instruction.keyword != "COPY" or instruction.stage != into:
            continue
        match = re.search(r"--from=(\S+)", instruction.value)
        if not match:
            continue
        operands = [
            token
            for token in instruction.value.split()
            if not token.startswith("--")
        ]
        if not operands:
            continue
        if operands[-1].rstrip("/") in {d.rstrip("/") for d in _EPHE_DESTINATIONS}:
            sources.add(match.group(1))
    return sources


def stage_asserts_swiss_retflag(instructions: list[Instruction], *, stage: str) -> bool:
    """Does ``stage`` run a build-time check that reads the FLG_SWIEPH bit?

    A build that merely COPYs files proves the files were copied, not that
    swisseph reads them. Requesting Swiss data with the files absent still
    SUCCEEDS on the fallback, so only the retflag bit is evidence.

    Naming the probe is not enough: a ``RUN`` body is a shell script, so
    ``RUN true # probe_ephemeris_source`` mentions it while executing nothing.
    Measured — an earlier version of this helper accepted exactly that, and a
    mutation that removed the real build-time assertion left this file GREEN.
    The command must actually invoke python AND reach the probe, in the same
    shell command rather than merely somewhere in the same RUN.
    """
    for instruction in instructions:
        if instruction.keyword != "RUN" or instruction.stage != stage:
            continue
        for command in split_shell_commands(instruction.value):
            if "probe_ephemeris_source" in command and re.search(r"\bpython\b", command):
                return True
    return False


# ---------------------------------------------------------------------------
# The parser must discriminate before it certifies anything
# ---------------------------------------------------------------------------


_COMMENTED_OUT = """
FROM python:3.10-slim AS ephemeris
# RUN python ci/fetch_swiss_ephemeris.py
FROM python:3.10-slim
# COPY --from=ephemeris /fetch/data/ephe ./data/ephe
RUN mkdir -p data/ephe
"""

_SELF_TEST_ONLY = """
FROM python:3.10-slim AS ephemeris
RUN python ci/fetch_swiss_ephemeris.py --self-test
FROM python:3.10-slim
COPY --from=ephemeris /fetch/data/ephe ./data/ephe
"""

_WRONG_DESTINATION = """
FROM python:3.10-slim AS ephemeris
RUN python ci/fetch_swiss_ephemeris.py
FROM python:3.10-slim
COPY --from=ephemeris /fetch/data/ephe ./unused/ephe
"""

_ORPHAN_STAGE = """
FROM python:3.10-slim AS ephemeris
RUN python ci/fetch_swiss_ephemeris.py
FROM python:3.10-slim
RUN mkdir -p data/ephe
"""

_CONTINUATION = """
FROM python:3.10-slim AS ephemeris
RUN python ci/fetch_swiss_ephemeris.py --self-test \\
 && python ci/fetch_swiss_ephemeris.py
FROM python:3.10-slim
COPY --from=ephemeris /fetch/data/ephe ./data/ephe
"""


def test_shell_splitter_respects_quotes() -> None:
    """Separators INSIDE quotes belong to the argument, not to the shell.

    Measured: a naive split on ``;`` tore the real build-time assertion — one
    ``python -c "import sys; from bphs_core.utils import probe_ephemeris_source; ..."``
    — into fragments where one held ``python`` and another held the probe, so
    no fragment held both and the assertion read as ABSENT. The guard reported
    a missing control that was in fact present, which is the mirror image of
    the failure it exists to catch and just as useless.
    """
    one_liner = (
        'python -c "import sys; from bphs_core.utils import probe_ephemeris_source; '
        'sys.exit(1)"'
    )
    assert split_shell_commands(one_liner) == [one_liner]

    assert split_shell_commands("a && b ; c || d") == ["a", "b", "c", "d"]
    assert split_shell_commands('echo "a && b"') == ['echo "a && b"']
    # An unquoted # comments out the rest of that command only.
    assert split_shell_commands("true # probe && real") == ["true", "real"]
    assert split_shell_commands("echo '# not a comment'") == ["echo '# not a comment'"]


def test_parser_discriminates() -> None:
    """The fixtures a textual scanner gets wrong. All five would grep clean."""
    commented = parse_dockerfile(_COMMENTED_OUT)
    assert stages_running_the_fetcher(commented) == set(), (
        "a COMMENTED-OUT fetch was read as a real one"
    )
    assert copies_into_ephe_from(commented, into=final_stage(commented)) == set(), (
        "a COMMENTED-OUT COPY was read as a real one"
    )

    self_test = parse_dockerfile(_SELF_TEST_ONLY)
    assert stages_running_the_fetcher(self_test) == set(), (
        "`--self-test` downloads nothing; it was counted as fetching the data"
    )

    wrong = parse_dockerfile(_WRONG_DESTINATION)
    assert copies_into_ephe_from(wrong, into=final_stage(wrong)) == set(), (
        "a COPY landing outside data/ephe was accepted"
    )

    orphan = parse_dockerfile(_ORPHAN_STAGE)
    assert stages_running_the_fetcher(orphan) == {"ephemeris"}
    assert copies_into_ephe_from(orphan, into=final_stage(orphan)) == set(), (
        "a fetching stage the final image never copies from was accepted"
    )

    # And the correct shape must still be ACCEPTED, so the checks above are
    # discrimination rather than blanket rejection.
    good = parse_dockerfile(_CONTINUATION)
    assert stages_running_the_fetcher(good) == {"ephemeris"}, (
        "a real fetch spread over a line continuation was missed"
    )
    assert copies_into_ephe_from(good, into=final_stage(good)) == {"ephemeris"}

    assert not stage_asserts_swiss_retflag(good, stage=final_stage(good))

    # Named but never executed — a shell comment. This exact shape defeated an
    # earlier version of the helper: a mutation that replaced the real
    # build-time assertion with `RUN true # ...probe_ephemeris_source...` left
    # this file green.
    commented_probe = parse_dockerfile(
        _CONTINUATION + "\nRUN true # probe_ephemeris_source\n"
    )
    assert not stage_asserts_swiss_retflag(
        commented_probe, stage=final_stage(commented_probe)
    ), "a probe named only inside a shell comment was accepted as an assertion"

    # Named, but nothing runs python — so no retflag is ever evaluated.
    echoed_probe = parse_dockerfile(_CONTINUATION + "\nRUN echo probe_ephemeris_source\n")
    assert not stage_asserts_swiss_retflag(
        echoed_probe, stage=final_stage(echoed_probe)
    ), "a probe merely echoed by the shell was accepted as an assertion"

    with_assert = parse_dockerfile(
        _CONTINUATION + '\nRUN python -c "from bphs_core.utils import probe_ephemeris_source"\n'
    )
    assert stage_asserts_swiss_retflag(with_assert, stage=final_stage(with_assert))


# ---------------------------------------------------------------------------
# The real Dockerfiles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [_DOCKERFILE, _DOCKERFILE_TEST], ids=lambda p: p.name)
def test_image_bakes_the_ephemeris_data(path: pathlib.Path) -> None:
    """Both images must ship the data, not expect it at runtime.

    ``Dockerfile.test`` is included deliberately: it is what
    ``scripts/test-in-docker.*`` runs to exercise real Swiss data, and leaving
    it on a host bind-mount keeps a human step ("download the files first")
    in the loop — the same "supplied out of band" shape as the production
    defect, one layer down.
    """
    instructions = parse_dockerfile(path.read_text(encoding="utf-8"))
    shipped = final_stage(instructions)

    fetching = stages_running_the_fetcher(instructions)
    assert fetching, (
        f"{path.name} never runs {_FETCHER}, so the image ships with an empty "
        "data/ephe and swisseph silently computes on the Moshier fallback"
    )

    copied_from = copies_into_ephe_from(instructions, into=shipped)
    assert copied_from, (
        f"{path.name}'s final stage never COPYs ephemeris data into data/ephe"
    )
    assert copied_from & fetching, (
        f"{path.name}'s final stage copies data/ephe from {sorted(copied_from)}, "
        f"but the checksum-verified fetch happens in {sorted(fetching)} — the "
        "shipped image would take its data from a stage that never fetched any"
    )


def test_production_image_proves_swiss_is_active_at_build_time() -> None:
    """The build itself must fail if swisseph does not read the baked files.

    Copying files proves a COPY ran. It does not prove swisseph loads them: a
    Swiss request with the data absent, corrupt, or at the wrong path still
    returns a plausible result with FLG_MOSEPH set. Asserting the retflag bit
    during the build makes an image that computes on the fallback impossible
    to produce, rather than merely unlikely.
    """
    instructions = parse_dockerfile(_DOCKERFILE.read_text(encoding="utf-8"))
    assert stage_asserts_swiss_retflag(instructions, stage=final_stage(instructions)), (
        "Dockerfile's final stage has no build-time probe_ephemeris_source() "
        "assertion — nothing proves the baked files are the ones swisseph reads"
    )


def test_the_build_time_proof_runs_as_the_serving_user() -> None:
    """The retflag assertion must come AFTER ``USER``, not before it.

    MEASURED, while writing this change. An earlier draft ran the assertion as
    root, above ``USER appuser``. It printed "Swiss ephemeris data verified
    active" and the built image genuinely contained all four data files — yet
    ``docker run`` reported ``retflag=65604``, because the fetcher's
    ``tempfile.mkstemp`` left the files mode 0600 and root could read what
    appuser could not. swisseph does not raise on an unreadable data file; it
    silently returns Moshier results.

    A control verified as a different principal than the one that serves
    requests is not verified — it is the same "a green build is not evidence
    the control is live" failure this whole change exists to close, reproduced
    inside the fix for it.
    """
    instructions = parse_dockerfile(_DOCKERFILE.read_text(encoding="utf-8"))
    shipped = final_stage(instructions)
    in_shipped = [i for i in instructions if i.stage == shipped]

    user_positions = [n for n, i in enumerate(in_shipped) if i.keyword == "USER"]
    assert user_positions, (
        "the shipped stage never drops privileges with USER — unexpected; this "
        "guard assumes the service runs unprivileged"
    )
    proof_positions = [
        n
        for n, i in enumerate(in_shipped)
        if i.keyword == "RUN"
        and any(
            "probe_ephemeris_source" in c and re.search(r"\bpython\b", c)
            for c in split_shell_commands(i.value)
        )
    ]
    assert proof_positions, "no build-time retflag proof found in the shipped stage"
    assert min(proof_positions) > min(user_positions), (
        "the build-time Swiss-ephemeris proof runs BEFORE `USER`, i.e. as root. "
        "It therefore proves root can read the data files, not that the serving "
        "user can — the exact difference that produced an image which passed its "
        "own build assertion and still computed every chart on the Moshier "
        "fallback. Move the assertion below `USER`."
    )


def test_final_stage_does_not_merely_mkdir_the_ephemeris_directory() -> None:
    """``mkdir -p data/ephe`` is what made ``os.path.isdir`` lie for months."""
    instructions = parse_dockerfile(_DOCKERFILE.read_text(encoding="utf-8"))
    shipped = final_stage(instructions)
    for instruction in instructions:
        if instruction.keyword == "RUN" and instruction.stage == shipped:
            assert "mkdir" not in instruction.value or "data/ephe" not in instruction.value, (
                "the shipped stage still creates an empty data/ephe; an empty "
                "directory is exactly the state that reported healthy while "
                "every chart came from the Moshier fallback"
            )


# ---------------------------------------------------------------------------
# The fetched files must be readable by the user that serves requests
# ---------------------------------------------------------------------------


def test_downloaded_files_are_readable_by_other_users(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``download()`` must leave the file group/world readable.

    ``tempfile.mkstemp`` creates its file 0600, and that mode survives both the
    atomic rename and a Docker ``COPY --from=`` into another build stage. The
    build runs as root; the service runs as an unprivileged ``appuser``. So
    without an explicit chmod the shipped image contains all four data files
    that the serving process cannot open — and swisseph does not raise on an
    unreadable data file, it silently returns Moshier results.

    MEASURED while writing this change: an image with all four files present,
    whose own build-time assertion printed "Swiss ephemeris data verified
    active", still reported ``retflag=65604`` under ``docker run`` as appuser.

    PLATFORM CAVEAT, stated rather than hidden: Windows does not implement
    POSIX permission bits, so ``st_mode`` there is already 0o666 and this
    assertion cannot fail on a Windows dev box. It is a real gate on Linux —
    which is where CI runs and where every image is built. The end-to-end
    backstop is the ``production-image-ephemeris`` job in test.yml, which runs
    the retflag probe inside the built image AS ``appuser``.
    """
    import io
    import urllib.request

    from ci import fetch_swiss_ephemeris as fetcher  # noqa: PLC0415

    payload = b"pretend ephemeris bytes"
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *_a, **_k: io.BytesIO(payload)
    )
    destination = tmp_path / "sepl_18.se1"
    fetcher.download("https://example.invalid/sepl_18.se1", destination)

    assert destination.read_bytes() == payload
    mode = destination.stat().st_mode & 0o777
    assert mode & 0o044 == 0o044, (
        f"downloaded file mode is {mode:#o} — not readable by group/other, so an "
        "unprivileged service user cannot open it after a Docker COPY, and "
        "swisseph will silently fall back to Moshier rather than raising"
    )


# ---------------------------------------------------------------------------
# CI must actually build that image and run the assertion
# ---------------------------------------------------------------------------


def _workflow() -> dict:
    return yaml.safe_load(_TEST_WORKFLOW.read_text(encoding="utf-8"))


def test_ci_builds_the_production_image() -> None:
    """A structural check of the Dockerfile is not a build.

    The build-time assertion above only protects anything if CI actually
    performs the build on every PR. ``docker-test-image`` builds
    ``Dockerfile.test``; nothing built the PRODUCTION ``Dockerfile`` before
    this change, so a broken fetch stage would first surface on a deploy.
    """
    jobs = _workflow()["jobs"]
    building = {
        name: job
        for name, job in jobs.items()
        if any(
            (step.get("with") or {}).get("file") in {"Dockerfile", "./Dockerfile"}
            or (
                step.get("uses", "").startswith("docker/build-push-action")
                and "file" not in (step.get("with") or {})
            )
            for step in job.get("steps", [])
        )
    }
    assert building, (
        ".github/workflows/test.yml has no job that builds the production "
        "Dockerfile, so its baked-in ephemeris data and its build-time Swiss "
        "assertion are never exercised on a PR"
    )
