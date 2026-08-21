"""Regression guard: EPHEMERIS_LICENSE.md must agree with the fetch manifest.

That document tells a human where the Swiss Ephemeris data files come from and how
to get them into a source checkout. It has already rotted once, and expensively:
it told readers to download from ``https://www.astro.com/ftp/ephe/`` long after that
path started returning 404, and CLAUDE.md records the consequence — "the instruction
had gone stale, which is how this repo's suite ended up never once running against
real ephemeris data."

Nothing prevented that. ``ci/swiss_ephemeris.json`` is the source of truth for where
the bytes come from, the doc restates it in prose, and the two were free to disagree
indefinitely because no check ever compared them. A doc that rots silently is the
same failure class as a control that silently disables itself: it looks maintained
and is not.

WHAT THIS PINS
--------------
1. The doc names the manifest's CURRENT ``upstream_repo`` and ``upstream_commit``.
   Bumping the pin without updating the doc reds here, in the same PR — which is the
   workspace rule (docs change with the thing they document) made mechanical.
2. No URL recorded in the manifest's ``known_dead_sources`` appears in the doc
   outside its "Dead links" section. Naming a dead path to say "this is dead, do not
   restore it" is useful history; naming one anywhere else is an instruction that
   will waste somebody's afternoon.
3. The manifest's dead-source record stays well-formed, so this guard cannot pass
   vacuously on an empty list.

Loaded by path, never ``from ci import ...``: ``ci/`` has no ``__init__.py`` by
design and CI runs the bare ``pytest ci/tests/ -q`` console script (see CLAUDE.md).
"""
from __future__ import annotations

import json
import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
_MANIFEST = _REPO / "ci" / "swiss_ephemeris.json"
_DOC = _REPO / "EPHEMERIS_LICENSE.md"

# The heading under which naming a dead URL is legitimate — as history, explicitly
# labelled. Anywhere else in the document, a dead URL reads as an instruction.
_DEAD_LINKS_HEADING_MARKER = "dead links"


def _manifest() -> dict:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def _sections(markdown: str) -> list[tuple[str, str]]:
    """(heading, body) pairs, split on ATX headings. The preamble gets heading ''."""
    sections: list[tuple[str, str]] = []
    heading = ""
    body: list[str] = []
    for line in markdown.splitlines():
        if line.lstrip().startswith("#"):
            sections.append((heading, "\n".join(body)))
            heading = line.lstrip("#").strip()
            body = []
        else:
            body.append(line)
    sections.append((heading, "\n".join(body)))
    return sections


def test_manifest_records_its_dead_sources_structurally() -> None:
    """The record this guard reads must exist and be non-empty.

    Without this, deleting ``known_dead_sources`` would make
    ``test_dead_urls_appear_only_under_the_dead_links_heading`` pass over an empty
    list — vacuously green, guarding nothing. An inventory that cannot drift cannot
    detect drift.
    """
    dead = _manifest().get("known_dead_sources")
    assert isinstance(dead, dict), (
        "ci/swiss_ephemeris.json no longer carries a 'known_dead_sources' object. "
        "That record is what stops EPHEMERIS_LICENSE.md re-acquiring a dead download "
        "instruction; removing it removes the guard, not the problem."
    )
    urls = dead.get("urls")
    assert isinstance(urls, list) and urls, "'known_dead_sources.urls' is empty"
    for url in urls:
        assert isinstance(url, str) and url.startswith("http"), f"malformed url: {url!r}"
    assert dead.get("probed_date"), (
        "'known_dead_sources.probed_date' is missing — a 404 claim with no date is "
        "not a measurement, and the next reader cannot tell how stale it is"
    )


def test_doc_names_the_manifests_current_source() -> None:
    """A pin bump must not be able to leave the doc describing the old source."""
    manifest = _manifest()
    doc = _DOC.read_text(encoding="utf-8")

    repo = manifest["upstream_repo"]
    assert repo in doc, (
        f"EPHEMERIS_LICENSE.md does not name the manifest's upstream_repo ({repo}). "
        "The doc is what a human reads to find the data; it must point where the "
        "fetcher actually goes."
    )

    commit = manifest["upstream_commit"]
    assert commit in doc, (
        f"EPHEMERIS_LICENSE.md does not name the manifest's upstream_commit "
        f"({commit}). The source is pinned to a COMMIT precisely so the path is "
        "content-addressed; a doc that names only the repository sends the reader to "
        "whatever the default branch holds today, which is a different set of bytes "
        "from the ones the goldens were recorded against. Update the doc in the same "
        "PR as the pin bump."
    )


def test_dead_urls_appear_only_under_the_dead_links_heading() -> None:
    """A URL measured 404 must never read as a live instruction again.

    The document may — and should — name these as history, so nobody "helpfully"
    restores one. That is what the "Dead links" section is for. Outside it, the same
    string is an instruction to download from a path that returns 404.
    """
    manifest = _manifest()
    dead_urls = manifest["known_dead_sources"]["urls"]
    doc = _DOC.read_text(encoding="utf-8")

    offenders: list[str] = []
    for heading, body in _sections(doc):
        if _DEAD_LINKS_HEADING_MARKER in heading.lower():
            continue
        for url in dead_urls:
            if url in body:
                offenders.append(f"{url!r} under heading {heading or '<preamble>'!r}")

    assert not offenders, (
        "EPHEMERIS_LICENSE.md names a URL the manifest records as 404, outside its "
        f"'Dead links' section: {offenders}. That is how this document rotted the "
        "first time — it told readers to download from a path that no longer served "
        "anything, and the suite ran on the Moshier fallback for months as a result. "
        "Move the mention under the 'Dead links' heading (where it is history) or "
        "delete it."
    )

    # ...and the section must actually exist, or the rule above is satisfied by a
    # document that simply never mentions them — losing the "do not restore this"
    # history the section carries.
    assert any(
        _DEAD_LINKS_HEADING_MARKER in heading.lower() for heading, _ in _sections(doc)
    ), (
        "EPHEMERIS_LICENSE.md has no 'Dead links' section. Keep it: it is what stops "
        "a future reader restoring the astro.com download instruction in good faith."
    )


def test_no_doc_overclaims_beyond_what_was_probed() -> None:
    """Three paths were probed. No doc may generalise that to all of astro.com.

    Both documents carried the wider claim: EPHEMERIS_LICENSE.md said the dead path
    and every variant of it on that host returns 404 — while, two sections earlier,
    sending readers to an astro.com URL for the Professional Licence — and CLAUDE.md
    repeated the generalisation. A claim wider than its measurement is how a
    document contradicts itself, and how its accurate parts stop being trusted.

    LIMITATION, stated rather than papered over: this matches strings, so it cannot
    tell a quotation from an assertion. A doc that wants to describe the old wording
    must paraphrase it, which is what EPHEMERIS_LICENSE.md's "Dead links" section
    does and says it does.
    """
    overclaims = ("every astro.com variant", "all astro.com", "every astro.com path")
    for path in (_DOC, _REPO / "CLAUDE.md"):
        text = path.read_text(encoding="utf-8").lower()
        for overclaim in overclaims:
            assert overclaim not in text, (
                f"{path.name} claims {overclaim!r}. Only the paths in the manifest's "
                "known_dead_sources were actually probed — say that, and say when. "
                "Nothing was measured about astro.com's licensing pages."
            )
