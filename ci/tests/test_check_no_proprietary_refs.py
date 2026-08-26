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
import json
import re
import subprocess
import symtable
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


_SUBPROCESS = "subprocess"
_DISPATCH = "run"


class _RunCallCollector(ast.NodeVisitor):
    """Every ``subprocess.run`` call in the gate, under ANY name it is bound to.

    WHAT THIS REPLACED, AND WHY IT HAD TO
    =====================================
    The previous matcher accepted exactly one spelling: an ``ast.Attribute``
    named ``run`` whose value is an ``ast.Name`` with ``id == "subprocess"``.
    Measured at this base commit, planting

        sp = subprocess
        def _alias_probe_commit_read(cwd):
            return sp.run(["git", "log", "--format=%B", "-1"],
                          capture_output=True, check=False, cwd=cwd)

    in the gate -- an unpinned commit-message read, bypassing ``_run_git_log``
    entirely, which is precisely what Arm B exists to refuse -- left ALL FOUR
    arms of the structural class GREEN. One rebinding defeated three controls at
    once. The sibling ``ci/tests/test_subprocess_decoding.py`` already resolved
    this form for the whole tree; the gate's own guard did not.

    ENUMERATE BINDINGS, THEN FAIL CLOSED ON WHAT IS LEFT
    ====================================================
    Enumerating binding constructs can never be finished -- ``getattr`` and
    ``importlib.import_module`` are reachable by construction and stay open in
    the sibling, which is a tree-wide guard and cannot refuse every unprovable
    receiver without drowning in ordinary code. So this collector does BOTH
    halves:

    * **Resolve** the constructs it models: ``import subprocess``,
      ``import subprocess as sp``, ``from subprocess import run``,
      ``from subprocess import run as r``, ``from subprocess import *``,
      ``sp = subprocess`` (transitively), and ``r = subprocess.run``. That last
      one was, when this collector was written, the form the sibling recorded
      in its KNOWN BOUNDS as measured and deliberately left open; it is now
      resolved in BOTH guards (the sibling's
      ``_RESOLVED_BINDING_FORMS`` enumerates it). What still separates the two
      is the REFUSAL half below, which is available here because this guard's
      subject is one file whose every ``.run(...)`` should reach subprocess,
      and is not available tree-wide where ``obj.run(...)`` is ordinary code.
      Two guards claiming different reaches must not be read as one.
    * **Refuse** everything it cannot prove innocent. ANY call whose callee is
      an attribute named ``run`` and whose receiver is not a name resolved to
      the subprocess module is recorded as UNRESOLVED, and the arm below fails
      on a non-empty unresolved list. So
      ``importlib.import_module("subprocess").run(...)`` and a receiver this
      collector has simply never seen do not slip past -- they red.
      ``getattr(subprocess, "run")(...)`` needs its own arm and has one: it
      produces no ``Attribute`` node at all, so the receiver sweep is blind to
      it, and any ``getattr`` whose first argument resolves to the module is
      refused outright. That hole was found by this class's own probe list
      rather than reasoned about, which is the argument for writing the
      evasions down as test data instead of as prose.

    The refusal half is what makes the ATTRIBUTE form complete without an
    exhaustive binding enumeration: the guard does not have to know how a name
    came to hold the module, only that it cannot prove the name does not.

    KNOWN BOUNDS, stated rather than claimed away
    =============================================
    * A BARE-name call (``r(...)``) is resolved only when this collector saw the
      binding. A name bound by a construct it does not model would be called
      without comment -- so ``test_no_module_scope_binding_construct_is_unmodelled``
      cross-checks the module-scope binding set against ``symtable``, the
      interpreter's own symbol table, and reds if the tree ever contains a
      binding construct this visitor does not record. That converts "we think we
      enumerated them" into a measured equality.
    * Bindings are tracked in SOURCE ORDER. A rebinding placed textually before
      its import, or built dynamically, is not resolved -- and, for the
      attribute form, is refused rather than missed.
    * A FUNCTION-LOCAL rebinding IS resolved, which an earlier revision of this
      block wrongly recorded as a module-scope-only limit. Measured: ``sp =
      subprocess`` inside a function body, then ``sp.run(...)``, is collected
      and named with its enclosing function. ``module_names`` is populated
      scope-independently; only ``_bind`` -- which feeds the ``symtable``
      cross-check below -- is gated on module scope. The bound was stated
      narrower than the code achieves, and an understated bound is still a wrong
      one: it invites somebody to add a guard that already exists.
    * What IS module-scope-only is the ``symtable`` completeness check, and so
      the bare-name path that rests on it: a name bound inside a function by a
      construct this visitor does not model is covered by the attribute-refusal
      sweep, not by the bare-name resolution.
    * Scope is this one file, read as a call SITE. It cannot prove what ``git``
      does at run time.
    """

    def __init__(self) -> None:
        self.module_names: set[str] = set()
        self.direct_names: set[str] = set()
        self.resolved: list[tuple[ast.Call, str]] = []
        self.unresolved: list[tuple[ast.Call, str, str]] = []
        self.module_scope_bindings: set[str] = set()
        self._scope: list[str] = []

    # -- binding provenance -------------------------------------------------
    def _bind(self, name: str) -> None:
        if not self._scope:
            self.module_scope_bindings.add(name)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._bind(alias.asname or alias.name.split(".")[0])
            if alias.name == _SUBPROCESS:
                self.module_names.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                if node.module == _SUBPROCESS:
                    # A star import binds every public name, so the dispatch
                    # arrives unqualified and un-renamed.
                    self.direct_names.add(_DISPATCH)
                    self._bind(_DISPATCH)
                continue
            self._bind(alias.asname or alias.name)
            if node.module == _SUBPROCESS and alias.name == _DISPATCH:
                self.direct_names.add(alias.asname or alias.name)
        self.generic_visit(node)

    def _is_module_ref(self, value: ast.expr) -> bool:
        return isinstance(value, ast.Name) and value.id in self.module_names

    def visit_Assign(self, node: ast.Assign) -> None:
        value = node.value
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            self._bind(target.id)
            if self._is_module_ref(value):
                self.module_names.add(target.id)           # sp = subprocess
            elif isinstance(value, ast.Name) and value.id in self.direct_names:
                self.direct_names.add(target.id)           # r2 = r
            elif (
                isinstance(value, ast.Attribute)
                and value.attr == _DISPATCH
                and self._is_module_ref(value.value)
            ):
                self.direct_names.add(target.id)           # r = subprocess.run
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self._bind(node.target.id)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name):
            self._bind(node.target.id)
        self.generic_visit(node)

    def _bind_targets(self, target: ast.expr) -> None:
        for sub in ast.walk(target):
            if isinstance(sub, ast.Name):
                self._bind(sub.id)

    def visit_For(self, node: ast.For) -> None:
        self._bind_targets(node.target)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._bind_targets(item.optional_vars)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._bind(node.name)
        self.generic_visit(node)

    # -- qualname tracking --------------------------------------------------
    def _push(self, node) -> None:
        self._bind(node.name)
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_FunctionDef = _push
    visit_AsyncFunctionDef = _push
    visit_ClassDef = _push

    # -- the calls themselves ----------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        where = ".".join(self._scope) or "<module scope>"
        func = node.func
        # `getattr(subprocess, "run")(...)` produces NO Attribute node at all --
        # the dispatch name is a runtime value -- so the receiver sweep below
        # cannot see it. Any getattr whose first argument is a resolved module
        # reference is therefore refused outright, whatever attribute it names:
        # this gate has no legitimate reason to reach into subprocess that way,
        # and "the guard could not read it" is not evidence of safety.
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and node.args
            and self._is_module_ref(node.args[0])
        ):
            self.unresolved.append(
                (node, where, f"getattr({node.args[0].id}, ...) -- runtime dispatch")
            )
        if isinstance(func, ast.Attribute) and func.attr == _DISPATCH:
            if self._is_module_ref(func.value):
                self.resolved.append((node, where))
            else:
                receiver = (
                    func.value.id
                    if isinstance(func.value, ast.Name)
                    else f"<{type(func.value).__name__} expression>"
                )
                self.unresolved.append((node, where, f"{receiver}.{_DISPATCH}(...)"))
        elif isinstance(func, ast.Name) and func.id in self.direct_names:
            self.resolved.append((node, where))
        self.generic_visit(node)


