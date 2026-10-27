"""Turn raw Windy and Open-Meteo responses into an hour-by-hour go/no-go forecast for the site.

Terms used below:
- member: one forecast series: one model at one point (Windy: the site or a neighbour),
  or one run of an ensemble.
- primary: the best-ranked deterministic model with data for an hour. Its values decide.
- band wind: mean wind over the operating heights (10-150 m), averaged by height.
- ensemble: many runs of one model. Pooled (each ensemble weighted equally) they give the
  10-90 % band and the chances of enough wind, rain and thunderstorms.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

from . import openmeteo
from .config import Config

HOUR_MS = 3_600_000
FROZEN_PTYPES = {3, 5, 7, 8}  # Windy: freezing rain, snow, rain+snow, ice pellets
PRESSURE_LEVELS = ["1000h", "950h", "925h", "900h", "850h"]
MIN_LEVEL_AGL = 20.0  # a pressure level closer to the ground than this is ignored

LEVEL_ORDER = {"go": 0, "caution": 1, "nogo": 2}


# --- Small numeric helpers ------------------------------------------------------------------

def _scale(units: dict, key: str, kind: str) -> float:
    """Factor that brings a Windy series to our units."""
    unit = (units or {}).get(key)
    if kind == "mm":  # precipitation, Windy gives metres
        return 1000.0 if unit in ("m", None) else 1.0
    return 1.0


def _speed_dir(u: float, v: float) -> tuple[float, float]:
    """Speed and the direction the wind blows FROM, in degrees."""
    return math.hypot(u, v), (math.degrees(math.atan2(-u, -v)) + 360) % 360


def _interp_log(z: float, zs: list[float], vals: list[float]) -> float:
    """Interpolate linearly in ln(height), the usual shape of the wind near the ground."""
    if z <= zs[0]:
        return vals[0]
    if z >= zs[-1]:
        return vals[-1]
    j = bisect.bisect_left(zs, z)
    z0, z1 = zs[j - 1], zs[j]
    w = (math.log(z) - math.log(z0)) / (math.log(z1) - math.log(z0))
    return vals[j - 1] + w * (vals[j] - vals[j - 1])


def _interp_time(t: int, ts: list[int], vals: list | None) -> float | None:
    """Linear interpolation in time; None outside the series or next to a missing value."""
    if not vals or not ts or t < ts[0] or t > ts[-1]:
        return None
    j = bisect.bisect_left(ts, t)
    if ts[j] == t:
        return vals[j]
    a, b = vals[j - 1], vals[j]
    if a is None or b is None:
        return None
    w = (t - ts[j - 1]) / (ts[j] - ts[j - 1])
    return a + w * (b - a)


def _covering(t: int, ts: list[int], vals: list | None) -> float | None:
    """For totals over the past 1-3 hours: the value whose window covers the hour starting at t."""
    if not vals or not ts:
        return None
    j = bisect.bisect_left(ts, t + HOUR_MS)
    if j >= len(ts) or ts[j] - 3 * HOUR_MS > t + HOUR_MS:
        return None
    return vals[j]


def _quantiles(pairs: list[tuple[float, float]], qs: list[float]) -> list[float]:
    """Weighted quantiles of (value, weight) pairs."""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    out, acc, k = [], 0.0, 0
    for q in qs:
        while k < len(pairs) - 1 and acc + pairs[k][1] < q * total:
            acc += pairs[k][1]
            k += 1
        out.append(pairs[k][0])
    return out


def sun_elevation(t_ms: int, lat: float, lon: float) -> float:
    """Solar elevation in degrees (NOAA low-precision formulas, good to ~0.5 deg)."""
    n = t_ms / 86_400_000 + 2440587.5 - 2451545.0
    L = (280.460 + 0.9856474 * n) % 360
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * n) % 24
    ha = math.radians((gmst * 15 + lon - ra + 540) % 360 - 180)
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)))


# --- Wind profile ---------------------------------------------------------------------------

RD, G, LAPSE = 287.05, 9.80665, 0.0065
DEFAULT_ALPHA = 1 / 7
ALPHA_RANGE = (0.0, 0.45)


def shear_exponent(speeds: list[float], heights: list[float]) -> float:
    """Power-law exponent that matches a profile between its lowest and highest height."""
    s0, s1 = speeds[0], speeds[-1]
    if s0 < 0.5 or s1 < 0.5 or heights[-1] <= heights[0]:
        return DEFAULT_ALPHA  # near-calm: the ratio means nothing
    a = math.log(s1 / s0) / math.log(max(heights[-1], 10.0) / max(heights[0], 10.0))
    return min(max(a, ALPHA_RANGE[0]), ALPHA_RANGE[1])


def profile_from_points(zs: list[float], speeds: list[float], heights: list[float],
                        alpha: float | None = None) -> list[float]:
    """Speeds at the requested heights from speeds known at zs (ascending, first = 10 m).

    Between known heights: log-height interpolation. Above the top one: the power law of the
    two highest. With the 10 m wind only: a power law with alpha (borrowed) or 1/7.
    """
    if len(zs) == 1:
        a = DEFAULT_ALPHA if alpha is None else alpha
        return [speeds[0] * (max(h, 10.0) / 10.0) ** a for h in heights]
    top_alpha = shear_exponent(speeds[-2:], zs[-2:])
    out = []
    for h in heights:
        h = max(h, zs[0])
        out.append(_interp_log(h, zs, speeds) if h <= zs[-1] else speeds[-1] * (h / zs[-1]) ** top_alpha)
    return out


def level_height(body: dict, i: int, lev: str, elevation_m: float) -> float | None:
    """Height above sea level of a Windy pressure level, for models without gh.

    Hypsometric equation from the model's pressure and temperatures; good to roughly 10-40 m.
    Windy's "pressure" is sea-level pressure; a value that low can only be station pressure.
    """
    ps = (body.get("pressure-surface") or [None])[i] if len(body.get("pressure-surface") or []) > i else None
    ts = (body.get("temp-surface") or [None])[i] if len(body.get("temp-surface") or []) > i else None
    if ps is None or ts is None:
        return None
    if ps > 97_500:            # sea-level pressure: start from sea level
        p0, z0, t0 = ps, 0.0, ts + LAPSE * elevation_m
    else:                      # station pressure: start from the ground
        p0, z0, t0 = ps, elevation_m, ts
    p = float(lev.rstrip("h")) * 100
    tl = body.get(f"temp-{lev}")
    t_lev = tl[i] if tl and tl[i] is not None else t0 * (p / p0) ** (RD * LAPSE / G)
    return z0 + RD * (t0 + t_lev) / 2 / G * math.log(p0 / p)


def has_levels(body: dict) -> bool:
    """True if a Windy response has wind on any pressure level (AROME through Windy has none)."""
    return any(x is not None for lev in PRESSURE_LEVELS for x in (body.get(f"wind_u-{lev}") or []))


def wind_profile(body: dict, i: int, elevation_m: float, heights: list[float], alpha: float | None = None):
    """Windy: speeds at the requested heights and the direction mid-band, for time index i.

    Built from the 10 m wind plus every pressure level above the ground (height from gh, or
    from pressure and temperature).
    """
    u10 = body.get("wind_u-surface", [None])[i] if i < len(body.get("wind_u-surface", [])) else None
    v10 = body.get("wind_v-surface", [None])[i] if i < len(body.get("wind_v-surface", [])) else None
    if u10 is None or v10 is None:
        return None
    zs, us, vs = [10.0], [u10], [v10]
    for lev in PRESSURE_LEVELS:
        u, v = body.get(f"wind_u-{lev}"), body.get(f"wind_v-{lev}")
        if not u or not v or u[i] is None or v[i] is None:
            continue
        gh = body.get(f"gh-{lev}")
        z_asl = gh[i] if gh and gh[i] is not None else level_height(body, i, lev, elevation_m)
        if z_asl is None:
            continue
        z = z_asl - elevation_m
        if z > max(MIN_LEVEL_AGL, zs[-1] + 1):
            zs.append(z)
            us.append(u[i])
            vs.append(v[i])
    speeds = profile_from_points(zs, [math.hypot(a, b) for a, b in zip(us, vs)], heights, alpha)
    if len(zs) == 1:
        mid_u, mid_v = u10, v10
    else:
        mid = heights[len(heights) // 2]
        mid_u, mid_v = _interp_log(mid, zs, us), _interp_log(mid, zs, vs)
    return speeds, _speed_dir(mid_u, mid_v)[1]


def band_mean(speeds: list[float], heights: list[float]) -> float:
    """Height-weighted mean (trapezoid rule), so uneven height spacing does not bias it."""
    if len(heights) == 1:
        return speeds[0]
    area = sum((heights[k + 1] - heights[k]) * (speeds[k] + speeds[k + 1]) / 2 for k in range(len(heights) - 1))
    return area / (heights[-1] - heights[0])


# --- Members ---------------------------------------------------------------------------------

@dataclass
class Member:
    model: str            # member id: windy:<name>, om:<name> or ens:<name>
    point: int            # Windy: 0 = the site, 1-4 = neighbours. Ensembles: run number
    ts: list[int]
    band: list            # band-mean wind, m/s
    profile: list         # per time: list of speeds at cfg heights
    direction: list       # degrees, at the middle of the band
    series: dict          # other fields, our units: rain mm/h, snow, cape J/kg, vis m, temp C, wcode
    levels: bool = True   # False: the 10-150 m shape is borrowed (see build)
    kind: str = "det"     # det or ens
    heights: list = field(default_factory=list)  # heights the source gives directly


def windy_member(model: str, point: int, body: dict, cfg: Config, alpha_at=None) -> Member:
    """alpha_at(t) gives the shear exponent to use when the response has no pressure levels."""
    ts = body.get("ts", [])
    units = body.get("units", {})
    band, profile, direction = [], [], []
    for i in range(len(ts)):
        alpha = alpha_at(ts[i]) if alpha_at else None
        p = wind_profile(body, i, cfg.site.elevation_m, cfg.heights_m, alpha)
        band.append(None if p is None else band_mean(p[0], cfg.heights_m))
        profile.append(None if p is None else p[0])
        direction.append(None if p is None else p[1])

    def get(key, f=lambda x: x):
        vals = body.get(key)
        return None if vals is None else [None if x is None else f(x) for x in vals]

    mm = _scale(units, "past3hprecip-surface", "mm")
    temp_unit = units.get("temp-surface")
    series = {
        "rain": get("past3hprecip-surface", lambda x: max(0.0, x) * mm / 3),       # mm/h
        "conv": get("past3hconvprecip-surface", lambda x: max(0.0, x) * mm / 3),
        "snow": get("past3hsnowprecip-surface", lambda x: max(0.0, x) * mm / 3),
        "ptype": get("ptype-surface"),
        "cape": get("cape-surface"),
        "vis": get("visibility-surface"),
        "temp": get("temp-surface", (lambda x: x - 273.15) if temp_unit in ("K", None) else (lambda x: x)),
    }
    lv = has_levels(body)
    return Member(model, point, ts, band, profile, direction, series, lv, "det",
                  [10.0] if not lv else [])


def om_member(model: str, run_no: int, run: dict, ts: list[int], cfg: Config, kind: str, alpha_at=None) -> Member:
    """One Open-Meteo run. Wind comes at real heights; precipitation is the past hour's total."""
    avail = [h for h in openmeteo.DET_HEIGHTS
             if any(x is not None for x in (run.get(f"wind_speed_{h}m") or []))]
    band, profile, direction = [], [], []
    for i, t in enumerate(ts):
        pts = [(float(h), run[f"wind_speed_{h}m"][i]) for h in avail if run[f"wind_speed_{h}m"][i] is not None]
        if not pts or pts[0][0] != 10.0:
            band.append(None)
            profile.append(None)
            direction.append(None)
            continue
        alpha = alpha_at(t) if (alpha_at and len(pts) == 1) else None
        speeds = profile_from_points([z for z, _ in pts], [s for _, s in pts], cfg.heights_m, alpha)
        band.append(band_mean(speeds, cfg.heights_m))
        profile.append(speeds)
        d100, d10 = run.get("wind_direction_100m"), run.get("wind_direction_10m")
        direction.append(d100[i] if d100 and d100[i] is not None else (d10[i] if d10 else None))
    series = {
        "rain": run.get("precipitation"),
        "snow": run.get("snowfall"),
        "cape": run.get("cape"),
        "vis": run.get("visibility"),
        "temp": run.get("temperature_2m"),
        "wcode": run.get("weather_code"),
    }
    return Member(model, run_no, ts, band, profile, direction, series, len(avail) > 1, kind, avail)


