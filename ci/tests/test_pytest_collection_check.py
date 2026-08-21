"""Guards on ci/check_pytest_collection.py — the one check that lives OUTSIDE pytest.

Why that script exists at all is measured, not assumed. Appending three lines to
``pyproject.toml`` on this branch and running ``pytest ci/tests/ -q``:

    addopts = "-k not_corpus"      -> exit 5, 88 deselected  (pytest itself fails)
    addopts = "-k 'not corpus'"    -> exit 1                 (the pytest guard reds)
    addopts = "-m 'not nothing'"   -> exit 1                 (the pytest guard reds)
    addopts = "-k 'not narrow'"    -> exit 0, 87 passed, 1 deselected
    addopts = "--collect-only"     -> exit 0, 88 collected, 0 executed

The last two are fail-open, and no pytest-resident guard can close them: under both,
the guard is among the tests that did not run. ``--collect-only`` is the sharper of
the two — it deselects nothing, so every reachability check still answers "yes, the
job reaches every gated module", truthfully, while not one assertion executes.

These tests exercise the checker against temporary trees rather than by mutating the
real ``pyproject.toml``, so a failure here can never leave the repository's own
configuration in a narrowed state.

Loaded by path via importlib, never ``from ci import ...``: ``ci/`` has no
``__init__.py`` by design, and CI runs the bare ``pytest ci/tests/ -q`` console
script, which does not put the CWD on sys.path (see CLAUDE.md).
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_CI_DIR = pathlib.Path(__file__).resolve().parent.parent
_REPO = _CI_DIR.parent
_CHECKER = _CI_DIR / "check_pytest_collection.py"

_BASE_PYPROJECT = '[project]\nname = "x"\nversion = "1.0.0"\n'


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_pytest_collection", _CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(tmp_path: pathlib.Path, pyproject_tail: str = "") -> pathlib.Path:
    (tmp_path / "pyproject.toml").write_text(
        _BASE_PYPROJECT + pyproject_tail, encoding="utf-8", newline="\n"
    )
    return tmp_path


# Each of these removes assertions from every pytest run in the repo.
_NARROWING = (
    '\n[tool.pytest.ini_options]\naddopts = "-k not_corpus"\n',
    "\n[tool.pytest.ini_options]\naddopts = \"-k 'not corpus'\"\n",
    "\n[tool.pytest.ini_options]\naddopts = \"-m 'not accuracy'\"\n",
    '\n[tool.pytest.ini_options]\naddopts = "--collect-only"\n',
    '\n[tool.pytest.ini_options]\naddopts = "--co"\n',
    '\n[tool.pytest.ini_options]\naddopts = "--ignore=tests/test_swiss_ephemeris.py"\n',
    '\n[tool.pytest.ini_options]\naddopts = "--ignore-glob=tests/test_*accuracy*.py"\n',
    '\n[tool.pytest.ini_options]\naddopts = ["-q", "--deselect", "tests/x.py::test_y"]\n',
    # One list entry holding two arguments, which pytest shell-splits.
    '\n[tool.pytest.ini_options]\naddopts = ["-k not_corpus"]\n',
)

# Each of these is legitimate and must NOT be refused. A checker that reds on
# ordinary configuration is a checker the next person deletes.
_CLEAN = (
    "",
    "\n[tool.pytest.ini_options]\n",
    '\n[tool.pytest.ini_options]\naddopts = "-q --strict-markers"\n',
    '\n[tool.pytest.ini_options]\naddopts = ["-q", "--color=no"]\n',
    '\n[tool.pytest.ini_options]\naddopts = "--maxfail=1 -x"\n',
    # A COMMENT, which is the entire reason this parses rather than greps.
    '\n[tool.pytest.ini_options]\n# addopts = "-k not_corpus"\n',
    # Defining markers is not selecting on them.
    '\n[tool.pytest.ini_options]\nmarkers = ["accuracy: needs swiss data"]\n',
    # A path that merely contains a flag's spelling is not that flag.
    '\n[tool.pytest.ini_options]\ntestpaths = ["tests", "ci/tests"]\n',
)


@pytest.mark.parametrize("tail", _NARROWING)
def test_narrowing_addopts_is_refused(tmp_path: pathlib.Path, tail: str) -> None:
    checker = _load_checker()
    found = checker.problems(_tree(tmp_path, tail))
    assert found, f"narrowing addopts was permitted: {tail!r}"
    assert "addopts" in found[0]


@pytest.mark.parametrize("tail", _CLEAN)
def test_legitimate_configuration_is_not_refused(
    tmp_path: pathlib.Path, tail: str
) -> None:
    checker = _load_checker()
    assert checker.problems(_tree(tmp_path, tail)) == [], (
        f"legitimate configuration was refused: {tail!r}"
    )


@pytest.mark.parametrize("name", ("pytest.ini", "tox.ini", "setup.cfg", ".pytest.ini"))
def test_a_config_file_this_checker_does_not_parse_is_refused(
    tmp_path: pathlib.Path, name: str
) -> None:
    """Fail closed on an unparsed config file rather than covering less than claimed.

    pytest honours `addopts` in all of these. Only pyproject.toml is parsed, so the
    honest response to another one appearing is to refuse until someone extends the
    checker deliberately — not to report clean about a file nothing opened.
    """
    checker = _load_checker()
    root = _tree(tmp_path)
    (root / name).write_text("[pytest]\naddopts = -k not_corpus\n", encoding="utf-8")
    found = checker.problems(root)
    assert found, f"{name} was ignored entirely"
    assert name in found[0]


def test_the_real_repository_configuration_is_clean() -> None:
    """The check this repo's CI actually performs, performed here too."""
    checker = _load_checker()
    assert checker.problems(_REPO) == []


def test_main_exit_codes(monkeypatch, tmp_path: pathlib.Path) -> None:
    """0 when clean, 1 when refused — a checker that exits 0 on a finding is not one."""
    checker = _load_checker()

    monkeypatch.setattr(checker, "REPO_ROOT", _tree(tmp_path))
    assert checker.main([]) == 0

    monkeypatch.setattr(
        checker,
        "REPO_ROOT",
        _tree(tmp_path, '\n[tool.pytest.ini_options]\naddopts = "--collect-only"\n'),
    )
    assert checker.main([]) == 1


def test_self_test_discriminates_and_passes() -> None:
    """`--self-test` must actually exercise both directions, and must pass today.

    The count in its own output is the non-vacuity floor: a self-test that
    discriminated zero fixtures would "pass" while proving nothing.
    """
    checker = _load_checker()
    assert checker.main(["--self-test"]) == 0


def test_self_test_fails_when_the_detector_stops_discriminating(monkeypatch) -> None:
    """Proof the self-test is not decorative.

    With the narrowing-flag table emptied, every narrowing fixture stops being
    detected — and `--self-test` must go non-zero rather than announcing OK. Without
    this, a gutted table would leave the self-test green and the CI step would
    certify the real configuration on a detector that detects nothing.
    """
    checker = _load_checker()
    monkeypatch.setattr(checker, "NARROWING_FLAGS", ())
    assert checker.main(["--self-test"]) == 1
