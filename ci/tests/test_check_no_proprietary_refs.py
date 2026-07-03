"""Tests for the proprietary-reference boundary gate (ci/check_no_proprietary_refs.py).

The gate must never carry any real downstream-brand literal in this public repo —
including in this test file. Every brand-shaped assertion here uses a SYNTHETIC
token (``zzznotarealbrand``), injected only through the ``PROPRIETARY_REF_TOKENS``
env var, exactly the mechanism the gate uses in CI. This test module intentionally
contains no brand literal of any kind.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_GATE_PATH = Path(__file__).resolve().parent.parent / "check_no_proprietary_refs.py"

# A synthetic, obviously-fake token. Never a real brand — see module docstring.
_SYNTHETIC_TOKEN = "zzznotarealbrand"

# The legacy base pattern's target word, built from parts so this test file
# itself never contains the literal token as a static, grep-able string — the
# gate under test would otherwise (correctly) flag its own regression fixture.
_LEGACY_WORD = "".join(["a", "s", "t", "r", "o"])


def _load_gate_module():
    """Import ci/check_no_proprietary_refs.py fresh as a module (it's a standalone
    script, not a package member), so each test can control its env before import-time
    state (like a compiled pattern list) is built."""
    spec = importlib.util.spec_from_file_location("check_no_proprietary_refs", _GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gate_with_synthetic_token(monkeypatch):
    monkeypatch.setenv("PROPRIETARY_REF_TOKENS", _SYNTHETIC_TOKEN)
    module = _load_gate_module()
    yield module
    sys.modules.pop("check_no_proprietary_refs", None)


@pytest.fixture
def gate_without_env_token(monkeypatch):
    monkeypatch.delenv("PROPRIETARY_REF_TOKENS", raising=False)
    module = _load_gate_module()
    yield module
    sys.modules.pop("check_no_proprietary_refs", None)


class TestForbiddenPatternsIsAList:
    def test_forbidden_is_a_list_of_compiled_patterns(self, gate_without_env_token):
        """PR-7 requires _FORBIDDEN to be refactored from a single regex into a
        list of compiled patterns, so additional brand tokens can be appended."""
        module = gate_without_env_token
        assert isinstance(module._FORBIDDEN, list)
        assert len(module._FORBIDDEN) >= 1
        import re

        for pattern in module._FORBIDDEN:
            assert isinstance(pattern, re.Pattern)


class TestLegacyOnlyFallback:
    """When PROPRIETARY_REF_TOKENS is unset, the gate must fall back to legacy-only
    behaviour (the base pattern built around _LEGACY_WORD) and must NOT fail just
    because the env var is missing — fail-closed applies to leaks, not to absent
    optional config."""

    def test_unset_env_does_not_add_extra_patterns(self, gate_without_env_token):
        module = gate_without_env_token
        assert len(module._FORBIDDEN) == 1

    def test_legacy_token_still_flagged(self, gate_without_env_token):
        module = gate_without_env_token
        out: list[str] = []
        module._scan_text("some/file.py", f"this line mentions {_LEGACY_WORD} directly", out)
        assert out, "legacy bare token must still be flagged"

    def test_synthetic_brand_not_flagged_when_env_unset(self, gate_without_env_token):
        """Without the env var, the gate has no knowledge of the extra token —
        proves the extra pattern is genuinely opt-in via env, not hardcoded."""
        module = gate_without_env_token
        out: list[str] = []
        module._scan_text(
            "some/file.py", f"this line mentions {_SYNTHETIC_TOKEN} only", out
        )
        assert out == []


class TestEnvSuppliedTokenIsFlagged:
    def test_synthetic_token_flagged_when_env_set(self, gate_with_synthetic_token):
        module = gate_with_synthetic_token
        out: list[str] = []
        module._scan_text(
            "some/file.py", f"leaked reference: {_SYNTHETIC_TOKEN}", out
        )
        assert out, "env-supplied brand token must be flagged when present"

    def test_env_token_adds_a_pattern_without_removing_legacy(
        self, gate_with_synthetic_token
    ):
        module = gate_with_synthetic_token
        assert len(module._FORBIDDEN) == 2

        out: list[str] = []
        module._scan_text("some/file.py", f"this line mentions {_LEGACY_WORD} directly", out)
        assert out, "legacy pattern must still be active alongside env token"

    def test_env_token_is_case_insensitive(self, gate_with_synthetic_token):
        module = gate_with_synthetic_token
        out: list[str] = []
        module._scan_text(
            "some/file.py", _SYNTHETIC_TOKEN.upper(), out
        )
        assert out, "env-supplied token match should be case-insensitive, like legacy"

    def test_env_supports_multiple_comma_separated_tokens(self, monkeypatch):
        monkeypatch.setenv(
            "PROPRIETARY_REF_TOKENS", f"{_SYNTHETIC_TOKEN},anothersynthetictoken"
        )
        module = _load_gate_module()
        try:
            assert len(module._FORBIDDEN) == 3  # legacy + 2 env tokens

            out: list[str] = []
            module._scan_text("f.py", "anothersynthetictoken appears here", out)
            assert out
        finally:
            sys.modules.pop("check_no_proprietary_refs", None)


class TestAstronomyWordsStillPass:
    """Regression guard: astro.com (Swiss Ephemeris site) and the astronomy/
    astronomical domain words must never be flagged, with or without the extra
    env-supplied token active."""

    @pytest.mark.parametrize(
        "safe_line",
        [
            "see https://www.astro.com for ephemeris data",
            "this module performs astronomy calculations",
            "an astronomical observation of planetary longitude",
        ],
    )
    def test_astronomy_words_pass_legacy_only(self, gate_without_env_token, safe_line):
        module = gate_without_env_token
        out: list[str] = []
        module._scan_text("some/file.py", safe_line, out)
        assert out == []

    @pytest.mark.parametrize(
        "safe_line",
        [
            "see https://www.astro.com for ephemeris data",
            "this module performs astronomy calculations",
            "an astronomical observation of planetary longitude",
        ],
    )
    def test_astronomy_words_pass_with_synthetic_token_active(
        self, gate_with_synthetic_token, safe_line
    ):
        module = gate_with_synthetic_token
        out: list[str] = []
        module._scan_text("some/file.py", safe_line, out)
        assert out == []


class TestCommitRangeScanning:
    """PR-7 widens the commit-message scan from `git log -1` to a pushed range,
    supplied by the caller (the CI workflow), not hardcoded to HEAD only."""

    def test_get_commit_messages_accepts_a_range(self, gate_without_env_token, tmp_path):
        module = gate_without_env_token
        assert hasattr(module, "_get_commit_messages")

        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "a.txt").write_text("one")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "first commit"], cwd=repo, check=True
        )
        first_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        (repo / "a.txt").write_text("two")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", f"second commit mentions {_SYNTHETIC_TOKEN}"],
            cwd=repo,
            check=True,
        )
        second_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        # Range scan must see the second (non-HEAD-only) commit's message.
        messages = module._get_commit_messages(f"{first_sha}..{second_sha}", cwd=repo)
        assert any(_SYNTHETIC_TOKEN in m for m in messages)

    def test_get_commit_messages_falls_back_when_range_invalid(
        self, gate_without_env_token, tmp_path
    ):
        """A non-existent 'before' SHA (first push on a branch) must not crash the
        gate — fall back to scanning just HEAD."""
        module = gate_without_env_token

        import subprocess

        repo = tmp_path / "repo2"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "a.txt").write_text("one")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "only commit"], cwd=repo, check=True)

        zero_sha = "0" * 40  # git's "before" sentinel for first-push events
        messages = module._get_commit_messages(f"{zero_sha}..HEAD", cwd=repo)
        assert any("only commit" in m for m in messages)

    def test_get_commit_messages_falls_back_on_empty_before(
        self, gate_without_env_token, tmp_path
    ):
        """github.event.before is unset (renders as an empty string) on non-push
        CI events like pull_request, producing a malformed '..HEAD' range. That
        must not crash the gate — fall back to scanning just HEAD, exactly like
        the null-SHA first-push case."""
        module = gate_without_env_token

        import subprocess

        repo = tmp_path / "repo3"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "a.txt").write_text("one")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "pull request head commit"], cwd=repo, check=True
        )

        messages = module._get_commit_messages("..HEAD", cwd=repo)
        assert any("pull request head commit" in m for m in messages)


class TestMainUsesCommitRangeArgument:
    def test_main_accepts_optional_commit_range_argv(
        self, gate_without_env_token, monkeypatch, tmp_path, capsys
    ):
        """main() must accept a commit-range positional argument (from sys.argv,
        as passed by the CI workflow) rather than being hardcoded to `git log -1`."""
        module = gate_without_env_token
        assert "commit_range" in module.main.__code__.co_varnames or (
            module.main.__defaults__ is not None
        )