def store_key(member_id: str) -> str:
    """Windy rows keep the bare model name in the database (as in v1)."""
    return member_id.split(":", 1)[1] if member_id.startswith("windy:") else member_id


# --- Decision ------------------------------------------------------------------------------------

def _worse(a: str, b: str) -> str:
    return a if LEVEL_ORDER[a] >= LEVEL_ORDER[b] else b


def _fmt(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}"


def _pct(p: float) -> str:
    return f"{round(p * 100)}%"


def decide(h: dict, lim: dict) -> tuple[str, dict, list[dict]]:
    """Status of one hour, the level of each driver, and the reasons in plain words.

    The best model decides. The other forecasts and the ensembles can only make it more careful:
    an amber hour, or a no-go when enough ensemble runs show a thunderstorm.
    """
    checks = {"wind": "go", "rain": "go", "storm": "go", "fog": "go"}
    reasons: list[dict] = []

    def add(key: str, level: str, code: str, text: str, **p):
        """code and p let the page word the reason in any language; text is the English version."""
        checks[key] = _worse(checks[key], level)
        reasons.append({"key": key, "level": level, "code": code, "p": p, "text": text})

    # Wind: too little, too uncertain, or too much.
    w, n = h["wind"], h.get("n_members", 1)
    prob = h.get("wind_prob")
    mw = lim["min_wind"]
    if w < mw:
        add("wind", "nogo", "wind_low", f"Not enough wind: {_fmt(w)} m/s, needs {_fmt(mw)}", w=round(w, 1), min=mw)
    elif prob is not None and prob < lim["min_wind_probability"]:
        add("wind", "caution", "wind_prob", f"Chance of {_fmt(mw)} m/s or more: only {_pct(prob)}",
            p=round(prob, 2), min=mw)
    elif n > 1 and h.get("wind_agree", 1) < lim["min_agreement"]:
        k = round(h["wind_agree"] * n)
        add("wind", "caution", "wind_disagree", f"Forecasts disagree: only {k} of {n} reach {_fmt(mw)} m/s",
            k=k, n=n, min=mw)
    top = h["wind_top"]
    if top >= lim["max_wind_nogo"]:
        add("wind", "nogo", "wind_high", f"Too much wind: {_fmt(top)} m/s aloft", w=round(top, 1))
    elif top >= lim["max_wind_caution"]:
        add("wind", "caution", "wind_strong", f"Strong wind: {_fmt(top)} m/s aloft", w=round(top, 1))

    # Rain: essentially none allowed.
    rain, rain_prob = h.get("rain"), h.get("rain_prob")
    if h.get("frozen"):
        add("rain", "nogo", "snow", "Snow or ice")
    elif rain is not None and rain >= lim["rain_nogo"]:
        add("rain", "nogo", "rain", f"Rain: {rain:.2f} mm/h", r=round(rain, 2))
    elif rain_prob is not None and rain_prob >= lim["rain_probability_caution"]:
        add("rain", "caution", "rain_prob", f"{_pct(rain_prob)} chance of rain", p=round(rain_prob, 2))
    elif (h.get("rain_hi") or 0) >= lim["rain_nogo"]:
        add("rain", "caution", "rain_other", "Some forecasts show rain")

    # Thunderstorms: forecast by the best model, or by enough ensemble runs, is no-go.
    storm_prob = h.get("storm_prob")
    cape = h.get("cape")
    if h.get("storm"):
        add("storm", "nogo", "storm", "Thunderstorm forecast" + (f" (CAPE {cape:.0f} J/kg)" if cape else ""),
            cape=round(cape) if cape else None)
    elif storm_prob is not None and storm_prob >= lim["storm_probability_nogo"]:
        add("storm", "nogo", "storm_prob", f"{_pct(storm_prob)} chance of a thunderstorm", p=round(storm_prob, 2))
    elif h.get("storm_other") or (storm_prob or 0) >= 0.05:
        add("storm", "caution", "storm_other", "Some forecasts show thunderstorms")
    elif (cape or 0) >= lim["cape_caution"]:
        add("storm", "caution", "unstable", f"Unstable air (CAPE {cape:.0f} J/kg)", cape=round(cape))

    vis = h.get("vis")
    if vis is not None and vis < lim["visibility_nogo"]:
        add("fog", "nogo", "fog", f"Thick fog: visibility {vis:.0f} m", vis=round(vis))

    status = "go"
    for lvl in checks.values():
        status = _worse(status, lvl)
    reasons.sort(key=lambda r: -LEVEL_ORDER[r["level"]])
    return status, checks, reasons


