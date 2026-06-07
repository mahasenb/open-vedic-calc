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

Download Swiss Ephemeris files into `data/ephe/` — see `EPHEMERIS_LICENSE.md`.

## API

OpenAPI spec: `openapi.yaml` (or `/docs` when running).

Endpoints under `/v1/`:

| POST | `/chart` | Full chart: D1–D60, lagna, ayanamsa |
| POST | `/strength` | Shadbala, Bhavabala, Ashtakavarga |
| POST | `/dashas` | Vimshottari / Yogini / Char dasha periods |
| POST | `/yogas` | All 284 yogas incl. Viparita Raja |
| POST | `/transits` | Saturn/Jupiter gochara, Sade Sati, Vedha |
| POST | `/special-points` | Arudha, Upapada, Atmakaraka, Karakamsa |
| GET | `/source` | License, source URL, running commit |
