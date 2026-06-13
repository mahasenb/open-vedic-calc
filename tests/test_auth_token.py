"""Tests for CALC-7 — reject a missing or weak service token in non-dev envs.

A guessable/default token (e.g. left at "changeme") is no better than no token,
so the import-time guard must fail closed on it outside development/local/test.
"""
import importlib

import pytest


def test_weakness_reason_pure():
    from app import auth

    assert auth._token_weakness_reason("") == "unset"
    assert auth._token_weakness_reason("changeme") == "a known placeholder value"
    assert auth._token_weakness_reason("CHANGEME") == "a known placeholder value"  # case-insensitive
    assert auth._token_weakness_reason("  test  ") == "a known placeholder value"  # trimmed
    assert auth._token_weakness_reason("short").startswith("too short")
    # A long random secret is acceptable.
    assert auth._token_weakness_reason("a" * 16) is None
    assert auth._token_weakness_reason("0123456789abcdef0123456789abcdef") is None


def _reload_auth_with(monkeypatch, *, environment, token):
    monkeypatch.setenv("ENVIRONMENT", environment)
    if token is None:
        monkeypatch.delenv("CALC_SERVICE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("CALC_SERVICE_TOKEN", token)
    from app import auth
    return importlib.reload(auth)


@pytest.fixture(autouse=True)
def _restore_auth_module():
    """Reload app.auth back to the test-env defaults after each test so a
    production-env reload in one test can't poison module state for others."""
    yield
    import os
    os.environ["ENVIRONMENT"] = "test"
    os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
    from app import auth
    importlib.reload(auth)


def test_production_weak_token_fails_to_import(monkeypatch):
    with pytest.raises(RuntimeError, match="placeholder"):
        _reload_auth_with(monkeypatch, environment="production", token="changeme")


def test_production_short_token_fails_to_import(monkeypatch):
    with pytest.raises(RuntimeError, match="too short"):
        _reload_auth_with(monkeypatch, environment="production", token="abc123")


def test_production_missing_token_fails_to_import(monkeypatch):
    with pytest.raises(RuntimeError, match="unset"):
        _reload_auth_with(monkeypatch, environment="production", token=None)


def test_production_strong_token_imports(monkeypatch):
    mod = _reload_auth_with(
        monkeypatch, environment="production", token="0123456789abcdef0123456789abcdef"
    )
    assert mod._TOKEN_REQUIRED is True


def test_dev_weak_token_tolerated(monkeypatch):
    """A weak token is fine in development — local convenience, never deployed."""
    mod = _reload_auth_with(monkeypatch, environment="development", token="test")
    assert mod._TOKEN_REQUIRED is False
