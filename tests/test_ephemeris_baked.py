"""The Swiss ephemeris data ships INSIDE the image, and its signal has consumers.

WHAT WENT WRONG
---------------
``probe_ephemeris_source()`` was a correct detector with no reader. Measured
2026-07-31 against the digest the staging revision was actually serving
(``calc-service@sha256:2b8a1557…``):

  * ``/app/data/ephe`` held 0 files and ``find / -name '*.se1'`` matched nothing;
  * the probe returned ``swiss_active=False, retflag=65604`` (FLG_MOSEPH set);
  * the SAME image with the data bind-mounted returned ``retflag=65602``;
  * ``/healthz`` answered ``200 {"status":"ok","ephe_loaded":false}``;
  * no repository anywhere read ``ephe_loaded``.

So every deployed instance computed on swisseph's built-in Moshier fallback,
the one signal that said so was read by nothing, and the endpoint carrying it
still called itself ``"ok"``.

WHAT THIS MODULE PINS
---------------------
1. The probe can be asked about a SPECIFIC body. ``sepl_18.se1`` carries the
   planets and ``semo_18.se1`` the Moon, so an image shipping only the first
   would report SWIEPH for the Sun while every Moon-derived value — nakshatra,
   pada, and every dasha in the system — silently came from the fallback. A
   Sun-only check cannot see that.
2. ``/healthz`` never says ``"ok"`` while the fallback is in use.
3. ``/source`` — the endpoint a deploy gate can actually reach through Cloud
   Run's frontend — carries the signal, so the post-deploy check has something
   to fail on.
4. The boot guard refuses to start a REAL deployment on the fallback.

Every assertion here drives the classification from ``utils.probe_ephemeris_source``
(patched at its source) rather than re-deriving a retflag by hand, so neutering
the real probe fails these tests instead of leaving them passing against a
private copy of the logic.
"""
from __future__ import annotations

import importlib

import pytest
import swisseph as swe
from fastapi.testclient import TestClient

from app import deployment, ephemeris_guard
from app.main import app
from bphs_core import utils

_SWISS_RETFLAG = 65602  # measured: FLG_SWIEPH set, FLG_MOSEPH clear
_MOSHIER_RETFLAG = 65604  # measured: FLG_MOSEPH set, FLG_SWIEPH clear


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _force_probe(monkeypatch: pytest.MonkeyPatch, swiss_active: bool) -> None:
    """Patch the ONE canonical detector, not a caller's private copy."""
    retflag = _SWISS_RETFLAG if swiss_active else _MOSHIER_RETFLAG

    def fake_probe(jd: float | None = None, body: int | None = None):
        return swiss_active, retflag

    monkeypatch.setattr(utils, "probe_ephemeris_source", fake_probe)


# ---------------------------------------------------------------------------
# 1. The probe must be answerable per body
# ---------------------------------------------------------------------------


def test_probe_asks_swisseph_about_the_body_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``body`` must REACH ``swe.calc_ut``, not merely be accepted and dropped.

    Asserting on the returned retflag cannot prove this. Without data files
    present — the ordinary state of this suite — the Sun and the Moon BOTH
    return the same Moshier retflag, so a probe that silently ignored ``body``
    and always asked about the Sun would satisfy any comparison of the result.
    Measured: neutering ``if body is None: body = swe.SUN`` into an
    unconditional ``body = swe.SUN`` left an earlier version of this test
    green. Capture the argument instead.

    This matters because the data set is split across files — sepl_18.se1
    (planets) and semo_18.se1 (Moon) — so a guard that can only ask about the
    Sun cannot see a data set that leaves every nakshatra, pada and dasha on
    the fallback.
    """
    seen: list[int] = []
    real_calc_ut = swe.calc_ut

    def recording_calc_ut(jd, body, flags):
        seen.append(body)
        return real_calc_ut(jd, body, flags)

    monkeypatch.setattr(swe, "calc_ut", recording_calc_ut)

    utils.probe_ephemeris_source(body=swe.MOON)
    assert seen == [swe.MOON], (
        f"probe_ephemeris_source(body=MOON) asked swisseph about {seen} — the "
        "body argument is being ignored"
    )

    seen.clear()
    utils.probe_ephemeris_source(body=swe.SATURN)
    assert seen == [swe.SATURN]

    seen.clear()
    utils.probe_ephemeris_source()
    assert seen == [swe.SUN], "omitting body must keep the historical Sun default"


def test_probe_classification_is_consistent_with_the_retflag() -> None:
    """Whichever engine this environment has, the verdict must match the bits."""
    swiss_active, retflag = utils.probe_ephemeris_source(body=swe.MOON)
    assert isinstance(swiss_active, bool)
    assert isinstance(retflag, int)
    assert swiss_active == (bool(retflag & swe.FLG_SWIEPH) and not retflag & swe.FLG_MOSEPH)


# ---------------------------------------------------------------------------
# 2. /healthz must stop calling the fallback "ok"
# ---------------------------------------------------------------------------


def test_healthz_is_not_ok_while_the_fallback_is_in_use(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact measured lie: 200 {"status":"ok","ephe_loaded":false}."""
    _force_probe(monkeypatch, swiss_active=False)
    body = client.get("/healthz").json()
    assert body["ephe_loaded"] is False
    assert body["status"] != "ok", (
        "/healthz reported status='ok' while swisseph was on the Moshier "
        "fallback — a health probe that passes with the control off is the "
        "fail-closed breach this endpoint exists to prevent"
    )
    assert body["status"] == "degraded"


