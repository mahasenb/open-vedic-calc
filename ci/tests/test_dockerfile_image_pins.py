"""Regression guard: pinned external images, and cross-stage COPY that names a stage.

WHY THIS FILE EXISTS
--------------------
Both Dockerfiles used to install the ``uv`` build tool with

    COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

which has two independent problems. Closing only one of them still leaves the
image floating, so the guard below checks both.

1. ``:latest`` is a MUTABLE tag. It is re-resolved on every build, so two
    builds of the same commit can install two different binaries and nothing in
    the repository records which one shipped. Every other input here is pinned:
    the interpreter in ``.python-version``, the dependency set in ``uv.lock``
    installed with ``uv sync --frozen``, the ephemeris data files to sha256 in
    ``ci/swiss_ephemeris.json``. The tool that performs the frozen install was
    the one input still resolved at build time — a supply-chain gap in the
    build tooling of an otherwise fully pinned build.

2. ``COPY --from=<registry image>`` is INVISIBLE to the automated dependency
    updater. ``.github/dependabot.yml`` already watches the ``docker``
    ecosystem for this directory, but that ecosystem reads ``FROM``
    instructions; an image named only in a ``COPY --from=`` is never parsed, so
    it is neither bumped for a disclosed CVE nor reported as outdated. Pinning
    a digest without also moving the reference onto a ``FROM`` line would trade
    a mutable reference for a frozen one that nothing is watching — arguably
    worse, because a frozen unwatched pin rots silently.

Declaring the image as a named build stage and copying ``--from=<stage>``
fixes both at once: the reference becomes a ``FROM`` the updater can see and
version-bump, and it carries an immutable digest between bumps.

WHY A PARSER, NOT A LINE-SCAN
-----------------------------
``grep -c 'sha256:' Dockerfile`` is satisfied by a digest in a comment, in a
commented-out instruction, or in prose like this docstring — none of which pins
anything. So the checks below are structural: which instruction a reference
belongs to, which stage names are actually declared, and what the operand of
each ``COPY --from=`` resolves to. ``test_the_rules_discriminate`` feeds those
rules the shapes a textual scanner gets wrong — in BOTH directions, because a
rule that rejects everything is as useless as one that accepts everything — and
the assertions against the real files re-invoke the very same helpers, so an
unproven rule can never certify a Dockerfile green.

WHY THE FILE LIST COMES FROM ``git ls-files``
---------------------------------------------
A hard-coded pair of paths cannot notice a THIRD Dockerfile being added, which
is the most likely way this regresses: the new file simply is not covered, and
the guard stays green while reporting nothing about it. The tracked-file list
is the source of truth for what this repository ships, so the sweep is derived
from it and then floored — "no violations found" is vacuously true of a sweep
that examined nothing.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

# The two Dockerfiles that exist today. This is a FLOOR on what the sweep must
# find, not the list it works from — see the module docstring.
_KNOWN_DOCKERFILES = frozenset({"Dockerfile", "Dockerfile.test"})

_DIGEST_SUFFIX = re.compile(r"@sha256:[0-9a-f]{64}$")

# A tag that is re-resolved rather than pinned. ``latest`` is the canonical
# one; the point is a moving target, not the spelling.
_FLOATING_TAGS = frozenset({"latest"})


# ---------------------------------------------------------------------------
# Reuse the proven Dockerfile parser rather than writing a second one.
# ---------------------------------------------------------------------------
def _load_dockerfile_parser():
    """Load ``parse_dockerfile`` from its sibling guard BY PATH.

    ``ci/`` has no ``__init__.py`` by design, so ``from ci.tests… import …``
    raises under the bare ``pytest`` console script CI actually invokes (it
    does not put the CWD on ``sys.path``; ``python -m pytest`` does). Path
    loading works under both, which is the documented pattern for this tree.

    A second, subtly different Dockerfile parser living in this tree would be a
    second thing to keep correct, and the sibling's is already proven against
    comments, commented-out instructions and line continuations.
    """
    module_path = pathlib.Path(__file__).with_name("test_image_bakes_ephemeris.py")
    spec = importlib.util.spec_from_file_location(
        "_image_bake_guard_for_image_pins", module_path
    )
    assert spec is not None and spec.loader is not None, (
        f"could not load the Dockerfile parser from {module_path}"
    )
    module = importlib.util.module_from_spec(spec)
    # Register before executing: the sibling defines a @dataclass, and
    # dataclasses resolves ``sys.modules[cls.__module__]`` while processing the
    # class. A module executed from a spec without being registered has no
    # entry there, and the decorator raises on Python 3.10.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parse_dockerfile = _load_dockerfile_parser().parse_dockerfile


# ---------------------------------------------------------------------------
# Image-reference vocabulary
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Finding:
    """One violation, addressed to whoever has to fix it."""

    path: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"{self.path}: {self.detail}"


def operands(value: str) -> list[str]:
    """The non-flag tokens of an instruction body.

    ``FROM --platform=$BUILDPLATFORM alpine:3 AS build`` and
    ``COPY --from=x --chown=1000:1000 /a /b`` both put flags before the operands
    they matter to, so taking ``value.split()[0]`` reads a flag as an image.
    """
    return [token for token in value.split() if not token.startswith("--")]


def repository_of(image: str) -> str:
    """The repository part of a reference: no digest, no tag.

    Tag detection is done on the LAST path segment on purpose. A registry host
    may carry a port (``localhost:5000/app``), so a bare ``":" in image`` test
    reads that port as a tag and truncates the host.
    """
    name = image.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        return name[: len(name) - len(last) + last.index(":")]
    return name


def tag_of(image: str) -> str | None:
    """The explicit tag, or ``None`` when the reference carries none."""
    name = image.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    if ":" not in last:
        return None
    return last.split(":", 1)[1] or None


def is_digest_pinned(image: str) -> bool:
    """Is this reference immutable?

    Only a trailing ``@sha256:<64 hex>`` is. A digest mentioned anywhere else in
    the string — in a comment the parser already dropped, or embedded in a tag
    name — pins nothing.
    """
    return bool(_DIGEST_SUFFIX.search(image))


def is_implicit_library_image(image: str) -> bool:
    """A bare official base image such as ``python:3.10-slim``.

    These have no registry host and no user namespace, so the repository part
    contains no ``/``: Docker expands it to ``docker.io/library/<name>``.
    """
    return "/" not in repository_of(image)


def declared_stages(instructions) -> list[str]:
    """Stage names in declaration order, lowercased.

    Docker treats stage names case-insensitively, so ``AS Builder`` and
    ``--from=builder`` are the same stage and a case-sensitive comparison would
    report a working build as broken.
    """
    names: list[str] = []
    for instruction in instructions:
        if instruction.keyword != "FROM":
            continue
        match = re.search(r"\bAS\s+(\S+)\s*$", instruction.value, flags=re.IGNORECASE)
        if match:
            names.append(match.group(1).lower())
    return names


def external_from_images(instructions) -> list[str]:
    """Images a FROM pulls from a registry.

    Excludes ``scratch`` (the empty image, which has no registry object to pin)
    and any FROM whose operand is a stage this file already declared — that is
    an internal reference, not a download.
    """
    stage_names = set(declared_stages(instructions))
    images: list[str] = []
    for instruction in instructions:
        if instruction.keyword != "FROM":
            continue
        parts = operands(instruction.value)
        if not parts:
            continue
        image = parts[0]
        if image.lower() == "scratch" or image.lower() in stage_names:
            continue
        images.append(image)
    return images


def copy_from_operands(instructions) -> list[str]:
    """The value of every ``COPY --from=`` / ``ADD --from=`` in the file."""
    found: list[str] = []
    for instruction in instructions:
        if instruction.keyword not in {"COPY", "ADD"}:
            continue
        match = re.search(r"--from=(\S+)", instruction.value)
        if match:
            found.append(match.group(1))
    return found


# ---------------------------------------------------------------------------
# The two rules
# ---------------------------------------------------------------------------
def unpinned_external_images(text: str) -> list[str]:
    """Every FROM image that is not pinned the way this repository requires.

    Two accepted shapes, and nothing else:

    * ``<name>:<tag>`` with no registry host and no namespace — an official
      base image. Its tag is not free-floating either: it is separately
      asserted against ``.python-version`` by
      ``ci/tests/test_environment_convergence.py``, which is why requiring a
      digest here as well would duplicate a pin rather than add one. A floating
      tag (``latest``) is still rejected, because nothing pins that.

    * ``<anything>:<tag>@sha256:<digest>`` — an immutable reference that ALSO
      names a version. The digest is what makes the build reproducible; the tag
      is what makes it maintainable, because the dependency updater compares
      versions to decide there is a newer one, and a human reading the diff can
      see what moved. A digest with no tag is reproducible and unmaintainable,
      so it is rejected too.
    """
    instructions = parse_dockerfile(text)
    problems: list[str] = []
    for image in external_from_images(instructions):
        tag = tag_of(image)
        if is_digest_pinned(image):
            if tag is None:
                problems.append(
                    f"`FROM {image}` is digest-pinned but carries no version tag. "
                    "Write it as `<image>:<version>@sha256:<digest>` — the "
                    "dependency updater compares TAGS to decide a newer release "
                    "exists, so a digest-only reference is frozen at whatever it "
                    "was and is never offered a bump, and a reader of the diff "
                    "cannot tell which version moved."
                )
            elif tag in _FLOATING_TAGS:
                problems.append(
                    f"`FROM {image}` pins a digest against the floating tag "
                    f"`{tag}`. The digest holds, but the version it corresponds "
                    "to is unrecorded, so the pin says nothing about what is "
                    "installed. Name the release explicitly."
                )
            continue
        if is_implicit_library_image(image):
            if tag is None or tag in _FLOATING_TAGS:
                problems.append(
                    f"`FROM {image}` resolves a moving tag on every build. Even "
                    "an official base image must name an explicit, non-floating "
                    "version."
                )
            continue
        problems.append(
            f"`FROM {image}` is an external image with no `@sha256:` digest, so "
            "the tag is re-resolved on every build and two builds of this commit "
            "can install different bytes. Pin it as "
            "`<image>:<version>@sha256:<digest>`, resolved with "
            "`docker buildx imagetools inspect <image>:<version>`."
        )
    return problems


def copy_from_registry_images(text: str) -> list[str]:
    """Every ``COPY --from=`` whose operand is not a declared build stage.

    A ``COPY --from=<registry image>`` downloads that image at build time while
    being invisible to the ``docker`` dependency-updater ecosystem, which reads
    ``FROM`` instructions. So an image reached this way is never bumped for a
    disclosed CVE and never reported as outdated, however carefully it is
    pinned. Declaring it as a build stage and copying ``--from=<stage>`` puts
    exactly the same bytes in the image while making the reference visible.

    A numeric operand (``COPY --from=0``) references a stage positionally,
    which is legal and downloads nothing; it is accepted when it is in range.
    """
    instructions = parse_dockerfile(text)
    names = declared_stages(instructions)
    stage_count = sum(1 for i in instructions if i.keyword == "FROM")
    problems: list[str] = []
    for operand in copy_from_operands(instructions):
        if operand.lower() in set(names):
            continue
        if operand.isdigit() and int(operand) < stage_count:
            continue
        problems.append(
            f"`COPY --from={operand}` does not name a build stage declared in "
            f"this file (declared: {names or 'none'}). If it is a registry "
            "image, the dependency updater cannot see it — it parses `FROM` "
            "instructions only — so the image is never bumped for a CVE no "
            "matter how it is pinned. Declare it as "
            "`FROM <image>:<version>@sha256:<digest> AS <name>` and copy "
            "`--from=<name>`."
        )
    return problems


# ---------------------------------------------------------------------------
# The rules must discriminate before they certify anything
# ---------------------------------------------------------------------------
_FAKE_DIGEST = "sha256:" + "0" * 64
_OTHER_DIGEST = "sha256:" + "1" * 64

_GOOD = f"""
FROM ghcr.io/astral-sh/uv:9.9.9@{_FAKE_DIGEST} AS uv
FROM python:3.10-slim AS builder
COPY --from=uv /uv /uvx /bin/
FROM python:3.10-slim
COPY --from=builder /app /app
"""

_COPY_FROM_REGISTRY = """
FROM python:3.10-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
"""

_COPY_FROM_PINNED_REGISTRY = f"""
FROM python:3.10-slim
COPY --from=ghcr.io/astral-sh/uv:9.9.9@{_FAKE_DIGEST} /uv /uvx /bin/
"""

_FLOATING_EXTERNAL_FROM = """
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.10-slim
COPY --from=uv /uv /uvx /bin/
"""

_DIGEST_WITHOUT_VERSION = f"""
FROM ghcr.io/astral-sh/uv@{_FAKE_DIGEST} AS uv
FROM python:3.10-slim
COPY --from=uv /uv /uvx /bin/
"""

_DIGEST_AGAINST_LATEST = f"""
FROM ghcr.io/astral-sh/uv:latest@{_FAKE_DIGEST} AS uv
FROM python:3.10-slim
COPY --from=uv /uv /uvx /bin/
"""

_FLOATING_OFFICIAL_BASE = """
FROM python:latest
"""

_UNTAGGED_OFFICIAL_BASE = """
FROM python
"""

_COMMENTED_OUT_VIOLATION = f"""
FROM ghcr.io/astral-sh/uv:9.9.9@{_FAKE_DIGEST} AS uv
# COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
# FROM some/unpinned:latest AS sneaky
FROM python:3.10-slim
COPY --from=uv /uv /uvx /bin/
"""

_CONTINUATION = f"""
FROM ghcr.io/astral-sh/uv:9.9.9@{_FAKE_DIGEST} AS uv
FROM python:3.10-slim
COPY --from=uv \\
     /uv /uvx \\
     /bin/
