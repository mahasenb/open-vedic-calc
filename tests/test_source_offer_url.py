"""The AGPL source offer is CONFIGURATION, not a literal baked into the handler.

``/source`` carries this service's AGPL-3.0 source offer — the place a recipient
of a running instance goes to obtain the Corresponding Source. That value used to
be a string literal written inline in the response constructor, with the
environment variable as an afterthought reachable only through
``os.environ.get(name, <literal>)``. Two defects followed from that shape, and
this module is the contract on both.

**A literal cannot follow the repository.** A source offer naming a location that
no longer serves the source is not a weaker offer, it is a broken one: the
recipient gets a 404 where the license promises code. Nothing about a repository
URL is permanent — an owner rename, a move between accounts or organisations, or
a mirror change all invalidate it, and none of those events touch this codebase.
The offer therefore has to be a first-class configuration value a deployment can
set, with the built-in literal demoted to a *default* that applies only when
nothing is configured.

**A blank offer must never be served.** Making it configurable creates a new way
to break it — a variable that resolves to the empty string (an unsubstituted
template, a secret that came back blank, ``PUBLIC_SOURCE_URL=`` left dangling in
an env file) would have served ``{"source_url": ""}`` at HTTP 200. That is the
shape this workspace refuses everywhere: a control that silently disables itself
when its input is absent and reports success anyway. The resolution is therefore
fail-closed, and the boundary is drawn exactly where this repository already
draws it for comparable configuration:

* **Unset** → the built-in default, because there IS a correct compiled-in
  answer. This is the shape ``ALLOWED_ORIGINS`` (absent means the empty
  allow-list) and ``GIT_COMMIT`` (absent means fall through to the next
  resolver) already take.
* **Set and invalid** → ``RuntimeError`` at import, in every environment. This is
  the shape ``ALLOWED_ORIGINS=*`` takes: a value that is present and wrong is an
  operator error, and the refusal is not gated on ``is_real_deployment()``
  because — unlike a missing ``CALC_SERVICE_TOKEN``, which a developer box
  legitimately runs without — there is no environment in which a blank source
  offer is the intended configuration.

Empty and whitespace-only are the mandated invalid cases. A value that cannot be
a location at all (``TODO``, ``changeme``, a bare hostname with no scheme, a
filesystem path) is refused on the same footing and for the same reason: serving
it would boot green with the offer broken. What is deliberately NOT attempted is
any claim about the URL *resolving* — no reachability probe, no scheme
allowlist. The 404-after-a-move failure that motivates this change is not
detectable by any local check, so a check implying otherwise would buy the
appearance of validation without the property.
"""
import importlib
import os
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The value tests/conftest.py established for the suite, restored after every
# reload so a refusing import in one test cannot poison module state for others.
_SUITE_DEFAULT = os.environ.get("PUBLIC_SOURCE_URL", "https://example.com")

# A configured override used to prove the served value FOLLOWS configuration.
# Deliberately not the built-in default: an override equal to the default would
# let the override test pass on a build that ignores configuration entirely.
_OVERRIDE = "https://git.example.org/mirror/vedic-calc"


def _reload_main_with(monkeypatch, *, source_url):
    if source_url is None:
        monkeypatch.delenv("PUBLIC_SOURCE_URL", raising=False)
    else:
        monkeypatch.setenv("PUBLIC_SOURCE_URL", source_url)
    import app.main as main_mod
    return importlib.reload(main_mod)


def _client(main_mod):
    return TestClient(main_mod.app, headers={"X-Calc-Service-Token": "test"})


@pytest.fixture(autouse=True)
def _restore_main():
    yield
    os.environ["PUBLIC_SOURCE_URL"] = _SUITE_DEFAULT
    import app.main as main_mod
    importlib.reload(main_mod)


# ---------------------------------------------------------------------------
# (a) Unset → the built-in canonical URL, and it is a DECLARED constant
# ---------------------------------------------------------------------------