def _collect_run_calls(tree: ast.AST) -> _RunCallCollector:
    """Walk *tree* once and hand back the collector, bindings and all.

    AST, never grep: a comment or a string literal mentioning the call must not
    be able to satisfy -- or defeat -- this guard.
    """
    collector = _RunCallCollector()
    collector.visit(tree)
    return collector


def _subprocess_run_calls(tree: ast.AST) -> list[ast.Call]:
    """Every ``subprocess.run(...)`` call, under any name the module is bound to."""
    return [call for call, _ in _collect_run_calls(tree).resolved]


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
        """Which function holds each resolved call.

        Taken from the collector rather than by re-walking each ``FunctionDef``
        subtree: the bindings that resolve an aliased call live at MODULE scope,
        so a per-subtree walk starts with an empty alias table and would resolve
        exactly the calls the naive matcher used to -- reintroducing the hole in
        the one place Arm B reads to decide who is allowed an unpinned call.
        """
        return {id(call): where for call, where in _collect_run_calls(tree).resolved}

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
        # Collected from the WHOLE tree and filtered by enclosing function, not
        # by re-walking the helper's subtree: the bindings that resolve an
        # aliased call live at module scope, so a subtree walk resolves only the
        # bare `subprocess.run` spelling and would put an aliased stray on the
        # "inside the helper" side of this test by failing to see it at all.
        collector = _collect_run_calls(tree)
        inside = {
            id(call) for call, where in collector.resolved if where == "_run_git_log"
        }
        assert inside, "_run_git_log no longer runs git"

        owner = self._enclosing_functions(tree)
        strays = [call for call, _ in collector.resolved if id(call) not in inside]

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
        # Same reason as Arm B: filter the whole-tree collection by enclosing
        # function rather than walking the helper's subtree without its module
        # scope.
        calls = [
            call
            for call, where in _collect_run_calls(tree).resolved
            if where == "_run_git_log"
        ]
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


