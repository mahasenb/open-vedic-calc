"""Tests for the proprietary-reference boundary gate (ci/check_no_proprietary_refs.py).

The gate must never carry any real downstream-brand literal in this public repo —
including in this test file. Every brand-shaped assertion here uses a SYNTHETIC
token (``zzznotarealbrand``), injected only through the ``PROPRIETARY_REF_TOKENS``
env var, exactly the mechanism the gate uses in CI. This test module intentionally
contains no brand literal of any kind.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
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


class TestCommitMessageScanExcludesLegacyWord:
    """Regression: commit-message scanning must catch brand tokens (real leaks) but
    NOT the legacy code-hygiene word, which is unavoidably common in this astrology
    repo's commit prose (e.g. a commit that documents the gate itself). File scanning
    still catches the legacy word."""

    def test_legacy_word_in_commit_message_is_allowed(self, monkeypatch):
        monkeypatch.setenv("PROPRIETARY_REF_TOKENS", _SYNTHETIC_TOKEN)
        module = _load_gate_module()
        out: list[str] = []
        module._scan_text(
            "<commit message>",
            f"ci: gate matched only the legacy token '{_LEGACY_WORD}' before this",
            out,
            module._BRAND_PATTERNS,
        )
        assert out == []

    def test_brand_token_in_commit_message_is_flagged(self, monkeypatch):
        monkeypatch.setenv("PROPRIETARY_REF_TOKENS", _SYNTHETIC_TOKEN)
        module = _load_gate_module()
        out: list[str] = []
        module._scan_text(
            "<commit message>", f"leaked {_SYNTHETIC_TOKEN} in a message", out,
            module._BRAND_PATTERNS,
        )
        assert len(out) == 1

    def test_legacy_word_in_file_is_still_flagged(self, monkeypatch):
        monkeypatch.setenv("PROPRIETARY_REF_TOKENS", _SYNTHETIC_TOKEN)
        module = _load_gate_module()
        out: list[str] = []
        module._scan_text("some_file.py", f"x = '{_LEGACY_WORD}'", out, module._FORBIDDEN)
        assert len(out) == 1


# ---------------------------------------------------------------------------
# Commit-message decoding
# ---------------------------------------------------------------------------
def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    return path


def _commit_with_raw_message(repo: Path, message: bytes) -> str:
    """Create a commit whose message bytes are EXACTLY ``message``.

    ``git commit`` cannot be used for this. Measured on Git for Windows: a
    message that is not valid UTF-8 is TRANSCODED on the way in (``0xE9`` is
    stored as ``0xC3 0xA9``, ``0x81`` as ``0xC2 0x81``) and the object ends up
    holding valid UTF-8 with no ``encoding`` header -- so the un-decodable shape
    cannot be reached through the porcelain.

    Nor can it be reached by pointing ``i18n.logOutputEncoding`` at a legacy
    codepage, which is the trigger the sibling guard in
    ``tests/test_line_endings.py`` uses: the gate pins that variable to UTF-8 in
    its own argv, and a ``-c`` override beats repository config (measured).

    What remains -- and what this builds -- is a commit object written directly,
    carrying non-UTF-8 message bytes and no ``encoding`` header for git to
    transcode from. That is the shape a history imported by ``fast-import`` or
    converted from CVS/SVN carries, and git hands those bytes back verbatim.
    """
    head_ref = subprocess.run(
        ["git", "symbolic-ref", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    parent = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=False,
    ).stdout.strip()

    marker = repo / "file.txt"
    marker.write_bytes(b"content " + str(len(message)).encode("ascii") + b"\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    tree = subprocess.run(
        ["git", "write-tree"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    header = f"tree {tree}\n"
    if parent:
        header += f"parent {parent}\n"
    header += (
        "author Test <test@example.com> 1700000000 +0000\n"
        "committer Test <test@example.com> 1700000000 +0000\n\n"
    )
    sha = subprocess.run(
        ["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=repo,
        input=header.encode("ascii") + message, capture_output=True, check=True,
    ).stdout.decode("ascii").strip()
    subprocess.run(["git", "update-ref", head_ref, sha], cwd=repo, check=True)
    return sha


# A brand token sitting beside bytes that are neither valid UTF-8 nor decodable
# by the Windows locale codepage. 0xE9 is latin-1 'e-acute'; 0x81 is undefined
# in cp1252.
_UNDECODABLE_MESSAGE = (
    b"chore: leaked " + _SYNTHETIC_TOKEN.encode("ascii") + b" caf\xe9brand \x81 tail\n"
)


class TestCommitMessageDecodingIsPinned:
    """``subprocess.run(..., text=True)`` decodes with the LOCALE codepage, which
    is cp1252 on a Windows checkout. Measured on the base commit, both of the
    gate's git invocations failed -- differently, and both wrongly:

    - the RANGE branch (what CI passes) died with
      ``AttributeError: 'NoneType' object has no attribute 'strip'``: the decode
      raised on a subprocess reader thread, the thread died, and ``stdout`` came
      back ``None`` -- a crash, not a verdict.
    - the FALLBACK branch (what the locally mandated ``python
      ci/check_no_proprietary_refs.py`` uses) returned ``[]`` from that same
      ``None``, so the gate scanned ZERO commit messages and printed
      ``OK: no proprietary consumer references found.`` with the synthetic token
      sitting in the message it had just failed to read. A silently disarmed
      boundary gate is strictly worse than a crashing one.

    Both are closed here, at both halves of the pipe: the producer is pinned
    (``git -c i18n.logOutputEncoding=UTF-8``) and the consumer decodes the raw
    bytes as UTF-8 STRICTLY, refusing loudly when that fails.
    """

    def test_the_fallback_branch_refuses_instead_of_returning_nothing(
        self, gate_with_synthetic_token, tmp_path
    ):
        module = gate_with_synthetic_token
        repo = _init_repo(tmp_path / "fallback")
        _commit_with_raw_message(repo, _UNDECODABLE_MESSAGE)

        with pytest.raises(module.CommitMessageDecodeError):
            module._get_commit_messages(None, cwd=repo)

    def test_the_range_branch_refuses_instead_of_crashing(
        self, gate_with_synthetic_token, tmp_path
    ):
        module = gate_with_synthetic_token
        repo = _init_repo(tmp_path / "range")
        _commit_with_raw_message(repo, b"chore: base commit\n")
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        _commit_with_raw_message(repo, _UNDECODABLE_MESSAGE)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        with pytest.raises(module.CommitMessageDecodeError):
            module._get_commit_messages(f"{base}..{head}", cwd=repo)

    def test_main_refuses_loudly_rather_than_reporting_clean(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """End to end: the exit code is the whole product of this gate."""
        module = gate_with_synthetic_token
        repo = _init_repo(tmp_path / "endtoend")
        _commit_with_raw_message(repo, _UNDECODABLE_MESSAGE)

        real = module._get_commit_messages
        monkeypatch.setattr(module, "_REPO_ROOT", repo)
        monkeypatch.setattr(
            module, "_get_commit_messages",
            lambda commit_range=None: real(commit_range, cwd=repo),
        )

        exit_code = module.main()
        captured = capsys.readouterr()

        assert exit_code == 1, (
            "an unreadable commit message must fail the gate, never pass it"
        )
        assert "OK:" not in captured.out, (
            "the gate reported a clean result on a message it could not read"
        )
        assert "utf-8" in captured.err.lower(), (
            "the refusal must say what went wrong, not fail mutely"
        )

    def test_the_refusal_is_not_swallowed_by_the_oserror_handler(
        self, gate_with_synthetic_token
    ):
        """``main`` wraps the commit scan in ``except OSError`` so a git that is
        absent degrades quietly. The decode refusal must NOT be absorbed by that
        arm -- if it were, the fix would restore exactly the silent pass it
        replaces."""
        module = gate_with_synthetic_token
        assert not issubclass(module.CommitMessageDecodeError, OSError), (
            "CommitMessageDecodeError must not be an OSError subclass, or main's "
            "`except OSError: pass` would swallow the refusal"
        )

    def test_the_log_output_encoding_is_pinned_at_the_producer(
        self, gate_with_synthetic_token, monkeypatch, tmp_path
    ):
        """Decoding as UTF-8 is only correct if git is EMITTING UTF-8.

        Measured: a contributor carrying ``i18n.logOutputEncoding=ISO-8859-1``
        in their git config makes ``git log --format=%B`` emit latin-1 bytes,
        which are not valid UTF-8 -- so a consumer-side pin alone would refuse
        on every run for that contributor. ``git -c i18n.logOutputEncoding=UTF-8``
        overrides repository config (measured) and closes that class at source.
        """
        module = gate_with_synthetic_token
        calls: list[tuple[list[str], dict]] = []

        def fake_run(argv, **kwargs):
            calls.append((list(argv), kwargs))
            return subprocess.CompletedProcess(argv, 0, b"chore: clean\n", b"")

        monkeypatch.setattr(module.subprocess, "run", fake_run)
        module._get_commit_messages("aaaaaaa..bbbbbbb", cwd=tmp_path)
        module._get_commit_messages(None, cwd=tmp_path)

        assert len(calls) >= 2, (
            f"expected both the range and fallback branches to invoke git; "
            f"recorded {len(calls)}"
        )
        for argv, kwargs in calls:
            assert argv[0] == "git"
            assert "i18n.logOutputEncoding=UTF-8" in argv, (
                f"git log invocation does not pin its output encoding: {argv}"
            )
            assert kwargs.get("text") is not True, (
                f"git invocation decodes at the locale's discretion: {argv}"
            )
            assert "encoding" not in kwargs, (
                "the gate decodes the raw bytes itself, so that a decode failure "
                "is catchable in the caller rather than swallowed on a subprocess "
                f"reader thread: {argv}"
            )

    def test_a_valid_utf8_message_decodes_as_utf8_not_the_locale_codepage(
        self, gate_without_env_token, tmp_path
    ):
        """Platform-conditional by nature: this is GREEN on a UTF-8 locale even
        unfixed, because there ``text=True`` already means UTF-8. On the cp1252
        checkout it was measured red -- an em dash came back as the three
        characters ``\\xe2\\u20ac\\u201d``."""
        module = gate_without_env_token
        repo = _init_repo(tmp_path / "validutf8")
        _commit_with_raw_message(
            repo, "chore: an em dash — and a degree sign 12°\n".encode("utf-8")
        )

        messages = module._get_commit_messages(None, cwd=repo)
        assert any("—" in m and "°" in m for m in messages), (
            f"commit message was not decoded as UTF-8: {[ascii(m) for m in messages]}"
        )

    def test_a_non_ascii_brand_token_survives_the_decode(self, monkeypatch, tmp_path):
        """The masking property, stated as a test.

        A replacement character cannot hide an ASCII token -- UTF-8 is
        self-synchronising, so ASCII bytes always decode to themselves whatever
        surrounds them (measured: ``zzznotarealbrand`` survives intact between
        two U+FFFD, and ``\\bastro\\b`` still matches beside one because U+FFFD
        is not a word character). A NON-ASCII token is a different story: it is
        exactly the thing a lossy or locale decode destroys, and this asserts it
        is still caught. Platform-conditional in the same way as the test above.
        """
        token = "cafébrand"
        monkeypatch.setenv("PROPRIETARY_REF_TOKENS", token)
        module = _load_gate_module()
        try:
            repo = _init_repo(tmp_path / "nonasciitoken")
            _commit_with_raw_message(
                repo, f"chore: leaked {token} here\n".encode("utf-8")
            )
            out: list[str] = []
            for msg in module._get_commit_messages(None, cwd=repo):
                module._scan_text("<commit message>", msg, out, module._BRAND_PATTERNS)
            assert out, (
                "a brand token spelled with a non-ASCII character was not caught -- "
                "this is precisely what a lossy or locale-dependent decode hides"
            )
        finally:
            sys.modules.pop("check_no_proprietary_refs", None)

    def test_a_clean_history_still_passes(self, gate_with_synthetic_token, tmp_path):
        """The refusal must not become the answer to everything: an ordinary
        ASCII history still yields its messages."""
        module = gate_with_synthetic_token
        repo = _init_repo(tmp_path / "clean")
        _commit_with_raw_message(repo, b"chore: a perfectly ordinary message\n")

        messages = module._get_commit_messages(None, cwd=repo)
        assert any("perfectly ordinary" in m for m in messages)


def _subprocess_run_calls(tree: ast.AST) -> list[ast.Call]:
    """Every ``subprocess.run(...)`` call in the parsed module.

    AST, never grep: a comment or a string literal mentioning the call must not
    be able to satisfy -- or defeat -- this guard.
    """
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "run":
            if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                found.append(node)
    return found


class TestSubprocessDecodingIsPinnedStructurally:
    """A behavioural test proves the fix works today; this proves it cannot be
    quietly undone. Reverting either call site to a bare ``text=True`` reddens
    this without needing a non-ASCII fixture to reach it."""

    def test_no_git_call_in_the_gate_decodes_at_the_locale_s_discretion(self):
        tree = ast.parse(_GATE_PATH.read_text(encoding="utf-8"))
        calls = _subprocess_run_calls(tree)

        # Floor: "every call is pinned" is vacuously true of no calls at all.
        assert len(calls) >= 1, (
            "found no subprocess.run call in the gate -- the scan is broken, not "
            "the gate clean"
        )

        offenders = []
        for call in calls:
            keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
            text_mode = any(
                isinstance(keywords.get(name), ast.Constant)
                and keywords[name].value is True
                for name in ("text", "universal_newlines")
            )
            if text_mode and "encoding" not in keywords:
                offenders.append(call.lineno)

        assert not offenders, (
            f"subprocess.run call(s) at line(s) {offenders} enable text mode "
            f"without an explicit encoding, so they decode with the locale "
            f"codepage (cp1252 on Windows). Measured on the base commit: that "
            f"returns stdout=None on a non-ASCII commit message, which this gate "
            f"turned into a clean verdict."
        )

    def test_git_access_is_funnelled_through_the_pinned_helper(self):
        """One place to pin, so a second git call cannot be added unpinned."""
        tree = ast.parse(_GATE_PATH.read_text(encoding="utf-8"))
        helper = next(
            (
                node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "_run_git_log"
            ),
            None,
        )
        assert helper is not None, "the pinned git helper _run_git_log is gone"

        inside = {id(call) for call in _subprocess_run_calls(helper)}
        assert inside, "_run_git_log no longer runs git"

        stray = [
            call.lineno
            for call in _subprocess_run_calls(tree)
            if id(call) not in inside
            and "encoding" not in {kw.arg for kw in call.keywords}
        ]
        assert not stray, (
            f"subprocess.run call(s) at line(s) {stray} bypass _run_git_log without "
            f"naming an explicit encoding -- route git access through the helper so "
            f"the output-encoding pin and the strict decode apply to it too"
        )
