"""Open-Meteo forecast and ensemble APIs (free, no key), plus a fake source for demo mode and tests.

Deterministic models give wind at real heights (AROME: 10-200 m). Ensembles give 18-51 runs of
one model; each run comes back as <variable>_memberNN next to the control run <variable>.
"""

from __future__ import annotations

import json
import math
import random
import urllib.error
import urllib.parse
import urllib.request

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

DET_HEIGHTS = [10, 20, 50, 80, 100, 120, 150, 180, 200]
ENS_HEIGHTS = [10, 80, 100, 120]
COMMON = ["precipitation", "snowfall", "cape", "weather_code"]
DET_VARS = ([f"wind_speed_{h}m" for h in DET_HEIGHTS]
            + ["wind_direction_10m", "wind_direction_100m", "visibility", "temperature_2m"] + COMMON)
ENS_VARS = [f"wind_speed_{h}m" for h in ENS_HEIGHTS] + ["wind_direction_10m"] + COMMON

THUNDER_CODES = {95, 96, 99}  # WMO weather codes: thunderstorm, with hail


class OMError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status


def fetch(model: str, lat: float, lon: float, ensemble: bool, days: int = 4, timeout: float = 60.0) -> dict:
    params = {
        "latitude": lat, "longitude": lon, "models": model,
        "hourly": ",".join(ENS_VARS if ensemble else DET_VARS),
        "wind_speed_unit": "ms", "timeformat": "unixtime", "forecast_days": days,
    }
    url = (ENSEMBLE_URL if ensemble else FORECAST_URL) + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "kitewind/1.5"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            reason = json.loads(e.read().decode()).get("reason", "")
        except ValueError:
            reason = ""
        raise OMError(e.code, reason[:300]) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise OMError(0, str(e)) from None
    if body.get("error"):
        raise OMError(400, str(body.get("reason"))[:300])
    return body


def runs(body: dict) -> list[dict[str, list]]:
    """Split a response into runs: [{variable: values}], control run first."""
    hourly = body.get("hourly", {})
    out: dict[str, dict[str, list]] = {}
    for key, vals in hourly.items():
        if key == "time":
            continue
        if "_member" in key:
            var, member = key.split("_member", 1)
        else:
            var, member = key, "00"
        out.setdefault(member, {})[var] = vals
    return [out[k] for k in sorted(out)]


# --- Fake source -----------------------------------------------------------------------------

_FAKE_MEMBERS = {"ecmwf_ifs025": 51, "icon_eu": 40, "gfs025": 31, "ukmo_global_ensemble_20km": 18}
_FAKE_LEN = {"meteofrance_arome_france_hd": 51, "meteofrance_arome_france": 51}


def fake_fetch(model: str, ensemble: bool, now_ms: int, days: int = 4) -> dict:
    """Same shape as the real API: a diurnal cycle, a ~3-day weather cycle, a rainy front."""
    rng = random.Random(f"{model}{ensemble}{now_ms // 3_600_000 // 6}")
    t0 = (now_ms // 86_400_000) * 86_400_000
    n = days * 24
    ts = [t0 // 1000 + 3600 * i for i in range(n)]
    valid = _FAKE_LEN.get(model, n)
    bias = rng.uniform(-0.6, 0.6)
    heights = ENS_HEIGHTS if ensemble else DET_HEIGHTS
    members = _FAKE_MEMBERS.get(model, 1) if ensemble else 1
    hourly: dict = {"time": ts}
    for m in range(members):
        suffix = "" if m == 0 else f"_member{m:02d}"
        jitter = [rng.gauss(0, 0.6 + i / n * 2.0) for i in range(n)]  # spread grows with lead time
        cols: dict[str, list] = {f"wind_speed_{h}m": [] for h in heights}
        cols.update({k: [] for k in ["wind_direction_10m", "precipitation", "snowfall", "cape", "weather_code"]})
        for i, t in enumerate(ts):
            if i >= valid:
                for k in cols:
                    cols[k].append(None)
                continue
            hour = (t // 3600) % 24
            day = t / 86400
            diurnal = math.sin((hour - 9) / 24 * 2 * math.pi)
            synoptic = 1.8 * math.sin(day * 2 * math.pi / 3.1 + 1.0)
            s10 = max(0.2, 3.4 + 2.2 * diurnal + synoptic + bias + jitter[i])
            alpha = 0.12 if diurnal > 0 else 0.3  # mixed by day, sheared at night
            for h in heights:
                cols[f"wind_speed_{h}m"].append(round(s10 * (h / 10) ** alpha, 2))
            cols["wind_direction_10m"].append(round((250 + 40 * math.sin(day * 2.1)) % 360))
            front = math.exp(-((day % 3.1) - 2.2) ** 2 / 0.02)
            rain = max(0.0, 1.2 * front + rng.gauss(0, 0.15) * front)
            cols["precipitation"].append(round(rain, 2))
            cols["snowfall"].append(0.0)
            cape = max(0.0, 700 * max(0, diurnal) * (0.4 + front) + rng.gauss(0, 40))
            cols["cape"].append(round(cape))
            cols["weather_code"].append(95 if cape > 900 and rain > 0.2 else 61 if rain > 0.1 else 1)
        if not ensemble:
            cols["wind_direction_100m"] = [None if v is None else (v + 15) % 360 for v in cols["wind_direction_10m"]]
            cols["visibility"] = [None if p is None else 24000 - min(23000, p * 15000) for p in cols["precipitation"]]
            cols["temperature_2m"] = [None if v is None else 14.0 for v in cols["wind_direction_10m"]]
            if model == "meteofrance_arome_france_hd":  # like the real one: no storm codes, no 120 m+
                for k in ["weather_code", "visibility", "wind_speed_120m", "wind_speed_150m",
                          "wind_speed_180m", "wind_speed_200m"]:
                    cols[k] = [None] * n
        for k, v in cols.items():
            hourly[k + suffix] = v
    return {"latitude": 40.42, "longitude": -3.7, "elevation": 650.0, "hourly": hourly}