def test_the_default_is_a_declared_constant_not_a_literal_in_the_handler():
    """Moving the repository must be an edit to ONE named constant.

    The pin is deliberate rather than decoration. A literal buried in the
    response constructor is invisible to anyone auditing the license obligation,
    and it is the reason this value could rot unnoticed in the first place;
    a named constant makes the offer greppable, and makes changing it an act
    someone has to perform on purpose, in a diff a reviewer can see.
    """
    import app.main as main_mod

    assert main_mod.DEFAULT_PUBLIC_SOURCE_URL == "https://github.com/aishwara-limited/open-vedic-calc"


def test_unset_serves_the_built_in_default(monkeypatch):
    """No PUBLIC_SOURCE_URL at all — a source checkout, a bare container run —
    must still serve a complete, valid offer rather than refusing or blanking."""
    mod = _reload_main_with(monkeypatch, source_url=None)
    assert mod._SOURCE_URL == mod.DEFAULT_PUBLIC_SOURCE_URL

    body = _client(mod).get("/source").json()
    assert body["source_url"] == mod.DEFAULT_PUBLIC_SOURCE_URL


# ---------------------------------------------------------------------------
# (b) Configured → the configured value is what is served, verbatim
# ---------------------------------------------------------------------------

def test_the_configured_override_is_what_is_served(monkeypatch):
    mod = _reload_main_with(monkeypatch, source_url=_OVERRIDE)
    assert _OVERRIDE != mod.DEFAULT_PUBLIC_SOURCE_URL, "the probe must differ from the default"
    assert mod._SOURCE_URL == _OVERRIDE

    r = _client(mod).get("/source")
    assert r.status_code == 200
    assert r.json()["source_url"] == _OVERRIDE


def test_surrounding_whitespace_is_trimmed_rather_than_refused(monkeypatch):
    """A padded value is a real offer that arrived through a YAML block or a
    shell heredoc, not a misconfiguration — trim it, do not refuse it. The trim
    also has to reach the SERVED value: stripping only for the validity check
    would refuse nothing and still serve the untrimmed string."""
    mod = _reload_main_with(monkeypatch, source_url=f"  {_OVERRIDE}\t\n")
    assert mod._SOURCE_URL == _OVERRIDE
    assert _client(mod).get("/source").json()["source_url"] == _OVERRIDE


def test_the_served_url_does_not_follow_a_mid_process_env_mutation(monkeypatch):
    """Resolved once at import, exactly as _COMMIT and _ALLOWED_ORIGINS are.

    Reading os.environ inside the handler would put the license obligation at
    the mercy of a runtime mutation, and would make the import-time refusal
    below unreachable — a process that already booted would start serving the
    blank offer the refusal exists to prevent.
    """
    mod = _reload_main_with(monkeypatch, source_url=_OVERRIDE)
    monkeypatch.setenv("PUBLIC_SOURCE_URL", "https://git.example.org/swapped-underneath")
    assert _client(mod).get("/source").json()["source_url"] == _OVERRIDE


# ---------------------------------------------------------------------------
# (c) Set-but-blank → fail closed at import, never a blank offer at 200
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "blank",
    ["", " ", "\t", "\n", "   ", " \t\n ", "\xa0"],
    ids=["empty", "space", "tab", "newline", "spaces", "mixed", "nbsp"],
)
def test_a_blank_override_refuses_to_boot(monkeypatch, blank):
    with pytest.raises(RuntimeError, match="PUBLIC_SOURCE_URL"):
        _reload_main_with(monkeypatch, source_url=blank)


