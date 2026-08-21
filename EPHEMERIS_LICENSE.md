# Swiss Ephemeris License

The ephemeris data files in `data/ephe/` are distributed under the
Swiss Ephemeris dual license (AGPL-3.0 or paid Professional License).

This service uses them under AGPL-3.0. The full license text is in `LICENSE`.

**These files are REDISTRIBUTED inside the container images this repo builds.**
`Dockerfile` and `Dockerfile.test` fetch them at build time and bake them into
`/app/data/ephe`, so every image built from this repo contains them. That
redistribution is under AGPL-3.0 — the same license this service ships under —
which is exactly what the dual license permits without a Professional License,
since the whole image remains AGPL-3.0 and its corresponding source is this
public repository. Baking them in does not and cannot relicense them: anyone
wanting the data files under proprietary terms still needs the paid
Professional License.

## Getting the files

The images fetch them for you at build time. To get them into a source
checkout, run the same script the build and CI use — it downloads from a
commit-pinned upstream location and verifies each file against the sha256
pinned in `ci/swiss_ephemeris.json`:

```
python ci/fetch_swiss_ephemeris.py
```

The files land in `data/ephe/` (git-ignored, and baked into the images) and are:

- `sepl_18.se1` — planets, AD 1800–2400 (~0.5 MB)
- `semo_18.se1` — Moon, AD 1800–2400 (~1.3 MB)
- `seas_18.se1` — main asteroids / derived bodies, AD 1800–2400 (~0.2 MB)

"AD 1800–2400" is upstream's label and is rounded at the top. Measured with
`bphs_core.utils.probe_ephemeris_source()` at one-day steps, the last day the
seven visible grahas answer from these files is **2400-01-10**; from 2400-01-11
swisseph silently falls back to its built-in Moshier ephemeris and still returns
a full, plausible result. The served date range — for **every** date field a
caller can send, not only `birth_date` — is bounded to the measured span rather
than the label: see `MIN_EPHEMERIS_DATE` / `MAX_EPHEMERIS_DATE` in
`app/schemas.py`, which additionally hold a day of margin at each end because a
single local date spans UTC instants a day either side once
`timezone_offset_hours` (\[-12, +14]) is applied.
- `sefstars.txt` — fixed stars (~0.1 MB)

## Where the bytes actually come from

**`ci/swiss_ephemeris.json` is the source of truth, not this file.** What follows
restates it for a human reader; if the two ever disagree, the manifest is right
and this document is stale.
`ci/tests/test_ephemeris_license_doc.py` fails the build if they drift apart, so
that disagreement should not survive a pull request.

- Upstream repository: <https://github.com/aloistr/swisseph> (directory `ephe/`)
- Pinned commit: `59ac051b5a5812c684973ca0fcedb1c8c3e9c5dc`
- Resolved download base:
  `https://raw.githubusercontent.com/aloistr/swisseph/59ac051b5a5812c684973ca0fcedb1c8c3e9c5dc/ephe/`

The commit, not a branch, is the load-bearing part: it makes the path
content-addressed, so the bytes cannot move under us and the committed golden
values stay reproducible. Each file additionally carries a trust-on-first-use
sha256 in the manifest, exactly like a lockfile entry.

Bumping that pin means updating this section in the same change — the guard
above checks for the manifest's *current* commit, so a bump that leaves this
document naming the old one fails CI rather than quietly misdirecting the next
reader.

## The paid Professional License

Required only for proprietary use of the data files themselves; this service does
not need it, because it ships under AGPL-3.0.

The Professional License is sold by the upstream authors (Astrodienst), whose
Swiss Ephemeris pages live at <https://www.astro.com/swisseph/>. **This repository
has not probed that page** — the only astro.com paths it measured are the
ephemeris-*data* directories listed under "Dead links" below, and their being dead
says nothing about the licensing pages. If that URL has also moved, start from the
upstream repository above, which carries the project's own licensing documentation
and is the source this repo actually fetches from.

## Dead links (do not restore these)

This file used to tell you to download the data from
`https://www.astro.com/ftp/ephe/`. That instruction had gone stale, and the cost
was not theoretical: it is how this repo's suite ended up never once running
against real ephemeris data (see `CLAUDE.md`, CALC-1).

Three astro.com ephemeris-data paths were probed on **2026-07-25** and every one
returned **404**:

- `https://www.astro.com/ftp/ephe/`
- `https://www.astro.com/ftp/swisseph/ephe/`
- `https://www.astro.com/swisseph/ephe/`

Those three paths, and that date, are recorded as data in
`ci/swiss_ephemeris.json` under `known_dead_sources`, so the guard reads the same
list this section prints.

**Scope, stated exactly.** Three paths were probed. That is not a finding about
astro.com as a whole, and this document deliberately no longer generalises it into
one. An earlier version did — it declared the whole host's variants dead while, two
sections up, still sending readers to an astro.com URL for the Professional
License. A claim wider than its measurement is how a document starts contradicting
itself, and how its accurate parts stop being believed.

(`ci/tests/test_ephemeris_license_doc.py` enforces this by refusing a short list of
generalising phrases. It matches strings, so it cannot tell a quotation from an
assertion — which is why the sentence above describes the old wording instead of
reproducing it.)