# ---------------------------------------------------------------------------
# File-scan COVERAGE -- which enumerated files actually get opened
# ---------------------------------------------------------------------------
class TestEveryEnumeratedFileIsScanned:
    """There is no extension allowlist, because an allowlist is fail-OPEN.

    The scope fix above asked git the right question. This is the other half:
    having got the right SET, the gate then dropped most of it on the floor.
    ``_is_scannable`` admitted twelve extensions plus the exact name
    ``Dockerfile``, so a file whose extension nobody had thought of was
    enumerated, skipped, and counted towards a clean verdict -- the gate saying
    ``OK:`` about a file it never opened, which is the same shape as the
    directory blacklist it replaced.

    Measured 2026-08-26 against this repository, EIGHT tracked files sat outside
    it: ``Dockerfile.test``, ``.env.example``, ``.github/CODEOWNERS``,
    ``.gitattributes``, ``.gitignore``, ``.python-version``, ``LICENSE`` and
    ``uv.lock``. The first three are prose a human writes about the deployment
    and about who owns which path, which is the register a downstream consumer
    gets named in.
    """

    def test_a_token_in_an_extensionless_tracked_file_is_found(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """``CODEOWNERS`` has no extension at all, so the allowlist never saw it.

        RED before the widening: the gate printed ``OK: no proprietary consumer
        references found.`` over a tracked, shipping file holding the token.
        """
        module = gate_with_synthetic_token
        root = _repo_with(
            tmp_path,
            "extensionless",
            {
                "README.md": "nothing here\n",
                ".github/CODEOWNERS": f"* @{_SYNTHETIC_TOKEN}-team\n",
            },
        )
        monkeypatch.setattr(module, "_REPO_ROOT", root)
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])

        exit_code = module.main()
        captured = capsys.readouterr()

        assert exit_code == 1, (
            "an extensionless tracked file holding the token was enumerated and "
            "then skipped -- the gate reported clean over a file this repo ships"
        )
        assert "CODEOWNERS" in captured.err
        assert "OK:" not in captured.out

    def test_a_token_in_a_suffixed_dockerfile_is_found(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """``Dockerfile.test`` matched neither arm of the old test.

        Its suffix ``.test`` was not in the extension set, and the exact-name
        arm compared the whole basename against ``dockerfile``. So the gate
        scanned ``Dockerfile`` and skipped the file beside it -- a distinction
        with no meaning to anyone writing a comment in either.
        """
        module = gate_with_synthetic_token
        root = _repo_with(
            tmp_path,
            "suffixed-dockerfile",
            {
                "Dockerfile": "FROM python:3.11-slim\n",
                "Dockerfile.test": (
                    f"# built for {_SYNTHETIC_TOKEN}\nFROM python:3.11-slim\n"
                ),
            },
        )
        monkeypatch.setattr(module, "_REPO_ROOT", root)
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])

        exit_code = module.main()
        captured = capsys.readouterr()

        assert exit_code == 1, (
            "Dockerfile.test was enumerated and skipped: its suffix was not in "
            "the extension set and its basename is not exactly 'Dockerfile'"
        )
        assert "Dockerfile.test" in captured.err

    def test_a_token_in_a_lock_file_is_found(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """Lock files are IN scope, and this is what that decision buys.

        ``uv.lock`` is machine-generated, which is the argument FOR scanning it
        rather than against: a private package name or a private index host
        lands there without a human ever typing it into a file the gate reads.
        """
        module = gate_with_synthetic_token
        root = _repo_with(
            tmp_path,
            "lockfile",
            {
                "pyproject.toml": "[project]\nname = \'x\'\n",
                "uv.lock": f'[[package]]\nname = "{_SYNTHETIC_TOKEN}-core"\n',
            },
        )
        monkeypatch.setattr(module, "_REPO_ROOT", root)
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])

        exit_code = module.main()
        captured = capsys.readouterr()

        assert exit_code == 1, "a token in uv.lock went unscanned"
        assert "uv.lock" in captured.err

    def test_an_unreadable_binary_file_is_REFUSED_not_skipped(
        self, gate_with_synthetic_token, tmp_path, monkeypatch, capsys
    ):
        """Widening the scope means binary reaches the decode. That is a refusal.

        The dangerous answer would be to skip it and carry on: "I could not read
        it" recorded as "there was nothing in it" is precisely the silent pass
        the strict decode exists to remove, and it would hand anyone a way to
        park a leak behind an unrecognised extension. So the verdict is rc 1,
        the file is NAMED, and no ``OK:`` is printed.

        Note what is NOT asserted: that the gate can read binary. It cannot, and
        it says so.
        """
        module = gate_with_synthetic_token
        root = _repo_with(tmp_path, "binary", {"README.md": "nothing here\n"})
        # Written as bytes, after the fixture's text files: invalid UTF-8 (a
        # lone 0x80 continuation byte) plus a NUL, i.e. an ordinary blob.
        (root / "blob.bin").write_bytes(b"\x00\x01\x80\xff binary payload \x80")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "blob"], cwd=root, check=True)
        monkeypatch.setattr(module, "_REPO_ROOT", root)
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])

        exit_code = module.main()
        captured = capsys.readouterr()

        assert exit_code == 1, (
            "an undecodable file in the scan set was skipped rather than "
            "refused -- 'I could not read it' became 'there was nothing in it'"
        )
        assert "blob.bin" in captured.err, (
            f"the refusal does not name the offending file: {captured.err!r}"
        )
        assert "OK:" not in captured.out

    def test_this_repository_has_NO_file_the_scan_leaves_out(
        self, gate_without_env_token, capsys
    ):
        """The standing anti-regression: enumerated == scanned, on the real tree.

        This is what makes the widening permanent rather than a one-off sweep.
        Reintroducing any filter -- an extension set, a name test, a size cap --
        reddens here on the commit that adds it.

        The gate's own source is the single deliberate exclusion (it necessarily
        spells the legacy token out in order to describe it), so it is subtracted
        rather than exempted by a rule that could grow a second member.
        """
        module = gate_without_env_token
        candidates, source = module.scan_paths(module._REPO_ROOT)
        assert source == "git", f"this checkout was enumerated by {source!r}"

        on_disk = [
            label for label in candidates if (module._REPO_ROOT / label).is_file()
        ]
        expected = [
            label
            for label in on_disk
            if (module._REPO_ROOT / label).resolve() != module._SELF
        ]
        # Non-vacuity: the subtraction must remove exactly the gate, and the
        # remainder must be a real tree rather than an empty list.
        assert len(on_disk) - len(expected) == 1, (
            "the gate's own file was not found in its own scan set"
        )
        assert len(expected) >= 100, (
            f"only {len(expected)} files enumerated -- the enumeration is "
            "broken, not the tree"
        )

        module.main(commit_range=None)
        captured = capsys.readouterr()
        reported = re.search(r"\((\d+) file\(s\) scanned", captured.out)
        assert reported is not None, (
            f"the gate did not report how many files it scanned: {captured.out!r}"
        )
        assert int(reported.group(1)) == len(expected), (
            f"the gate scanned {reported.group(1)} of {len(expected)} enumerated "
            f"files. Every file git lists must be opened: a file that is "
            f"enumerated and then skipped still counts towards the clean "
            f"verdict, which is the gate reporting OK: about a file it never "
            f"read."
        )


