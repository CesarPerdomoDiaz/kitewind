"""Telegram alerts: tell the team as soon as a flyable window appears in the forecast.

A window is a run of consecutive GO hours (daylight only) at least min_hours long. Each window is
announced once. Afterwards the team only hears about it again if it moves or changes length by
change_hours or more, if it disappears from cancel_after forecast updates in a row, or on the
morning of the day (optional reminder). What has been announced is kept in notify_state.json.

The bot token comes from the TELEGRAM_BOT_TOKEN environment variable (or secrets.env);
the chat to post to is chat_id under [notify].
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("kitewind")
HOUR = 3_600_000
API = "https://api.telegram.org/bot{token}/{method}"

DAYS = {"es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
        "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
WORDS = {
    "es": {"new": "🪁 <b>Ventana apta</b>", "update": "🔄 <b>Cambia la ventana</b>", "cancel": "❌ <b>Se cancela la ventana</b>",
           "today": "☀️ <b>Hoy hay ventana apta</b>", "now": "ahora", "today_w": "hoy", "tomorrow": "mañana",
           "wind": "Viento 10–150 m", "likely": "probable", "chance": "probabilidad de ≥ {m} m/s",
           "dry": "Sin lluvia ni tormentas previstas.", "was": "antes", "why": "Motivo",
           "causes": {"wind": "falta viento", "rain": "lluvia", "storm": "tormenta", "fog": "niebla", "other": "el pronóstico cambió"},
           "test": "✅ Kite Wind está conectado a este canal. Aquí avisará cuando haya una ventana apta para volar."},
    "en": {"new": "🪁 <b>Flyable window</b>", "update": "🔄 <b>Window changed</b>", "cancel": "❌ <b>Window called off</b>",
           "today": "☀️ <b>Flyable window today</b>", "now": "now", "today_w": "today", "tomorrow": "tomorrow",
           "wind": "Wind 10–150 m", "likely": "likely", "chance": "chance of ≥ {m} m/s",
           "dry": "No rain or thunderstorms forecast.", "was": "was", "why": "Reason",
           "causes": {"wind": "not enough wind", "rain": "rain", "storm": "thunderstorm", "fog": "fog", "other": "the forecast changed"},
           "test": "✅ Kite Wind is connected to this channel. It will post here when there is a window to fly."},
}


def local_time(t_ms: int, offset_h: float = 1.0, eu_summer_time: bool = True) -> datetime:
    """Local wall-clock time without a timezone database (Windows Python has none built in).

    EU rule: summer time from the last Sunday of March to the last Sunday of October, 01:00 UTC.
    """
    utc = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc)
    off = offset_h
    if eu_summer_time:
        def last_sunday(month: int) -> datetime:
            d = datetime(utc.year, month, 31, 1, tzinfo=timezone.utc)
            return d - timedelta(days=(d.weekday() + 1) % 7)
        if last_sunday(3) <= utc < last_sunday(10):
            off += 1
    return (utc + timedelta(hours=off)).replace(tzinfo=None)


def find_windows(hours: list[dict], min_hours: int) -> list[dict]:
    """Runs of consecutive GO hours, with what the team needs to know about each."""
    out, run = [], []
    for h in hours + [{"status": "end"}]:
        if h.get("status") == "go":
            run.append(h)
            continue
        if len(run) >= min_hours:
            probs = [x["wind_prob"] for x in run if x.get("wind_prob") is not None]
            out.append({
                "start": run[0]["t"], "end": run[-1]["t"] + HOUR,
                "wind": round(sum(x["wind"] for x in run) / len(run), 1),
                "lo": round(min(x.get("wind_lo", x["wind"]) for x in run), 1),
                "hi": round(max(x.get("wind_hi", x["wind"]) for x in run), 1),
                "prob": round(sum(probs) / len(probs), 2) if probs else None,
            })
        run = []
    return out


def _overlap(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


class Notifier:
    def __init__(self, data_dir: Path, settings: dict, page_url: str = "", demo: bool = False):
        self.state_path = data_dir / "notify_state.json"
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = str(settings.get("chat_id", "")).strip()
        self.min_hours = int(settings.get("min_hours", 2))
        self.change_ms = int(float(settings.get("change_hours", 2)) * HOUR)
        self.cancel_after = int(settings.get("cancel_after", 2))
        self.morning_hour = int(settings.get("morning_hour", 8))
        self.langs = [x for x in settings.get("languages", ["es", "en"]) if x in WORDS] or ["en"]
        self.offset_h = float(settings.get("utc_offset_h", 1))
        self.eu_dst = bool(settings.get("eu_summer_time", True))
        self.page_url = settings.get("page_url", "") or page_url
        self.active = bool(self.token and self.chat_id) and not demo
        self.state = self._load()

    # --- state ---
    def _load(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"windows": [], "version": None}

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    # --- sending ---
    def call(self, method: str, payload: dict | None = None) -> dict:
        req = urllib.request.Request(API.format(token=self.token, method=method),
                                     data=json.dumps(payload or {}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            raise RuntimeError(f"Telegram {method}: HTTP {e.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RuntimeError(f"Telegram {method}: {e}") from None

    def send(self, text: str) -> bool:
        try:
            self.call("sendMessage", {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                      "disable_web_page_preview": True})
            return True
        except RuntimeError as e:
            log.warning("alert not sent: %s", e)
            return False

    # --- wording ---
    def _when(self, w: dict, lang: str, now_ms: int, with_day: bool = True) -> str:
        a = local_time(w["start"], self.offset_h, self.eu_dst)
        b = local_time(w["end"], self.offset_h, self.eu_dst)
        today = local_time(now_ms, self.offset_h, self.eu_dst).date()
        t = WORDS[lang]
        if not with_day:
            day = ""
        elif a.date() == today:
            day = t["today_w"]
        elif a.date() == today + timedelta(days=1):
            day = t["tomorrow"]
        else:
            day = f"{DAYS[lang][a.weekday()]} {a.day}"
        start = t["now"] if w["start"] <= now_ms else f"{a:%H:%M}"
        span = f"{start}–{b:%H:%M} ({round((w['end'] - max(w['start'], now_ms)) / HOUR)} h)"
        return f"{day}, {span}" if day else span

    def _num(self, x: float, lang: str) -> str:
        s = f"{x:.1f}"
        return s.replace(".", ",") if lang == "es" else s

    def _details(self, w: dict, lang: str, min_wind: float) -> str:
        t = WORDS[lang]
        line = f"{t['wind']}: {self._num(w['wind'], lang)} m/s ({t['likely']} {self._num(w['lo'], lang)}–{self._num(w['hi'], lang)})"
        if w.get("prob") is not None:
            line += f" · {round(w['prob'] * 100)} % {t['chance'].format(m=self._num(min_wind, lang))}"
        return line + "\n" + t["dry"]

    def message(self, kind: str, w: dict, now_ms: int, min_wind: float, old: dict | None = None, cause: str = "other") -> str:
        parts = []
        for lang in self.langs:
            t = WORDS[lang]
            head = f"{t[kind]}: {self._when(w, lang, now_ms, with_day=kind != 'today')}"
            if kind == "update" and old:
                head += f"\n<i>{t['was']}: {self._when(old, lang, now_ms)}</i>"
            body = f"{t['why']}: {t['causes'].get(cause, t['causes']['other'])}" if kind == "cancel" else self._details(w, lang, min_wind)
            parts.append(f"{head}\n{body}")
        if self.page_url:
            parts.append(self.page_url)
        return "\n\n".join(parts)

    # --- the check, run after every forecast rebuild ---
    def check(self, snap: dict, now_ms: int) -> None:
        if not self.active or not snap or not snap.get("hours"):
            return
        min_wind = snap["limits"]["min_wind"]
        # Only a new forecast can create, move or cancel windows.
        version = max((m.get("fetched") or 0) for m in snap.get("models", []) if m.get("used", True)) if snap.get("models") else 0
        if version and version != self.state.get("version"):
            if self._compare(snap, now_ms, min_wind):
                self.state["version"] = version
                self._save()
        self._remind(now_ms, min_wind)

    def _cause(self, snap: dict, w: dict) -> str:
        counts: dict[str, int] = {}
        for h in snap["hours"]:
            if w["start"] <= h["t"] < w["end"]:
                for k, lvl in (h.get("checks") or {}).items():
                    if lvl != "go":
                        counts[k] = counts.get(k, 0) + 1
        return max(counts, key=counts.get) if counts else "other"

    def _compare(self, snap: dict, now_ms: int, min_wind: float) -> bool:
        current = [w for w in find_windows(snap["hours"], self.min_hours) if w["end"] > now_ms]
        kept, ok, matched = [], True, set()
        for w in self.state["windows"]:
            if w["end"] <= now_ms:
                continue  # over: forget it quietly
            same = [c for c in current if _overlap(c, w)]
            if same:
                c = max(same, key=lambda x: x["end"] - x["start"])
                matched.update(id(x) for x in same)
                moved = abs(c["start"] - w["start"]) >= self.change_ms or abs(c["end"] - w["end"]) >= self.change_ms
                if moved and not self.send(self.message("update", c, now_ms, min_wind, old=w)):
                    ok = False
                    kept.append(w)
                    continue
                kept.append({**c, "missed": 0, "reminded": w.get("reminded")} if moved
                            else {**w, "missed": 0, "prob": c["prob"], "wind": c["wind"]})
            elif w.get("missed", 0) + 1 >= self.cancel_after:
                if not self.send(self.message("cancel", w, now_ms, min_wind, cause=self._cause(snap, w))):
                    ok = False
                    kept.append(w)
            else:
                kept.append({**w, "missed": w.get("missed", 0) + 1})
        now_local = local_time(now_ms, self.offset_h, self.eu_dst)
        for c in current:
            if id(c) in matched:
                continue
            if self.send(self.message("new", c, now_ms, min_wind)):
                # Announced on its own day after the reminder hour: that counts as the reminder.
                same_day = local_time(c["start"], self.offset_h, self.eu_dst).date() == now_local.date()
                reminded = now_local.date().isoformat() if same_day and now_local.hour >= self.morning_hour else None
                kept.append({**c, "missed": 0, "reminded": reminded})
            else:
                ok = False
        self.state["windows"] = sorted(kept, key=lambda w: w["start"])
        if not ok:
            self._save()
        return ok

    def _remind(self, now_ms: int, min_wind: float) -> None:
        if self.morning_hour < 0:
            return
        now = local_time(now_ms, self.offset_h, self.eu_dst)
        if not (self.morning_hour <= now.hour < self.morning_hour + 3):
            return
        today = now.date().isoformat()
        changed = False
        for w in self.state["windows"]:
            start = local_time(w["start"], self.offset_h, self.eu_dst)
            if start.date().isoformat() != today or w.get("reminded") == today or w["end"] <= now_ms or w.get("missed"):
                continue
            if self.send(self.message("today", w, now_ms, min_wind)):
                w["reminded"] = today
                changed = True
        if changed:
            self._save()

    # --- setup helpers ---
    def chats(self) -> list[tuple[str, str, str]]:
        """Chats the bot has been added to or seen recently: (id, type, title)."""
        res = self.call("getUpdates", {"allowed_updates": ["my_chat_member", "channel_post", "message"]})
        seen: dict[str, tuple[str, str, str]] = {}
        for u in res.get("result", []):
            for key in ("my_chat_member", "channel_post", "message"):
                chat = (u.get(key) or {}).get("chat")
                if chat:
                    seen[str(chat["id"])] = (str(chat["id"]), chat.get("type", ""),
                                             chat.get("title") or chat.get("username") or "")
        return list(seen.values())

    def test(self) -> bool:
        return self.send("\n\n".join(WORDS[lang]["test"] for lang in self.langs)
                         + (f"\n\n{self.page_url}" if self.page_url else ""))


def now_ms() -> int:
    return int(time.time() * 1000)
