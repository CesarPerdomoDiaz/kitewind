"""Load the configuration.

config.toml (in the repository) holds the defaults and an example site. A config.local.toml in
the data folder, if present, overrides any of it: your own site, where to publish, and so on.
Secrets come from environment variables, or from a secrets.env file in the data folder.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Site:
    name: str
    lat: float
    lon: float
    elevation_m: float


@dataclass
class Model:
    """A Windy model. Its member id is windy:<name>; the database keeps the bare name."""
    name: str
    label: str
    resolution: str
    every_minutes: int
    neighbours_deg: float = 0.0

    @property
    def id(self) -> str:
        return f"windy:{self.name}"

    def points(self, site: Site) -> list[tuple[float, float]]:
        """Centre first, then N, S, E, W. Windy rounds to 0.01 deg, so we do too."""
        pts = [(site.lat, site.lon)]
        d = self.neighbours_deg
        if d > 0:
            pts += [(site.lat + d, site.lon), (site.lat - d, site.lon),
                    (site.lat, site.lon + d), (site.lat, site.lon - d)]
        return [(round(a, 2), round(b, 2)) for a, b in pts]

    def requests_per_day(self, site: Site) -> int:
        return len(self.points(site)) * (24 * 60 // self.every_minutes)


@dataclass
class OMModel:
    """An Open-Meteo model: deterministic (om:<name>) or ensemble (ens:<name>)."""
    name: str
    label: str
    resolution: str
    ensemble: bool = False

    @property
    def id(self) -> str:
        return f"{'ens' if self.ensemble else 'om'}:{self.name}"


@dataclass
class Config:
    site: Site
    heights_m: list[float]
    daylight_only: bool
    horizon_hours: int
    limits: dict[str, float]
    daily_cap: int
    models: list[Model]                 # Windy
    host: str
    port: int
    data_dir: Path
    om_models: list[OMModel] = field(default_factory=list)
    ensembles: list[OMModel] = field(default_factory=list)
    om_every_minutes: int = 30
    ens_every_minutes: int = 60
    forecast_days: int = 4
    rank: list[str] = field(default_factory=list)
    use_windy_testing: bool = False
    publish: dict = field(default_factory=dict)
    notify: dict = field(default_factory=dict)
    api_key: str | None = None
    demo: bool = False

    @property
    def planned_requests_per_day(self) -> int:
        return sum(m.requests_per_day(self.site) for m in self.models)

    @property
    def key_mode(self) -> str:
        if self.demo:
            return "demo"
        if not self.api_key:
            return "none"
        return os.environ.get("WINDY_KEY_TIER", "testing")

    @property
    def windy_decides(self) -> bool:
        """Windy data takes part in decisions only when it is real (or the user insists)."""
        return self.key_mode in ("professional", "demo") or (self.key_mode == "testing" and self.use_windy_testing)

    def label(self, member_id: str) -> str:
        for m in self.models + self.om_models + self.ensembles:
            if m.id == member_id:
                return m.label
        return member_id


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _merge(base: dict, over: dict) -> dict:
    """Tables merge key by key; anything else (numbers, lists of models) is replaced."""
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(config_path: Path, data_dir: Path, demo: bool = False) -> Config:
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    local = data_dir / "config.local.toml"
    if local.exists():
        raw = _merge(raw, tomllib.loads(local.read_text(encoding="utf-8")))
    site = Site(**raw["site"])
    op = raw.get("operation", {})
    windy = raw.get("windy", {})
    om = raw.get("openmeteo", {})
    secrets = read_env_file(data_dir / "secrets.env")
    for k, v in secrets.items():
        os.environ.setdefault(k, v)
    om_models = [OMModel(**m) for m in raw.get("openmeteo_models", [])]
    ensembles = [OMModel(**m, ensemble=True) for m in raw.get("ensembles", [])]
    models = [Model(**m) for m in raw.get("windy_models", raw.get("models", []))]
    rank = raw.get("decision", {}).get("rank") or [m.id for m in om_models + models]
    return Config(
        site=site,
        heights_m=[float(h) for h in op.get("heights_m", [10, 25, 50, 75, 100, 125, 150])],
        daylight_only=bool(op.get("daylight_only", True)),
        horizon_hours=int(op.get("horizon_hours", 72)),
        limits={k: float(v) for k, v in raw["limits"].items()},
        daily_cap=int(windy.get("daily_cap", raw.get("budget", {}).get("daily_cap", 490))),
        models=models,
        host=raw.get("server", {}).get("host", "127.0.0.1"),
        port=int(raw.get("server", {}).get("port", 8740)),
        data_dir=data_dir,
        om_models=om_models,
        ensembles=ensembles,
        om_every_minutes=int(om.get("every_minutes", 30)),
        ens_every_minutes=int(om.get("ensemble_every_minutes", 60)),
        forecast_days=int(om.get("forecast_days", 4)),
        rank=list(rank),
        use_windy_testing=bool(windy.get("use_testing_data", False)),
        publish=dict(raw.get("publish", {})),
        notify=dict(raw.get("notify", {})),
        api_key=os.environ.get("WINDY_API_KEY") or None,
        demo=demo,
    )