"""

_SCRATCH_AND_FLAGS = f"""
FROM scratch AS empty
FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:9.9.9@{_OTHER_DIGEST} AS uv
FROM python:3.10-slim
COPY --from=uv --chown=1000:1000 /uv /bin/
COPY --from=0 /nothing /nothing
"""

_CASE_INSENSITIVE_STAGE = f"""
FROM ghcr.io/astral-sh/uv:9.9.9@{_FAKE_DIGEST} AS Uv
FROM python:3.10-slim
COPY --from=uv /uv /bin/
"""

_REGISTRY_WITH_PORT = f"""
FROM localhost:5000/team/tool:2.1@{_FAKE_DIGEST} AS tool
FROM python:3.10-slim
COPY --from=tool /t /bin/
"""


def test_reference_vocabulary_parses_registry_ports_and_digests() -> None:
    """A registry PORT is not a tag, and a digest is not part of the tag.

    ``localhost:5000/team/tool`` has a colon in the registry host. A reference
    parser that looks for the first ``:`` reads ``5000/team/tool`` as the tag
    and the host as the repository — which would make an unpinned image on a
    port-bearing registry look tagged, i.e. accepted.
    """
    assert repository_of("python:3.10-slim") == "python"
    assert tag_of("python:3.10-slim") == "3.10-slim"

    assert repository_of("ghcr.io/astral-sh/uv:0.12.5") == "ghcr.io/astral-sh/uv"
    assert tag_of("ghcr.io/astral-sh/uv:0.12.5") == "0.12.5"

    assert repository_of("localhost:5000/team/tool") == "localhost:5000/team/tool"
    assert tag_of("localhost:5000/team/tool") is None
    assert tag_of("localhost:5000/team/tool:2.1") == "2.1"

    assert repository_of(f"ghcr.io/astral-sh/uv:0.12.5@{_FAKE_DIGEST}") == (
        "ghcr.io/astral-sh/uv"
    )
    assert tag_of(f"ghcr.io/astral-sh/uv:0.12.5@{_FAKE_DIGEST}") == "0.12.5"
    assert tag_of(f"ghcr.io/astral-sh/uv@{_FAKE_DIGEST}") is None

    assert is_implicit_library_image("python:3.10-slim")
    assert not is_implicit_library_image("ghcr.io/astral-sh/uv:0.12.5")
    assert not is_implicit_library_image("someuser/someimage:1.0")

    assert is_digest_pinned(f"x:1@{_FAKE_DIGEST}")
    assert not is_digest_pinned("x:1")
    # A digest-shaped string that is not the reference's own digest suffix.
    assert not is_digest_pinned(f"x:{_FAKE_DIGEST}-suffix")
    # A truncated digest is not a digest.
    assert not is_digest_pinned("x:1@sha256:abc123")


def test_the_rules_discriminate() -> None:
    """The shapes a textual scanner gets wrong — in both directions.

    Every fixture below would satisfy ``grep sha256: Dockerfile`` or
    ``grep -v ':latest' Dockerfile`` or both, and several of them ship a
    floating image anyway. The accepted fixtures matter just as much: a rule
    that rejects every input passes an "it can fail" check while being useless.
    """
    # --- accepted -----------------------------------------------------------
    assert unpinned_external_images(_GOOD) == []
    assert copy_from_registry_images(_GOOD) == []

    assert unpinned_external_images(_CONTINUATION) == []
    assert copy_from_registry_images(_CONTINUATION) == [], (
        "a COPY --from spread over line continuations was misread"
    )

    assert unpinned_external_images(_SCRATCH_AND_FLAGS) == [], (
        "`scratch` has no registry object to pin, and a --platform flag is not "
        "an image name"
    )
    assert copy_from_registry_images(_SCRATCH_AND_FLAGS) == [], (
        "a --chown flag or a positional stage index was misread as an image"
    )

    assert copy_from_registry_images(_CASE_INSENSITIVE_STAGE) == [], (
        "Docker matches stage names case-insensitively; `AS Uv` / `--from=uv` "
        "is one stage, and reporting it would be a false positive on a build "
        "that works"
    )

    assert unpinned_external_images(_REGISTRY_WITH_PORT) == [], (
        "a registry port was misread as a tag"
    )

    assert unpinned_external_images(_COMMENTED_OUT_VIOLATION) == []
    assert copy_from_registry_images(_COMMENTED_OUT_VIOLATION) == [], (
        "a COMMENTED-OUT violation was reported as a real one — the guard must "
        "not fail on text Docker never executes"
    )

    # --- rejected -----------------------------------------------------------
    assert copy_from_registry_images(_COPY_FROM_REGISTRY), (
        "a COPY --from a registry image — the exact shape the updater cannot "
        "see — was accepted"
    )
    assert copy_from_registry_images(_COPY_FROM_PINNED_REGISTRY), (
        "a digest-pinned COPY --from a registry image was accepted. The digest "
        "is not the issue: the updater parses FROM instructions, so this image "
        "is still never offered a CVE bump, and a frozen unwatched pin rots "
        "quietly"
    )
    assert unpinned_external_images(_FLOATING_EXTERNAL_FROM), (
        "an external FROM with a version tag but no digest was accepted — the "
        "tag is still re-resolved, and a republished tag moves the bytes"
    )
    assert unpinned_external_images(_DIGEST_WITHOUT_VERSION), (
        "a digest with no version tag was accepted; nothing can offer it a bump"
    )
    assert unpinned_external_images(_DIGEST_AGAINST_LATEST), (
        "a digest pinned against `:latest` was accepted; the version it "
        "corresponds to is unrecorded"
    )
    assert unpinned_external_images(_FLOATING_OFFICIAL_BASE), (
        "`FROM python:latest` was accepted"
    )
    assert unpinned_external_images(_UNTAGGED_OFFICIAL_BASE), (
        "`FROM python` (implicitly :latest) was accepted"
    )


# ---------------------------------------------------------------------------
# The real Dockerfiles — every one this repository tracks
# ---------------------------------------------------------------------------
def tracked_dockerfiles() -> list[pathlib.Path]:
    """Every tracked ``Dockerfile*`` / ``*.Dockerfile``, from ``git ls-files``.

    The tracked-file list, not a directory walk: a walk descends into ignored
    build output, vendored checkouts and other worktrees, so it can both invent
    files this repository does not ship and — with the wrong prune list — miss
    one it does. ``git ls-files`` is the definition of what is shipped.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        "`git ls-files` failed in "
        f"{_REPO_ROOT}: {result.stderr.decode('utf-8', 'replace').strip()}. "
        "This guard derives its scope from the tracked-file list and fails "
        "closed rather than silently checking nothing."
    )
    paths = [p for p in result.stdout.decode("utf-8").split("\0") if p]
    matched = [
        p
        for p in paths
        if (name := pathlib.PurePosixPath(p).name).startswith("Dockerfile")
        or name.endswith(".Dockerfile")
    ]
    return [_REPO_ROOT / p for p in sorted(matched)]


