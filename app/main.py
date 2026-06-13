import os
import subprocess
from datetime import datetime, time, date, timedelta

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .auth import require_token
from .schemas import (
    PersonalDataIn, DashaRequest, TransitRequest,
    ChartResponse, StrengthResponse, DashaPeriodOut,
    YogaOut, TransitResponse, TransitPlanetPlacement, GocharaVedha,
    SpecialPointsResponse, JaiminiKaraka, SourceInfo,
    PlanetPlacement, ShadbalaItem, BhavabalaItem, VimshopakaItem,
    RashiDrishti, RashiDrishtiPlanet, InduLagnaOut, SphutaOut,
    MuhurtRequest, MuhurtResponse,
    LagnaShuddhiRequest, LagnaShuddhiResponse, LagnaShuddhiSample, TimeWindow,
    LagnaShuddhiAlternative,
    FamilyLagnaShuddhiRequest, FamilyLagnaShuddhiResponse, FamilyMemberSample,
    CompatRequest, CompatResponse, KutaScore, MangalDoshaResult, DashaOverlap,
    ProfileResponse,
)
from bphs_core.chart import Chart, PersonalData, ChartSnapshot, PlanetData
from bphs_core import strength as strength_mod
from bphs_core import dashas as dashas_mod
from bphs_core import yogas as yogas_mod
from bphs_core import transits as transits_mod
from bphs_core import special_points as sp_mod
from bphs_core import vimshopaka as vimshopaka_mod
from bphs_core import rashi_drishti as rashi_drishti_mod
from bphs_core import muhurat as muhurat_mod
from bphs_core import lagna_shuddhi as lagna_shuddhi_mod
from bphs_core import utils
from bphs_core import compat as compat_mod


def _resolve_version() -> str:
    """Cache-invalidation key for the calc engine, resolved in priority order.

    ``GIT_COMMIT`` (set at image build time via the Dockerfile ARG/ENV, which
    the CI build populates with the building commit) is the *authoritative*
    value: the exact running-commit a downstream consumer can key its cache on.
    The in-container ``git rev-parse`` only fires for checkouts that still carry
    a ``.git`` directory (local development) — the published image has none.

    The source-content hash (``'src-' + sha256[:16]``) is a deterministic,
    logic-tracking fallback for local/dev images built without ``GIT_COMMIT``:
    it is *not* an authoritative commit, but it still changes whenever the calc
    logic changes, which is the only property a content-keyed cache relies on.

    The literal ``"unknown"`` is returned only if even the source tree is
    unreadable; it is the unresolved sentinel that signals to a downstream
    consumer that the version could not be determined and must be treated as
    non-cacheable (never substituted silently for a real commit).
    """
    commit = os.environ.get("GIT_COMMIT")
    if commit:
        return commit.strip()
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        pass
    try:
        import hashlib

        digest = hashlib.sha256()
        roots = [
            os.path.join(os.path.dirname(__file__), os.pardir, "bphs_core"),
            os.path.dirname(__file__),
        ]
        for root in roots:
            for dirpath, _dirs, files in os.walk(root):
                for fname in sorted(files):
                    if not fname.endswith(".py"):
                        continue
                    with open(os.path.join(dirpath, fname), "rb") as fh:
                        digest.update(fh.read())
        return "src-" + digest.hexdigest()[:16]
    except Exception:
        return "unknown"


_COMMIT = _resolve_version()

_ALLOWED_ORIGINS = [o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o]

MAX_MUHURAT_DAYS = int(os.environ.get("MAX_MUHURAT_DAYS", "365"))
MAX_LAGNA_SHUDDHI_DAYS = int(os.environ.get("MAX_LAGNA_SHUDDHI_DAYS", "365"))
# Vimshottari is a 120-year (43,830-day) cycle.  The cap must be large enough
# to pass a full-life request (birth → birth+120y) without truncation.  47,000
# days (~128.6 years) gives the 120-year cycle plus headroom for the elapsed-
# fraction back-dating (up to ~20 years × 365.25 days).  This is intentionally
# NOT the 365-day muhurat value — a sub-120-year cap would reject every
# full-life dasha request.
MAX_DASHA_DAYS = int(os.environ.get("MAX_DASHA_DAYS", "47000"))

