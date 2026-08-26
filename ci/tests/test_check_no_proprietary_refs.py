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
import re
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


def _gate_constant(name: str) -> str:
    """A string constant read out of the gate's SOURCE, never re-typed here.

    Parsed rather than imported: this runs at collection time, and the gate
    builds its pattern list from the environment at import. A hand-copied
    duplicate of the pin would be one more thing that can silently disagree with
    the code it claims to describe.
    """
    tree = ast.parse(_GATE_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            assert isinstance(node.value, ast.Constant), f"{name} is not a literal"
            return node.value.value
    raise AssertionError(f"{name} is not defined at module scope of {_GATE_PATH.name}")


# The output-encoding pin, taken from the gate itself.
_PIN = _gate_constant("_LOG_OUTPUT_ENCODING_PIN")


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
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=True,
        ).stdout.strip()

        (repo / "a.txt").write_text("two")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", f"second commit mentions {_SYNTHETIC_TOKEN}"],
            cwd=repo,
            check=True,
        )
        second_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=True,
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
        capture_output=True, text=True, check=True, encoding="utf-8", errors="replace",
    ).stdout.strip()
    parent = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=repo,
        capture_output=True, text=True, check=False, encoding="utf-8", errors="replace",
    ).stdout.strip()

    marker = repo / "file.txt"
    marker.write_bytes(b"content " + str(len(message)).encode("ascii") + b"\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    tree = subprocess.run(
        ["git", "write-tree"], cwd=repo, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=True,
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
            capture_output=True, text=True, check=True, encoding="utf-8", errors="replace",
        ).stdout.strip()
        _commit_with_raw_message(repo, _UNDECODABLE_MESSAGE)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=True, encoding="utf-8", errors="replace",
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

    # The four keywords CPython ORs together to decide text mode, in
    # `subprocess.Popen.__init__`: `encoding or errors or text or
    # universal_newlines`. Naming all four, not just `text`, because `encoding=`
    # and `errors=` select text mode implicitly -- a call can be locale-free and
    # still put its decode on the reader thread.
    _TEXT_MODE_KEYWORDS = frozenset({"encoding", "errors", "text", "universal_newlines"})

    # ------------------------------------------------------------------
    # TWO ORTHOGONAL PROPERTIES, ENFORCED SEPARATELY. Neither implies the other,
    # and an earlier revision of this class asserted only the first while
    # claiming to be "stricter" than the rule it replaced. Measured: adding a
    # second commit-message read outside the pinned helper, in bytes mode --
    #     subprocess.run(["git", "log", "--format=%B", "-1"],
    #                    capture_output=True, check=False, cwd=cwd)
    # -- was rc 1 RED at 71cbf45 and rc 0 GREEN under that revision. The two
    # exemptions admit DISJOINT sets, so replacing one with the other traded a
    # control away rather than tightening it.
    #
    #   NEAR end of the pipe -- where the bytes are DECODED. Bytes mode, so the
    #   decode happens in this process and its failure is catchable, instead of
    #   on a reader thread that dies and hands back stdout=None, returncode=0.
    #
    #   FAR end of the pipe -- what git EMITS. `-c i18n.logOutputEncoding=UTF-8`,
    #   because a contributor carrying `i18n.logOutputEncoding=ISO-8859-1` makes
    #   `git log` emit latin-1 (measured; CLAUDE.md says the read is UTF-8 at
    #   BOTH ends). Bytes mode says nothing about this: the caller faithfully
    #   decodes latin-1 bytes as UTF-8 and refuses, or worse, mis-scans.
    #
    # Arm A below enforces the near end on EVERY call, helper included. Arms B
    # and C enforce the far end: B funnels anything that could carry a commit
    # message through the helper, C proves the helper still carries the pin.
    # ------------------------------------------------------------------

    # Git subcommands a stray call may invoke, each mapped to the reason it needs
    # no output-encoding pin. An ALLOWLIST, not a denylist: a subcommand nobody
    # has reasoned about is refused, so the next git call added to this gate has
    # to justify itself rather than inherit an exemption by looking harmless.
    # (`git log`, `show`, `whatchanged`, `rev-list --format` and friends all
    # render commit messages; enumerating the dangerous ones correctly is the bet
    # an allowlist does not have to make.)
    _PIN_FREE_SUBCOMMANDS = {
        "ls-files": (
            "emits index PATHS, which carry no commit-message encoding header "
            "and which i18n.logOutputEncoding does not touch. -z makes the "
            "records exact and the caller decodes them in-process."
        ),
    }

    # The one function permitted to make a stray call. Without this a THIRD git
    # call could inherit `_git_scan_paths`'s exemption simply by sitting beside
    # it, and the previous revision's floor -- "at least one stray exists" --
    # was satisfied by any number of unpinned calls.
    _PIN_FREE_CALLERS = frozenset({"_git_scan_paths"})

    # KNOWN BOUNDS, stated rather than claimed away, per this repo's convention
    # (`ci/tests/test_subprocess_decoding.py`'s KNOWN BOUNDS block and
    # `ci/check_pytest_collection.py`'s `Known bound:` note):
    #   * argv must be a LITERAL list. A call assembling its argv elsewhere is
    #     refused, not analysed -- this guard cannot read what it cannot see.
    #   * scope is `subprocess.run` in this one file. A git call made through
    #     another module, `Popen`, or `os.system` is outside it; the sibling
    #     `ci/tests/test_subprocess_decoding.py` polices the tracked tree for the
    #     decoding half.
    #   * it reads the call SITE. It cannot prove what `git` does at run time.

    @staticmethod
    def _literal_argv(call: ast.Call) -> list[str | None] | None:
        """The call's argv as tokens, with ``None`` for any element it cannot read.

        A module-level string constant is RESOLVED rather than refused -- the
        helper spells its pin as ``_LOG_OUTPUT_ENCODING_PIN``, which is the
        single-source-of-truth shape this repo wants, and a reader that only
        understood inline literals would push the pin back into a duplicated
        string. A ``*args`` splat becomes ``None``: unreadable, not fatal, since
        what matters is whether anything unreadable sits BEFORE the subcommand.

        Returns ``None`` only when the first argument is not a list at all.
        """
        if not call.args or not isinstance(call.args[0], ast.List):
            return None
        tokens: list[str | None] = []
        for element in call.args[0].elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                tokens.append(element.value)
            elif isinstance(element, ast.Name):
                try:
                    tokens.append(_gate_constant(element.id))
                except AssertionError:
                    tokens.append(None)
            else:  # a Starred splat, an f-string, a call -- unreadable here
                tokens.append(None)
        return tokens

    @classmethod
    def _git_subcommand(cls, call: ast.Call) -> str | None:
        """The subcommand a git argv invokes, skipping ``-c k=v`` pairs.

        ``None`` means "this guard cannot tell", which its caller treats as a
        refusal rather than as permission.
        """
        argv = cls._literal_argv(call)
        if not argv or argv[0] != "git":
            return None
        index = 1
        while index < len(argv):
            token = argv[index]
            if token is None:
                return None  # unreadable before the subcommand: refuse
            if not token.startswith("-"):
                return token
            index += 2 if token == "-c" else 1
        return None

    @staticmethod
    def _enclosing_functions(tree: ast.AST) -> dict[int, str]:
        owner: dict[int, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in _subprocess_run_calls(node):
                owner.setdefault(id(call), node.name)
        return owner

    # --- Arm A: the NEAR end, on every call including the helper's own --------
    def test_every_subprocess_call_in_the_gate_runs_in_bytes_mode(self):
        """No call here may decode where the failure is invisible. No exceptions.

        Universal on purpose: the helper is not exempt from the property it
        exists to provide, and an explicit ``encoding=`` does not earn an
        exemption either -- the sibling guard measures that an explicit encoding
        does not remove the hazard, it only changes which codec dies on the
        reader thread.
        """
        tree = ast.parse(_GATE_PATH.read_text(encoding="utf-8"))
        calls = _subprocess_run_calls(tree)
        assert len(calls) >= 2, (
            f"only {len(calls)} subprocess.run call(s) found -- this gate makes "
            "two (the commit-message read and the file listing), so the scan is "
            "broken or a call site has gone"
        )
        offenders = [
            f"line {call.lineno}: {sorted({kw.arg for kw in call.keywords} & self._TEXT_MODE_KEYWORDS)}"
            for call in calls
            if {kw.arg for kw in call.keywords} & self._TEXT_MODE_KEYWORDS
        ]
        assert not offenders, (
            "subprocess.run call(s) running in TEXT mode:\n  "
            + "\n  ".join(offenders)
            + f"\nAny of {sorted(self._TEXT_MODE_KEYWORDS)} selects text mode, "
            "which moves the decode onto a reader thread where a "
            "UnicodeDecodeError kills the thread and the caller is handed "
            "stdout=None with returncode=0. Read bytes and decode in-process."
        )

    # --- Arm B: the FAR end, funnelled ---------------------------------------
    def test_only_a_registered_pin_free_git_call_may_bypass_the_pinned_helper(self):
        """Anything that could carry a COMMIT MESSAGE goes through the helper.

        Bytes mode does not make a stray ``git log`` safe: it fixes the decode,
        not what git emitted. This arm is what the previous revision gave up,
        and it is restored as an allowlist so the exemption cannot spread by
        proximity -- both the subcommand and the calling function must be
        registered.
        """
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

        owner = self._enclosing_functions(tree)
        strays = [c for c in _subprocess_run_calls(tree) if id(c) not in inside]

        # Floor, and it names WHICH call rather than merely counting: "at least
        # one stray exists" was satisfiable by any number of unpinned calls.
        stray_owners = {owner.get(id(call), "<module scope>") for call in strays}
        assert stray_owners == set(self._PIN_FREE_CALLERS), (
            f"the set of functions making an unpinned git call is "
            f"{sorted(stray_owners)}, registered is "
            f"{sorted(self._PIN_FREE_CALLERS)}. Every entry must be deliberate: "
            f"a new one needs a reason recorded in _PIN_FREE_SUBCOMMANDS, and a "
            f"missing one means the file listing no longer asks git."
        )

        refused = []
        for call in strays:
            where = f"line {call.lineno} in {owner.get(id(call), '<module scope>')}()"
            subcommand = self._git_subcommand(call)
            if subcommand is None:
                refused.append(
                    f"{where}: argv is not a literal git command list, so this "
                    f"guard cannot read which subcommand runs. Spell it out."
                )
            elif subcommand not in self._PIN_FREE_SUBCOMMANDS:
                refused.append(
                    f"{where}: `git {subcommand}` bypasses _run_git_log, so it "
                    f"runs WITHOUT `-c {_PIN!s}`. Bytes mode does not help here "
                    f"-- it fixes how the bytes are decoded, not which encoding "
                    f"git emitted them in. Registered pin-free subcommands: "
                    f"{sorted(self._PIN_FREE_SUBCOMMANDS)}."
                )
        assert not refused, "Unpinned git access:\n  " + "\n  ".join(refused)

    # --- Arm C: the pin the funnel funnels TOWARDS ---------------------------
    def test_the_pinned_helper_actually_carries_the_output_encoding_pin(self):
        """Arms A and B are worth nothing if the helper stopped pinning.

        Structural, and complementary to
        ``test_the_log_output_encoding_is_pinned_at_the_producer``, which drives
        ``_get_commit_messages`` under a monkeypatched ``subprocess.run`` and so
        only ever observes calls made THROUGH this helper.
        """
        tree = ast.parse(_GATE_PATH.read_text(encoding="utf-8"))
        helper = next(
            (
                node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "_run_git_log"
            ),
            None,
        )
        assert helper is not None, "the pinned git helper _run_git_log is gone"
        calls = _subprocess_run_calls(helper)
        assert calls, "_run_git_log no longer runs git"
        for call in calls:
            argv = self._literal_argv(call)
            assert argv is not None, (
                f"line {call.lineno}: the helper's argv is not a literal list, "
                "so the pin cannot be read at the call site"
            )
            assert "-c" in argv and _PIN in argv, (
                f"line {call.lineno}: the helper's argv {argv} does not carry "
                f"`-c {_PIN}`. Without it git emits whatever the contributor's "
                f"own i18n.logOutputEncoding says -- measured, ISO-8859-1 config "
                f"yields latin-1 bytes -- and the strict UTF-8 decode downstream "
                f"then refuses (or a non-ASCII token silently stops matching)."
            )
            assert argv.index("-c") < argv.index("log"), (
                f"line {call.lineno}: `-c` must precede the `log` subcommand to "
                f"be a git option rather than an argument to it: {argv}"
            )


# A synthetic brand token spelled with a non-ASCII character. Obviously fake, as
# the standing rule requires -- but non-ASCII, which is the property that makes
# the masking below possible at all.
_NON_ASCII_SYNTHETIC_TOKEN = "zzzcafébrand"


class TestFileScanDecoding:
    """The FILE scan decodes strictly and refuses, for the same reason the
    commit-message scan does.

    PR #75 pinned the commit-message read and deliberately left this one alone
    rather than widen its scope, recording it upward as an open question: the
    file scan's encoding was already pinned (``encoding="utf-8"``), so it was
    never locale-dependent -- but it carried ``errors="replace"``, and the
    masking argument that decided the commit-message policy applies here
    unchanged. A replacement character cannot hide an ASCII token, because UTF-8
    is self-synchronising and U+FFFD is not a word character; it destroys a
    token spelled with a non-ASCII character, which is exactly what the brand
    tokens arriving from a secret this repo cannot read might be.
    """

    def _tree_with(self, root, name: str, payload: bytes):
        root.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(payload)
        return root

    def test_the_masking_this_closes_is_real_not_hypothetical(self):
        """Measure the hole before asserting it is shut.

        Without this, the refusal below could be defending against nothing.
        """
        raw = f"leak: {_NON_ASCII_SYNTHETIC_TOKEN}".encode("latin-1")
        pattern = re.compile(re.escape(_NON_ASCII_SYNTHETIC_TOKEN), re.IGNORECASE)

        # The old policy: lossy, and the token does not survive it.
        lossy = raw.decode("utf-8", "replace")
        assert not pattern.search(lossy), (
            "the premise of this whole class is wrong: the non-ASCII token "
            f"survived a lossy decode as {lossy!r}"
        )
        # The bytes really are undecodable, so strict really does refuse.
        with pytest.raises(UnicodeDecodeError):
            raw.decode("utf-8")
        # And an ASCII token in the same bytes WOULD have survived -- which is
        # why this is about non-ASCII tokens specifically.
        assert "leak:" in lossy

    def test_a_file_that_is_not_utf8_is_refused_rather_than_scanned_lossily(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """An unreadable file is possible concealment, never absence."""
        module = gate_with_synthetic_token
        root = self._tree_with(
            tmp_path / "filescan",
            "notes.md",
            f"leak: {_NON_ASCII_SYNTHETIC_TOKEN}".encode("latin-1"),
        )
        monkeypatch.setattr(module, "_REPO_ROOT", root)
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])

        exit_code = module.main()
        captured = capsys.readouterr()

        assert exit_code == 1, (
            "a file that could not be decoded was scanned lossily and the gate "
            "reported success -- this is the silent pass the commit-message "
            "half already refuses"
        )
        assert "OK:" not in captured.out
        assert "notes.md" in captured.err, (
            f"the refusal must name the file it could not read: {captured.err}"
        )
        assert "utf-8" in captured.err.lower()

    def test_a_readable_tree_still_passes(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """The don't-over-refuse control: strictness must not redden clean trees."""
        module = gate_with_synthetic_token
        root = self._tree_with(
            tmp_path / "clean", "notes.md", "nothing to see here\n".encode("utf-8")
        )
        monkeypatch.setattr(module, "_REPO_ROOT", root)
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])

        assert module.main() == 0
        assert "OK:" in capsys.readouterr().out

    def test_one_unreadable_file_does_not_abort_the_rest_of_the_scan(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """A refusal on one file must not stop the others being scanned.

        Otherwise a single undecodable file becomes a way to hide a leak sitting
        in a perfectly readable one later in the walk.
        """
        module = gate_with_synthetic_token
        root = tmp_path / "mixed"
        self._tree_with(root, "aaa_unreadable.md", b"\xff\xfe\x80 bad bytes\n")
        (root / "zzz_readable.md").write_bytes(
            f"leak: {_SYNTHETIC_TOKEN}".encode("utf-8")
        )
        monkeypatch.setattr(module, "_REPO_ROOT", root)
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])

        exit_code = module.main()
        captured = capsys.readouterr()

        assert exit_code == 1
        assert "aaa_unreadable.md" in captured.err, "the refusal was not reported"
        assert "zzz_readable.md" in captured.err, (
            "the scan stopped at the unreadable file and never reached the "
            f"readable one holding the token: {captured.err}"
        )

    def test_the_file_refusal_is_not_swallowed_by_the_oserror_handler(
        self, gate_with_synthetic_token
    ):
        """``main``'s file loop absorbs OSError so an unreadable file is skipped.

        A decode refusal must not ride that arm, for the same reason its
        commit-message sibling must not: absorbing it restores the silent pass.
        """
        module = gate_with_synthetic_token
        assert hasattr(module, "FileDecodeError")
        assert not issubclass(module.FileDecodeError, OSError), (
            "FileDecodeError must not be an OSError subclass, or main's file "
            "loop would swallow the refusal and continue"
        )