# ---------------------------------------------------------------------------
# The collector itself: a rebinding must not be a way out of three controls
# ---------------------------------------------------------------------------
# The reviewer's probe, verbatim: an UNPINNED commit-message read reached through
# a module alias. `_run_git_log` is bypassed, so `-c i18n.logOutputEncoding=UTF-8`
# never applies -- exactly what Arm B exists to refuse. Measured at the base
# commit, all four arms of TestSubprocessDecodingIsPinnedStructurally stayed
# GREEN with this sitting in the gate.
_ALIAS_PROBE = """

sp = subprocess


def _alias_probe_commit_read(cwd):
    return sp.run(["git", "log", "--format=%B", "-1"],
                  capture_output=True, check=False, cwd=cwd)
"""


class TestTheRunCallCollectorSeesEveryBinding:
    """Resolve what can be enumerated; REFUSE what cannot.

    Enumerating binding constructs can never be finished, which is why the
    sibling guard records ``r = subprocess.run`` in its KNOWN BOUNDS as
    reachable-but-unclosed. This collector closes that one AND adds the half
    that does not depend on enumeration: an attribute call named ``run`` whose
    receiver it cannot prove is not the subprocess module is REFUSED.
    """

    # (description, source) -- each binds the dispatch under a different name.
    _MODELLED = [
        ("import subprocess", "import subprocess\nsubprocess.run([1])\n"),
        ("import subprocess as sp", "import subprocess as sp\nsp.run([1])\n"),
        ("sp = subprocess", "import subprocess\nsp = subprocess\nsp.run([1])\n"),
        (
            "chained rebinding",
            "import subprocess\nsp = subprocess\nq = sp\nq.run([1])\n",
        ),
        ("from subprocess import run", "from subprocess import run\nrun([1])\n"),
        (
            "from subprocess import run as r",
            "from subprocess import run as r\nr([1])\n",
        ),
        ("from subprocess import *", "from subprocess import *\nrun([1])\n"),
        (
            "r = subprocess.run",
            "import subprocess\nr = subprocess.run\nr([1])\n",
        ),
        (
            "aliased inside a function",
            "import subprocess as sp\ndef f(c):\n    return sp.run([1], cwd=c)\n",
        ),
        # A rebinding in a FUNCTION BODY, not merely a module-level alias used
        # from one. The KNOWN BOUNDS above used to record this as out of reach;
        # it is not, and an understated bound invites a redundant guard.
        (
            "function-local rebinding",
            "import subprocess\ndef f(c):\n    sp = subprocess\n"
            "    return sp.run([1], cwd=c)\n",
        ),
    ]

    def test_every_modelled_binding_form_is_resolved(self):
        """Each spelling below reaches ``subprocess.run``; each must be collected.

        The last of them, ``r = subprocess.run``, is the form the sibling guard
        names in its own KNOWN BOUNDS as measured-but-not-closed. It is closed
        here.
        """
        missed = []
        for description, source in self._MODELLED:
            collector = _collect_run_calls(ast.parse(source))
            if len(collector.resolved) != 1:
                missed.append(
                    f"{description}: resolved {len(collector.resolved)} call(s), "
                    f"expected 1"
                )
        assert not missed, (
            "binding forms that reach subprocess.run and were not collected:\n  "
            + "\n  ".join(missed)
        )

    def test_an_unprovable_receiver_is_REFUSED_rather_than_missed(self):
        """The half that does not rest on enumeration being complete.

        A receiver this collector has never seen bound is not evidence of
        safety. Each of these is recorded as UNRESOLVED, which Arm D fails on --
        so the answer to a shape nobody modelled is a red build, not silence.
        """
        unprovable = [
            ("getattr dispatch", 'import subprocess\ngetattr(subprocess, "run")([1])\n'),
            (
                "dynamic import",
                'import importlib\nimportlib.import_module("subprocess").run([1])\n',
            ),
            ("a receiver never seen bound", "mystery.run([1])\n"),
            (
                "a rebinding that precedes its import",
                "sp = subprocess\nimport subprocess\nsp.run([1])\n",
            ),
        ]
        silent = []
        for description, source in unprovable:
            collector = _collect_run_calls(ast.parse(source))
            if not collector.unresolved and not collector.resolved:
                silent.append(description)
        assert not silent, (
            "shapes that reach a `.run(...)` call and were neither resolved nor "
            f"refused -- they would pass unremarked: {silent}"
        )

    def test_a_benign_run_method_is_not_mistaken_for_the_dispatch(self):
        """Don't-over-claim: refusal must be reported as refusal, not as a hit.

        ``unittest.TextTestRunner().run(suite)`` is an attribute call named
        ``run`` that has nothing to do with subprocess. The collector must not
        put it in ``resolved`` -- a guard that reports it as a subprocess call
        would be lying about what it found, even though refusing it is correct.
        """
        collector = _collect_run_calls(
            ast.parse("import unittest\nrunner = unittest.TextTestRunner()\n"
                      "runner.run(suite)\n")
        )
        assert collector.resolved == [], (
            "a non-subprocess `.run(...)` was reported as a subprocess call"
        )
        assert collector.unresolved, (
            "it must still be REFUSED -- this guard cannot prove the receiver is "
            "not subprocess, and unprovable means refused"
        )

    def test_the_alias_probe_would_now_red_arm_B(self):
        """The measured evasion, reproduced against the REAL gate source.

        Not against a synthetic snippet: the probe is appended to this gate's
        actual text, so what is asserted is that the arm which stayed green at
        the base commit now fails. Arm B's rule is an EQUALITY on the set of
        functions making an unpinned call, so the probe's function appearing in
        that set is precisely what reddens it.

        The gate file on disk is not touched.
        """
        source = _GATE_PATH.read_text(encoding="utf-8") + _ALIAS_PROBE
        collector = _collect_run_calls(ast.parse(source))

        owners = {where for _, where in collector.resolved}
        assert "_alias_probe_commit_read" in owners, (
            f"the aliased git read was not collected at all: {sorted(owners)}. "
            "That is the base-commit behaviour -- one rebinding defeating three "
            "controls at once."
        )
        strays = owners - {"_run_git_log"}
        registered = set(TestSubprocessDecodingIsPinnedStructurally._PIN_FREE_CALLERS)
        assert strays != registered, (
            "Arm B compares the set of functions making an unpinned git call "
            f"against {sorted(registered)}; with the probe present that set is "
            f"{sorted(strays)}, which must differ or the arm stays green"
        )


