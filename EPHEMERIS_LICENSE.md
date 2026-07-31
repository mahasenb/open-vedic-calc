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

To obtain the paid Professional License (required for proprietary use of
the data files themselves), see: https://www.astro.com/swisseph/

## Files

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
- `sefstars.txt` — fixed stars (~0.1 MB)

The upstream source is the Swiss Ephemeris distribution repository,
https://github.com/aloistr/swisseph (directory `ephe/`), pinned to a specific
commit in `ci/swiss_ephemeris.json`.

This file previously told you to download from `https://www.astro.com/ftp/ephe/`.
That path, and every astro.com variant of it, now returns **404** (probed
2026-07-25: `/ftp/ephe/`, `/ftp/swisseph/ephe/`, `/swisseph/ephe/`) — the
instruction had gone stale, which is how this repo's suite ended up never once
running against real ephemeris data.