def _storm(m: Member, t: int, lim: dict) -> bool | None:
    """Does this forecast show a thunderstorm in the hour starting at t?"""
    code = _covering(t, m.ts, m.series.get("wcode"))
    cape = _interp_time(t, m.ts, m.series.get("cape"))
    rain = _covering(t, m.ts, m.series.get("rain"))
    if code is None and cape is None:
        return None
    if code is not None and round(code) in openmeteo.THUNDER_CODES:
        return True
    if cape is not None and (cape >= lim["cape_nogo"] or (cape >= lim["cape_caution"] and (rain or 0) >= lim["rain_nogo"])):
        return True
    return False


def _frozen(m: Member, t: int) -> bool:
    snow = _covering(t, m.ts, m.series.get("snow"))
    ptype = _covering(t, m.ts, m.series.get("ptype"))
    code = _covering(t, m.ts, m.series.get("wcode"))
    return ((snow or 0) > 0.05 or (ptype is not None and round(ptype) in FROZEN_PTYPES)
            or (code is not None and round(code) in {56, 57, 66, 67, 71, 73, 75, 77, 85, 86}))


# --- Snapshot ------------------------------------------------------------------------------------

def _r(x, nd=1):
    return None if x is None else round(x, nd)


def build(cfg: Config, latest: dict, now_ms: int) -> dict:
    """latest maps (member id, point) -> (fetched_ms, body). Returns the dashboard's JSON.

    Windy ids are windy:<name> with points 0-4; Open-Meteo ids are om:<name> / ens:<name>, point 0.
    """
    lim = cfg.limits
    rank = {mid: k for k, mid in enumerate(cfg.rank)}

    def usable(mid: str) -> bool:
        return not mid.startswith("windy:") or cfg.windy_decides

    def make(alpha_at=None) -> list[Member]:
        out = []
        for (mid, point), (_, body) in latest.items():
            if mid.startswith("windy:"):
                out.append(windy_member(mid, point, body, cfg, alpha_at))
            else:
                ts = [int(t) * 1000 for t in body.get("hourly", {}).get("time", [])]
                kind = "ens" if mid.startswith("ens:") else "det"
                for k, run in enumerate(openmeteo.runs(body)):
                    out.append(om_member(mid, k, run, ts, cfg, kind, alpha_at))
        return out

    members = make()
    # Forecasts with only a 10 m wind (Windy AROME, some ensembles) keep their own 10 m wind and
    # borrow the change of wind with height from the best model that has heights, hour by hour.
    dets = sorted([m for m in members if m.kind == "det" and usable(m.model)],
                  key=lambda m: (rank.get(m.model, 99), m.point))
    donor = next((m for m in dets if m.point == 0 and m.levels), None)
    if donor is not None:
        alphas = [None if p is None else shear_exponent(p, cfg.heights_m) for p in donor.profile]

        def alpha_at(t):
            return _interp_time(t, donor.ts, alphas)
        members = make(alpha_at)
    dets = sorted([m for m in members if m.kind == "det" and usable(m.model)],
                  key=lambda m: (rank.get(m.model, 99), m.point))
    site_dets = [m for m in dets if m.point == 0]
    shown_only = [m for m in members if m.kind == "det" and not usable(m.model) and m.point == 0]
    ens = [m for m in members if m.kind == "ens"]
    ens_size: dict[str, int] = {}
    for m in ens:
        ens_size[m.model] = ens_size.get(m.model, 0) + 1

    start = (now_ms // HOUR_MS) * HOUR_MS
    hours = []
    for k in range(cfg.horizon_hours):
        t = start + k * HOUR_MS
        day = sun_elevation(t + HOUR_MS // 2, cfg.site.lat, cfg.site.lon) > -0.833
        vals = [(m, b) for m in dets if (b := _interp_time(t, m.ts, m.band)) is not None]
        prim = next((m for m, _ in vals if m.point == 0), None)
        if prim is None:
            hours.append({"t": t, "day": day, "status": "nodata", "reasons": [], "checks": {}})
            continue
        j = bisect.bisect_left(prim.ts, t)
        if prim.ts[j] == t:
            profile, direction = prim.profile[j], prim.direction[j]
        else:  # between model steps: blend the two neighbouring profiles
            w = (t - prim.ts[j - 1]) / (prim.ts[j] - prim.ts[j - 1])
            a, b = prim.profile[j - 1], prim.profile[j]
            profile = [x + w * (y - x) for x, y in zip(a, b)]
            direction = prim.direction[j - 1 if w < 0.5 else j]
        bands = [b for _, b in vals]

        def best(name: str, how=_interp_time):
            """The field from the best model that has it: AROME HD has no storm codes, for one."""
            for m in site_dets:
                v = how(t, m.ts, m.series.get(name))
                if v is not None:
                    return v
            return None

        rain = best("rain", _covering)
        cape = best("cape")
        vis = best("vis")
        storm_flags = [(m, _storm(m, t, lim)) for m in site_dets]
        first = next(((m, s) for m, s in storm_flags if s is not None), (None, None))
        others_rain = [r for m in site_dets if (r := _covering(t, m.ts, m.series.get("rain"))) is not None]

        # Ensembles: each ensemble weighs the same, whatever its number of runs.
        wb, wr, ws = [], [], []
        for m in ens:
            b = _interp_time(t, m.ts, m.band)
            if b is None:
                continue
            w = 1.0 / ens_size[m.model]
            wb.append((b, w))
            r = _covering(t, m.ts, m.series.get("rain"))
            if r is not None:
                wr.append((r >= lim["rain_nogo"], w))
            s = _storm(m, t, lim)
            if s is not None:
                ws.append((s, w))
        h = {
            "t": t, "day": day, "primary": prim.model,
            "wind": band_mean(profile, cfg.heights_m),
            "wind_top": max(profile),
            "profile": [round(s, 2) for s in profile],
            "dir": _r(direction, 0),
            "n_members": len(bands),
            "wind_agree": sum(b >= lim["min_wind"] for b in bands) / len(bands),
            "rain": rain, "rain_hi": max(others_rain) if others_rain else None,
            "frozen": _frozen(prim, t),
            "cape": cape, "vis": vis, "temp": best("temp"),
            "storm": bool(first[1]),
            "storm_other": any(s for m, s in storm_flags if s and m is not first[0]),
            "models": [[cfg.label(m.model), _r(b)] for m, b in vals if m.point == 0]
                      + [[cfg.label(m.model) + " · Testing key", _r(b)] for m in shown_only
                         if (b := _interp_time(t, m.ts, m.band)) is not None],
        }
        if wb:
            total = sum(w for _, w in wb)
            p10, p50, p90 = _quantiles(wb, [0.1, 0.5, 0.9])
            h.update(wind_lo=p10, wind_p50=p50, wind_hi=p90, band="ensemble",
                     wind_prob=sum(w for b, w in wb if b >= lim["min_wind"]) / total,
                     n_runs=len(wb))
            if wr:
                h["rain_prob"] = sum(w for x, w in wr if x) / sum(w for _, w in wr)
            if ws:
                h["storm_prob"] = sum(w for x, w in ws if x) / sum(w for _, w in ws)
        else:
            h.update(wind_lo=min(bands), wind_hi=max(bands), band="models")
        status, checks, reasons = decide(h, lim)
        if cfg.daylight_only and not day:
            status = "night"
        h.update(status=status, checks=checks, reasons=reasons)
        for key in ("wind", "wind_lo", "wind_hi", "wind_p50", "wind_top", "temp"):
            if key in h:
                h[key] = _r(h[key])
        for key in ("rain", "rain_hi"):
            h[key] = _r(h[key], 2)
        for key in ("cape", "vis"):
            h[key] = _r(h[key], 0)
        for key in ("wind_agree", "wind_prob", "rain_prob", "storm_prob"):
            if key in h:
                h[key] = round(h[key], 2)
        hours.append(h)

    return {
        "generated": now_ms,
        "site": {"name": cfg.site.name, "lat": cfg.site.lat, "lon": cfg.site.lon,
                 "elevation_m": cfg.site.elevation_m},
        "heights": cfg.heights_m,
        "limits": lim,
        "daylight_only": cfg.daylight_only,
        "key_mode": cfg.key_mode,
        "windy_used": cfg.windy_decides,
        "hours": hours,
        "models": sources(cfg, latest, members, donor),
    }


def sources(cfg: Config, latest: dict, members: list[Member], donor: Member | None) -> list[dict]:
    """One row per model for the "where this comes from" table."""
    donor_label = cfg.label(donor.model) if donor else None
    rows = []

    def heights(ms: list[Member]) -> list:
        """Where the 10-150 m wind comes from, as [kind, ...] for the page to word in any language."""
        if not ms:
            return ["none"]
        m = ms[0]
        if m.model.startswith("windy:"):
            if m.levels:
                return ["levels"]
        elif m.levels:
            hs = [int(h) for h in m.heights]
            return ["measured", hs[0], hs[-1], hs[-1] < cfg.heights_m[-1]]
        return ["shear", donor_label] if donor_label and donor.model != m.model else ["law"]

    def heights_text(hf: list) -> str:
        return {"none": "–", "levels": "10 m + pressure levels",
                "law": "10 m + 1/7 law"}.get(hf[0]) or (
            f"measured {hf[1]}–{hf[2]} m" + (" + extended" if hf[3] else "") if hf[0] == "measured"
            else f"10 m + shear from {hf[1]}")

    for m in cfg.models:
        got = [(p, latest[(m.id, p)][0], latest[(m.id, p)][1]) for p in range(len(m.points(cfg.site)))
               if (m.id, p) in latest]
        ts = got[0][2].get("ts", []) if got else []
        mine = [x for x in members if x.model == m.id and x.point == 0]
        rows.append({
            "id": m.id, "store": store_key(m.id), "source": "Windy", "label": m.label,
            "resolution": m.resolution, "points": [len(got), len(m.points(cfg.site))],
            "fetched": max((g[1] for g in got), default=None), "until": _until(mine),
            "step_h": round((ts[1] - ts[0]) / HOUR_MS) if len(ts) > 1 else None,
            "hf": heights(mine), "heights_from": heights_text(heights(mine)), "used": cfg.windy_decides,
        })
    for m in cfg.om_models + cfg.ensembles:
        got = latest.get((m.id, 0))
        mine = [x for x in members if x.model == m.id]
        ts = mine[0].ts if mine else []
        rows.append({
            "id": m.id, "store": m.id, "source": "Open-Meteo ensemble" if m.ensemble else "Open-Meteo",
            "label": m.label, "resolution": m.resolution,
            "runs": len(mine),
            "fetched": got[0] if got else None, "until": _until(mine[:1]),
            "step_h": round((ts[1] - ts[0]) / HOUR_MS) if len(ts) > 1 else None,
            "hf": heights(mine), "heights_from": heights_text(heights(mine)), "used": True,
        })
    return rows


def _until(ms: list[Member]) -> int | None:
    if not ms:
        return None
    m = ms[0]
    idx = [i for i, b in enumerate(m.band) if b is not None]
    return m.ts[idx[-1]] if idx else None