class TestTheGateItselfHasNoUnresolvableRunCall:
    """Arm D: the refusal list must be empty for the gate as it stands."""

    def test_no_run_call_in_the_gate_is_unresolvable(self):
        collector = _collect_run_calls(
            ast.parse(_GATE_PATH.read_text(encoding="utf-8"))
        )
        refused = [
            f"line {call.lineno} in {where}(): {why}"
            for call, where, why in collector.unresolved
        ]
        assert not refused, (
            "call(s) in the gate whose `.run(...)` receiver this guard cannot "
            "prove is not the subprocess module:\n  " + "\n  ".join(refused)
            + "\nUnprovable is refused, never assumed innocent. Spell the call "
            "as `subprocess.run(...)`, or bind the module with a form the "
            "collector models."
        )
        # Non-vacuity: an empty refusal list is also what a collector that found
        # nothing at all would produce.
        assert len(collector.resolved) >= 2, (
            f"only {len(collector.resolved)} resolved call(s) -- this gate makes "
            "two (the commit-message read and the file listing), so the "
            "collector is broken rather than the gate clean"
        )

    def test_no_module_scope_binding_construct_is_unmodelled(self):
        """The completeness claim, MEASURED against the interpreter's own tables.

        The refusal sweep covers the attribute form without needing a complete
        binding enumeration, but a BARE-name call (``r(...)``) is resolved only
        when the collector saw the binding. So the claim "this visitor records
        every module-scope binding in this file" has to be checked rather than
        asserted, and ``symtable`` is the checker: it is built by the compiler
        and knows every construct that binds a name.

        Measured on this gate: 28 names, identical both ways. A construct the
        visitor does not model -- a walrus at module scope, a match capture, a
        starred unpack -- shows up here as a divergence on the commit that adds
        it, rather than as a name that can quietly hold ``subprocess.run``.
        """
        source = _GATE_PATH.read_text(encoding="utf-8")
        collector = _collect_run_calls(ast.parse(source))
        table = symtable.symtable(source, _GATE_PATH.name, "exec")
        by_symtable = {
            symbol.get_name()
            for symbol in table.get_symbols()
            if symbol.is_assigned() or symbol.is_imported()
        }

        assert by_symtable, "symtable reported no module-scope bindings at all"
        missing = by_symtable - collector.module_scope_bindings
        assert not missing, (
            f"symtable reports module-scope binding(s) this visitor never "
            f"recorded: {sorted(missing)}. A name bound by a construct the "
            f"collector does not model can hold subprocess.run and be CALLED "
            f"BARE, which the attribute-refusal sweep does not see. Model the "
            f"construct, or record it in KNOWN BOUNDS."
        )
        # The other direction is a correctness check on the visitor, not a
        # coverage one: a name it thinks is bound at module scope but which the
        # compiler does not is a scope-tracking bug.
        assert not (collector.module_scope_bindings - by_symtable), (
            f"the visitor recorded module-scope binding(s) symtable does not: "
            f"{sorted(collector.module_scope_bindings - by_symtable)}"
        )


# ---------------------------------------------------------------------------
# THE COMMIT RANGE, DERIVED FROM THE EVENT RATHER THAN INTERPOLATED
#
# WHAT WAS MEASURED, on this repository's own CI runs rather than reasoned about
# ---------------------------------------------------------------------------
# `ci.yml` used to build the range by textual interpolation of
# `github.event.before` and `github.sha`. Read off five real runs of that step
# (`gh run view <id> --log`), the string it produced was, verbatim:
#
#   push, branch's FIRST push  "0000000000000000000000000000000000000000..3ccd529"
#   push, later push           "3ccd529..4b8ad23"
#   pull_request, opened       "..bf8cc63"
#   pull_request, synchronize  "659f43d..039d0db"
#
# Both of the first-of-each-kind shapes scan ONE message:
#
# * the all-zero sentinel is refused by `_get_commit_messages` (correctly -- it
#   is not a resolvable range), so a branch's first push scans HEAD alone. The
#   first push is exactly the one carrying every commit on the branch: measured
#   on this repository, `claude/guard-batch2`'s first push carried SIX commits
#   and `claude/guard-follow-through`'s carried FOUR, one message read each.
#
# * `github.event.before` is ABSENT on a pull_request opened/reopened/edited (it
#   exists only on `synchronize`), so the expression renders empty and the range
#   is `..<sha>`. That is git for `HEAD..<sha>`, and `actions/checkout` has put
#   HEAD *at* `github.sha`: measured on run 32966124847, "HEAD is now at bf8cc63
#   Merge 3ccd529... into dd2a533...", with `github.sha` = bf8cc63. So the range
#   is empty, the fallback reads HEAD, and HEAD is GitHub's synthetic MERGE
#   commit -- whose message is "Merge <sha> into <sha>" and contains no author
#   text at all. On those events the commit-message half of the gate scanned
#   ZERO author-written messages, not one.
#
# The range is now DERIVED, in Python, from the event payload -- the same
# "GitHub forwards facts, the policy decision is made where it is testable"
# shape `ci/check_pr_text.py` uses.
# ---------------------------------------------------------------------------

