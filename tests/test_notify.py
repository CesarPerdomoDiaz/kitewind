import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from kitewind import notify

HOUR = 3_600_000
DAY0 = 1_793_059_200_000  # 2026-10-27 00:00 UTC (Tuesday)


def hours(go_from: int, go_to: int, n: int = 72, start: int = DAY0) -> list[dict]:
    """n hours from start; GO between the given hour offsets, NO-GO (not enough wind) elsewhere."""
    out = []
    for k in range(n):
        go = go_from <= k < go_to
        out.append({"t": start + k * HOUR, "status": "go" if go else "nogo", "wind": 6.0 if go else 3.0,
                    "wind_lo": 5.0, "wind_hi": 7.5, "wind_prob": 0.7 if go else 0.1,
                    "checks": {"wind": "go" if go else "nogo", "rain": "go", "storm": "go", "fog": "go"}})
    return out


def snap(hs: list[dict], version: int) -> dict:
    return {"hours": hs, "limits": {"min_wind": 5.0}, "models": [{"fetched": version, "used": True}]}


class FakeNotifier(notify.Notifier):
    def __init__(self, data_dir, **settings):
        os.environ["TELEGRAM_BOT_TOKEN"] = "fake"
        try:
            super().__init__(Path(data_dir), {"chat_id": "-100", "morning_hour": -1, **settings})
        finally:
            del os.environ["TELEGRAM_BOT_TOKEN"]
        self.sent: list[str] = []
        self.fail = False

    def call(self, method, payload=None):
        if self.fail:
            raise RuntimeError("down")
        self.sent.append(payload["text"])
        return {"ok": True}


class LocalTimeTests(unittest.TestCase):
    def test_winter_and_summer_time(self):
        self.assertEqual(notify.local_time(DAY0 + 12 * HOUR).hour, 13)            # 27 October: winter
        summer = int(datetime(2026, 7, 1, 12, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(notify.local_time(summer).hour, 14)                       # 1 July: summer


class WindowTests(unittest.TestCase):
    def test_find_windows(self):
        ws = notify.find_windows(hours(10, 15), 2)
        self.assertEqual(len(ws), 1)
        self.assertEqual((ws[0]["start"], ws[0]["end"]), (DAY0 + 10 * HOUR, DAY0 + 15 * HOUR))
        self.assertEqual(ws[0]["wind"], 6.0)
        self.assertEqual(notify.find_windows(hours(10, 11), 2), [])  # one hour is not enough


class AlertFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.n = FakeNotifier(self.tmp.name, page_url="https://example.org/")
        self.now = DAY0 + 8 * HOUR

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_window_announced_once(self):
        self.n.check(snap(hours(34, 39), 1), self.now)
        self.assertEqual(len(self.n.sent), 1)
        msg = self.n.sent[0]
        self.assertIn("Ventana apta", msg)
        self.assertIn("mañana, 11:00–16:00 (5 h)", msg)
        self.assertIn("tomorrow, 11:00–16:00 (5 h)", msg)
        self.assertIn("https://example.org/", msg)
        self.n.check(snap(hours(34, 39), 1), self.now + 300_000)   # same forecast again
        self.n.check(snap(hours(34, 40), 2), self.now + HOUR)      # new forecast, small change
        self.assertEqual(len(self.n.sent), 1)

    def test_moved_window_gets_an_update(self):
        self.n.check(snap(hours(34, 39), 1), self.now)
        self.n.check(snap(hours(36, 41), 2), self.now + HOUR)
        self.assertEqual(len(self.n.sent), 2)
        self.assertIn("Cambia la ventana", self.n.sent[1])
        self.assertIn("13:00–18:00", self.n.sent[1])

    def test_cancel_needs_two_forecasts(self):
        self.n.check(snap(hours(34, 39), 1), self.now)
        self.n.check(snap(hours(0, 0), 2), self.now + HOUR)
        self.assertEqual(len(self.n.sent), 1)                      # one bad run: wait
        self.n.check(snap(hours(0, 0), 3), self.now + 2 * HOUR)
        self.assertEqual(len(self.n.sent), 2)
        self.assertIn("Se cancela", self.n.sent[1])
        self.assertIn("falta viento", self.n.sent[1])

    def test_window_back_after_one_miss_is_not_reannounced(self):
        self.n.check(snap(hours(34, 39), 1), self.now)
        self.n.check(snap(hours(0, 0), 2), self.now + HOUR)
        self.n.check(snap(hours(34, 39), 3), self.now + 2 * HOUR)
        self.assertEqual(len(self.n.sent), 1)

    def test_failed_send_is_retried(self):
        self.n.fail = True
        self.n.check(snap(hours(34, 39), 1), self.now)
        self.assertEqual(self.n.sent, [])
        self.n.fail = False
        self.n.check(snap(hours(34, 39), 1), self.now + 300_000)
        self.assertEqual(len(self.n.sent), 1)

    def test_state_survives_a_restart(self):
        self.n.check(snap(hours(34, 39), 1), self.now)
        again = FakeNotifier(self.tmp.name)
        again.check(snap(hours(34, 39), 2), self.now + HOUR)
        self.assertEqual(again.sent, [])

    def test_morning_reminder_once(self):
        n = FakeNotifier(self.tmp.name, morning_hour=8)
        n.check(snap(hours(34, 39), 1), self.now)                  # announced the day before
        morning = DAY0 + 24 * HOUR + 7 * HOUR + 15 * 60_000          # 08:15 local the next day
        n.check(snap(hours(34, 39), 1), morning)
        n.check(snap(hours(34, 39), 1), morning + 600_000)
        self.assertEqual(len(n.sent), 2)
        self.assertIn("Hoy hay ventana apta</b>: 11:00–16:00", n.sent[1])

    def test_inactive_without_token_or_chat(self):
        n = notify.Notifier(Path(self.tmp.name), {"chat_id": "-100"})
        self.assertFalse(n.active)


if __name__ == "__main__":
    unittest.main()
