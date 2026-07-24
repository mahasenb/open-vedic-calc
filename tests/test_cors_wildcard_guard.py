"""Tests for CALC-4 — reject a bare wildcard ALLOWED_ORIGINS at boot.

CLAUDE.md:47 states the rule ("Do not introduce a wildcard [\"*\"] origin"),
but nothing enforced it: ALLOWED_ORIGINS flowed straight from the environment
into CORSMiddleware with no validation, so a deploy-time
``ALLOWED_ORIGINS=*`` (typo or copy-paste from another service) would
silently open the API to any browser origin. This mirrors app.auth's
fail-closed CALC_SERVICE_TOKEN boot guard: reject at import time, not at
first request.
"""
import importlib

import pytest


def _reload_main_with(monkeypatch, *, allowed_origins):
    if allowed_origins is None:
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("ALLOWED_ORIGINS", allowed_origins)
    import app.main as main_mod
    return importlib.reload(main_mod)


@pytest.fixture(autouse=True)
def _restore_main():
    """Reload app.main back to the test-env default (no ALLOWED_ORIGINS) after
    each test so a wildcard reload in one test can't poison module state for
    others."""
    yield
    import os
    os.environ.pop("ALLOWED_ORIGINS", None)
    import app.main as main_mod
    importlib.reload(main_mod)


def test_bare_wildcard_origin_fails_to_import(monkeypatch):
    with pytest.raises(RuntimeError, match="wildcard"):
        _reload_main_with(monkeypatch, allowed_origins="*")


def test_wildcard_among_other_origins_fails_to_import(monkeypatch):
    """A wildcard mixed into a comma-separated list must also be rejected —
    the guard checks every parsed origin, not just a single-entry list."""
    with pytest.raises(RuntimeError, match="wildcard"):
        _reload_main_with(
            monkeypatch,
            allowed_origins="https://example.com,*,https://other.example.com",
        )


def test_whitespace_only_wildcard_fails_to_import(monkeypatch):
    with pytest.raises(RuntimeError, match="wildcard"):
        _reload_main_with(monkeypatch, allowed_origins=" * ")


def test_missing_allowed_origins_imports_fine(monkeypatch):
    """No ALLOWED_ORIGINS at all (local/dev convenience) must not be rejected
    — the guard targets an explicit wildcard, not an empty allow-list."""
    mod = _reload_main_with(monkeypatch, allowed_origins=None)
    assert mod._ALLOWED_ORIGINS == []


def test_real_origin_list_imports_fine(monkeypatch):
    mod = _reload_main_with(
        monkeypatch, allowed_origins="https://example.com,https://other.example.com"
    )
    assert mod._ALLOWED_ORIGINS == ["https://example.com", "https://other.example.com"]