def test_healthz_is_ok_when_swiss_data_is_really_in_use(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discrimination: the status must still be 'ok' on real data.

    Without this, hardcoding ``"degraded"`` would satisfy the test above.
    """
    _force_probe(monkeypatch, swiss_active=True)
    body = client.get("/healthz").json()
    assert body == {"status": "ok", "ephe_loaded": True}


def test_healthz_stays_http_200_on_the_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberate: the STATUS STRING carries the signal, the code stays 200.

    ``/healthz`` is the unauthenticated container liveness probe used by the
    docker-compose healthchecks. In a real deployment the boot guard below
    means the process never reaches this state at all, so a 503 here could
    only ever fire in development/local/test — the environments that
    deliberately tolerate the fallback — where it would break the dev loop
    and buy no accuracy. Pinned so a later "make it 503" edit is a conscious
    decision with this reasoning in front of it.
    """
    _force_probe(monkeypatch, swiss_active=False)
    assert client.get("/healthz").status_code == 200


# ---------------------------------------------------------------------------
# 3. /source must carry the signal — it is the reachable endpoint
# ---------------------------------------------------------------------------


def test_source_carries_the_ephemeris_signal(client: TestClient) -> None:
    """``/source`` is what a post-deploy gate can actually reach.

    Cloud Run's frontend intercepts ``/healthz`` and answers it with a Google
    404 that never reaches this container (measured 2026-07-31 against
    ``staging-backend``: ``/healthz`` returns Google's "Error 404 (Not
    Found)!!1" page while every other unmatched path returns a different 404
    body). The backend's calc readiness probe therefore already speaks to
    ``/source``, so that is where a consumable signal has to live.
    """
    payload = client.get("/source", headers={"X-Calc-Service-Token": "test"}).json()
    assert "ephe_loaded" in payload, (
        "/source does not expose ephe_loaded — the post-deploy gate has "
        "nothing reachable to fail on"
    )
    assert isinstance(payload["ephe_loaded"], bool)
    assert payload["ephemeris_source"] in {"swiss", "moshier"}


@pytest.mark.parametrize(
    ("swiss_active", "expected_source"), [(True, "swiss"), (False, "moshier")]
)
def test_source_signal_comes_from_the_canonical_probe(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    swiss_active: bool,
    expected_source: str,
) -> None:
    """Both directions, driven by patching the real detector."""
    _force_probe(monkeypatch, swiss_active=swiss_active)
    payload = client.get("/source", headers={"X-Calc-Service-Token": "test"}).json()
    assert payload["ephe_loaded"] is swiss_active
    assert payload["ephemeris_source"] == expected_source


# ---------------------------------------------------------------------------
# 4. The boot guard
# ---------------------------------------------------------------------------


def _probe_returning(*, sun: bool, moon: bool):
    def fake_probe(jd: float | None = None, body: int | None = None):
        active = moon if body == swe.MOON else sun
        return active, (_SWISS_RETFLAG if active else _MOSHIER_RETFLAG)

    return fake_probe


def test_guard_refuses_a_real_deployment_on_the_fallback() -> None:
    reason = ephemeris_guard.ephemeris_failure_reason(
        env="production", probe=_probe_returning(sun=False, moon=False)
    )
    assert reason is not None
    assert "Sun" in reason and "Moon" in reason


def test_guard_catches_a_partial_data_set() -> None:
    """The exact gap a Sun-only check cannot see: planets present, Moon absent."""
    reason = ephemeris_guard.ephemeris_failure_reason(
        env="production", probe=_probe_returning(sun=True, moon=False)
    )
    assert reason is not None, (
        "the guard passed with the Moon on the Moshier fallback — every "
        "nakshatra, pada and dasha in the system derives from the Moon"
    )
    assert "Moon" in reason
    assert "Sun" not in reason


def test_guard_passes_on_real_swiss_data() -> None:
    assert (
        ephemeris_guard.ephemeris_failure_reason(
            env="production", probe=_probe_returning(sun=True, moon=True)
        )
        is None
    )


@pytest.mark.parametrize("env", sorted(deployment.LENIENT_ENVS))
def test_guard_tolerates_the_fallback_outside_a_real_deployment(env: str) -> None:
    """development/local/test may run on Moshier — the suite itself does."""
    assert (
        ephemeris_guard.ephemeris_failure_reason(
            env=env, probe=_probe_returning(sun=False, moon=False)
        )
        is None
    )


def test_guard_defaults_to_refusing_when_environment_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-secure default, matching app.auth: unset ENVIRONMENT means production."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert deployment.environment() == "production"
    assert deployment.is_real_deployment() is True


def test_guard_raises_at_import_in_a_real_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail at startup, not on the first chart request.

    Reloading the module re-runs its module-level guard, which is the code
    path a container start actually takes.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(utils, "probe_ephemeris_source", _probe_returning(sun=False, moon=False))
    with pytest.raises(RuntimeError, match="Moshier"):
        importlib.reload(ephemeris_guard)
    # Restore the module to the state the rest of the session expects.
    monkeypatch.undo()
    importlib.reload(ephemeris_guard)


def test_guard_is_wired_into_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """app.main must import the guard, or it never runs in the real process."""
    import app.main as main_mod

    assert getattr(main_mod, "ephemeris_guard", None) is ephemeris_guard, (
        "app/main.py does not hold a reference to app.ephemeris_guard — the "
        "boot guard would never execute in the served process"
    )
