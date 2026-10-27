"""Windy Point Forecast API v2 client, plus a fake source for demo mode and tests."""

from __future__ import annotations

import json
import math
import random
import re
import urllib.error
import urllib.request

API_URL = "https://api.windy.com/api/point-forecast/v2"

# Everything useful for a go/no-go decision. A model that lacks a parameter just leaves it out.
PARAMETERS = [
    "wind", "windGust", "temp", "dewpoint", "rh", "pressure", "gh",
    "precip", "convPrecip", "snowPrecip", "ptype", "cape",
    "lclouds", "mclouds", "hclouds", "cbase", "visibility",
]
# Windy rejects the whole request if one parameter is not offered by the model, so each model
# gets only what it has (lists from the API's own error messages, October 2026).
MODEL_PARAMETERS = {
    "arome": ["wind", "windGust", "temp", "dewpoint", "rh", "pressure",
              "precip", "snowPrecip", "ptype", "cape", "lclouds", "mclouds", "hclouds"],
    "icon": ["wind", "windGust", "temp", "dewpoint", "rh", "pressure",
             "precip", "convPrecip", "snowPrecip", "ptype", "cape", "lclouds", "mclouds", "hclouds"],
    "gfs": ["wind", "windGust", "temp", "dewpoint", "rh", "gh", "pressure",
            "precip", "convPrecip", "snowPrecip", "ptype", "cape", "lclouds", "mclouds", "hclouds"],
}
# Wind, temp, dewpoint, rh and gh come at these levels. At a site a few hundred metres up the
# 1000 and 950 hPa levels are often underground; gh (or temperature and pressure) tells which are not.
LEVELS = ["surface", "1000h", "950h", "925h", "900h", "850h"]
_ALLOWED_RE = re.compile(r"Allowed parameters for model '[^']+': \[([^\]]*)\]")


def parameters_for(model: str) -> list[str]:
    return MODEL_PARAMETERS.get(model, PARAMETERS)


class WindyError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def fetch(key: str, model: str, lat: float, lon: float, timeout: float = 30.0) -> dict:
    """One API request. Returns the JSON body; raises WindyError on any failure.

    If Windy says a parameter is not offered, the model's list is narrowed to what it allows,
    so the next request succeeds (this one still counts against the budget and fails).
    """
    try:
        return _post(key, model, lat, lon, parameters_for(model), timeout)
    except WindyError as e:
        m = _ALLOWED_RE.search(str(e))
        if e.status == 400 and m:
            allowed = {p.strip() for p in m.group(1).split(",")}
            MODEL_PARAMETERS[model] = [p for p in parameters_for(model) if p in allowed]
        raise


def _post(key: str, model: str, lat: float, lon: float, parameters: list[str], timeout: float) -> dict:
    body = json.dumps({
        "lat": lat, "lon": lon, "model": model,
        "parameters": parameters, "levels": LEVELS, "key": key,
    }).encode()
    req = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "kitewind/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 204:
                raise WindyError(204, "model has none of the requested parameters here")
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise WindyError(e.code, detail) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise WindyError(0, str(e)) from None


# --- Fake source -----------------------------------------------------------------------------

# Steps and lengths as Windy served them on 2026-10-27.
_FAKE_STEPS = {"arome": (3, 48), "iconEu": (3, 117), "icon": (3, 171), "gfs": (3, 237)}
_FAKE_BIAS = {"arome": 0.0, "iconEu": 0.6, "icon": -0.5, "gfs": 1.1}


