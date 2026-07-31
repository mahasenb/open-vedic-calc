# Open Vedic Calc

A generic, stateless HTTP service for Vedic astrology (BPHS) calculations.

Computes planetary positions, divisional charts, Shadbala, Bhavabala, Ashtakavarga,
dasha periods, yogas, transits, and special points from birth data.

**License:** AGPL-3.0. Full source available at this repository.

## Self-hosting

```bash
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000
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