@pytest.mark.parametrize(
    "bad",
    [
        "TODO",
        "changeme",
        "github.com/owner/repo",
        "/srv/src",
        "owner/repo",
        "//github.com/owner/repo",
        "mailto:source@example.org",
        "http://[oops",
    ],
    ids=[
        "todo", "placeholder", "no-scheme", "fs-path", "slug",
        "netloc-without-scheme", "scheme-without-netloc", "unparseable",
    ],
)
def test_a_value_that_cannot_be_a_location_refuses_to_boot(monkeypatch, bad):
    """Same footing as blank: serving it would boot green with a broken offer.

    Three of these probes are here because a mutation proved the others could
    not tell the guard's arms apart. The scheme test and the host test are
    separate conditions, and a probe missing BOTH parts (``TODO``) is caught by
    either one alone — so neutering one arm left the whole parametrisation green
    and the arm unguarded. ``//github.com/owner/repo`` (a protocol-relative URL:
    a host, no scheme) can only be refused by the scheme arm, and
    ``mailto:source@example.org`` (a scheme, no host) only by the host arm, so
    each arm now has a probe that fails when it alone is removed.

    ``mailto:`` doubles as the pin on a stated bound rather than an accident:
    a written offer by email is AGPL section 6(b), while this field is named
    ``source_url`` and documented as a URL a consumer fetches.

    ``http://[oops`` is the parse-failure arm — urlsplit raises ValueError on an
    unterminated IPv6 host — and an exception escaping the resolver would be a
    refusal by accident rather than by policy, with a traceback in place of a
    message naming the variable.
    """
    with pytest.raises(RuntimeError, match="PUBLIC_SOURCE_URL"):
        _reload_main_with(monkeypatch, source_url=bad)


def test_the_refusal_names_the_variable_and_the_reason(monkeypatch):
    """An operator reads this message with no access to the source. It must say
    which variable is wrong, what it holds, and what to do — the shape
    app/auth.py's token refusal already takes."""
    with pytest.raises(RuntimeError) as excinfo:
        _reload_main_with(monkeypatch, source_url="   ")
    message = str(excinfo.value)
    assert "PUBLIC_SOURCE_URL" in message
    assert "empty" in message or "whitespace" in message
    assert "unset" in message, "the message must name the remedy, not only the fault"


def test_the_policy_helper_is_callable_without_process_env_gymnastics():
    """The verdict is a pure function of the string, mirroring
    app/auth.py::_token_weakness_reason — so the policy is testable without a
    reload, and a reload-based test is never the only thing asserting it."""
    import app.main as main_mod

    assert main_mod._source_url_rejection_reason(_OVERRIDE) is None
    assert main_mod._source_url_rejection_reason(main_mod.DEFAULT_PUBLIC_SOURCE_URL) is None
    assert main_mod._source_url_rejection_reason("") is not None
    assert main_mod._source_url_rejection_reason("   ") is not None
    assert main_mod._source_url_rejection_reason("TODO") is not None


# ---------------------------------------------------------------------------
# The declared default is the ONE copy — the config template follows it
# ---------------------------------------------------------------------------

def test_the_env_template_carries_the_declared_default():
    """.env.example is a second declaration of this setting, and it was WRONG —
    it shipped ``https://github.com/your-org/bphs-calc-service``, an org and a
    repository name that have never existed, so an operator who copied the
    template into place published a source offer pointing at nothing. Binding it
    to the constant is what stops the two drifting apart again.
    """
    import app.main as main_mod

    template = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    match = re.search(r"^PUBLIC_SOURCE_URL=(.*)$", template, re.MULTILINE)
    assert match, ".env.example no longer declares PUBLIC_SOURCE_URL"
    assert match.group(1).strip() == main_mod.DEFAULT_PUBLIC_SOURCE_URL


def test_the_readme_documents_the_declared_default():
    """The README is where a self-hoster learns whether this variable is
    required. It said ``<URL of this public repo>``, which reads as mandatory
    and names no value — so the documented answer and the served answer could
    not be compared. It now states the default, and this binds the two."""
    import app.main as main_mod

    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert main_mod.DEFAULT_PUBLIC_SOURCE_URL in readme
