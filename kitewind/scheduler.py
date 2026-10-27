"""Fetch every forecast on its own interval.

Windy: every (model, point), never above the daily request cap. Windy counts requests per day;
we count every Windy attempt (failed ones too) since 00:00 UTC.
Open-Meteo: one request per model; free, no key, well below its fair-use limits.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from . import forecast, openmeteo, windy
from .config import Config
from .publish import Publisher
from .store import Store

log = logging.getLogger("kitewind")
DAY_MS = 86_400_000
RETRY_MS = 10 * 60_000
KEEP_ENSEMBLES_MS = 14 * DAY_MS  # raw ensemble responses are large; keep two weeks


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Job:
    source: str          # windy, om or ens
    member_id: str       # windy:<name>, om:<name>, ens:<name>
    model: str           # the name the API knows
    store: str           # the name in the database
    point: int
    lat: float
    lon: float
    every_ms: int
    due_ms: int = 0


class Scheduler:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.snapshot: dict | None = None
        self.snapshot_lock = threading.Lock()
        self.jobs: list[Job] = []
        site = cfg.site
        for m in cfg.models:
            for p, (lat, lon) in enumerate(m.points(site)):
                self.jobs.append(Job("windy", m.id, m.name, forecast.store_key(m.id), p, lat, lon,
                                     m.every_minutes * 60_000))
        for m in cfg.om_models:
            self.jobs.append(Job("om", m.id, m.name, m.id, 0, site.lat, site.lon, cfg.om_every_minutes * 60_000))
        for m in cfg.ensembles:
            self.jobs.append(Job("ens", m.id, m.name, m.id, 0, site.lat, site.lon, cfg.ens_every_minutes * 60_000))
        t = now_ms()
        for k, job in enumerate(self.jobs):
            last = store.last_attempt(job.store, job.lat, job.lon)
            # Spread first fetches a few seconds apart; resume the cadence after a restart.
            job.due_ms = t + k * 2000 if last is None else max(t, last + job.every_ms)
        self._stop = threading.Event()
        self._last_prune = 0
        self.publisher = Publisher(cfg.data_dir, cfg.publish, cfg.demo)
        if self.publisher.active:
            log.info("publishing to %s every %d min", cfg.publish.get("repo"), self.publisher.every_ms // 60_000)

    # --- budget (Windy only) ---
    def used_today(self) -> int:
        t = now_ms()
        return self.store.count_since(t - t % DAY_MS, windy_only=True)

    # --- fetching ---
    def run_job(self, job: Job) -> None:
        t = now_ms()
        try:
            if job.source == "windy":
                if self.cfg.demo:
                    body = windy.fake_fetch(job.model, job.lat, job.lon, t, self.cfg.site.elevation_m)
                else:
                    body = windy.fetch(self.cfg.api_key, job.model, job.lat, job.lon)
            elif self.cfg.demo:
                body = openmeteo.fake_fetch(job.model, job.source == "ens", t, self.cfg.forecast_days)
            else:
                body = openmeteo.fetch(job.model, job.lat, job.lon, job.source == "ens", self.cfg.forecast_days)
            self.store.add(t, job.store, job.lat, job.lon, 200, body=body)
            job.due_ms = t + job.every_ms
        except (windy.WindyError, openmeteo.OMError) as e:
            log.warning("%s point %d: %s", job.member_id, job.point, e)
            self.store.add(t, job.store, job.lat, job.lon, e.status, error=str(e)[:500])
            # A 400/401/403 will not fix itself quickly; anything else gets a sooner retry.
            # Except a Windy parameter list that was just corrected: the next request will work.
            if "Allowed parameters" in str(e):
                job.due_ms = t + 60_000
            elif e.status in (400, 401, 403, 204):
                job.due_ms = t + job.every_ms
            else:
                job.due_ms = t + min(job.every_ms, RETRY_MS)

    def windy_active(self) -> bool:
        return bool(self.cfg.api_key) or self.cfg.demo

    def rebuild(self) -> None:
        latest = {}
        for job in self.jobs:
            got = self.store.latest_ok(job.store, job.lat, job.lon)
            if got is not None:
                latest[(job.member_id, job.point)] = got
        snap = forecast.build(self.cfg, latest, now_ms())
        snap["budget"] = {"used_today": self.used_today(), "cap": self.cfg.daily_cap,
                          "planned": self.cfg.planned_requests_per_day}
        for m in snap["models"]:
            err = self.store.last_error(m["store"])
            m["error"] = None if err is None else {"t": err[0], "text": err[1]}
        with self.snapshot_lock:
            self.snapshot = snap

    def tick(self) -> bool:
        """Run every due job (Windy within its budget). Returns True if anything was fetched."""
        fetched = False
        for job in sorted(self.jobs, key=lambda j: j.due_ms):
            if job.due_ms > now_ms():
                break
            if job.source == "windy":
                if not self.windy_active():
                    continue
                if self.used_today() >= self.cfg.daily_cap:
                    t = now_ms()
                    log.warning("Windy daily cap of %d requests reached; waiting for 00:00 UTC", self.cfg.daily_cap)
                    for j in self.jobs:
                        if j.source == "windy":
                            j.due_ms = max(j.due_ms, t - t % DAY_MS + DAY_MS + 5000)
                    continue
            self.run_job(job)
            fetched = True
        return fetched

    def prune(self) -> None:
        if now_ms() - self._last_prune > DAY_MS:
            n = self.store.drop_bodies("ens:%", now_ms() - KEEP_ENSEMBLES_MS)
            if n:
                log.info("dropped %d old ensemble responses", n)
            self._last_prune = now_ms()

    def loop(self) -> None:
        last_rebuild = 0
        while not self._stop.is_set():
            try:
                fetched = self.tick()
                # Rebuild after new data, and every 5 minutes so "now" moves on.
                if fetched or now_ms() - last_rebuild > 300_000:
                    self.rebuild()
                    last_rebuild = now_ms()
                if self.publisher.due(now_ms()) and self.snapshot:
                    with self.snapshot_lock:
                        snap = self.snapshot
                    self.publisher.publish(snap, now_ms())
                self.prune()
            except Exception:  # keep the service alive whatever happens; the log says why
                log.exception("scheduler tick failed")
            self._stop.wait(15)

    def start(self) -> threading.Thread:
        self.rebuild()
        th = threading.Thread(target=self.loop, name="scheduler", daemon=True)
        th.start()
        return th

    def stop(self) -> None:
        self._stop.set()
