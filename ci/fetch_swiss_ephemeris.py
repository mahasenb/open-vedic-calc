#!/usr/bin/env python3
"""Fetch and checksum-verify the Swiss Ephemeris data files into ``data/ephe`` (CALC-1).

Until this existed, every automated run of this repo's suite validated against the
Moshier fallback engine — never the Swiss ephemeris data files the service actually
runs on. For a determinism-first astronomical engine that is the wrong thing to
test: a regression that only manifests with real Swiss data (a data-file-dependent
edge case, an off-by-epoch bug) could merge green.

This script is the data half of the fix. The accuracy half is
``tests/test_swiss_ephemeris.py``, which refuses to pass on Moshier when
``REQUIRE_SWISS_EPHEMERIS=1``.

Usage
-----
    python ci/fetch_swiss_ephemeris.py            # download (if absent) + verify
    python ci/fetch_swiss_ephemeris.py --verify-only
    python ci/fetch_swiss_ephemeris.py --self-test

FAIL-CLOSED BEHAVIOUR
---------------------
Every outcome other than "all four files present and matching their pinned
sha256" is a non-zero exit:

  * download failure                 -> exit 1 (never a warning; the job that needs
                                        the data must not proceed on Moshier)
  * checksum mismatch                -> exit 1, and the file is REMOVED so a
                                        cached bad copy cannot persist
  * pinned sha256 is null            -> exit 1, printing the computed value to
                                        record in ci/swiss_ephemeris.json. A null
                                        pin is an unfinished lockfile entry, NOT
                                        "skip verification" — the single most
                                        tempting way to turn this into a control
                                        that silently disables itself.
  * manifest missing/unparseable     -> exit 1

The checksums are trust-on-first-use, like a lockfile: they do not certify the
upstream publisher, they certify that the bytes CI runs on today are the bytes the
committed golden values were produced from.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST = _REPO_ROOT / "ci" / "swiss_ephemeris.json"
_DEST = _REPO_ROOT / "data" / "ephe"

_CHUNK = 1 << 16
_TIMEOUT_SECONDS = 120


class FetchError(RuntimeError):
    """Any condition that must fail the job."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in ci/tests/test_fetch_swiss_ephemeris.py)