app = FastAPI(
    title="Open Vedic Calc",
    description="Generic BPHS calculation service — AGPL-3.0",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

AUTH = [Depends(require_token)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_personal_data(p: PersonalDataIn) -> PersonalData:
    return PersonalData(
        name=p.name,
        birth_date=datetime.combine(p.birth_date, datetime.min.time()),
        birth_time=p.birth_time,
        birth_place=p.birth_place,
        latitude=p.latitude,
        longitude=p.longitude,
        timezone_offset_hours=p.timezone_offset_hours,
    )


def _pd_to_schema(pd: PlanetData) -> PlanetPlacement:
    is_gandanta, gandanta_proximity = utils.check_gandanta(pd.sign, pd.degrees)
    total_lon = (utils.SIGNS.index(pd.sign) * 30 + pd.degrees) % 360
    pada_lord = utils.nakshatra_pada_lord(total_lon)
    return PlanetPlacement(
        planet=pd.planet, sign=pd.sign, degrees=pd.degrees,
        nakshatra=pd.nakshatra, dignity=pd.dignity, house=pd.house,
        conjunctions=pd.conjunctions, aspects=pd.aspects,
        is_retrograde=pd.is_retrograde,
        is_gandanta=is_gandanta,
        gandanta_proximity_degrees=gandanta_proximity if is_gandanta else None,
        is_combust=pd.is_combust,
        combust_proximity_degrees=pd.combust_proximity_degrees,
        chalit_house=pd.chalit_house,
        pada_lord=pada_lord,
    )


def _chart_to_response(s: ChartSnapshot) -> ChartResponse:
    def to_list(varga: dict) -> list[PlanetPlacement]:
        return [_pd_to_schema(pd) for pd in varga.values()]

    rashi_drishti = RashiDrishti(
        sign_table=rashi_drishti_mod.get_rashi_drishti_table(),
        per_planet=[
            RashiDrishtiPlanet(
                planet=p.planet, sign=p.sign,
                aspects_signs=p.aspects_signs, aspects_planets=p.aspects_planets,
            )
            for p in rashi_drishti_mod.get_planet_rashi_drishti(s)
        ],
    )

    return ChartResponse(
        lagna=s.lagna, lagna_lord=s.lagna_lord,
        yoga_karaka=yogas_mod.get_yoga_karaka_planet(s),
        ayanamsa_value=s.ayanamsa_value,
        bhava_chalit_cusps=[round(c, 6) for c in s.chalit_cusps],
        rashi_drishti=rashi_drishti,
        rasi=to_list(s.rasi_chart),
        hora=to_list(s.hora_chart),
        drekkana=to_list(s.drekkana_chart),
        saptamsa=to_list(s.saptamsa_chart),
        navamsa=to_list(s.navamsa_chart),
        decamsa=to_list(s.decamsa_chart),
        dwadasamsa=to_list(s.dwadasamsa_chart),
        chaturvimsa=to_list(s.chaturvimsa_chart),
        trimshamsa=to_list(s.trimshamsa_chart),
        shashtyamsa=to_list(s.shashtyamsa_chart),
    )


def _get_chart(p: PersonalDataIn) -> tuple[Chart, ChartSnapshot]:
    person = _to_personal_data(p)
    chart = Chart(person)
    return chart, chart.snapshot()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/v1/chart", response_model=ChartResponse, dependencies=AUTH)
def chart_endpoint(p: PersonalDataIn):
    _, s = _get_chart(p)
    return _chart_to_response(s)


@app.post("/v1/strength", response_model=StrengthResponse, dependencies=AUTH)
def strength_endpoint(p: PersonalDataIn):
    _, s = _get_chart(p)

    planets_with_shadbala = [
        pl for pl in strength_mod.SHADBALA_MINIMUMS if pl in s.rasi_chart
    ]
    shadbala = [
        ShadbalaItem(**vars(strength_mod.compute_shadbala(s, pl)))
        for pl in planets_with_shadbala
    ]
    bhavabala = [
        BhavabalaItem(**vars(r))
        for r in strength_mod.compute_all_bhavabala(s)
    ]
    akv = strength_mod.compute_ashtakavarga(s)

    vimshopaka = {
        r.planet: VimshopakaItem(
            total=r.total, grade=r.grade, contributions=r.contributions,
        )
        for r in vimshopaka_mod.compute_all_vimshopaka(s)
    }

    return StrengthResponse(
        shadbala=shadbala,
        bhavabala=bhavabala,
        ashtakavarga=akv,
        vimshopaka=vimshopaka,
    )


@app.post("/v1/dashas", response_model=list[DashaPeriodOut], dependencies=AUTH)
def dashas_endpoint(req: DashaRequest):
    start = datetime.strptime(req.from_date, "%Y-%m-%d")
    end = datetime.strptime(req.to_date, "%Y-%m-%d")
    if end < start:
        raise HTTPException(status_code=422, detail="to_date must be on or after from_date")
    # Cap measured from birth date, not from_date.  The real cost driver in
    # vimshottari_mahadashas is cycle_count, which scales with (to_date - BIRTH),
    # not (to_date - from_date).  A narrow far-future window (e.g. 9900→9999) with
    # a normal birth can still force dozens of cycles from birth before window-
    # filtering; capping from birth directly bounds cycle_count to ≈3 regardless
    # of from_date.  Birth→birth+120y is 43,830 days < 47,000, so every legitimate
    # full-life request still passes.
    birth_dt = datetime.combine(req.birth_date, datetime.min.time())
    if (end - birth_dt).days > MAX_DASHA_DAYS:
        raise HTTPException(status_code=422, detail=f"Date range exceeds {MAX_DASHA_DAYS} days")
    _, s = _get_chart(req)
    periods = dashas_mod.get_dasha_timeline(s, start, end, req.systems)
    return [
        DashaPeriodOut(
            lord=d.lord, level=d.level, system=d.system,
            start_date=d.start_date, end_date=d.end_date,
            duration_years=d.duration_years,
        )
        for d in periods
    ]


@app.post("/v1/yogas", response_model=list[YogaOut], dependencies=AUTH)
def yogas_endpoint(p: PersonalDataIn):
    _, s = _get_chart(p)
    yogas = yogas_mod.detect_all_yogas(s)
    return [
        YogaOut(
            name=y.name, description=y.description,
            planets_involved=y.planets_involved,
            houses_involved=y.houses_involved,
            strength=y.strength,
            is_viparita_raja=y.is_viparita_raja,
            activating_lords=y.activating_lords,
        )
        for y in yogas
    ]


@app.post("/v1/transits", response_model=TransitResponse, dependencies=AUTH)
def transits_endpoint(req: TransitRequest):
    _, s = _get_chart(req)
    at = datetime.strptime(req.at_date, "%Y-%m-%d")

    current = transits_mod.get_current_transits(s, at)
    saturn = current.get("Saturn")
    jupiter = current.get("Jupiter")

    signals = transits_mod.compute_transit_signals(s, current)
    houses_lagna = transits_mod.compute_house_from_lagna(s, current)
    planets = [
        TransitPlanetPlacement(
            planet=tp.planet, sign=tp.sign, degrees=tp.degrees, nakshatra=tp.nakshatra,
            house_from_lagna=houses_lagna.get(tp.planet),
            house_from_moon=signals.get(tp.planet, {}).get("house_from_moon"),
            favourable=signals.get(tp.planet, {}).get("favourable"),
            bindu_score=signals.get(tp.planet, {}).get("bindu_score"),
        )
        for tp in current.values()
    ]

    sade_sati = transits_mod.get_sade_sati_info(s, at)

    saturn_vedha = transits_mod.check_ashtakavarga_vedha(s, "Saturn",
                                                          saturn.sign if saturn else "")
    jupiter_vedha = transits_mod.check_ashtakavarga_vedha(s, "Jupiter",
                                                           jupiter.sign if jupiter else "")

    gochara_vedha = [
        GocharaVedha(
            blocked_planet=v.blocked_planet,
            blocking_planet=v.blocking_planet,
            blocked_house=v.blocked_house,
            vedha_house=v.vedha_house,
            neutralised=v.neutralised,
        )
        for v in transits_mod.compute_gochara_vedha(s, current)
    ]

    derived = transits_mod.compute_transit_derived(s, current)

    return TransitResponse(
        planets=planets,
        sade_sati_active=sade_sati.is_active,
        sade_sati_phase=sade_sati.phase if sade_sati.is_active else None,
        saturn_vedha_blocked=saturn_vedha,
        jupiter_vedha_blocked=jupiter_vedha,
        gochara_vedha=gochara_vedha,
        chandrashtama=derived["chandrashtama"],
        dhaiya_active=derived["dhaiya_active"],
        dhaiya_phase=derived["dhaiya_phase"],
    )


@app.post("/v1/special-points", response_model=SpecialPointsResponse, dependencies=AUTH)
def special_points_endpoint(p: PersonalDataIn):
    _, s = _get_chart(p)
    karakas_raw = sp_mod.get_jaimini_karakas(s)

    indu = sp_mod.get_indu_lagna(s)
    beeja = sp_mod.get_beeja_sphuta(s)
    kshetra = sp_mod.get_kshetra_sphuta(s)

    def _sphuta_out(sp) -> SphutaOut:
        return SphutaOut(
            longitude=sp.longitude, sign=sp.sign, navamsa_sign=sp.navamsa_sign,
            sign_parity=sp.sign_parity, navamsa_parity=sp.navamsa_parity,
            strength=sp.strength, sign_lord=sp.sign_lord,
            sign_lord_dignity=sp.sign_lord_dignity,
        )

    return SpecialPointsResponse(
        arudha_lagna=sp_mod.get_arudha_lagna(s).sign,
        upapada=sp_mod.get_upapada(s).sign,
        atmakaraka=sp_mod.get_atmakaraka(s),
        karakamsa=sp_mod.get_karakamsa(s).sign,
        jaimini_karakas=[JaiminiKaraka(**k) for k in karakas_raw],
        indu_lagna=InduLagnaOut(
            sign=indu.sign, house_from_lagna=indu.house_from_lagna,
            occupants=indu.occupants, lord=indu.lord,
            lord_dignity=indu.lord_dignity, lord_house=indu.lord_house,
        ),
        beeja_sphuta=_sphuta_out(beeja),
        kshetra_sphuta=_sphuta_out(kshetra),
    )


@app.post("/v1/profile", response_model=ProfileResponse, dependencies=AUTH)
def profile_endpoint(p: PersonalDataIn):
    from bphs_core.profile import compute_profile
    _, s = _get_chart(p)
    result = compute_profile(s, p.birth_date, name=p.name)
    return ProfileResponse(**result)


@app.post("/v1/muhurat", response_model=MuhurtResponse, dependencies=AUTH)
def muhurat_endpoint(req: MuhurtRequest):
    _, s = _get_chart(req)
    
    # Extract natal Moon's nakshatra and sign from Rasi chart
    moon_pd = s.rasi_chart.get("Moon")
    birth_nak = moon_pd.nakshatra if moon_pd else None
    birth_sign = moon_pd.sign if moon_pd else None
    
    # Parse date range
    start_dt = datetime.strptime(req.start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(req.end_date, "%Y-%m-%d").date()
    if end_dt < start_dt:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    if (end_dt - start_dt).days > MAX_MUHURAT_DAYS:
        raise HTTPException(status_code=422, detail=f"Date range exceeds {MAX_MUHURAT_DAYS} days")

    days = []
    curr = start_dt
    place = utils.make_place(req.name, req.latitude, req.longitude, req.timezone_offset_hours)
    
    while curr <= end_dt:
        day_data = muhurat_mod.compute_muhurat_for_day(
            place=place,
            target_date=curr,
            birth_nakshatra=birth_nak,
            birth_moon_sign=birth_sign
        )
        days.append(day_data)
        curr += timedelta(days=1)
        
    return MuhurtResponse(days=days)


@app.post("/v1/muhurat/lagna-shuddhi", response_model=LagnaShuddhiResponse, dependencies=AUTH)
def lagna_shuddhi_endpoint(req: LagnaShuddhiRequest):
    start_dt = datetime.strptime(req.start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(req.end_date, "%Y-%m-%d").date()
    if end_dt < start_dt:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    if (end_dt - start_dt).days > MAX_LAGNA_SHUDDHI_DAYS:
        raise HTTPException(status_code=422, detail=f"Date range exceeds {MAX_LAGNA_SHUDDHI_DAYS} days")

    _, s = _get_chart(req)
    moon_pd = s.rasi_chart.get("Moon")
    birth_nak = moon_pd.nakshatra if moon_pd else None
    birth_sign = moon_pd.sign if moon_pd else None

    result = lagna_shuddhi_mod.scan_lagna_shuddhi(
        lat=req.latitude,
        lon=req.longitude,
        tz_offset=req.timezone_offset_hours,
        birth_nakshatra=birth_nak,
        birth_moon_sign=birth_sign,
        start_date=req.start_date,
        end_date=req.end_date,
        activity=req.activity_category,
        step_seconds=req.step_seconds,
    )

    best_raw = result["best_instant"]
    best_window_raw = result["best_window"]
    top_raw = result["top_samples"]

    def _to_sample(d: dict) -> LagnaShuddhiSample:
        return LagnaShuddhiSample(**d)

    return LagnaShuddhiResponse(
        best_instant=_to_sample(best_raw) if best_raw else None,
        best_window=TimeWindow(**best_window_raw) if best_window_raw else None,
        top_samples=[_to_sample(d) for d in top_raw],
        clearance_summary=result.get("clearance_summary"),
        alternatives=[LagnaShuddhiAlternative(**a) for a in result.get("alternatives", [])],
    )


MAX_FAMILY_MEMBERS = 6


@app.post("/v1/muhurat/family-lagna-shuddhi", response_model=FamilyLagnaShuddhiResponse, dependencies=AUTH)
def family_lagna_shuddhi_endpoint(req: FamilyLagnaShuddhiRequest):
    if len(req.members) < 2:
        raise HTTPException(status_code=422, detail="At least 2 members required")
    if len(req.members) > MAX_FAMILY_MEMBERS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_FAMILY_MEMBERS} members allowed")

    start_dt = datetime.strptime(req.start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(req.end_date, "%Y-%m-%d").date()
    if end_dt < start_dt:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    if (end_dt - start_dt).days > MAX_LAGNA_SHUDDHI_DAYS:
        raise HTTPException(status_code=422, detail=f"Date range exceeds {MAX_LAGNA_SHUDDHI_DAYS} days")

    # Build per-member dicts for the scan, extracting natal Moon from chart.
    member_dicts = []
    for m in req.members:
        _, s = _get_chart(m)
        moon_pd = s.rasi_chart.get("Moon")
        birth_nak = moon_pd.nakshatra if moon_pd else None
        birth_sign = moon_pd.sign if moon_pd else None
        member_dicts.append({
            "name": m.name,
            "lat": m.latitude,
            "lon": m.longitude,
            "tz_offset": m.timezone_offset_hours,
            "birth_nakshatra": birth_nak,
            "birth_moon_sign": birth_sign,
        })

    result = lagna_shuddhi_mod.scan_family_lagna_shuddhi(
        members=member_dicts,
        start_date=req.start_date,
        end_date=req.end_date,
        activity=req.activity_category,
        step_seconds=req.step_seconds,
    )

    per_member_out = [FamilyMemberSample(**md) for md in result["per_member"]]
    best_window_raw = result["best_window"]

    return FamilyLagnaShuddhiResponse(
        instant=result["instant"],
        best_window=TimeWindow(**best_window_raw) if best_window_raw else None,
        score=result["score"],
        score_100=result["score_100"],
        band=result["band"],
        per_member=per_member_out,
        consensus_quality=result["consensus_quality"],
        compromised_members=result["compromised_members"],
        clearance_summary=result.get("clearance_summary"),
        alternatives=[LagnaShuddhiAlternative(**a) for a in result.get("alternatives", [])],
    )


@app.post("/v1/compat", response_model=CompatResponse, dependencies=AUTH)
def compat_endpoint(req: CompatRequest):
    _, snap_a = _get_chart(req.person_a)
    _, snap_b = _get_chart(req.person_b)
    result = compat_mod.compute_compat(snap_a, snap_b, req.reference_date or date.today())

    kutas = [
        KutaScore(name=k.name, score=k.score, max_score=k.max_score,
                  interpretation=k.interpretation)
        for k in result.kutas
    ]
    dosha_a = MangalDoshaResult(
        has_dosha=result.mangal_dosha_a.has_dosha,
        severity=result.mangal_dosha_a.severity,
        cancellation=result.mangal_dosha_a.cancellation,
    )
    dosha_b = MangalDoshaResult(
        has_dosha=result.mangal_dosha_b.has_dosha,
        severity=result.mangal_dosha_b.severity,
        cancellation=result.mangal_dosha_b.cancellation,
    )
    overlaps = [
        DashaOverlap(
            start_date=o.start_date, end_date=o.end_date,
            person_a_lord=o.person_a_lord, person_b_lord=o.person_b_lord,
            quality=o.quality,
        )
        for o in result.dasha_overlaps
    ]
    return CompatResponse(
        total_score=result.total_score,
        max_score=result.max_score,
        kutas=kutas,
        mangal_dosha_a=dosha_a,
        mangal_dosha_b=dosha_b,
        nakshatra_compatibility=result.nakshatra_compatibility,
        dasha_overlaps=overlaps,
        composite_strength_notes=result.composite_strength_notes,
    )


# Intentionally unauthenticated: this is the container liveness probe. The
# docker-compose healthchecks and any orchestrator startup probe hit /healthz
# with no Authorization header, and the service runs on internal-only ingress.
# The body carries no sensitive data — only a status string and a boolean for
# whether the ephemeris data directory is mounted.
@app.get("/healthz")
def healthz():
    ephe_ok = os.path.isdir(os.path.join(os.path.dirname(__file__), "../data/ephe"))
    return {"status": "ok", "ephe_loaded": ephe_ok}


# Authenticated: provenance (commit + source URL) is served only to the
# bearer-token-holding backend. The public AGPL source offer is the public
# GitHub repository, not this internal endpoint.
@app.get("/source", response_model=SourceInfo, dependencies=AUTH)
def source():
    return SourceInfo(
        source_url=os.environ.get("PUBLIC_SOURCE_URL", "https://github.com/mahasenb/open-vedic-calc"),
        commit=_COMMIT,
    )