def fake_fetch(model: str, lat: float, lon: float, now_ms: int, elevation_m: float = 650) -> dict:
    """A plausible Windy-shaped response: a diurnal wind cycle, a front with rain, afternoon CAPE.

    Same shape and keys as the real API, so demo mode exercises the real processing code.
    """
    step_h, length_h = _FAKE_STEPS.get(model, (3, 120))
    rng = random.Random(f"{model}{lat:.2f}{lon:.2f}{now_ms // 3_600_000 // 6}")
    t0 = (now_ms // 3_600_000) * 3_600_000 - 3 * 3_600_000
    ts = [t0 + i * step_h * 3_600_000 for i in range(length_h // step_h + 1)]
    bias = _FAKE_BIAS.get(model, 0.0) + rng.uniform(-0.4, 0.4)
    out: dict = {"ts": ts, "units": {}}

    def put(key: str, unit: str | None, values: list):
        out[key] = values
        out["units"][key] = unit

    s10, d10, rain, cape = [], [], [], []
    for t in ts:
        hour_utc = (t // 3_600_000) % 24
        day = t / 86_400_000
        diurnal = math.sin((hour_utc - 9) / 24 * 2 * math.pi)        # peaks mid-afternoon
        synoptic = 1.8 * math.sin(day * 2 * math.pi / 3.1 + 1.0)      # ~3-day weather cycle
        s = max(0.3, 3.6 + 2.2 * diurnal + synoptic + bias + rng.gauss(0, 0.35))
        s10.append(s)
        d10.append((250 + 40 * math.sin(day * 2.1) + rng.gauss(0, 8)) % 360)
        front = math.exp(-((day % 3.1) - 2.2) ** 2 / 0.02)
        rain.append(max(0.0, 3.0 * front + rng.gauss(0, 0.05)) / 1000)  # m per 3 h
        cape.append(max(0.0, 700 * max(0, diurnal) * (0.4 + front) + rng.gauss(0, 30)))

    def uv(speed, direction):
        rad = math.radians(direction)
        return -speed * math.sin(rad), -speed * math.cos(rad)

    put("wind_u-surface", "m*s-1", [uv(s, d)[0] for s, d in zip(s10, d10)])
    put("wind_v-surface", "m*s-1", [uv(s, d)[1] for s, d in zip(s10, d10)])
    put("gust-surface", "m*s-1", [s * 1.55 + rng.uniform(0, 1) for s in s10])
    put("temp-surface", "K", [285 + 6 * math.sin(((t // 3_600_000) % 24 - 9) / 24 * 2 * math.pi) for t in ts])
    put("past3hprecip-surface", "m", rain)
    put("past3hconvprecip-surface", "m", [r * 0.3 for r in rain])
    put("past3hsnowprecip-surface", "m", [0.0 for _ in ts])
    put("ptype-surface", None, [1 if r > 0.0002 else 0 for r in rain])
    put("cape-surface", "J*kg-1", cape)
    put("lclouds-surface", "%", [min(100, r * 3e5 + 10) for r in rain])
    put("mclouds-surface", "%", [30 for _ in ts])
    put("hclouds-surface", "%", [20 for _ in ts])
    put("visibility-surface", "m", [24000 - min(20000, r * 6e6) for r in rain])
    put("cbase-surface", "m", [1200 - min(900, r * 3e5) for r in rain])
    put("pressure-surface", "Pa", [101600 for _ in ts])  # sea-level pressure
    # gh above sea level; wind grows with height (stronger at night aloft: low-level jet).
    for lev, gh, factor in [("1000h", 140, 1.0), ("950h", 560, 1.0), ("925h", 790, 1.45),
                            ("900h", 1010, 1.7), ("850h", 1480, 1.9)]:
        put(f"gh-{lev}", "m", [gh + rng.gauss(0, 8) for _ in ts])
        put(f"temp-{lev}", "K", [out["temp-surface"][i] - 0.0065 * (gh - elevation_m) for i in range(len(ts))])
        u, v = [], []
        for s, d, t in zip(s10, d10, ts):
            hour_utc = (t // 3_600_000) % 24
            jet = 1.0 + (0.25 if hour_utc < 7 or hour_utc > 21 else 0.0)
            uu, vv = uv(s * factor * jet + 0.5, d + 10)
            u.append(uu)
            v.append(vv)
        put(f"wind_u-{lev}", "m*s-1", u)
        put(f"wind_v-{lev}", "m*s-1", v)
    # Keep only what the real model offers, so demo mode goes through the same gaps.
    # Windy's AROME answers with nulls for every pressure level and for pressure.
    if model == "arome":
        for k in out:
            if k not in ("ts", "units") and (not k.endswith("-surface") or k == "pressure-surface"):
                out[k] = [None] * len(ts)
    allowed = set(parameters_for(model))
    for k in [k for k in out if k not in ("ts", "units")]:
        if _KEY_PARAM.get(k.split("-")[0]) not in allowed:
            out.pop(k)
            out["units"].pop(k)
    return out


_KEY_PARAM = {
    "wind_u": "wind", "wind_v": "wind", "gust": "windGust", "temp": "temp", "dewpoint": "dewpoint",
    "rh": "rh", "gh": "gh", "pressure": "pressure", "past3hprecip": "precip",
    "past3hconvprecip": "convPrecip", "past3hsnowprecip": "snowPrecip", "ptype": "ptype", "cape": "cape",
    "lclouds": "lclouds", "mclouds": "mclouds", "hclouds": "hclouds", "cbase": "cbase",
    "visibility": "visibility",
}
