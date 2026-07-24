FROM python:3.14-slim

# Bake the building commit into the image so /source can return the authoritative
# running-commit (the value a downstream consumer keys its cache on). The CI build
# passes this via --build-arg GIT_COMMIT=<sha>; _resolve_version() prefers it over
# every fallback. Empty default => local/dev builds fall back to the source-content
# hash, never a stale constant.
ARG GIT_COMMIT=""
ENV GIT_COMMIT=${GIT_COMMIT}

WORKDIR /app

# Install uv itself from its official distroless image (pinned digest-free tag is
# fine here — uv is the installer, not a project dependency; supply-chain risk for
# the actual app deps is covered by the frozen lockfile install below).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install from the committed, hash-pinned lockfile only — `--frozen` fails the
# build if pyproject.toml and uv.lock have drifted, instead of silently
# re-resolving. Replaces the old floating `pip install -e .`, which ignored
# uv.lock entirely and could pull an unpinned/compromised transitive version of
# pyswisseph (native C) or any other dependency.
COPY pyproject.toml uv.lock README.md LICENSE EPHEMERIS_LICENSE.md ./
RUN uv sync --frozen --no-install-project --no-dev
ENV PATH="/app/.venv/bin:${PATH}"

COPY bphs_core ./bphs_core
COPY app ./app
# data/ephe must be volume-mounted or COPY'd separately (not committed to git)
RUN mkdir -p data/ephe

# Second `uv sync --frozen` installs the project itself (editable, into the venv
# from the first sync) now that its source is present — still frozen against the
# same committed lockfile, no re-resolution.
RUN uv sync --frozen --no-dev

EXPOSE 8000

RUN adduser --disabled-password --gecos '' appuser
USER appuser

# --timeout-keep-alive 75: the default (5s) closes idle keep-alive connections
# fast, which races clients that pool connections (the backend's CalcClient keeps
# up to 20) — a send on a just-closed connection surfaces as httpx.ReadError.
# A 75s idle keep-alive outlives typical client idle gaps and removes that race;
# the client also retries the residual case. Keep ≥ any upstream/LB idle timeout.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "75"]