# ---------------------------------------------------------------------------
# File-scan SCOPE
# ---------------------------------------------------------------------------
def _repo_with(tmp_path: Path, name: str, files: dict[str, str], commit: bool = True):
    """A throwaway git repo holding *files*, all but the ignored ones committed."""
    root = _init_repo(tmp_path / name)
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    if commit:
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "fixture"], cwd=root, check=True
        )
    return root


class TestFileScanScopeComesFromGit:
    """The scan set is asked of git, not assembled by walking the filesystem.

    A walk answers "what is on this disk", which is not the question. It is
    wrong in BOTH directions at once, and both were measured on this repository
    rather than reasoned about:

    * **Fail open.** ``_SKIP_DIRS`` drops a directory by NAME, so a tracked file
      under any of those names ships unscanned. Nothing tracked sits under one
      today, which is precisely why the hole is invisible: it opens on the
      commit that adds the first such file, and the gate reports ``OK:``.
    * **Scope inflation.** Measured 2026-08-26 in the shared root checkout of
      this repository: the walk enumerated **221** scannable files against a
      tracked-plus-untracked set of **106** -- **115** extra, **111** of them
      another branch's working tree under ``.claude/worktrees/``. So the local
      gate's verdict depended on which unrelated branches happened to be checked
      out beside it, and a leak in someone else's worktree reddened this one.

    ``git ls-files --cached --others --exclude-standard`` answers the real
    question: everything that ships, plus everything that is one ``git add``
    away from shipping, minus everything git is told to ignore. ``-z`` because a
    path holding non-ASCII bytes is otherwise QUOTED and octal-escaped -- the
    fail-open ``ci/check_pytest_collection.py`` was measured committing.
    """

    def test_a_tracked_file_under_a_skipped_directory_name_is_scanned(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """The fail-OPEN direction: ``_SKIP_DIRS`` matched by name, not by status.

        RED before the switch: the walk skips every path component named
        ``data``, so this tracked, shipping file was never read and the gate
        printed ``OK: no proprietary consumer references found.`` over a leak.
        """
        module = gate_with_synthetic_token
        root = _repo_with(
            tmp_path,
            "skipdir",
            {
                "README.md": "nothing here\n",
                "data/notes.md": f"leak: {_SYNTHETIC_TOKEN}\n",
            },
        )
        monkeypatch.setattr(module, "_REPO_ROOT", root)
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])

        exit_code = module.main()
        captured = capsys.readouterr()

        assert exit_code == 1, (
            "a TRACKED file holding the token was never scanned, because a "
            "directory in its path is named in _SKIP_DIRS -- the gate reported "
            "clean on a file this repo ships"
        )
        assert "data/notes.md" in captured.err.replace("\\", "/")
        assert "OK:" not in captured.out

    def test_a_git_ignored_file_is_out_of_scope(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """The scope-inflation direction: an ignored file does not ship.

        RED before the switch, for the opposite reason to the test above: the
        walk read a build artefact git is told to ignore and failed the gate on
        it. That is how the local gate came to depend on what else was on the
        disk.
        """
        module = gate_with_synthetic_token
        root = _repo_with(
            tmp_path,
            "ignored",
            {
                "README.md": "nothing here\n",
                ".gitignore": "build/\n",
                "build/generated.md": f"leak: {_SYNTHETIC_TOKEN}\n",
            },
        )
        monkeypatch.setattr(module, "_REPO_ROOT", root)
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])

        exit_code = module.main()
        captured = capsys.readouterr()

        assert exit_code == 0, (
            "the gate failed on a file git is told to ignore, which this repo "
            f"does not ship: {captured.err}"
        )
        assert "OK:" in captured.out

    def test_an_untracked_but_committable_file_is_still_scanned(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """``--others --exclude-standard`` is the half that keeps local coverage.

        Tracked-only would be a REGRESSION against the walk: the gate's whole
        point is to run before the push that would publish the leak, and at that
        moment the offending file is typically still untracked. This is the
        don't-lose-what-the-walk-had control.
        """
        module = gate_with_synthetic_token
        root = _repo_with(
            tmp_path, "untracked", {"README.md": "nothing here\n"}
        )
        (root / "draft.md").write_text(
            f"leak: {_SYNTHETIC_TOKEN}\n", encoding="utf-8", newline="\n"
        )
        monkeypatch.setattr(module, "_REPO_ROOT", root)
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])

        exit_code = module.main()
        captured = capsys.readouterr()

        assert exit_code == 1, (
            "an untracked file one `git add` away from shipping was not scanned"
        )
        assert "draft.md" in captured.err

    def test_the_enumeration_names_which_source_answered(
        self, gate_with_synthetic_token, tmp_path
    ):
        """git where git can answer, the walk where it cannot -- and it SAYS which.

        A silent fallback is how a guard ends up measuring something other than
        what its docstring claims.
        """
        module = gate_with_synthetic_token
        repo = _repo_with(tmp_path, "sourced", {"README.md": "clean\n"})
        paths, source = module.scan_paths(repo)
        assert source == "git", f"a real checkout was enumerated by {source!r}"
        assert "README.md" in paths

        loose = tmp_path / "not-a-repo"
        loose.mkdir()
        (loose / "README.md").write_text("clean\n", encoding="utf-8")
        _paths, fallback_source = module.scan_paths(loose)
        assert fallback_source == "walk", (
            "outside a git checkout the enumeration must fall back to the walk, "
            f"not answer nothing: got {fallback_source!r}"
        )

    def test_git_being_unavailable_falls_back_rather_than_scanning_nothing(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """"git cannot answer" is not "there is nothing to scan".

        Simulating the FAILURE shape (git absent), never inventing a success
        shape: the leak must still be found, by the walk.
        """
        module = gate_with_synthetic_token
        root = _repo_with(
            tmp_path, "nogit", {"notes.md": f"leak: {_SYNTHETIC_TOKEN}\n"}
        )

        def _no_git(*_args, **_kwargs):
            raise OSError("git not found")

        monkeypatch.setattr(module.subprocess, "run", _no_git)
        monkeypatch.setattr(module, "_REPO_ROOT", root)
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])

        assert module.main() == 1, "the fallback did not scan the tree at all"
        assert "notes.md" in capsys.readouterr().err

    def test_an_empty_scan_set_is_a_refusal_not_a_pass(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """A scan that examined nothing must never print ``OK:``.

        This is a NEW failure mode the switch itself introduces and therefore
        has to close in the same change: a walk of a populated tree cannot come
        back empty, but ``git ls-files`` answers rc 0 with no output in a repo
        that has nothing committed and nothing to add -- which would have been a
        clean verdict over a scan of zero files.
        """
        module = gate_with_synthetic_token
        root = _init_repo(tmp_path / "empty")
        monkeypatch.setattr(module, "_REPO_ROOT", root)
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])

        exit_code = module.main()
        captured = capsys.readouterr()

        assert exit_code == 1, "a scan of zero files reported success"
        assert "OK:" not in captured.out
        assert "no files" in captured.err.lower() or "zero" in captured.err.lower(), (
            f"the refusal must say the scan examined nothing: {captured.err}"
        )

    def test_the_listing_is_asked_for_in_a_form_that_cannot_be_re_encoded(
        self, gate_without_env_token
    ):
        """``-z``, and bytes mode -- read structurally, not from a comment.

        Both halves are the lesson ``ci/check_pytest_collection.py`` paid for:
        without ``-z`` git QUOTES a non-ASCII path and octal-escapes its bytes,
        and a text-mode read hands the decode to a thread where its failure is
        invisible. This gate may not commit the defect its sibling guards
        police.
        """
        module = gate_without_env_token
        source = _GATE_PATH.read_bytes().decode("utf-8")
        tree = ast.parse(source)

        listings = [
            call
            for call in _subprocess_run_calls(tree)
            if any(
                isinstance(arg, ast.Constant) and arg.value == "ls-files"
                for element in call.args
                if isinstance(element, ast.List)
                for arg in element.elts
            )
        ]
        assert len(listings) == 1, (
            f"expected exactly one `git ls-files` call site, found {len(listings)}"
        )
        argv = [
            arg.value
            for element in listings[0].args
            if isinstance(element, ast.List)
            for arg in element.elts
            if isinstance(arg, ast.Constant)
        ]
        assert "-z" in argv, (
            f"`git ls-files` is invoked without -z: {argv}. git quotes a path "
            "holding non-ASCII bytes and renders each byte as a backslash-octal "
            "escape, so the record read back is not the path on disk."
        )
        keywords = {kw.arg for kw in listings[0].keywords}
        assert not (keywords & {"text", "encoding", "errors", "universal_newlines"}), (
            f"the listing is read in text mode ({sorted(keywords)}) -- the decode "
            "then happens on a reader thread where its failure is invisible"
        )
        # The premise of the assertion above: the decode happens in-process.
        assert "_git_scan_paths" in module.__dict__

    def test_this_repository_is_itself_enumerated_from_git(
        self, gate_without_env_token
    ):
        """Non-vacuity: every assertion above is true of an empty scan set."""
        module = gate_without_env_token
        paths, source = module.scan_paths(module._REPO_ROOT)
        assert source == "git", f"this checkout was enumerated by {source!r}"
        assert "CLAUDE.md" in paths
        assert len(paths) >= 100, (
            f"only {len(paths)} paths enumerated for this repository -- the "
            "enumeration is broken, not the tree"
        )