def test_the_sweep_actually_covers_the_shipped_dockerfiles() -> None:
    """"No violations found" is vacuously true of a sweep that found no files.

    So the scope is asserted before the rules are: the enumeration must return
    the Dockerfiles known to exist, and it must not have silently returned only
    those — a third one added later is covered by construction, and if the
    enumeration breaks, this fails rather than the rules passing on nothing.
    """
    found = tracked_dockerfiles()
    names = {p.name for p in found}
    print(f"tracked Dockerfiles under test: {sorted(names)}")

    assert _KNOWN_DOCKERFILES <= names, (
        f"expected at least {sorted(_KNOWN_DOCKERFILES)} among the tracked "
        f"Dockerfiles, found {sorted(names)} — either a file was removed (in "
        "which case update this floor deliberately) or the `git ls-files` "
        "enumeration stopped working, and the checks below would then be "
        "passing on an empty set"
    )
    for path in found:
        assert path.is_file(), f"{path} is tracked but not readable from the worktree"


def test_every_copy_from_names_a_build_stage() -> None:
    """No ``COPY --from=<registry image>`` in any tracked Dockerfile.

    This is the rule that keeps the image references VISIBLE to the dependency
    updater. It is checked across every tracked Dockerfile rather than the two
    known ones, so a third file cannot be added outside the rule.
    """
    findings: list[Finding] = []
    examined = 0
    for path in tracked_dockerfiles():
        text = path.read_text(encoding="utf-8")
        examined += len(copy_from_operands(parse_dockerfile(text)))
        findings += [
            Finding(path.name, detail) for detail in copy_from_registry_images(text)
        ]

    print(f"examined {examined} `COPY --from=` operand(s)")
    assert examined >= 2, (
        f"only {examined} `COPY --from=` instruction(s) found across the tracked "
        "Dockerfiles. Both images copy their ephemeris data forward from a build "
        "stage, so fewer than two means the parser stopped seeing them and this "
        "check is passing on nothing."
    )
    assert not findings, "\n  ".join(["", *[str(f) for f in findings]])