_ZERO_SHA = "0" * 40


def _run(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    return result.stdout.decode("utf-8").strip()


def _branch_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A checkout shaped like the one CI scans, with a leak buried mid-branch.

    ``origin/main`` is a real remote-tracking ref (``actions/checkout`` fetches
    ``+refs/heads/*:refs/remotes/origin/*``), the branch carries three commits,
    and the forbidden token sits in the message of the MIDDLE one -- never in
    HEAD's, which is the whole point: HEAD's message is the one a HEAD-only scan
    already reads.
    """
    repo = tmp_path / "checkout"
    repo.mkdir()
    _run(repo, "init", "-q", "-b", "main")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")

    sha: dict[str, str] = {}
    for label, message in (
        ("base", "chore: the state of main before the branch"),
        ("first", "feat: an ordinary first commit on the branch"),
        ("middle", f"fix: a leak buried mid-branch, {_SYNTHETIC_TOKEN}"),
        ("head", "docs: a perfectly clean commit at the tip"),
    ):
        (repo / "f.txt").write_text(label, encoding="utf-8", newline="\n")
        _run(repo, "add", "f.txt")
        _run(repo, "commit", "-q", "-m", message)
        sha[label] = _run(repo, "rev-parse", "HEAD")
        if label == "base":
            # The remote-tracking ref a real checkout carries.
            _run(repo, "update-ref", "refs/remotes/origin/main", sha["base"])
    return repo, sha


def _merge_checkout(repo: Path, sha: dict[str, str]) -> str:
    """Put the checkout in the state ``actions/checkout`` leaves it in for a
    pull_request: HEAD on GitHub's synthetic merge commit, which is also
    ``github.sha``."""
    _run(repo, "checkout", "-q", sha["base"])
    _run(
        repo, "merge", "-q", "--no-ff",
        "-m", f"Merge {sha['head']} into {sha['base']}", sha["head"],
    )
    return _run(repo, "rev-parse", "HEAD")


def _event(tmp_path: Path, payload: dict) -> str:
    """Write a synthetic GitHub event payload and return its path.

    GitHub hands the whole payload to every step as a JSON file named by
    ``GITHUB_EVENT_PATH``. Reading it is what makes the derivation testable
    without a workflow run -- and it needs no ``${{ }}`` interpolation into a
    ``run:`` body, which is the other half of what this change buys.
    """
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    return str(path)


class TestTheCommitRangeIsDerivedFromTheEvent:
    """Both trigger shapes must scan the whole push, not HEAD alone."""

    def test_the_measured_defect_is_real_on_a_first_push(
        self, gate_without_env_token, tmp_path
    ):
        """The premise, re-measured here rather than taken from a report.

        This asserts what the OLD interpolation produced, so the fix below is
        measured against a demonstrated defect rather than a described one. It
        passes before and after the fix -- a pin on the old string's behaviour,
        not the red-to-green arm.
        """
        module = gate_without_env_token
        repo, sha = _branch_repo(tmp_path)
        messages = module._get_commit_messages(f"{_ZERO_SHA}..{sha['head']}", cwd=repo)
        assert len(messages) == 1, messages
        assert not any(_SYNTHETIC_TOKEN in m for m in messages), (
            "the premise is wrong: the null-sentinel range did NOT miss the leak"
        )

    def test_the_measured_defect_is_real_on_a_pull_request(
        self, gate_without_env_token, tmp_path
    ):
        """Same, for the ``..<sha>`` shape a pull_request produced."""
        module = gate_without_env_token
        repo, sha = _branch_repo(tmp_path)
        merge_sha = _merge_checkout(repo, sha)
        messages = module._get_commit_messages(f"..{merge_sha}", cwd=repo)
        assert len(messages) == 1, messages
        assert messages[0].startswith("Merge "), messages
        assert not any(_SYNTHETIC_TOKEN in m for m in messages), (
            "the premise is wrong: the empty-before range did NOT miss the leak"
        )

    # -- the fix -----------------------------------------------------------
    def test_a_first_push_derives_the_range_from_the_default_branch(
        self, gate_without_env_token, tmp_path
    ):
        module = gate_without_env_token
        repo, sha = _branch_repo(tmp_path)
        decision = module.resolve_commit_range(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_SHA": sha["head"],
                "GITHUB_EVENT_PATH": _event(
                    tmp_path,
                    {"before": _ZERO_SHA, "repository": {"default_branch": "main"}},
                ),
            },
            cwd=repo,
        )
        assert decision.refusal is None, decision.refusal
        messages = module._get_commit_messages(decision.commit_range, cwd=repo)
        assert len(messages) == 3, [m.splitlines()[0] for m in messages]
        assert any(_SYNTHETIC_TOKEN in m for m in messages), (
            f"the leak buried mid-branch went unscanned: {decision.provenance}"
        )

    def test_a_later_push_uses_the_before_sha_it_was_given(
        self, gate_without_env_token, tmp_path
    ):
        """A real ``before`` is the most precise answer and must still win."""
        module = gate_without_env_token
        repo, sha = _branch_repo(tmp_path)
        decision = module.resolve_commit_range(
            {
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_SHA": sha["head"],
                "GITHUB_EVENT_PATH": _event(
                    tmp_path,
                    {"before": sha["first"], "repository": {"default_branch": "main"}},
                ),
            },
            cwd=repo,
        )
        assert decision.commit_range == f"{sha['first']}..{sha['head']}"
        messages = module._get_commit_messages(decision.commit_range, cwd=repo)
        assert any(_SYNTHETIC_TOKEN in m for m in messages)

    def test_a_pull_request_derives_the_range_from_its_base_and_head(
        self, gate_without_env_token, tmp_path
    ):
        """``github.sha`` is the MERGE commit on a pull_request, so the range is
        built from the payload's base and head, never from it."""
        module = gate_without_env_token
        repo, sha = _branch_repo(tmp_path)
        merge_sha = _merge_checkout(repo, sha)
        decision = module.resolve_commit_range(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_SHA": merge_sha,
                "GITHUB_EVENT_PATH": _event(
                    tmp_path,
                    {
                        "pull_request": {
                            "base": {"sha": sha["base"]},
                            "head": {"sha": sha["head"]},
                        }
                    },
                ),
            },
            cwd=repo,
        )
        assert decision.refusal is None, decision.refusal
        assert merge_sha not in (decision.commit_range or ""), (
            "the merge commit reached the range; on a pull_request github.sha is "
            f"not the head of the branch: {decision.commit_range}"
        )
        messages = module._get_commit_messages(decision.commit_range, cwd=repo)
        assert len(messages) == 3, [m.splitlines()[0] for m in messages]
        assert any(_SYNTHETIC_TOKEN in m for m in messages), (
            f"the leak buried mid-branch went unscanned: {decision.provenance}"
        )

    def test_an_edited_pull_request_carries_no_before_and_still_resolves(
        self, gate_without_env_token, tmp_path
    ):
        """``edited``/``opened``/``reopened`` payloads have no ``before`` key at
        all -- the exact shape that rendered as ``..<sha>``. Nothing in the
        derivation may depend on that key on a pull_request."""
        module = gate_without_env_token
        repo, sha = _branch_repo(tmp_path)
        decision = module.resolve_commit_range(
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_SHA": sha["head"],
                "GITHUB_EVENT_PATH": _event(
                    tmp_path,
                    {
                        "action": "edited",
                        "pull_request": {
                            "base": {"sha": sha["base"]},
                            "head": {"sha": sha["head"]},
                        },
                    },
                ),
            },
            cwd=repo,
        )
        assert any(
            _SYNTHETIC_TOKEN in m
            for m in module._get_commit_messages(decision.commit_range, cwd=repo)
        )

    # -- what the derivation refuses to do ---------------------------------
    @pytest.mark.parametrize(
        "hostile",
        [
            "--upload-pack=touch /tmp/pwned",
            "-C/etc",
            "HEAD --all",
            "main; rm -rf /",
            "",
            "not-hex-at-all",
            "abc",  # hex, but shorter than git's own 7-character minimum
            "f" * 65,  # hex, but longer than a SHA-256 object name
            "0" * 40,  # git's "there is no prior commit" sentinel, not a revision
            "0" * 40 + "..HEAD",  # a second revision smuggled into one field
        ],
    )
    def test_a_sha_that_is_not_a_sha_never_reaches_git(
        self, gate_without_env_token, tmp_path, hostile
    ):
        """Object names are validated as hex before being spliced into a range.

        ``git log`` reads ``--flags`` positionally, so an unvalidated field is
        argument injection rather than merely a wrong answer -- and the event
        payload is the one input to this gate that arrives from outside the
        tree.

        One case in an earlier version of this table was WRONG rather than
        revealing: a 42-character hex string, written as "an over-long SHA".
        It is inside git's abbreviated-SHA-256 range and the validator
        correctly accepted it (git then declines to resolve it, and the scan
        degrades to HEAD). The table now bounds both ends deliberately --
        shorter than git's own 7-character minimum, and longer than a full
        SHA-256 -- rather than asserting a number nobody had checked.
        """
        module = gate_without_env_token
        repo, sha = _branch_repo(tmp_path)
        decision = module.resolve_commit_range(
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_SHA": sha["head"],
                "GITHUB_EVENT_PATH": _event(
                    tmp_path,
                    {
                        "pull_request": {
                            "base": {"sha": hostile},
                            "head": {"sha": sha["head"]},
                        }
                    },
                ),
            },
            cwd=repo,
        )
        assert decision.commit_range is None, decision.commit_range

    def test_a_derivation_that_fails_under_actions_is_a_refusal(
        self, gate_without_env_token, tmp_path
    ):
        """Fail closed where it costs something.

        Under GitHub Actions on a push or pull_request, a range that cannot be
        derived means a fact GitHub always supplies was missing -- something is
        wrong, and scanning HEAD alone while printing OK is the silent
        degradation this whole change is about.
        """
        module = gate_without_env_token
        repo, sha = _branch_repo(tmp_path)
        decision = module.resolve_commit_range(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_SHA": sha["head"],
                "GITHUB_EVENT_PATH": str(tmp_path / "no-such-payload.json"),
            },
            cwd=repo,
        )
        assert decision.commit_range is None
        assert decision.refusal, "a CI run derived no range and did not refuse"

    def test_the_same_failure_outside_actions_is_not_a_refusal(
        self, gate_without_env_token, tmp_path
    ):
        """A developer running the gate by hand has no event payload and never
        did. Refusing there would make the local pre-push gate unrunnable, at a
        real cost against no gain: locally, HEAD's message is the commit that
        exists to be checked."""
        module = gate_without_env_token
        repo, _ = _branch_repo(tmp_path)
        decision = module.resolve_commit_range({}, cwd=repo)
        assert decision.commit_range is None
        assert decision.refusal is None
        assert "HEAD" in decision.provenance

    def test_an_explicit_argv_range_still_wins(
        self, gate_without_env_token, tmp_path
    ):
        """``python ci/check_no_proprietary_refs.py <range>`` keeps working: the
        argument is an operator saying exactly what to scan, and it must not be
        second-guessed by an environment that happens to be set."""
        module = gate_without_env_token
        repo, sha = _branch_repo(tmp_path)
        explicit = f"{sha['first']}..{sha['head']}"
        decision = module.resolve_commit_range(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_SHA": sha["head"],
                "GITHUB_EVENT_PATH": _event(tmp_path, {"before": _ZERO_SHA}),
            },
            cwd=repo,
            explicit=explicit,
        )
        assert decision.commit_range == explicit
        assert "argument" in decision.provenance

    def test_main_reports_how_many_commit_messages_it_actually_read(
        self, gate_without_env_token, tmp_path, monkeypatch, capsys
    ):
        """The count is the observability that makes a degraded run legible.

        A range that names six commits and a scan that read one look identical
        at exit 0 unless the number is printed. That is precisely how the
        defect above survived: the step said `OK: no proprietary consumer
        references found` on both shapes.
        """
        module = gate_without_env_token
        repo, sha = _branch_repo(tmp_path)
        monkeypatch.setattr(module, "_REPO_ROOT", repo)
        monkeypatch.setattr(module, "scan_paths", lambda root: (["f.txt"], "git"))
        # `_get_commit_messages`'s `cwd` default was bound at import, so pointing
        # `_REPO_ROOT` at the fixture is not enough to redirect it. Wrapping the
        # REAL function keeps the range logic under test and only redirects the
        # checkout it reads.
        real = module._get_commit_messages
        monkeypatch.setattr(
            module, "_get_commit_messages",
            lambda commit_range=None: real(commit_range, cwd=repo),
        )
        rc = module.main(
            argv=[],
            environ={
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_SHA": sha["head"],
                "GITHUB_EVENT_PATH": _event(
                    tmp_path,
                    {"before": _ZERO_SHA, "repository": {"default_branch": "main"}},
                ),
            },
        )
        out = capsys.readouterr()
        assert rc == 0, out
        assert "3 commit message(s)" in out.out, out.out
        assert "origin/main" in out.out, out.out


    def test_a_range_refusal_does_not_print_encoding_advice(
        self, gate_without_env_token, tmp_path, monkeypatch, capsys
    ):
        """Advice aimed at the wrong defect is worse than no advice.

        The refusal footer is about STRICT UTF-8 decoding -- "rewrite the
        offending file(s) or commit message(s) as UTF-8". A commit range that
        could not be derived is a refusal too, and printing that footer beside
        it sends the reader hunting for an encoding problem that is not there.
        Found by reading this gate's own output while verifying the fail-closed
        arm, which is the argument for looking at what a control PRINTS and not
        only at what it returns.
        """
        module = gate_without_env_token
        repo, _ = _branch_repo(tmp_path)
        monkeypatch.setattr(module, "_REPO_ROOT", repo)
        monkeypatch.setattr(module, "scan_paths", lambda root: (["f.txt"], "git"))
        monkeypatch.setattr(module, "_get_commit_messages", lambda commit_range=None: [])
        rc = module.main(
            argv=[],
            environ={
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_SHA": "0" * 40,
                "GITHUB_EVENT_PATH": str(tmp_path / "absent.json"),
            },
        )
        err = capsys.readouterr().err
        assert rc == 1, err
        assert "could not derive the commit range" in err, err
        assert "utf-8" not in err.lower(), err


class TestTheWorkflowWiresTheDerivation:
    """A fix nothing invokes is not a fix."""

    @staticmethod
    def _ci_workflow() -> dict:
        import yaml

        path = _GATE_PATH.parent.parent / ".github" / "workflows" / "ci.yml"
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @classmethod
    def _boundary_step(cls) -> dict:
        steps = cls._ci_workflow()["jobs"]["boundary"]["steps"]
        gate_steps = [
            step
            for step in steps
            if "check_no_proprietary_refs.py" in str(step.get("run", ""))
        ]
        assert len(gate_steps) == 1, gate_steps
        return gate_steps[0]

    def test_the_run_body_interpolates_nothing(self):
        """A ``${{ }}`` expression is textual substitution performed BEFORE the
        shell parses the command. The gate reads the event payload itself now,
        so the run body has no reason to carry one -- and the safest expression
        is the one that is not there."""
        body = str(self._boundary_step()["run"])
        assert "${{" not in body, body

    def test_the_gate_is_invoked_with_no_range_argument(self):
        """The derivation lives in Python, where it is tested. A positional
        range here would silently take precedence over it."""
        body = str(self._boundary_step()["run"]).strip()
        assert body == "python ci/check_no_proprietary_refs.py", body

    def test_the_checkout_still_fetches_the_whole_history(self):
        """``fetch-depth: 0``. A derived range is worth nothing against a
        shallow clone that does not contain the commits it names."""
        checkouts = [
            step
            for step in self._ci_workflow()["jobs"]["boundary"]["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout")
        ]
        assert len(checkouts) == 1, checkouts
        assert str(checkouts[0].get("with", {}).get("fetch-depth")) == "0"
