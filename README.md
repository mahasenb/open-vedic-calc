# Open Vedic Calc

A generic, stateless HTTP service for Vedic astrology (BPHS) calculations.

Computes planetary positions, divisional charts, Shadbala, Bhavabala, Ashtakavarga,
dasha periods, yogas, transits, and special points from birth data.

**License:** AGPL-3.0. Full source available at this repository.

## Self-hosting

```bash
uv sync --frozen        # the exact set in uv.lock, on the interpreter in .python-version
uv run --frozen uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`--frozen` is not incidental, and neither is the interpreter. `uv.lock` forks at
Python 3.11 — `numpy` resolves to 2.2.6 below it and 2.4.6 at or above,
`timezonefinder` to 8.2.0 and 8.2.5 — so "the locked set" does not name a single
set of versions until the interpreter is fixed, and a base-image tag edit changes
what installs while `pyproject.toml` and `uv.lock` stay byte-identical. That is
why `.python-version` exists, and why the images, CI and the command above all
read it instead of each naming a version of their own.

Neither forking package is reached by the compute path as the code stands, which
was measured rather than assumed — with the instrument proven live first, then
counted across every route this service serves and the full test suite: numpy is
called exactly twice, both times at import, building one constant lookup table
inside a dependency — a table measured identical on both sides of the fork.
So the pin is here to install the set these values were recorded against, not to
guard numpy arithmetic. A floating `pip install -e .` resolves whatever is newest
today instead — measured, that differed from the shipped image on 13 packages
that actually ship.

Developing on it? Add the test extras and run the suite the way CI runs it:

```bash
uv sync --frozen --extra dev
uv run --frozen pytest tests/ -q
uv run --frozen pytest ci/tests/ -q
```

Set the following environment variables:

```
CALC_SERVICE_TOKEN=<any secret>
PUBLIC_SOURCE_URL=<URL of this public repo>
```

Swiss Ephemeris data files: **nothing to do if you run the Docker images.**
`Dockerfile` and `Dockerfile.test` fetch and checksum-verify them during the
build and bake them into `/app/data/ephe`. Do not mount a volume over that
path — it shadows the baked files, and swisseph responds by silently computing
on its built-in Moshier fallback rather than raising.

Running from a source checkout instead (`uvicorn app.main:app`)? Fetch them once:

```
python ci/fetch_swiss_ephemeris.py
```

See `EPHEMERIS_LICENSE.md`.

## Astronomical conventions

Two engines can both be defensible and still disagree, because Vedic astrology has
genuine methodological choices. This service makes them explicitly, so a number
that differs from another tool can be explained rather than guessed at:

| Choice | This service | Where it is set |
|---|---|---|
| Ayanamsa | **Lahiri** (Chitrapaksha) | `bphs_core/utils.py` |
| Lunar nodes (Rahu / Ketu) | **True (osculating) node**; Ketu = Rahu + 180° | `bphs_core/utils.py`, `LUNAR_NODE_MODEL` |
| Planetary position frame | **Geometric** (`FLG_TRUEPOS`), sidereal, geocentric — flag word `66386` | `bphs_core/utils.py`, `POSITION_FLAGS` |
| Primary house frame | **Whole-sign**, counted from the lagna sign | `bphs_core/chart.py` |
| Bhava-Chalit cusps | Sidereal **Placidus**, supplementary only | `chalit_cusps` in the chart response |
| Ascendant | Computed directly from `swe.houses`, not from the chart library | `bphs_core/chart.py` |
| Sunrise / sunset | **Disc centre at the true (geometric) horizon** — the classical Hindu convention; `rsmi` words `897` / `898` | `bphs_core/utils.py`, `RISE_FLAGS` / `SET_FLAGS` |
| Ephemeris | Swiss Ephemeris data files (AD 1800–2400) | baked into the images |

The node model is the choice most likely to explain a visible discrepancy.
Measured over 1800–2400 at one-day steps against the real Swiss data files, the
true and mean nodes differ by up to **1.98°**, and place Rahu in a different
nakshatra pada on **28.85%** of days, a different nakshatra on 7.19%, and a
different rasi on 3.20%. So if your Rahu is a degree or two from what this
service returns, a mean-node engine is the first thing to check.

That model is pinned deliberately rather than inherited from a dependency
default, and the service refuses to start if its dependency stops honouring it —
see `bphs_core/utils.py` and `tests/test_lunar_node_model.py`.

The **position-flag word** is pinned the same way. Every longitude here is a
*geometric* position (`FLG_TRUEPOS`): the graha's true place on the ecliptic,
without the light-time and aberration corrections that give an *apparent*
position. Measured over 1800–2400 at one-day steps against the real Swiss data
files, the two frames differ by up to **60.54″** (Mercury) — small next to the
node model, but enough to move a placement across a pada boundary. If your chart
sits within a minute of arc of this service's, an apparent-position engine is the
thing to check. The word is declared as `POSITION_FLAGS` in `bphs_core/utils.py`
and the service refuses to start if the dependency ever builds a different one;
`tests/test_position_flags.py` is the contract.

The **sunrise/sunset convention** is pinned the same way. Every panchanga and
electional limb — tithi, nakshatra, yoga, karana, the thirty muhurtas and the
muhurat/lagna-shuddhi scan — is anchored to the day boundary, and this service
defines that boundary as the Sun's disc **centre** at the **true, unrefracted
horizon** (the classical *madhya-bimba* Surya-Siddhanta convention), not the upper
limb at the apparent, refracted horizon a secular almanac reports. That choice is
a settled decision, not an open question: the disc-centre, unrefracted convention
is the one the Siddhantic tithi arithmetic assumes, while the civil upper-limb
alternative remains a defensible convention a secular almanac uses. Measured at
Colombo on 2024-03-20, the two conventions place sunrise **147.99 s** apart, so a
quietly refracted engine would shift every day-boundary computation. The words are
declared as `RISE_FLAGS` / `SET_FLAGS` in `bphs_core/utils.py`, distinct from the
ephemeris frame above, and the service refuses to start if the dependency ever
builds a different one; `tests/test_rise_set_flags.py` is the contract.

## API

The API contract is defined in `app/schemas.py` (request/response models) and the
endpoint table below. `/docs` and `/openapi.json` are intentionally disabled at
runtime (unauthenticated introspection endpoints were a security risk) — there is
no served or committed OpenAPI document; read `app/schemas.py` for the exact
shapes.

Endpoints under `/v1/`:

| POST | `/chart` | Full chart: D1–D60, lagna, ayanamsa |
| POST | `/strength` | Shadbala, Bhavabala, Ashtakavarga |
| POST | `/dashas` | Vimshottari / Yogini / Char dasha periods |
| POST | `/yogas` | All 284 yogas incl. Viparita Raja |
| POST | `/transits` | Saturn/Jupiter gochara, Sade Sati, Vedha |
| POST | `/special-points` | Arudha, Upapada, Atmakaraka, Karakamsa |
| GET | `/source` | License, source URL, running commit |

### Errors

Request-validation failures return `422` with `{"detail": [...]}` (the standard
FastAPI shape). One engine-compute failure has its own structured shape: when a
muhurat/electional limb that decides *which* time can be recommended cannot be
computed for the requested date/place, the engine raises rather than fabricating
a day frame, and the synchronous routes surface that as a `422` naming the limb:

```json
{"detail": {"code": "muhurat_limb_error", "limb": "sunrise",
            "target_date": "2026-05-26", "message": "..."}}
```

The stable `code` is the field to branch on; `limb` names what failed. This is
returned instead of an opaque `500`, and never as a `200` with an empty or
partial result. On the async scan-job endpoints the same failure is reported in
the job's `error` field on poll, not as an HTTP error on submit.