def test_every_external_from_is_pinned() -> None:
    """No floating external image in any tracked Dockerfile.

    An unpinned reference means two builds of the same commit can install
    different bytes, with nothing in the repository recording which shipped —
    against a build whose every other input (interpreter, dependency set,
    ephemeris data) is pinned to an exact version or hash.
    """
    findings: list[Finding] = []
    examined = 0
    for path in tracked_dockerfiles():
        text = path.read_text(encoding="utf-8")
        examined += len(external_from_images(parse_dockerfile(text)))
        findings += [
            Finding(path.name, detail) for detail in unpinned_external_images(text)
        ]

    print(f"examined {examined} external `FROM` image reference(s)")
    assert examined >= 4, (
        f"only {examined} external `FROM` image(s) found across the tracked "
        "Dockerfiles. Each image declares a base plus at least one build stage, "
        "so fewer than four means the parser stopped seeing them and this check "
        "is passing on nothing."
    )
    assert not findings, "\n  ".join(["", *[str(f) for f in findings]])


# ---------------------------------------------------------------------------
# The updater must actually be configured to watch these files
# ---------------------------------------------------------------------------
def test_the_dependency_updater_watches_the_dockerfile_directory() -> None:
    """Moving a reference onto a FROM line only helps if something reads it.

    Both Dockerfiles sit at the repository root, so a ``docker`` ecosystem
    entry on directory ``/`` is what puts their ``FROM`` lines under automated
    CVE-driven bumps. Without that entry the digests pinned above would simply
    freeze and never be revisited, which is a different failure from the
    floating tag but no better an outcome.
    """
    import yaml  # noqa: PLC0415 - only this test needs it

    config = yaml.safe_load(
        (_REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    docker_entries = [
        entry
        for entry in config.get("updates", [])
        if entry.get("package-ecosystem") == "docker"
    ]
    assert docker_entries, (
        ".github/dependabot.yml has no `docker` ecosystem entry, so no `FROM` "
        "line in any Dockerfile is watched for CVE-driven bumps"
    )

    # Every tracked Dockerfile must fall under some watched directory. Both the
    # singular `directory:` and the plural `directories:` list are valid config,
    # so reading only the singular one would report a perfectly good config as
    # broken — a false positive, which costs exactly as much trust as a miss.
    watched: set[str] = set()
    for entry in docker_entries:
        if "directories" in entry:
            watched.update(str(d) for d in entry["directories"])
        else:
            watched.add(str(entry.get("directory", "/")))
    print(f"dependabot docker directories: {sorted(watched)}")
    for path in tracked_dockerfiles():
        relative = path.relative_to(_REPO_ROOT).as_posix()
        directory = "/" + pathlib.PurePosixPath(relative).parent.as_posix()
        directory = "/" if directory == "/." else directory
        assert directory in watched, (
            f"{relative} lives in `{directory}`, which no `docker` entry in "
            f".github/dependabot.yml watches (watched: {sorted(watched)}). Its "
            "base images would never be offered a bump."
        )


@pytest.mark.parametrize("name", sorted(_KNOWN_DOCKERFILES))
def test_the_build_tool_is_installed_from_a_pinned_stage(name: str) -> None:
    """The specific reference this guard was written for.

    Both images install the same ``uv`` binaries, and they must install them
    the same way — the test image exists to exercise what the production image
    ships, so a divergence in the tooling installed makes that comparison
    weaker without anything looking wrong. This asserts the SHAPE (a digest-
    pinned stage, copied from by name), never a particular version or digest:
    the whole point of the conversion is that the updater moves those, and a
    test pinning them would fail on every bump it exists to enable.
    """
    instructions = parse_dockerfile((_REPO_ROOT / name).read_text(encoding="utf-8"))
    stage_names = set(declared_stages(instructions))

    providers = {
        stage
        for stage in stage_names
        for operand in copy_from_operands(instructions)
        if operand.lower() == stage
    }
    # Match on the COPIED PATHS, not on the raw instruction text. Measured while
    # writing this guard: a `re.search(r"\b/uvx?\b", instruction.value)` looked
    # right and was matching the `/uv` inside `--from=ghcr.io/astral-sh/uv:latest`
    # — the image reference — rather than the `/uv` binary being copied. It went
    # green on the unfixed file for the wrong reason and then failed on the fixed
    # one, where the image name is no longer on the line. Splitting into operands
    # (which drops flags, so `--from=` cannot be read as a path) and taking the
    # basename removes the ambiguity entirely. The last operand is the
    # destination, so the sources are everything before it.
    uv_sources = {
        operand.lower()
        for instruction in instructions
        if instruction.keyword == "COPY"
        and any(
            pathlib.PurePosixPath(source).name in {"uv", "uvx"}
            for source in operands(instruction.value)[:-1]
        )
        for operand in re.findall(r"--from=(\S+)", instruction.value)
    }
    assert uv_sources, (
        f"{name} no longer copies the uv binaries from anywhere. If the frozen "
        "install moved to another tool, point this guard at it — do not delete "
        "the check."
    )
    assert uv_sources <= providers, (
        f"{name} copies the uv binaries from {sorted(uv_sources)}, which is not "
        f"a build stage declared in the file (declared: {sorted(stage_names)}). "
        "A registry image named here is invisible to the dependency updater."
    )
