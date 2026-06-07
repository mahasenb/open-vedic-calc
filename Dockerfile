FROM python:3.10-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE EPHEMERIS_LICENSE.md ./
RUN pip install --no-cache-dir -e .

COPY bphs_core ./bphs_core
COPY app ./app
# data/ephe must be volume-mounted or COPY'd separately (not committed to git)
RUN mkdir -p data/ephe

EXPOSE 8000

RUN adduser --disabled-password --gecos '' appuser
USER appuser

# --timeout-keep-alive 75: the default (5s) closes idle keep-alive connections
# fast, which races clients that pool connections (the backend's CalcClient keeps
# up to 20) — a send on a just-closed connection surfaces as httpx.ReadError.
# A 75s idle keep-alive outlives typical client idle gaps and removes that race;
# the client also retries the residual case. Keep ≥ any upstream/LB idle timeout.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "75"]