# ---------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(manifest_path: Path = _MANIFEST) -> tuple[str, list[dict]]:
    """Parse the manifest, rejecting anything structurally wrong.

    Parsed as JSON rather than scanned as text so a checksum inside a comment
    string, or a duplicated/reordered entry, cannot be mistaken for a pin.
    """
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FetchError(f"manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise FetchError(f"manifest is not valid JSON: {manifest_path}: {exc}") from exc

    base_url = document.get("base_url")
    files = document.get("files")
    if not isinstance(base_url, str) or not base_url:
        raise FetchError("manifest has no usable 'base_url'")
    if not isinstance(files, list) or not files:
        raise FetchError("manifest has no 'files' entries")

    # The upstream location must stay pinned to a commit, never a branch: a branch
    # URL would silently serve different bytes over time, and the checksum pins would
    # then fail as a *mystery* rather than as a reviewable upstream change. Requiring
    # the commit to appear IN the URL stops the two drifting apart — someone
    # repointing base_url at `master` while leaving `upstream_commit` in place is
    # exactly the edit this catches.
    commit = document.get("upstream_commit")
    if not isinstance(commit, str) or len(commit) != 40 or not _is_hex(commit):
        raise FetchError(
            f"manifest 'upstream_commit' must be a 40-character commit sha, got {commit!r}"
        )
    if commit not in base_url:
        raise FetchError(
            "manifest 'base_url' does not contain 'upstream_commit' — the data source "
            "must be pinned to that commit, not to a moving branch.\n"
            f"        base_url:        {base_url}\n"
            f"        upstream_commit: {commit}"
        )

    names: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise FetchError(f"manifest 'files' entry is not an object: {entry!r}")
        name = entry.get("name")
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in {
            ".",
            "..",
        }:
            raise FetchError(f"manifest entry has an unusable 'name': {name!r}")
        if name in names:
            raise FetchError(f"manifest lists {name!r} twice")
        names.add(name)
        pinned = entry.get("sha256")
        if pinned is not None and (
            not isinstance(pinned, str) or len(pinned) != 64 or not _is_hex(pinned)
        ):
            raise FetchError(f"manifest entry {name!r} has a malformed sha256: {pinned!r}")
    return base_url, files


def _is_hex(value: str) -> bool:
    return all(c in "0123456789abcdefABCDEF" for c in value)


def verify_file(path: Path, pinned: str | None, *, name: str) -> None:
    """Raise unless ``path`` exists and matches ``pinned``. Never returns on doubt."""
    if not path.is_file():
        raise FetchError(f"{name}: expected at {path} but it is not there")
    actual = sha256_of(path)
    if pinned is None:
        raise FetchError(
            f"{name}: sha256 is not pinned in ci/swiss_ephemeris.json.\n"
            f"        Record this value there (reviewable diff, then re-run):\n"
            f'          "sha256": "{actual}"'
        )
    if actual != pinned:
        raise FetchError(
            f"{name}: sha256 mismatch — the bytes are not what the committed golden "
            f"values were produced from.\n"
            f"        pinned: {pinned}\n"
            f"        actual: {actual}"
        )


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


def download(url: str, destination: Path) -> None:
    """Download to a temp file in the destination dir, then atomically move it.

    Downloading in place would leave a truncated file behind on a network failure,
    which a cache restore could later present as a complete one.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(destination.parent), suffix=".part")
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            with temp_path.open("wb") as out:
                shutil.copyfileobj(response, out, _CHUNK)
        # tempfile.mkstemp creates its file 0600 (owner-only), and that mode
        # survives both the rename and a Docker `COPY --from=` into another
        # build stage. The service image runs as an unprivileged `appuser`
        # while the build runs as root, so 0600 data files are UNREADABLE to
        # the process that actually serves requests — and swisseph does not
        # raise when it cannot read its data files, it silently returns Moshier
        # results. Measured while writing this: an image containing all four
        # files, whose build-time assertion passed as root, still reported
        # retflag 65604 when run as appuser. World-readable is right for
        # public, licensed reference data that nothing writes at runtime.
        temp_path.chmod(0o644)
        temp_path.replace(destination)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        temp_path.unlink(missing_ok=True)
        raise FetchError(f"download failed: {url}: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(*, verify_only: bool, dest: Path = _DEST, manifest_path: Path = _MANIFEST) -> list[str]:
    """Ensure every manifest file is present in ``dest`` and matches its pin.

    Returns the list of problems; empty means success.
    """
    base_url, files = load_manifest(manifest_path)
    problems: list[str] = []

    for entry in files:
        name = entry["name"]
        target = dest / name
        pinned = entry.get("sha256")

        if not target.is_file():
            if verify_only:
                problems.append(f"{name}: missing from {dest} (--verify-only, not fetching)")
                continue
            url = base_url.rstrip("/") + "/" + name
            print(f"fetching {url}")
            try:
                download(url, target)
            except FetchError as exc:
                problems.append(str(exc))
                continue
        else:
            print(f"present  {name} (cached)")

        try:
            verify_file(target, pinned, name=name)
        except FetchError as exc:
            problems.append(str(exc))
            # A mismatching file must never survive into the cache for the next run.
            if pinned is not None:
                target.unlink(missing_ok=True)
            continue
        print(f"verified {name}")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="do not download; only verify what is already present",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the verifier's discrimination fixtures and exit",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        failures = self_test_failures()
        for failure in failures:
            print(f"SELF-TEST FAILED: {failure}", file=sys.stderr)
        if failures:
            return 1
        print("self-test OK")
        return 0

    try:
        problems = run(verify_only=args.verify_only)
    except FetchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if problems:
        print(
            "\nERROR: the Swiss ephemeris data is NOT usable, so the accuracy job must "
            "not run against the Moshier fallback:",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"\nAll Swiss ephemeris files verified in {_DEST}")
    return 0


# ---------------------------------------------------------------------------
# Self-test: the verifier must DISCRIMINATE
# ---------------------------------------------------------------------------


def self_test_failures() -> list[str]:
    """Fixtures a verifier that stopped verifying would get wrong.

    Kept in the script itself (not only in ci/tests/) so `--self-test` can prove the
    mechanism from CI without collecting a test suite.
    """
    failures: list[str] = []
    payload = b"swiss ephemeris self test payload"
    good = hashlib.sha256(payload).hexdigest()
    wrong = "0" * 64

    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        target = tmp / "sepl_18.se1"
        target.write_bytes(payload)

        # 1. Matching pin must pass.
        try:
            verify_file(target, good, name="sepl_18.se1")
        except FetchError as exc:
            failures.append(f"a matching checksum was rejected: {exc}")

        # 2. Wrong pin must fail (and remove nothing — removal is run()'s job).
        try:
            verify_file(target, wrong, name="sepl_18.se1")
            failures.append("a MISMATCHING checksum was accepted")
        except FetchError:
            pass

        # 3. A null pin must fail, not pass silently.
        try:
            verify_file(target, None, name="sepl_18.se1")
            failures.append("an UNPINNED (null) checksum was accepted")
        except FetchError:
            pass

        # 4. A missing file must fail.
        try:
            verify_file(tmp / "absent.se1", good, name="absent.se1")
            failures.append("a MISSING file was accepted")
        except FetchError:
            pass

        # 5. A truncated file must fail against the full-file pin.
        truncated = tmp / "truncated.se1"
        truncated.write_bytes(payload[:-1])
        try:
            verify_file(truncated, good, name="truncated.se1")
            failures.append("a TRUNCATED file was accepted")
        except FetchError:
            pass

        # 6. Structurally broken manifests must be rejected, not partially honoured.
        pinned_commit = "a" * 40
        pinned_base = f"https://example.invalid/{pinned_commit}/ephe/"
        for label, document in (
            ("no base_url", {"upstream_commit": pinned_commit, "files": [{"name": "a", "sha256": None}]}),
            ("no files", {"upstream_commit": pinned_commit, "base_url": pinned_base}),
            (
                "duplicate name",
                {
                    "upstream_commit": pinned_commit,
                    "base_url": pinned_base,
                    "files": [{"name": "a", "sha256": None}, {"name": "a", "sha256": None}],
                },
            ),
            (
                "path traversal in name",
                {
                    "upstream_commit": pinned_commit,
                    "base_url": pinned_base,
                    "files": [{"name": "../x", "sha256": None}],
                },
            ),
            (
                "malformed sha256",
                {
                    "upstream_commit": pinned_commit,
                    "base_url": pinned_base,
                    "files": [{"name": "a", "sha256": "nope"}],
                },
            ),
            (
                "missing upstream_commit",
                {"base_url": pinned_base, "files": [{"name": "a", "sha256": None}]},
            ),
            (
                "upstream_commit is a branch name",
                {
                    "upstream_commit": "master",
                    "base_url": "https://example.invalid/master/ephe/",
                    "files": [{"name": "a", "sha256": None}],
                },
            ),
            (
                "base_url points at a branch while a commit is claimed",
                {
                    "upstream_commit": pinned_commit,
                    "base_url": "https://example.invalid/master/ephe/",
                    "files": [{"name": "a", "sha256": None}],
                },
            ),
        ):
            manifest = tmp / "manifest.json"
            manifest.write_text(json.dumps(document), encoding="utf-8")
            try:
                load_manifest(manifest)
                failures.append(f"a structurally invalid manifest was accepted ({label})")
            except FetchError:
                pass

        # A correctly-pinned manifest must still be ACCEPTED, so the checks above
        # are discrimination and not a blanket rejection.
        manifest = tmp / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "upstream_commit": pinned_commit,
                    "base_url": pinned_base,
                    "files": [{"name": "sepl_18.se1", "sha256": good}],
                }
            ),
            encoding="utf-8",
        )
        try:
            load_manifest(manifest)
        except FetchError as exc:
            failures.append(f"a correctly-pinned manifest was rejected: {exc}")

    # The repo's own manifest must be structurally valid — a typo there would
    # otherwise only surface as a confusing download failure in CI.
    try:
        load_manifest(_MANIFEST)
    except FetchError as exc:
        failures.append(f"the committed ci/swiss_ephemeris.json is invalid: {exc}")

    return failures


if __name__ == "__main__":
    sys.exit(main())
