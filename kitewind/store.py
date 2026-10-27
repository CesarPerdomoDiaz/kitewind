"""Every API request is kept in SQLite: the request log doubles as the daily budget counter."""

from __future__ import annotations

import json
import sqlite3
import threading
import zlib
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY,
    t_ms INTEGER NOT NULL,          -- when the request was sent (UTC ms)
    model TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    status INTEGER NOT NULL,        -- HTTP status, 0 for network errors
    error TEXT,
    body BLOB                       -- zlib-compressed JSON, only for successful requests
);
CREATE INDEX IF NOT EXISTS requests_by_job ON requests(model, lat, lon, t_ms);
CREATE INDEX IF NOT EXISTS requests_by_time ON requests(t_ms);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._db.close()

    def add(self, t_ms: int, model: str, lat: float, lon: float, status: int,
            body: dict | None = None, error: str | None = None) -> None:
        blob = zlib.compress(json.dumps(body).encode()) if body is not None else None
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO requests (t_ms, model, lat, lon, status, error, body) VALUES (?,?,?,?,?,?,?)",
                (t_ms, model, lat, lon, status, error, blob))

    def count_since(self, t_ms: int, windy_only: bool = False) -> int:
        """Requests since t_ms. Windy rows are the ones without a source prefix (om:, ens:)."""
        sql = "SELECT COUNT(*) FROM requests WHERE t_ms >= ?" + (" AND model NOT LIKE '%:%'" if windy_only else "")
        with self._lock:
            return self._db.execute(sql, (t_ms,)).fetchone()[0]

    def drop_bodies(self, model_like: str, before_ms: int) -> int:
        """Forget the stored responses (keep the request log) for old requests of some models."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE requests SET body = NULL WHERE model LIKE ? AND t_ms < ? AND body IS NOT NULL",
                (model_like, before_ms))
            return cur.rowcount

    def last_attempt(self, model: str, lat: float, lon: float) -> int | None:
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(t_ms) FROM requests WHERE model=? AND lat=? AND lon=?", (model, lat, lon)).fetchone()
        return row[0]

    def latest_ok(self, model: str, lat: float, lon: float) -> tuple[int, dict] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT t_ms, body FROM requests WHERE model=? AND lat=? AND lon=? AND body IS NOT NULL "
                "ORDER BY t_ms DESC LIMIT 1", (model, lat, lon)).fetchone()
        if row is None:
            return None
        return row[0], json.loads(zlib.decompress(row[1]))

    def last_error(self, model: str) -> tuple[int, str] | None:
        """The latest failed request for a model, if it came after its latest success."""
        with self._lock:
            row = self._db.execute(
                "SELECT t_ms, status, error FROM requests WHERE model=? ORDER BY t_ms DESC LIMIT 1",
                (model,)).fetchone()
        if row is None or row[1] == 200:
            return None
        return row[0], f"HTTP {row[1]}: {row[2]}" if row[1] else str(row[2])
